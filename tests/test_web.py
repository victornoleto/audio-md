"""Web layer: group hash ordering, the mocked job flow, and the disk fallback."""

from __future__ import annotations

import dataclasses
import io
import json
import shutil

import pytest

from audio_md import web
from audio_md.config import Settings

# capturado antes de qualquer fixture trocar a função pelo dublê
_REAL_TRANSCRIBE_FILE = web._transcribe_file


def test_group_hash_is_order_sensitive():
    a, b = "a" * 64, "b" * 64
    assert web.group_hash([a, b]) == web.group_hash([a, b])
    assert web.group_hash([a, b]) != web.group_hash([b, a])


@pytest.fixture
def client(tmp_path, monkeypatch):
    settings = dataclasses.replace(Settings.resolve(web._default_args()), outdir=str(tmp_path))
    monkeypatch.setattr(web, "_settings", settings)
    monkeypatch.setattr(web, "_transcribe_file", lambda audio, f, meta, model: (f"texto de {f['name']}", model))
    monkeypatch.setattr(web._summarize, "summarize", lambda transcript, model, provider: "## ok")
    monkeypatch.setattr(web.shutil, "which", lambda _: "/bin/true")  # provider CLI "installed"
    web._jobs.clear()
    while not web._queue.empty():
        web._queue.get_nowait()
    return web.app.test_client()


def _submit(client, names):
    res = client.post(
        "/api/jobs",
        data={"files": [(io.BytesIO(name.encode()), name) for name in names]},
        content_type="multipart/form-data",
    )
    assert res.status_code == 201
    return res.get_json()["id"]


def _drain():
    """Run queued jobs synchronously (no worker thread in tests)."""
    while not web._queue.empty():
        web._run_job(web._queue.get_nowait())


def test_job_flow_keeps_order(client):
    gid = _submit(client, ["parte1.ogg", "parte2.ogg"])
    assert client.get(f"/api/jobs/{gid}").get_json()["status"] == "queued"

    _drain()
    data = client.get(f"/api/jobs/{gid}").get_json()
    assert data["status"] == "done"
    assert data["transcript"] == "texto de parte1.ogg\n\ntexto de parte2.ogg"
    assert data["summary"].strip() == "## ok"
    assert [f["name"] for f in data["meta"]["files"]] == ["parte1.ogg", "parte2.ogg"]

    # same content, other order => a different group
    assert _submit(client, ["parte2.ogg", "parte1.ogg"]) != gid


def test_summary_failure_keeps_transcript_and_is_retryable(client, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(web._summarize, "summarize", boom)
    gid = _submit(client, ["x.ogg"])
    _drain()

    data = client.get(f"/api/jobs/{gid}").get_json()
    assert data["status"] == "done"  # the transcript survived the summary failure
    assert data["summary"] is None
    assert "boom" in data["meta"]["summary_error"]
    assert data["transcript"] == "texto de x.ogg"

    # failed summary is retryable: resubmitting re-queues instead of deduping
    monkeypatch.setattr(web._summarize, "summarize", lambda *a: "## ok")
    assert _submit(client, ["x.ogg"]) == gid
    _drain()
    data = client.get(f"/api/jobs/{gid}").get_json()
    assert data["summary"].strip() == "## ok"
    assert "summary_error" not in data["meta"]


def _submit_url(client, url):
    return client.post("/api/jobs", data={"url": url}, content_type="multipart/form-data")


@pytest.fixture
def fake_download(monkeypatch):
    def download(video_id, dest):
        path = dest / f"{video_id}.m4a"
        path.write_bytes(b"audio")
        return path, {
            "video_id": video_id,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "title": "Um vídeo",
            "uploader": "Canal",
        }

    monkeypatch.setattr(web.youtube, "download_audio", download)
    return download


def test_youtube_url_becomes_a_group(client, fake_download, tmp_path):
    res = _submit_url(client, "https://www.youtube.com/watch?v=awdC4RZdT8A")
    assert res.status_code == 201
    gid = res.get_json()["id"]

    _drain()
    data = client.get(f"/api/jobs/{gid}").get_json()
    assert data["status"] == "done"
    assert data["transcript"] == "texto de Um vídeo"
    assert data["summary"].strip() == "## ok"
    assert data["meta"]["youtube"]["title"] == "Um vídeo"
    assert (tmp_path / "youtube" / "awdC4RZdT8A" / "transcript.txt").exists()


def test_youtube_reuses_the_cli_cache_instead_of_downloading(client, fake_download, tmp_path, monkeypatch):
    gid = _submit_url(client, "https://www.youtube.com/watch?v=awdC4RZdT8A").get_json()["id"]
    _drain()

    shutil.rmtree(tmp_path / "groups")  # only the group is gone; youtube/<id>/ remains
    web._jobs.clear()
    monkeypatch.setattr(web.youtube, "download_audio", lambda *a: pytest.fail("baixou de novo"))

    assert _submit_url(client, "https://youtu.be/awdC4RZdT8A").get_json()["id"] == gid
    _drain()
    data = client.get(f"/api/jobs/{gid}").get_json()
    assert data["status"] == "done"
    assert data["transcript"] == "texto de Um vídeo"


def test_youtube_rejects_a_non_youtube_url(client):
    res = _submit_url(client, "https://example.com/algum-video")
    assert res.status_code == 400
    assert "inválido" in res.get_json()["error"]


def test_history_and_disk_fallback(client):
    gid = _submit(client, ["a.ogg", "b.ogg"])
    _drain()

    hist = client.get("/api/history").get_json()
    assert [h["id"] for h in hist] == [gid]
    assert hist[0]["files"] == ["a.ogg", "b.ogg"]

    web._jobs.clear()  # "server restarted": memory gone, disk remains
    data = client.get(f"/api/jobs/{gid}").get_json()
    assert data["status"] == "done"
    assert data["summary"].strip() == "## ok"
    assert client.get("/api/jobs/deadbeef").status_code == 404


class _FakeProcess:
    """Dublê do _ModelProcess que só registra o ciclo de vida."""

    def __init__(self, closed: list, boom: bool = False):
        self._closed, self._boom = closed, boom

    def transcribe(self, audio, f, meta):
        if self._boom:
            raise RuntimeError("boom")
        return f"texto de {f['name']}"

    def close(self):
        self._closed.append(self)


def _use_real_transcribe_file(monkeypatch, closed, boom=False):
    monkeypatch.setattr(web, "_transcribe_file", _REAL_TRANSCRIBE_FILE)
    monkeypatch.setattr(web, "_ModelProcess", lambda: _FakeProcess(closed, boom))


def test_one_worker_per_job_and_it_is_closed(client, monkeypatch):
    """Um worker por job (reusado entre arquivos) e fechado no fim.

    O modelo vive no subprocesso; não fechar é o que deixava a memória presa —
    uma cópia por job, nunca liberada, até a máquina travar.
    """
    closed: list = []
    _use_real_transcribe_file(monkeypatch, closed)

    _submit(client, ["parte1.ogg", "parte2.ogg"])
    _drain()

    assert len(closed) == 1  # um só worker atendeu os dois arquivos, e foi fechado


def test_worker_is_closed_when_the_job_fails(client, monkeypatch):
    """Job que falha não pode deixar o worker órfão segurando o modelo."""
    closed: list = []
    _use_real_transcribe_file(monkeypatch, closed, boom=True)

    _submit(client, ["parte1.ogg"])
    # _drain chama _run_job direto; quem marca o job como "error" é _worker
    with pytest.raises(RuntimeError, match="boom"):
        _drain()

    assert len(closed) == 1  # o finally fechou o worker mesmo com o job explodindo


def _mixed(client, items):
    return client.post("/api/jobs", data={
        "items": json.dumps(items),
        "files": [(io.BytesIO(b"voice"), "voz.ogg")],
    })


def test_mixed_group_order_cache_and_history(client, fake_download, monkeypatch):
    items = [{"type": "youtube", "url": "https://youtu.be/OKKSUpDTfXQ"},
             {"type": "file", "file_index": 0},
             {"type": "youtube", "url": "https://youtu.be/awdC4RZdT8A"}]
    summaries = []
    monkeypatch.setattr(web._summarize, "summarize", lambda text, *args: summaries.append(text) or "resumo")
    gid = _mixed(client, items).get_json()["id"]
    _drain()
    result = client.get(f"/api/jobs/{gid}").get_json()
    assert result["status"] == "done"
    assert result["transcript"].index("OKKSUpDTfXQ") < result["transcript"].index("voz.ogg")
    assert result["transcript"].index("voz.ogg") < result["transcript"].index("awdC4RZdT8A")
    assert summaries == [result["transcript"]]
    assert len(result["meta"]["files"]) == 3
    web._jobs.clear()
    assert client.get(f"/api/jobs/{gid}").get_json()["meta"] == result["meta"]
    monkeypatch.setattr(web.youtube, "download_audio", lambda *a: pytest.fail("cache miss"))
    reordered = _mixed(client, list(reversed(items))).get_json()["id"]
    assert reordered != gid
    _drain()
    assert all(f["status"] == "cached" for f in web._jobs[reordered]["files"])


@pytest.mark.parametrize("items", [None, {}, [], [1], [{"type": "other"}],
    [{"type": "file", "file_index": -1}], [{"type": "file", "file_index": True}],
    [{"type": "file", "file_index": 1}], [{"type": "youtube", "url": "https://example.com"}],
    [{"type": "youtube", "url": "OKKSUpDTfXQ"}],
    [{"type": "file", "file_index": 0}, {"type": "file", "file_index": 0}]])
def test_manifest_validation(client, items):
    assert _mixed(client, items).status_code == 400
    assert web._queue.empty()


def test_mixed_failure_keeps_cache_closes_worker_and_retries(client, fake_download, monkeypatch):
    items = [{"type": "file", "file_index": 0},
             {"type": "youtube", "url": "OKKSUpDTfXQ"}]
    closed = []
    _use_real_transcribe_file(monkeypatch, closed)
    monkeypatch.setattr(web.youtube, "download_audio", lambda *a: (_ for _ in ()).throw(RuntimeError("HTTP 403")))
    gid = _mixed(client, items).get_json()["id"]
    with pytest.raises(RuntimeError, match="HTTP 403"):
        _drain()
    assert len(closed) == 1
    assert web._jobs[gid]["status"] == "error"
    assert not web._group_dir(gid).exists()
    assert not web.Path(web._jobs[gid]["tmpdir"]).exists()
    monkeypatch.setattr(web.youtube, "download_audio", fake_download)
    assert _mixed(client, items).get_json()["id"] == gid
    _drain()
    assert web._jobs[gid]["files"][0]["status"] == "cached"
    assert web._jobs[gid]["status"] == "done"
    assert len(closed) == 2


def test_one_worker_for_multiple_youtube_videos(client, fake_download, monkeypatch):
    closed = []
    _use_real_transcribe_file(monkeypatch, closed)
    res = client.post("/api/jobs", data={"items": json.dumps([
        {"type": "youtube", "url": "OKKSUpDTfXQ"},
        {"type": "youtube", "url": "awdC4RZdT8A"},
    ])})
    assert res.status_code == 201
    _drain()
    assert len(closed) == 1


def test_malformed_manifest_does_not_enqueue(client):
    assert client.post("/api/jobs", data={"items": "["}).status_code == 400
    assert web._queue.empty()


def test_local_video_group_labels_and_summary_input(client, monkeypatch):
    def transcribe(audio, f, meta, model):
        meta["has_video"] = f["name"].endswith(".mp4")
        return f"texto de {f['name']}", model

    texts = []
    monkeypatch.setattr(web, "_transcribe_file", transcribe)
    monkeypatch.setattr(web._summarize, "summarize", lambda text, *a: texts.append(text) or "ok")
    gid = _submit(client, ["video.mp4", "voice.ogg"])
    _drain()
    result = client.get(f"/api/jobs/{gid}").get_json()
    assert "[1 · video.mp4]" in result["transcript"]
    assert "[2 · voice.ogg]" in result["transcript"]
    assert texts == [result["transcript"]]
    assert result["meta"]["files"][0]["has_video"] is True
