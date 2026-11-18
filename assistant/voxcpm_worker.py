"""Persistent local worker for VoxCPM2. Communicates with voice.py over JSONL."""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import soundfile as sf
import torch
from voxcpm import VoxCPM


MODEL_ID = "openbmb/VoxCPM2"
CACHE_DIR = Path(os.environ.get("QUANTA_VOXCPM_CACHE") or r"C:\Users\Public\Quanta\models\hf")
DEFAULT_STEPS = 10
_model: VoxCPM | None = None
_model_device: str | None = None
_load_seconds = 0.0


def _target_device(requested: str) -> str:
    value = str(requested or "auto").strip().lower()
    if value == "cpu":
        return "cpu"
    if value == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("VoxCPM2 просит CUDA, но CUDA недоступна")
    if value in ("cuda", "auto"):
        return "cuda" if torch.cuda.is_available() else "cpu"
    return "cuda" if torch.cuda.is_available() else "cpu"


def _setup_device(device: str) -> None:
    global _model_device
    if _model is None:
        raise RuntimeError("VoxCPM2 ещё не загружена")
    tts = _model.tts_model
    if _model_device != device:
        tts.to(device)
        tts.device = device
        tts.config.device = device
        dtype = torch.bfloat16 if tts.config.dtype in ("bfloat16", "bf16") else torch.float16
        tts.base_lm.setup_cache(1, tts.config.max_length, device, dtype)
        tts.residual_lm.setup_cache(1, tts.config.max_length, device, dtype)
        _model_device = device
    if device == "cuda":
        torch.cuda.synchronize()


def _load_model(device: str) -> None:
    global _model, _model_device, _load_seconds
    if _model is not None:
        _setup_device(device)
        return
    start = time.perf_counter()
    _model = VoxCPM.from_pretrained(
        MODEL_ID,
        cache_dir=str(CACHE_DIR),
        local_files_only=True,
        load_denoiser=False,
        optimize=False,
        device=device,
    )
    _model_device = device
    _load_seconds = time.perf_counter() - start


def _offload_to_cpu() -> float:
    global _model_device
    if _model is None or _model_device != "cuda":
        return 0.0
    start = time.perf_counter()
    _setup_device("cpu")
    torch.cuda.empty_cache()
    torch.cuda.synchronize()
    elapsed = time.perf_counter() - start
    return elapsed


def _synthesize(request: dict[str, Any]) -> dict[str, Any]:
    global _load_seconds
    device = _target_device(str(request.get("device") or "auto"))
    _load_model(device)
    load_seconds = _load_seconds
    _load_seconds = 0.0
    instruct = str(request.get("instruct") or "").strip()
    text = str(request.get("text") or "").strip()
    if not text:
        raise ValueError("Пустой текст")
    steps = max(2, min(10, int(request.get("steps") or DEFAULT_STEPS)))
    cfg_value = max(0.1, min(10.0, float(request.get("cfg_value", 2.0))))
    output_path = Path(str(request.get("output_path") or "")).resolve()
    if not output_path.name:
        raise ValueError("Не задан путь WAV-файла")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prompt = f"({instruct}){text}" if instruct else text
    start = time.perf_counter()
    audio = _model.generate(
        text=prompt,
        cfg_value=cfg_value,
        inference_timesteps=steps,
    )
    elapsed = time.perf_counter() - start
    sample_rate = int(_model.tts_model.sample_rate)
    sf.write(str(output_path), audio, sample_rate)
    duration = float(len(audio) / sample_rate)
    offload_seconds = _offload_to_cpu() if device == "cuda" else 0.0
    return {
        "ok": True,
        "path": str(output_path),
        "duration": duration,
        "sample_rate": sample_rate,
        "device": device,
        "steps": steps,
        "generation_seconds": elapsed,
        "load_seconds": load_seconds,
        "offload_seconds": offload_seconds,
        "rtf": elapsed / duration if duration else None,
        "loaded": _model is not None,
        "resident_device": _model_device,
    }


def _warmup(request: dict[str, Any]) -> dict[str, Any]:
    global _load_seconds
    device = _target_device(str(request.get("device") or "auto"))
    _load_model(device)
    load_seconds = _load_seconds
    _load_seconds = 0.0
    if device == "cuda":
        # A tiny warmup removes lazy-initialization cost from the first real reply.
        _model.generate(
            text="(Natural, warm, conversational Russian female voice.)Привет.",
            cfg_value=2.0,
            inference_timesteps=DEFAULT_STEPS,
        )
    offload_seconds = _offload_to_cpu() if device == "cuda" else 0.0
    return {
        "ok": True,
        "mode": "warmup",
        "load_seconds": load_seconds,
        "offload_seconds": offload_seconds,
        "loaded": _model is not None,
        "resident_device": _model_device,
    }


def _respond(value: dict[str, Any]) -> None:
    protocol = sys.__stdout__
    protocol.write(json.dumps(value, ensure_ascii=True) + "\n")
    protocol.flush()


def main() -> None:
    # Keep stdout exclusively for the JSONL protocol if libraries print logs.
    sys.stdout = sys.stderr
    for line in sys.stdin:
        try:
            request = json.loads(line)
            operation = str(request.get("op") or "speak").lower()
            if operation == "shutdown":
                _respond({"ok": True, "mode": "shutdown"})
                break
            if operation == "warmup":
                _respond(_warmup(request))
            elif operation == "speak":
                _respond(_synthesize(request))
            else:
                raise ValueError(f"Неизвестная операция VoxCPM2: {operation}")
        except Exception as exc:  # noqa: BLE001
            if _model is not None and _model_device == "cuda":
                try:
                    _offload_to_cpu()
                except Exception:  # noqa: BLE001
                    torch.cuda.empty_cache()
            _respond({"ok": False, "error": str(exc), "trace": traceback.format_exc(limit=4)})


if __name__ == "__main__":
    main()
