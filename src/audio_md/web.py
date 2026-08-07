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
import tempfile
import threading
import time
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
    if url:
        return _create_youtube_job(url)

    uploads = request.files.getlist("files")
    if not uploads:
        return jsonify({"error": "nenhum arquivo enviado"}), 400

    tmpdir = Path(tempfile.mkdtemp(prefix="audio-md-web-"))
    try:
        files = []
        for i, up in enumerate(uploads):
            path = tmpdir / f"{i:03d}"  # upload names never touch the filesystem
            up.save(path)
            files.append({
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
    gid = group_hash([f["sha256"] for f in files])

    with _jobs_lock:
        # Same group already finished (disk) or already queued/running (memory)?
        existing = _jobs.get(gid)
        if _finished(gid) or (existing and existing["status"] != "error"):
            shutil.rmtree(tmpdir, ignore_errors=True)
            return jsonify({"id": gid}), 201
        job = {"id": gid, "status": "queued", "error": None, "tmpdir": str(tmpdir), "files": files}
        _jobs[gid] = job
        _queue.put(job)
    return jsonify({"id": gid}), 201


def _create_youtube_job(url: str):
    video_id = youtube.video_id_of(url)
    if video_id is None:
        return jsonify({"error": "link do YouTube inválido"}), 400

    gid = group_hash([f"youtube:{video_id}"])
    with _jobs_lock:
        existing = _jobs.get(gid)
        if _finished(gid) or (existing and existing["status"] != "error"):
            return jsonify({"id": gid}), 201
        job = {
            "id": gid, "status": "queued", "error": None, "tmpdir": None,
            "video_id": video_id,
            "files": [{"name": url, "sha256": None, "path": None,
                       "status": "pending", "progress": 0.0, "duration_sec": None}],
        }
        _jobs[gid] = job
        _queue.put(job)
    return jsonify({"id": gid}), 201


@app.errorhandler(413)
def too_large(_e):
    return jsonify({"error": "upload grande demais (limite: 1 GB)"}), 413


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
        finally:
            if job["tmpdir"]:
                shutil.rmtree(job["tmpdir"], ignore_errors=True)


def _run_job(job: dict) -> None:
    transcripts = _transcribe_youtube(job) if job.get("video_id") else _transcribe_uploads(job)
    _finish_group(job, transcripts)


def _transcribe_uploads(job: dict) -> list[str]:
    job["status"] = "transcribing"
    model = None  # (device, WhisperModel), loaded on the first cache miss and reused
    transcripts: list[str] = []

    for f in job["files"]:
        fdir = _outdir() / "audios" / f["sha256"][:SHORT_HASH_LEN]
        tpath = fdir / "transcript.txt"
        if tpath.exists():  # shared with the CLI cache, both directions
            f["status"], f["progress"] = "cached", 1.0
            try:
                f["duration_sec"] = json.loads((fdir / "meta.json").read_text(encoding="utf-8")).get("duration_sec")
            except Exception:  # noqa: BLE001
                pass
            transcripts.append(tpath.read_text(encoding="utf-8").strip())
            continue

        f["status"] = "transcribing"
        meta = {"source_filename": f["name"], "sha256": f["sha256"]}
        text, model = _transcribe_file(Path(f["path"]), f, meta, model)
        f["duration_sec"] = meta.get("duration_sec")
        fdir.mkdir(parents=True, exist_ok=True)
        tpath.write_text(text + "\n", encoding="utf-8")
        (fdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        f["status"], f["progress"] = "done", 1.0
        transcripts.append(text)

    model = None  # release VRAM between jobs  # noqa: F841
    return transcripts


def _transcribe_youtube(job: dict) -> list[str]:
    video_id = job["video_id"]
    f = job["files"][0]
    vdir = _outdir() / "youtube" / video_id
    tpath = vdir / "transcript.txt"

    if tpath.exists():
        f["status"], f["progress"] = "cached", 1.0
        try:
            cached = json.loads((vdir / "meta.json").read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            cached = {}
        f["name"] = cached.get("title") or f["name"]
        f["duration_sec"] = cached.get("duration_sec")
        job["youtube"] = _video_info(cached, video_id)
        return [tpath.read_text(encoding="utf-8").strip()]

    job["status"] = f["status"] = "downloading"
    tmp = Path(tempfile.mkdtemp(prefix="audio-md-web-yt-"))
    job["tmpdir"] = str(tmp)
    audio, info = youtube.download_audio(video_id, tmp)

    job["status"] = f["status"] = "transcribing"
    f["name"] = info.get("title") or f["name"]
    meta = dict(info)  # download_audio already includes video_id
    text, _ = _transcribe_file(audio, f, meta, None)

    vdir.mkdir(parents=True, exist_ok=True)
    tpath.write_text(text + "\n", encoding="utf-8")
    (vdir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    f["status"], f["progress"] = "done", 1.0
    f["duration_sec"] = meta.get("duration_sec")
    job["youtube"] = _video_info(meta, video_id)
    return [text]


def _video_info(meta: dict, video_id: str) -> dict:
    return {
        "video_id": video_id,
        "url": meta.get("url") or f"https://www.youtube.com/watch?v={video_id}",
        "title": meta.get("title"),
        "uploader": meta.get("uploader"),
    }


def _finish_group(job: dict, transcripts: list[str]) -> None:
    merged = "\n\n".join(t for t in transcripts if t).strip()
    gdir = _group_dir(job["id"])
    gdir.mkdir(parents=True, exist_ok=True)
    (gdir / "transcript.txt").write_text(merged + "\n", encoding="utf-8")

    meta = {
        "group_hash": job["id"],
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": [{"name": f["name"], "sha256": f["sha256"], "duration_sec": f["duration_sec"]}
                  for f in job["files"]],
    }
    if job.get("youtube"):
        meta["youtube"] = job["youtube"]
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


def _transcribe_file(audio: Path, f: dict, meta: dict, model):
    """Transcribe one file updating ``f["progress"]``; returns (text, model) for reuse.

    Same device-fallback contract as pipeline._run_transcription, minus the console.
    """
    devices = _transcribe.devices_for(_settings.device)
    if model is not None:  # stick with the device that worked for the previous file
        devices = sorted(devices, key=lambda d: d[0] != model[0])
    for i, (device, compute) in enumerate(devices):
        try:
            if model is None or model[0] != device:
                model = (device, _transcribe.load_model(_settings.whisper_model, device, compute))
            segments, info = _transcribe.start(
                model[1], audio, _settings.lang,
                beam_size=_settings.beam_size, batch_size=_settings.batch_size,
            )
            t0 = time.time()
            parts: list[str] = []
            for seg in segments:
                parts.append(seg.text)
                f["progress"] = min(seg.end / info.duration, 1.0) if info.duration else 0.0
            meta.update({
                "whisper_model": _settings.whisper_model,
                "device": device,
                "beam_size": _settings.beam_size,
                "batch_size": _settings.batch_size,
                "language": info.language,
                "language_probability": round(info.language_probability, 4),
                "duration_sec": round(info.duration, 2),
                "transcribe_sec": round(time.time() - t0, 1),
            })
            return "".join(parts).strip(), model
        except Exception:  # noqa: BLE001
            if i == len(devices) - 1:
                raise
            model = None  # GPU backend broke; retry loading on the next device
    return "", model  # unreachable (last device either returns or raises)


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
