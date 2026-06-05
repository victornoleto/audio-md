"""Settings resolution from CLI args + environment (.env supported)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_WHISPER_MODEL = "large-v3"
DEFAULT_LANG = "pt"

# Transcription device. "auto" tries the GPU and falls back to CPU; "cuda"/"cpu"
# force one (no fallback) so the choice in .env is honored exactly.
DEVICES = ("auto", "cuda", "cpu")
DEFAULT_DEVICE = "auto"
DEFAULT_BEAM_SIZE = 5   # faster-whisper default; 1 is ~2x faster, slightly less accurate
DEFAULT_BATCH_SIZE = 8  # BatchedInferencePipeline batch (fits large-v3 int8_float16 on 6 GB)

# Summarization is done by a headless agent CLI (no API key here) — each one
# authenticates through its own existing login.
PROVIDERS = ("claude-cli", "opencode", "codex-cli")
DEFAULT_PROVIDER = "claude-cli"

# Per-provider default model. opencode has no safe default — it needs an explicit
# "provider/model" string pointing at a model the user has connected.
DEFAULT_MODELS = {
    "claude-cli": "sonnet",   # claude CLI alias (sonnet/opus/haiku)
    "opencode": None,
    "codex-cli": "gpt-5.5",
}


def load_env(dirs: list[Path]) -> None:
    """Load .env from each dir (first wins), without overriding the real environment."""
    for d in dirs:
        load_dotenv(d / ".env", override=False)


@dataclass(frozen=True)
class Settings:
    whisper_model: str
    device: str
    beam_size: int
    batch_size: int
    provider: str
    model: str
    lang: str | None  # None = auto-detect
    no_summary: bool
    force: bool
    outdir: str

    @classmethod
    def resolve(cls, args) -> "Settings":
        lang = args.lang if args.lang is not None else os.getenv("WHISPER_LANG", DEFAULT_LANG)

        device = (args.device or os.getenv("WHISPER_DEVICE", DEFAULT_DEVICE)).lower()
        if device not in DEVICES:
            raise ValueError(
                f"unknown device {device!r}; choose one of {', '.join(DEVICES)}"
            )

        provider = args.provider or os.getenv("SUMMARY_PROVIDER", DEFAULT_PROVIDER)
        if provider not in PROVIDERS:
            raise ValueError(
                f"unknown provider {provider!r}; choose one of {', '.join(PROVIDERS)}"
            )

        model = args.summary_model or os.getenv("SUMMARY_MODEL") or DEFAULT_MODELS[provider]
        _validate_model(provider, model)

        return cls(
            whisper_model=args.model or os.getenv("WHISPER_MODEL", DEFAULT_WHISPER_MODEL),
            device=device,
            beam_size=_as_int(args.beam_size, os.getenv("WHISPER_BEAM_SIZE"), DEFAULT_BEAM_SIZE),
            batch_size=_as_int(args.batch_size, os.getenv("WHISPER_BATCH_SIZE"), DEFAULT_BATCH_SIZE),
            provider=provider,
            model=model,
            lang=(lang or None),  # empty string => auto-detect
            no_summary=args.no_summary,
            force=args.force,
            outdir=args.outdir,
        )


def _as_int(flag, env_val: str | None, default: int) -> int:
    """CLI flag (int|None) > env var (str) > default; rejects non-positive values."""
    val = flag if flag is not None else (int(env_val) if env_val not in (None, "") else default)
    if val < 1:
        raise ValueError(f"expected a positive integer, got {val}")
    return val


def _validate_model(provider: str, model: str | None) -> None:
    """opencode needs a 'provider/model' string; the others need a non-empty alias."""
    if provider == "opencode":
        if not model:
            raise ValueError(
                "opencode needs an explicit model in 'provider/model' form "
                "(e.g. anthropic/claude-sonnet-4-5, openai/gpt-5.5). "
                "Set --summary-model or SUMMARY_MODEL."
            )
        if "/" not in model:
            raise ValueError(
                f"opencode model {model!r} must be in 'provider/model' form "
                "(e.g. openai/gpt-5.5)."
            )
    elif not model:
        raise ValueError(
            f"no model for provider {provider!r}; set --summary-model or SUMMARY_MODEL."
        )
