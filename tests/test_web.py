"""Web layer: group hash ordering, the mocked job flow, and the disk fallback."""

from __future__ import annotations

import dataclasses
import io
import shutil

import pytest

from audio_md import web
from audio_md.config import Settings


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
    web._jobs.clear()
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
