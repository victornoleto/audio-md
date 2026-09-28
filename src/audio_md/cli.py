"""Command-line entry point for audio-md."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from audio_md import pipeline, youtube
from audio_md.config import Settings, load_env


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="audio-md",
        description=(
            "Transcribe an audio file or YouTube video locally (faster-whisper) and "
            "summarize it into Markdown via an agent CLI (claude/opencode/codex). "
            "Output is written under <outdir>/audios/<sha256>/ or "
            "<outdir>/youtube/<video-id>/."
        ),
    )
    p.add_argument("input", help="Path to an audio/video file, YouTube URL, or YouTube video id.")
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
                   help="Base directory for audios/{hash}/ and youtube/{video-id}/ (default: ./outputs).")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # An existing file wins — a filename could also look like a video id.
    audio = Path(args.input)
    is_file = audio.is_file()
    video_id = None if is_file else youtube.video_id_of(args.input)
    if not is_file and video_id is None:
        print(f"[error] not an audio file nor a YouTube URL/id: {args.input}", file=sys.stderr)
        return 2

    load_env([audio.resolve().parent, Path.cwd()] if is_file else [Path.cwd()])
    try:
        settings = Settings.resolve(args)
    except ValueError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2
    return pipeline.run(str(audio), settings) if is_file else pipeline.run_youtube(video_id, settings)


if __name__ == "__main__":
    raise SystemExit(main())
