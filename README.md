# audio-md

Transcribe an audio file **locally** and distill it into a Markdown summary. The output
folder is a short slice of the file's **SHA-256 hash**, so the same audio is never reprocessed.

```
audio  ->  ./outputs/{short-hash}/transcript.txt
       ->  ./outputs/{short-hash}/summary.md
       ->  ./outputs/{short-hash}/meta.json   # includes the full sha256
```

- **Transcription:** [`faster-whisper`](https://github.com/SYSTRAN/faster-whisper) (GPU, with automatic CPU fallback).
- **Summarization:** a headless agent CLI — `claude`, `opencode` or `codex` — no API key to manage (each uses its own login).

---

## How it works

1. **Hash** the audio (SHA-256) → the first 8 chars name the output folder (dedup cache).
2. **Transcribe** with faster-whisper (GPU, auto-falling back to CPU), showing a progress bar over the audio timeline.
3. **Summarize** the transcript into `summary.md` via your chosen agent CLI (`claude`/`opencode`/`codex`).

Each step prints a `▸` header so it's always clear where the run is.

---

## Install

Requires **Python ≥ 3.12**, [uv](https://docs.astral.sh/uv/), `ffmpeg`, and — for the
summary — one agent CLI logged in: the [`claude` CLI](https://claude.com/claude-code)
(`claude login`), `opencode`, or `codex`.

```bash
uv sync --extra transcribe                 # CPU only: faster-whisper + CTranslate2
uv sync --extra transcribe --extra gpu     # + GPU acceleration (NVIDIA, recommended)
```

`--extra transcribe` pulls in faster-whisper + CTranslate2. Without it you can still
run the package, but transcription will fail until it's installed.

Install as a standalone command (then drop the `uv run` prefix):

```bash
uv tool install '.[transcribe,gpu]'
audio-md --help
```

### GPU (recommended on the RTX 4050)

The `gpu` extra bundles the CUDA libraries CTranslate2 needs (cuDNN 9 + cuBLAS 12).
They are **preloaded automatically** at runtime, so no `LD_LIBRARY_PATH` setup is
needed — just install the extra and the GPU is used.

The device is chosen by `WHISPER_DEVICE` (or `--device`):

- `auto` (default) — try the GPU, fall back to CPU if it fails.
- `cuda` — force the GPU (errors out if it can't run, instead of silently using CPU).
- `cpu` — force the CPU.

`meta.json` records the `device` actually used, so you can confirm the GPU ran.
`large-v3` with `int8_float16` uses ~3 GB VRAM (fits in 6 GB). On weaker machines use
`--model medium` or `--model small`, or trade accuracy for speed with `--beam-size 1`.

---

## Usage

```bash
uv run audio-md path/to/audio.mp3
uv run audio-md audio.wav --model medium --lang pt
uv run audio-md audio.ogg --no-summary      # transcript only
uv run audio-md audio.mp3 --force           # ignore the cache

# pick the summarization CLI:
uv run audio-md audio.mp3 --provider claude-cli --summary-model opus
uv run audio-md audio.mp3 --provider codex-cli  --summary-model gpt-5.5
uv run audio-md audio.mp3 --provider opencode   --summary-model anthropic/claude-sonnet-4-5
```

Options: `--model` (whisper), `--device` (auto/cuda/cpu), `--beam-size`, `--batch-size`,
`--lang` (empty = auto-detect), `--provider` (claude-cli/opencode/codex-cli),
`--summary-model`, `--no-summary`, `--force`, `--outdir`. Equivalent env vars in
`.env` (see `.env.example`).

Also runnable as a module: `uv run python -m audio_md audio.mp3`.

---

## Layout

```
src/audio_md/
  cli.py          # argparse + entry point
  config.py       # Settings (args + .env)
  console.py      # rich output — sole terminal writer (steps + progress bar)
  hashing.py      # streamed sha256
  transcribe.py   # faster-whisper (GPU/CPU)
  providers.py    # summary agent CLIs (claude/opencode/codex, headless)
  summarize.py    # prompt + provider dispatch
  pipeline.py     # hash → transcribe → summarize
```

## Notes

- Formats: anything `ffmpeg` decodes (mp3, wav, ogg, m4a, ...).
- `meta.json` records the whisper model, detected language, duration, transcription
  time, and the summary provider/model used.
