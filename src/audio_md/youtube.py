"""YouTube input: video-id parsing and audio-only download (yt-dlp).

Only the audio stream is fetched (``bestaudio``) — transcription never uses the
video track, and faster-whisper decodes m4a/webm directly (ffmpeg), so there is
no post-processing/conversion step.
"""

from __future__ import annotations

import re
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


def download_audio(video_id: str, dest: Path) -> tuple[Path, dict]:
    """Download the audio stream of a video into ``dest``. Returns (file, meta)."""
    from yt_dlp import YoutubeDL  # lazy, like faster_whisper in transcribe.py

    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(dest / "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        # YouTube extraction needs a JS runtime; accept whichever is installed
        # (yt-dlp's default is deno-only, which this machine doesn't have).
        "js_runtimes": {"deno": {}, "node": {}},
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=True)
        path = Path(ydl.prepare_filename(info))
    meta = {
        "video_id": video_id,
        "url": info.get("webpage_url"),
        "title": info.get("title"),
        "uploader": info.get("uploader"),
    }
    return path, meta
