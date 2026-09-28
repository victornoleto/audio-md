"""video_id_of parsing — the one branchy piece of the YouTube flow."""

from audio_md.youtube import video_id_of
from audio_md import youtube
from pathlib import Path
import pytest
import yt_dlp
from yt_dlp.utils import DownloadError

VID = "awdC4RZdT8A"


def test_url_forms():
    assert video_id_of(VID) == VID
    assert video_id_of(f"https://www.youtube.com/watch?v={VID}") == VID
    assert video_id_of(f"https://youtube.com/watch?v={VID}&t=42s") == VID
    assert video_id_of(f"youtube.com/watch?v={VID}") == VID
    assert video_id_of(f"https://youtu.be/{VID}") == VID
    assert video_id_of(f"https://youtu.be/{VID}?si=xyz") == VID
    assert video_id_of(f"https://www.youtube.com/shorts/{VID}") == VID
    assert video_id_of(f"https://www.youtube.com/live/{VID}") == VID
    assert video_id_of(f"https://music.youtube.com/watch?v={VID}") == VID


def test_non_matches():
    assert video_id_of("audio.mp3") is None
    assert video_id_of("tooshort") is None
    assert video_id_of("https://vimeo.com/12345678901") is None
    assert video_id_of("https://www.youtube.com/watch") is None
    assert video_id_of("https://www.youtube.com/@somechannel") is None
    assert video_id_of("https://www.youtube.com/playlist?list=PLx") is None


@pytest.mark.parametrize("failures, expected_calls", [(1, 2), (2, 2), (0, 1)])
def test_download_reextracts_once_on_403_and_cleans_partials(tmp_path, monkeypatch, failures, expected_calls):
    calls = []
    monkeypatch.setattr(youtube, "_js_runtimes", lambda: {"node": {}})

    class FakeDL:
        def __init__(self, opts):
            self.folder = Path(opts["outtmpl"]).parent
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, url, download):
            assert not list(self.folder.iterdir())
            calls.append(url)
            (self.folder / "partial.part").write_bytes(b"partial")
            if len(calls) <= failures:
                raise DownloadError("HTTP Error 403: Forbidden")
            (self.folder / "audio.webm").write_bytes(b"ok")
            return {"id": VID, "title": "video"}
        def prepare_filename(self, info):
            return str(self.folder / "audio.webm")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeDL)
    if failures == 2:
        with pytest.raises(RuntimeError, match=f"{VID}.*403"):
            youtube.download_audio(VID, tmp_path)
        assert not list(tmp_path.iterdir())
    else:
        path, meta = youtube.download_audio(VID, tmp_path)
        assert path.read_bytes() == b"ok"
        assert meta["video_id"] == VID
        assert list(tmp_path.iterdir()) == [path]
    assert len(calls) == expected_calls


def test_runtime_configuration(monkeypatch):
    monkeypatch.setattr(youtube, "find_spec", lambda _: None)
    with pytest.raises(RuntimeError, match="yt-dlp-ejs"):
        youtube._js_runtimes()
    monkeypatch.setattr(youtube, "find_spec", lambda _: object())
    monkeypatch.setenv("YOUTUBE_NODE_PATH", "/configured/node")
    seen = []
    from types import SimpleNamespace
    monkeypatch.setattr(youtube.subprocess, "run", lambda args, **kw: seen.append(args) or SimpleNamespace(stdout="v24.17.0"))
    assert youtube._js_runtimes() == {"node": {"path": "/configured/node"}}
    assert seen == [["/configured/node", "--version"]]


def test_non_403_is_not_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(youtube, "_js_runtimes", lambda: {"node": {}})
    calls = []

    class FailedDL:
        def __init__(self, opts):
            calls.append(opts)
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def extract_info(self, *args, **kwargs):
            raise DownloadError("Video unavailable")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FailedDL)
    with pytest.raises(RuntimeError, match="Video unavailable"):
        youtube.download_audio(VID, tmp_path)
    assert len(calls) == 1
    assert not list(tmp_path.iterdir())


def test_old_node_is_rejected(monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(youtube, "find_spec", lambda _: object())
    monkeypatch.setenv("YOUTUBE_NODE_PATH", "/configured/node")
    monkeypatch.setattr(youtube.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="v18.20.0"))
    with pytest.raises(RuntimeError, match="Node precisa ser >= 22"):
        youtube._js_runtimes()
