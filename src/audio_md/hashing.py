"""Streamed file hashing — the digest is the output folder name (natural cache)."""

from __future__ import annotations

import hashlib
from pathlib import Path

_BLOCK = 1024 * 1024  # 1 MB


def sha256_of(path: str | Path) -> str:
    """Return the hex sha256 digest of a file, reading it in blocks."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(_BLOCK), b""):
            h.update(block)
    return h.hexdigest()
