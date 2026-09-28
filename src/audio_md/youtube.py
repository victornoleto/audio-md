"""YouTube input: video-id parsing and audio-only download (yt-dlp).

Only the audio stream is fetched (``bestaudio``) — transcription never uses the
video track, and faster-whisper decodes m4a/webm directly (ffmpeg), so there is
no post-processing/conversion step.
"""

from __future__ import annotations

import re
import os
import shutil
import subprocess
import tempfile
from importlib.util import find_spec
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}


def video_id_of(arg: str) -> str | None:
    """Extract the 11-char video id from a YouTube URL or a bare id; None if neither."""
    if _ID.match(arg):
        return arg
    url = urlparse(arg if "//" in arg else f"https://{arg}")
    if url.hostname not in _HOSTS:
        return None
    if url.hostname == "youtu.be":
        candidate = url.path.lstrip("/").split("/")[0]
    elif url.path == "/watch":
        candidate = (parse_qs(url.query).get("v") or [""])[0]
    elif url.path.startswith(("/shorts/", "/live/", "/embed/")):
        candidate = url.path.split("/")[2]
    else:
        return None
    return candidate if _ID.match(candidate) else None


def _js_runtimes() -> dict:
    if find_spec("yt_dlp_ejs") is None:
        raise RuntimeError('YouTube: falta yt-dlp-ejs. Execute uv sync com yt-dlp[default].')
    node = os.getenv("YOUTUBE_NODE_PATH") or shutil.which("node")
    if node:
        try:
            version = subprocess.run([node, "--version"], capture_output=True, text=True,
                                     check=True, timeout=5).stdout.strip()
            if int(version.lstrip("v").split(".")[0]) < 22:
                raise ValueError("Node precisa ser >= 22")
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            raise RuntimeError(f"YouTube: Node inválido ({e}). Ajuste YOUTUBE_NODE_PATH.") from e
        return {"node": {"path": node}}
    if shutil.which("deno"):
        return {"deno": {}}
    raise RuntimeError("YouTube: runtime JavaScript ausente. Configure YOUTUBE_NODE_PATH com Node >= 22.")


def download_audio(video_id: str, dest: Path) -> tuple[Path, dict]:
    """Download the audio stream of a video into ``dest``. Returns (file, meta)."""
    from yt_dlp import YoutubeDL  # lazy, like faster_whisper in transcribe.py
    from yt_dlp.utils import DownloadError

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(dest / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        # YouTube extraction needs a JS runtime; accept whichever is installed
        # (yt-dlp's default is deno-only, which this machine doesn't have).
        "js_runtimes": _js_runtimes(),
        "cachedir": False,
        "retries": 0,
        "fragment_retries": 0,
    }
    dest.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        # Each attempt owns its partial files, so a 403 cannot reuse stale data.
        with tempfile.TemporaryDirectory(prefix="download-", dir=dest) as tmp:
            try:
                with YoutubeDL({**opts, "outtmpl": str(Path(tmp) / "%(id)s.%(ext)s")}) as ydl:
                    info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
                    downloaded = Path(ydl.prepare_filename(info))
                path = dest / downloaded.name
                shutil.move(str(downloaded), path)
                break
            except DownloadError as e:
                if attempt == 0 and "HTTP Error 403" in str(e):
                    continue
                raise RuntimeError(f"YouTube {video_id}: {e}") from e
    meta = {
        "video_id": video_id,
        "url": info.get("webpage_url"),
        "title": info.get("title"),
        "uploader": info.get("uploader"),
    }
    return path, meta
