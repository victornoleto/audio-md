"""Transcrição num processo separado, para o SO devolver a memória no fim do job.

O ``WhisperModel`` (faster-whisper/CTranslate2) não devolve ao sistema a memória que
aloca: em CPU são ~3 GiB por carga, e ``web.py`` carregava uma cópia nova a cada job,
sem liberar as anteriores. Rodando aqui, matar o processo é o que libera — garantido
pelo SO, sem depender de refcount ou do allocator.

Protocolo, uma linha JSON por mensagem:

    pai   -> {"audio", "model", "devices", "lang", "beam_size", "batch_size"}
    filho -> {"progress": float}*  e então  {"text", "meta"}  ou  {"error"}

O modelo é carregado no primeiro pedido e reusado nos seguintes, então um processo
atende um job inteiro (vários arquivos) antes de morrer. Avisos vão para o stderr,
que o pai herda — no serviço, caem direto no journal.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from audio_md import transcribe as _transcribe


def _emit(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()  # o pai lê linha a linha; sem flush ele espera para sempre


def _transcribe_one(req: dict, state: dict) -> dict:
    """Transcreve um arquivo, tentando cada device em ordem.

    Mesmo contrato de fallback de ``pipeline._run_transcription``, com o aviso indo
    para o stderr em vez do console.
    """
    media = _transcribe.inspect_media(Path(req["audio"]))
    devices = [tuple(d) for d in req["devices"]]
    model = state.get("model")
    if model is not None:  # fica no device que funcionou no arquivo anterior
        devices = sorted(devices, key=lambda d: d[0] != model[0])

    for i, (device, compute) in enumerate(devices):
        try:
            if model is None or model[0] != device:
                model = (device, _transcribe.load_model(req["model"], device, compute))
                state["model"] = model
            segments, info = _transcribe.start(
                model[1], Path(req["audio"]), req["lang"],
                beam_size=req["beam_size"], batch_size=req["batch_size"],
            )
            t0 = time.time()
            parts: list[str] = []
            for seg in segments:
                parts.append(seg.text)
                _emit({"progress": min(seg.end / info.duration, 1.0) if info.duration else 0.0})
            return {
                "text": "".join(parts).strip(),
                "meta": {
                    **media,
                    "whisper_model": req["model"],
                    "device": device,
                    "beam_size": req["beam_size"],
                    "batch_size": req["batch_size"],
                    "language": info.language,
                    "language_probability": round(info.language_probability, 4),
                    "duration_sec": round(info.duration, 2),
                    "transcribe_sec": round(time.time() - t0, 1),
                },
            }
        except Exception as e:  # noqa: BLE001
            if i == len(devices) - 1:
                raise
            print(f"{device} backend failed ({e}); falling back to {devices[i + 1][0]}",
                  file=sys.stderr, flush=True)
            model = state["model"] = None
    raise AssertionError("unreachable: o último device retorna ou levanta")


def main() -> int:
    state: dict = {}  # guarda o modelo carregado entre pedidos do mesmo job
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            _emit(_transcribe_one(json.loads(line), state))
        except Exception as e:  # noqa: BLE001
            _emit({"error": f"{type(e).__name__}: {e}"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
