"""Hash → transcribe → summarize for a single audio file.

Output goes to ``<outdir>/<sha256>/`` — the hash is the folder name, so the same
audio is never reprocessed (natural cache). Use ``--force`` to override.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from audio_md import console, providers, summarize as _summarize, transcribe as _transcribe
from audio_md.hashing import sha256_of

# Short, git-style folder name. 8 hex chars = 32 bits — plenty for a personal cache.
SHORT_HASH_LEN = 8


def _run_transcription(audio: Path, settings, meta: dict) -> str:
    """Transcribe, trying each device in order and falling back on failure.

    Updates ``meta`` in place with the device/language/timing actually used.
    """
    devices = _transcribe.devices_for(settings.device)
    for i, (device, compute) in enumerate(devices):
        last = i == len(devices) - 1
        try:
            with console.status(f"loading whisper model ({settings.whisper_model} · {device})"):
                model = _transcribe.load_model(settings.whisper_model, device, compute)
            console.step(f"Transcribing [dim]({settings.whisper_model} · {device} · beam {settings.beam_size})[/dim]")
            segments, info = _transcribe.start(
                model, audio, settings.lang,
                beam_size=settings.beam_size, batch_size=settings.batch_size,
            )
            t0 = time.time()
            parts: list[str] = []
            with console.transcribe_bar(info.duration) as advance:
                for seg in segments:
                    parts.append(seg.text)
                    advance(seg.end)
            meta.update({
                "whisper_model": settings.whisper_model,
                "device": device,
                "beam_size": settings.beam_size,
                "batch_size": settings.batch_size,
                "language": info.language,
                "language_probability": round(info.language_probability, 4),
                "duration_sec": round(info.duration, 2),
                "transcribe_sec": round(time.time() - t0, 1),
            })
            return "".join(parts).strip()
        except Exception as e:  # noqa: BLE001
            if last:
                raise
            console.warn(f"{device} backend failed ({e}); falling back to CPU")
    return ""  # unreachable (last device either returns or raises)


def run(audio_path: str, settings) -> int:
    audio = Path(audio_path).expanduser().resolve()
    if not audio.is_file():
        console.warn(f"file not found: {audio}")
        return 1

    # 1) Hash -> output folder ------------------------------------------------
    console.step(f"Hashing [bold]{audio.name}[/bold]")
    digest = sha256_of(audio)
    short = digest[:SHORT_HASH_LEN]
    outdir = Path(settings.outdir).expanduser().resolve() / short
    outdir.mkdir(parents=True, exist_ok=True)
    transcript_path = outdir / "transcript.txt"
    summary_path = outdir / "summary.md"
    meta_path = outdir / "meta.json"
    console.note(f"{short} [dim]({digest})[/dim]")
    console.note(f"output: {outdir}")

    meta: dict = {"source_filename": audio.name, "sha256": digest}
    if meta_path.exists():
        try:
            meta.update(json.loads(meta_path.read_text(encoding="utf-8")))
        except Exception:
            pass

    # 2) Transcribe (cached) --------------------------------------------------
    if transcript_path.exists() and not settings.force:
        console.step("Transcribe [dim](cached)[/dim]")
        transcript = transcript_path.read_text(encoding="utf-8")
    else:
        transcript = _run_transcription(audio, settings, meta)
        transcript_path.write_text(transcript + "\n", encoding="utf-8")
        console.note(f"{len(transcript)} chars in {meta['transcribe_sec']}s")

    # 3) Summarize (cached) ---------------------------------------------------
    if settings.no_summary:
        console.note("skipping summary (--no-summary)")
    elif not transcript:
        console.note("transcript empty; skipping summary")
    elif summary_path.exists() and not settings.force:
        console.step("Summarize [dim](cached)[/dim]")
    elif shutil.which(providers.BINARIES[settings.provider]) is None:
        binary = providers.BINARIES[settings.provider]
        console.warn(f"'{binary}' CLI not found in PATH; skipping summary (transcript saved)")
    else:
        label = f"{settings.provider} · {settings.model}"
        console.step(f"Summarizing [dim]({label})[/dim]")
        try:
            with console.status(f"asking {settings.provider} ({settings.model})"):
                md = _summarize.summarize(transcript, settings.model, settings.provider)
            meta.update({"summary_provider": settings.provider, "summary_model": settings.model})
            summary_path.write_text(md + "\n", encoding="utf-8")
            console.note(f"{len(md)} chars")
        except Exception as e:  # noqa: BLE001
            console.warn(f"summary failed: {e}")

    # 4) meta.json + final report --------------------------------------------
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    console.info("")
    console.info("[green]✓[/green] done")
    console.info(f"  {transcript_path}")
    if summary_path.exists():
        console.info(f"  {summary_path}")
    console.info(f"  {meta_path}")
    return 0
