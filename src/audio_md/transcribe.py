"""Local speech-to-text via faster-whisper.

The device comes from config ("auto" tries GPU then CPU; "cuda"/"cpu" force one).
faster-whisper is lazy, so GPU runtime errors (e.g. a missing libcublas/libcudnn)
only surface while the segments are consumed, not at load — the pipeline iterates
the device list and falls back on failure when in "auto" mode.

Transcription uses ``BatchedInferencePipeline`` (VAD-chunked, batched), which is
several times faster than the sequential path on GPU.
"""

from __future__ import annotations

from pathlib import Path

# compute_type per device. int8_float16 fits large-v3 in ~3 GB VRAM; int8 on CPU.
_COMPUTE = {"cuda": "int8_float16", "cpu": "int8"}


def devices_for(device: str) -> list[tuple[str, str]]:
    """Resolve the configured device into an ordered (device, compute_type) list.

    "auto" yields GPU-then-CPU (fallback allowed); "cuda"/"cpu" yield a single
    entry so an explicit choice is honored exactly (its failure surfaces).
    """
    if device == "auto":
        return [("cuda", _COMPUTE["cuda"]), ("cpu", _COMPUTE["cpu"])]
    return [(device, _COMPUTE[device])]


def _preload_cuda_libs() -> None:
    """Make the pip-installed NVIDIA cuDNN/cuBLAS libs loadable by ctranslate2.

    The nvidia-*-cu12 wheels drop their .so files under
    ``site-packages/nvidia/<pkg>/lib``, which is not on the dynamic-loader path,
    so CUDA would fail with e.g. ``libcudnn.so.9 not found``. We preload them
    (cuBLAS first — cuDNN depends on it) into the global symbol namespace so a
    later ``dlopen`` by ctranslate2 reuses them, no LD_LIBRARY_PATH needed.
    No-op when the wheels are absent (a CPU-only install).
    """
    import ctypes
    import glob
    import os

    try:
        import nvidia  # namespace package provided by the nvidia-*-cu12 wheels
    except ImportError:
        return
    for sub in ("cublas", "cudnn"):  # order matters: cuDNN links against cuBLAS
        for root in nvidia.__path__:
            for so in sorted(glob.glob(os.path.join(root, sub, "lib", "*.so*"))):
                try:
                    ctypes.CDLL(so, mode=ctypes.RTLD_GLOBAL)
                except OSError:
                    pass


def load_model(model_name: str, device: str, compute_type: str):
    """Load a WhisperModel for a specific device/compute type."""
    from faster_whisper import WhisperModel

    if device == "cuda":
        _preload_cuda_libs()
    return WhisperModel(model_name, device=device, compute_type=compute_type)


def start(model, audio: Path, lang: str | None, *, beam_size: int, batch_size: int):
    """Begin transcription via the batched pipeline. Returns ``(segments, info)``.

    Lazy: no real work happens until the segments iterator is consumed.
    """
    from faster_whisper import BatchedInferencePipeline

    batched = BatchedInferencePipeline(model=model)
    return batched.transcribe(
        str(audio),
        language=lang or None,
        beam_size=beam_size,
        batch_size=batch_size,
        vad_filter=True,
    )
