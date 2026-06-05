"""Command-line entry point for audio-md."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from audio_md import pipeline
from audio_md.config import Settings, load_env


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="audio-md",
        description=(
            "Transcribe an audio file locally (faster-whisper) and summarize it into "
            "Markdown via an agent CLI (claude/opencode/codex). Output is written "
            "under <outdir>/<sha256>/."
        ),
    )
    p.add_argument("audio", help="Path to the audio file.")
    p.add_argument("--model", default=None,
                   help="faster-whisper model: tiny/base/small/medium/large-v3 (default: large-v3).")
    p.add_argument("--device", default=None,
                   help="Transcription device: auto/cuda/cpu (default: auto = GPU then CPU fallback).")
    p.add_argument("--beam-size", type=int, default=None,
                   help="Whisper beam size (default: 5; 1 is ~2x faster, slightly less accurate).")
    p.add_argument("--batch-size", type=int, default=None,
                   help="Batched-inference batch size (default: 8).")
    p.add_argument("--lang", default=None,
                   help="Audio language (ISO code; empty string = auto-detect). Default: pt.")
    p.add_argument("--provider", default=None,
                   help="Summary agent CLI: claude-cli/opencode/codex-cli (default: claude-cli).")
    p.add_argument("--summary-model", default=None,
                   help="Summary model. claude-cli: sonnet/opus/haiku; codex-cli: gpt-5.5; "
                        "opencode: 'provider/model' (e.g. anthropic/claude-sonnet-4-5).")
    p.add_argument("--no-summary", action="store_true",
                   help="Only produce transcript.txt (skip the summary).")
    p.add_argument("--force", action="store_true",
                   help="Reprocess even if cached output exists for this hash.")
    p.add_argument("--outdir", default="outputs",
                   help="Base directory where the {hash}/ folder is created (default: ./outputs).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    audio = Path(args.audio)
    if not audio.exists():
        print(f"[error] file not found: {audio}", file=sys.stderr)
        return 2

    load_env([audio.resolve().parent, Path.cwd()])
    try:
        settings = Settings.resolve(args)
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2
    return pipeline.run(str(audio), settings)


if __name__ == "__main__":
    raise SystemExit(main())
