"""Local web UI: drop N audios as ONE ordered group → merged transcript + one summary.

Runs on localhost for a single user. Jobs live in memory and are processed by a
single daemon worker thread (the GPU serializes transcription anyway); the browser
polls ``GET /api/jobs/<id>``. Per-file transcripts share the CLI cache at
``<outdir>/audios/<sha256[:8]>/``; the merged transcript + summary of a group go to
``<outdir>/groups/<group-hash>/``, which doubles as the history on disk.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request

from audio_md import providers, summarize as _summarize, transcribe as _transcribe, youtube
from audio_md.config import Settings, load_env
from audio_md.hashing import sha256_of
from audio_md.pipeline import SHORT_HASH_LEN

app = Flask(__name__, static_folder=str(Path(__file__).parent / "static"), static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 1 << 30  # 1 GB per request; plenty for voice messages

# ponytail: unbounded in-memory job dict + a lone worker thread — fine for a local,
# restart-often, single-user app. Persist/queue-broker only if this ever leaves localhost.
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()  # guards check-then-insert in create_job (Flask is threaded)
_queue: queue.Queue = queue.Queue()
_settings: Settings | None = None  # set by main() (tests inject their own)


def group_hash(digests: list[str]) -> str:
    """Order-sensitive group id: hash of the per-file sha256s, joined in user order."""
    return hashlib.sha256("\n".join(digests).encode()).hexdigest()[:SHORT_HASH_LEN]


def _outdir() -> Path:
    return Path(_settings.outdir).expanduser().resolve()


def _group_dir(gid: str) -> Path:
    return _outdir() / "groups" / gid


def _finished(gid: str) -> bool:
    """A group is final on disk unless its summary failed (that one is retryable)."""
    meta_path = _group_dir(gid) / "meta.json"
    if not meta_path.exists():
        return False
    try:
        return "summary_error" not in json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False


# Routes ------------------------------------------------------------------------


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.post("/api/jobs")
def create_job():
    url = (request.form.get("url") or "").strip()
    uploads = request.files.getlist("files")
    try:
        if "items" in request.form:
            if url:
                raise ValueError("use items ou url, não ambos")
            items = json.loads(request.form["items"])
        else:
            items = ([{"type": "youtube", "url": url}] if url else
                     [{"type": "file", "file_index": i} for i in range(len(uploads))])
        if not isinstance(items, list) or not items:
            raise ValueError("nenhum item enviado")
        indexes = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("item inválido")
            if item.get("type") == "youtube":
                value = item.get("url")
                vid = youtube.video_id_of(value.strip()) if isinstance(value, str) else None
                if vid is None:
                    raise ValueError("link do YouTube inválido")
                item["video_id"] = vid
            elif item.get("type") == "file":
                idx = item.get("file_index")
                if type(idx) is not int or not 0 <= idx < len(uploads):
                    raise ValueError("referência de arquivo inválida")
                indexes.append(idx)
            else:
                raise ValueError("tipo de item inválido")
        if sorted(indexes) != list(range(len(uploads))):
            raise ValueError("cada arquivo deve aparecer exatamente uma vez")
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400

    tmpdir = Path(tempfile.mkdtemp(prefix="audio-md-web-"))
    try:
        files = []
        for i, item in enumerate(items):
            if item["type"] == "youtube":
                files.append({"type": "youtube", "video_id": item["video_id"],
                              "name": item["url"].strip(), "sha256": None, "path": None,
                              "status": "pending", "progress": 0.0, "duration_sec": None})
                continue
            up = uploads[item["file_index"]]
            path = tmpdir / f"{i:03d}"  # upload names never touch the filesystem
            up.save(path)
            files.append({
                "type": "file",
                "name": up.filename or f"audio-{i + 1}",
                "sha256": sha256_of(path),
                "path": str(path),
                "status": "pending",
                "progress": 0.0,
                "duration_sec": None,
            })
    except Exception as e:  # noqa: BLE001 — disk full, aborted stream, ...
        shutil.rmtree(tmpdir, ignore_errors=True)
        return jsonify({"error": f"falha ao receber os arquivos: {e}"}), 500
    gid = group_hash([f"youtube:{f['video_id']}" if f["type"] == "youtube" else f["sha256"]
                      for f in files])

    with _jobs_lock:
        # Same group already finished (disk) or already queued/running (memory)?
        existing = _jobs.get(gid)
        if _finished(gid) or (existing and existing["status"] not in ("error", "done")):
            shutil.rmtree(tmpdir, ignore_errors=True)
            return jsonify({"id": gid}), 201
        job = {"id": gid, "status": "queued", "error": None, "tmpdir": str(tmpdir), "files": files}
        _jobs[gid] = job
        _queue.put(job)
    return jsonify({"id": gid}), 201


@app.errorhandler(413)
def too_large(_e):
    return jsonify({"error": "upload grande demais (limite: 1 GiB)"}), 413


@app.get("/api/jobs/<gid>")
def get_job(gid: str):
    job = _jobs.get(gid)
    if job is not None:
        out = {
            "id": gid, "status": job["status"], "error": job["error"],
            "files": [{k: f[k] for k in ("name", "status", "progress")} for f in job["files"]],
        }
        if job["status"] == "done":
            out.update(_read_result(gid))
        return jsonify(out)

    result = _read_result(gid)  # disk fallback: history item / server restarted
    if result:
        files = [{"name": f["name"], "status": "done", "progress": 1.0}
                 for f in result["meta"].get("files", [])]
        return jsonify({"id": gid, "status": "done", "error": None, "files": files, **result})
    return jsonify({"error": "job não encontrado"}), 404


@app.get("/api/history")
def history():
    items = []
    for meta_path in _outdir().glob("groups/*/meta.json"):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        items.append({
            "id": meta.get("group_hash", meta_path.parent.name),
            "created_at": meta.get("created_at", ""),
            "files": [f.get("name", "?") for f in meta.get("files", [])],
        })
    items.sort(key=lambda x: x["created_at"], reverse=True)
    return jsonify(items)


def _read_result(gid: str) -> dict:
    """Transcript/summary/meta of a finished group, or {} if it isn't on disk."""
    gdir = _group_dir(gid)
    meta_path = gdir / "meta.json"
    if not meta_path.exists():
        return {}
    summary = gdir / "summary.md"
    return {
        "transcript": (gdir / "transcript.txt").read_text(encoding="utf-8").strip(),
        "summary": summary.read_text(encoding="utf-8") if summary.exists() else None,
        "meta": json.loads(meta_path.read_text(encoding="utf-8")),
    }


# Worker ------------------------------------------------------------------------


def _worker() -> None:
    while True:
        job = _queue.get()
        try:
            _run_job(job)
        except Exception as e:  # noqa: BLE001
            job["status"] = "error"
            job["error"] = str(e)


def _run_job(job: dict) -> None:
    try:
        transcripts = _transcribe_items(job)
        _finish_group(job, transcripts)
    except Exception as e:
        job["status"], job["error"] = "error", str(e)
        raise
    finally:
        if job["tmpdir"]:
            shutil.rmtree(job["tmpdir"], ignore_errors=True)


def _transcribe_items(job: dict) -> list[str]:
    job["status"] = "transcribing"
    model = None  # _ModelProcess do job, criado no primeiro cache miss e reusado
    transcripts: list[str] = []

    try:
        for f in job["files"]:
            vid = f.get("video_id")
            fdir = (_outdir() / "youtube" / vid if vid else
                    _outdir() / "audios" / f["sha256"][:SHORT_HASH_LEN])
            tpath = fdir / "transcript.txt"
            if tpath.exists():  # shared with the CLI cache, both directions
                f["status"], f["progress"] = "cached", 1.0
                try:
                    cached = json.loads((fdir / "meta.json").read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    cached = {}
                f["duration_sec"] = cached.get("duration_sec")
                f["has_video"] = cached.get("has_video", False)
                if vid:
                    f["name"] = cached.get("title") or f["name"]
                    f["youtube"] = _video_info(cached, vid)
                transcripts.append(tpath.read_text(encoding="utf-8").strip())
                continue

            meta = {"source_filename": f["name"], "sha256": f["sha256"]}
            try:
                if vid:
                    job["status"] = f["status"] = "downloading"
                    dest = Path(job["tmpdir"]) / vid
                    dest.mkdir(parents=True, exist_ok=True)
                    audio, meta = youtube.download_audio(vid, dest)
                    f["name"] = meta.get("title") or f["name"]
                    f["youtube"] = _video_info(meta, vid)
                else:
                    audio = Path(f["path"])
                job["status"] = f["status"] = "transcribing"
                text, model = _transcribe_file(audio, f, meta, model)
            except Exception as e:
                f["status"] = "error"
                raise RuntimeError(f"{f['name']}: {e}") from e
            f["duration_sec"] = meta.get("duration_sec")
            f["has_video"] = meta.get("has_video", False)
            fdir.mkdir(parents=True, exist_ok=True)
            tpath.write_text(text + "\n", encoding="utf-8")
            (fdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            f["status"], f["progress"] = "done", 1.0
            transcripts.append(text)
    finally:
        if model is not None:
            model.close()  # release memory between jobs (o SO devolve ao matar o worker)
    return transcripts


def _video_info(meta: dict, video_id: str) -> dict:
    return {
        "video_id": video_id,
        "url": meta.get("url") or f"https://www.youtube.com/watch?v={video_id}",
        "title": meta.get("title"),
        "uploader": meta.get("uploader"),
    }


def _finish_group(job: dict, transcripts: list[str]) -> None:
    merged = "\n\n".join(t for t in transcripts if t).strip()
    if len(transcripts) > 1 and any(f.get("youtube") or f.get("has_video") for f in job["files"]):
        merged = "\n\n".join(
            f"[{i + 1} · {f['name']}]\n" +
            (f["youtube"]["url"] + "\n" if f.get("youtube") else "") + t
            for i, (f, t) in enumerate(zip(job["files"], transcripts)) if t
        )
    gdir = _group_dir(job["id"])
    gdir.mkdir(parents=True, exist_ok=True)
    (gdir / "transcript.txt").write_text(merged + "\n", encoding="utf-8")

    meta = {
        "group_hash": job["id"],
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": [{k: f[k] for k in ("name", "sha256", "duration_sec", "type", "youtube", "has_video") if k in f}
                  for f in job["files"]],
    }
    if len(job["files"]) == 1 and job["files"][0].get("youtube"):
        meta["youtube"] = job["files"][0]["youtube"]
    # A summary failure must never discard a finished transcript: the job still
    # completes, the error is recorded, and resubmitting the group retries it.
    binary = providers.BINARIES[_settings.provider]
    if not merged:
        pass  # nothing to summarize
    elif shutil.which(binary) is None:
        meta["summary_error"] = f"CLI '{binary}' não encontrado no PATH"
    else:
        job["status"] = "summarizing"
        try:
            md = _summarize.summarize(merged, _settings.model, _settings.provider)
            (gdir / "summary.md").write_text(md + "\n", encoding="utf-8")
            meta.update({"summary_provider": _settings.provider, "summary_model": _settings.model})
        except Exception as e:  # noqa: BLE001
            meta["summary_error"] = str(e)
    (gdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    job["status"] = "done"


class _ModelProcess:
    """Subprocesso que segura o modelo carregado enquanto um job roda.

    Existe porque o WhisperModel não devolve a memória que aloca: matar o processo
    é o que a devolve ao SO. Um por job, reusado entre os arquivos daquele job.
    """

    def __init__(self) -> None:
        self._p = subprocess.Popen(
            [sys.executable, "-m", "audio_md.transcribe_worker"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1,
            # stderr herdado de propósito: os avisos de fallback caem no log do serviço
        )

    def transcribe(self, audio: Path, f: dict, meta: dict) -> str:
        """Manda um arquivo e consome o progresso até o resultado final."""
        req = {
            "audio": str(audio),
            "model": _settings.whisper_model,
            "devices": _transcribe.devices_for(_settings.device),
            "lang": _settings.lang,
            "beam_size": _settings.beam_size,
            "batch_size": _settings.batch_size,
        }
        self._p.stdin.write(json.dumps(req) + "\n")
        self._p.stdin.flush()
        for line in self._p.stdout:
            msg = json.loads(line)
            if "progress" in msg:
                f["progress"] = msg["progress"]
                continue
            if "error" in msg:
                raise RuntimeError(msg["error"])
            meta.update(msg["meta"])
            return msg["text"]
        raise RuntimeError("worker de transcrição morreu sem responder")

    def close(self) -> None:
        """Mata o worker — é isto que devolve a memória do modelo ao SO."""
        if self._p.poll() is not None:
            return
        self._p.stdin.close()
        try:
            self._p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._p.kill()
            self._p.wait()


def _transcribe_file(audio: Path, f: dict, meta: dict, model):
    """Transcribe one file updating ``f["progress"]``; returns (text, model) for reuse.

    ``model`` é o _ModelProcess do job: criado no primeiro arquivo, reusado nos
    seguintes. O fallback de device acontece dentro dele (ver transcribe_worker).
    """
    if model is not None:
        return model.transcribe(audio, f, meta), model  # já é do chamador: ele fecha
    model = _ModelProcess()
    try:
        return model.transcribe(audio, f, meta), model
    except Exception:
        model.close()  # falhou antes de chegar ao chamador: fecha quem criou
        raise


def start_worker() -> None:
    threading.Thread(target=_worker, daemon=True).start()


def _default_args() -> argparse.Namespace:
    """All-None namespace: Settings.resolve falls through to .env / defaults."""
    return argparse.Namespace(
        model=None, device=None, beam_size=None, batch_size=None, lang=None,
        provider=None, summary_model=None, no_summary=False, force=False, outdir="outputs",
    )


def main() -> int:
    global _settings
    load_env([Path.cwd()])
    _settings = Settings.resolve(_default_args())
    start_worker()
    # Loopback by default (nothing reaches the network). A container sets
    # WEB_HOST=0.0.0.0 and lets the port publishing keep it on the host's loopback.
    host = os.getenv("WEB_HOST", "127.0.0.1")
    port = int(os.getenv("WEB_PORT", "8765"))
    print(f"audio-md web · http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}")
    # debug=False: the reloader would fork a second process and duplicate the worker.
    app.run(host=host, port=port, threaded=True, debug=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
