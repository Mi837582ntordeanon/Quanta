"""Local voice pipeline for Q.W.A.N.T.A.: Silero/Qwen3-TTS synthesis and faster-whisper STT."""
from __future__ import annotations

import gc
import hashlib
import importlib.util
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from .qwen_talker import model_status as qwen_talker_model_status
from .storage import MEDIA_DIR, ROOT, Store
from .text_normalize import normalize_for_speech, stress_status

VOICE_DIR = MEDIA_DIR / "voice"
VOICE_DIR.mkdir(parents=True, exist_ok=True)
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
os.environ.setdefault("HF_HOME", str(VOICE_DIR / "hf"))

_QWEN_STATE: dict[str, Any] = {"loaded": False, "model": None, "model_id": None, "device": None, "error": None}
_SILERO_STATE: dict[str, Any] = {"loaded": False, "model": None, "model_path": None, "error": None}
_WHISPER_STATE: dict[str, Any] = {
    "loaded": False, "model": None, "error": None,
    "device": None, "model_size": None, "requested_device": None,
}
_VOXCPM_STATE: dict[str, Any] = {
    "process": None, "responses": None, "loaded": False,
    "resident_device": None, "error": None, "error_trace": None, "load_seconds": None,
}
_QWEN_TALKER_STATE: dict[str, Any] = {
    "process": None, "loaded": False, "backend": None, "error": None, "load_seconds": None,
}
_TTS_LOCK = threading.Lock()
_WHISPER_LOCK = threading.RLock()
DEFAULT_VOXCPM_STEPS = 10
DEFAULT_VOXCPM_CFG = 1.0
DEFAULT_VOXCPM_INSTRUCT = (
    "A young adult woman speaking Russian in a natural, warm mid-range voice. "
    "Relaxed conversational delivery at a steady, unhurried pace. "
    "Clear and close, with subtle pitch variation and lightly amused, playful confidence."
)

# Silero v4 Russian voices. It runs on CPU at ~0.02 RTF, so speech starts in
# fractions of a second and never pauses waiting for the next sentence.
SILERO_SPEAKERS = ("baya", "kseniya", "xenia")
DEFAULT_SILERO_SPEAKER = "baya"
SILERO_MODEL_URL = "https://models.silero.ai/models/tts/ru/v4_ru.pt"
# SHA-256 of the v4_ru.pt artifact served by SILERO_MODEL_URL.
SILERO_MODEL_SHA256 = "896ab96347d5bd781ab97959d4fd6885620e5aab52405d3445626eb7c1414b00"
SILERO_MODEL_MIN_BYTES = 1_000_000
SILERO_SAMPLE_RATE = 48000

# Female CustomVoice speakers. 0.6b ignores tone instructions entirely, so a
# stable default speaker matters much more there than for 1.7b.
FEMALE_SPEAKERS = ("Serena", "Vivian", "Ono_Anna", "Sohee")
DEFAULT_SPEAKER = "Serena"
DEFAULT_INSTRUCT = (
    "Говори женским русским голосом: бодро, уверенно и живо, в нормальном темпе, "
    "с лёгкой иронией. Без сонливости и без излишней эмоциональности."
)
DEFAULT_QWEN_TALKER_INSTRUCT = (
    "A cool, grounded young female voice, early 20s, with a low, slightly raspy alto timbre. "
    "Her delivery is deadpan, dryly sarcastic, and unbothered—like a sharp, cynical tech partner. "
    "She speaks at a relaxed, steady pace with flat, matter-of-fact inflections, zero high-pitched "
    "energy, and no overly emotional ups and downs. Confident, blunt, and direct; calm and steady "
    "when serious, never bubbly or cute."
)
# Lower temperature/top_p than the library defaults keep the prosody from
# swinging between "sleepy" and "hysterical" from phrase to phrase.
_TTS_SAMPLING: dict[str, Any] = {
    "do_sample": True,
    "temperature": 0.72,
    "top_p": 0.9,
    "top_k": 50,
    "repetition_penalty": 1.05,
    "subtalker_temperature": 0.72,
    "subtalker_top_p": 0.9,
}


def _clean_text(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"```[\s\S]*?```", " ", value)
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", value)
    value = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"[#*>`_~\[\]()«»…-]", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > 900:
        value = value[:897].rstrip() + "..."
    return value


def _silero_model_path() -> Path:
    """Return an ASCII-safe checkpoint path.

    torch.package opens the archive through C++ fopen(), which cannot handle
    the Cyrillic user folder on Windows. %PUBLIC% is always ASCII.
    """
    override = str(os.environ.get("QUANTA_SILERO_MODEL") or "").strip()
    if override:
        return Path(override)
    base = Path(os.environ.get("PUBLIC") or r"C:\Users\Public")
    return base / "Quanta" / "models" / "silero" / "v4_ru.pt"


def _ensure_silero_model() -> Path:
    path = _silero_model_path()
    if path.is_file() and path.stat().st_size > SILERO_MODEL_MIN_BYTES:
        digest = hashlib.sha256()
        with path.open("rb") as cached:
            for chunk in iter(lambda: cached.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() == SILERO_MODEL_SHA256:
            return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")

    try:
        digest = hashlib.sha256()
        with urllib.request.urlopen(SILERO_MODEL_URL, timeout=180) as src, tmp.open("wb") as dst:
            for chunk in iter(lambda: src.read(1024 * 1024), b""):
                dst.write(chunk)
                digest.update(chunk)
        if tmp.stat().st_size < SILERO_MODEL_MIN_BYTES:
            raise RuntimeError("Silero: скачанный файл модели слишком мал")
        if digest.hexdigest() != SILERO_MODEL_SHA256:
            raise RuntimeError("Silero: SHA-256 скачанной модели не совпадает с ожидаемым")
        os.replace(tmp, path)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return path


def _load_silero():
    cached = _SILERO_STATE.get("model")
    if cached is not None:
        return cached
    import torch

    path = _ensure_silero_model()
    threads = max(2, min(8, os.cpu_count() or 4))
    torch.set_num_threads(threads)
    importer = torch.package.PackageImporter(str(path))
    model = importer.load_pickle("tts_models", "model")
    _SILERO_STATE.update({
        "loaded": True,
        "model": model,
        "model_path": str(path),
        "error": None,
    })
    return model


def _generate_silero(text: str, speaker: str) -> dict[str, Any]:
    import numpy as np
    import soundfile

    chosen = str(speaker or "").strip().lower()
    if chosen not in SILERO_SPEAKERS:
        chosen = DEFAULT_SILERO_SPEAKER
    model = _load_silero()
    audio = model.apply_tts(text=text, speaker=chosen, sample_rate=SILERO_SAMPLE_RATE)
    if isinstance(audio, (list, tuple)):
        audio = audio[0]
    array = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
    path = VOICE_DIR / f"quanta_silero_{uuid.uuid4().hex[:8]}_{int(time.time())}.wav"
    soundfile.write(str(path), array, SILERO_SAMPLE_RATE)
    return {
        "ok": True,
        "path": str(path),
        "url": f"/media/voice/{path.name}",
        "engine": "silero",
        "model": "v4_ru",
        "speaker": chosen,
        "duration": float(len(array) / SILERO_SAMPLE_RATE),
        "sample_rate": SILERO_SAMPLE_RATE,
    }


def _qwen_ready() -> bool:
    # Status polling must not import PyTorch/Transformers; those can take
    # several seconds and print CUDA/SoX warnings before any TTS is requested.
    return importlib.util.find_spec("qwen_tts") is not None


def _silero_ready() -> bool:
    return all(importlib.util.find_spec(name) is not None for name in ("soundfile", "torch"))


def _whisper_ready() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def _voxcpm_paths() -> tuple[Path, Path, Path]:
    public = Path(os.environ.get("PUBLIC") or r"C:\Users\Public")
    python = Path(os.environ.get("QUANTA_VOXCPM_PYTHON") or public / "Quanta" / "voxcpm-env" / "Scripts" / "python.exe")
    worker = Path(__file__).with_name("voxcpm_worker.py")
    cache = Path(os.environ.get("QUANTA_VOXCPM_CACHE") or public / "Quanta" / "models" / "hf")
    return python, worker, cache


def _voxcpm_ready() -> bool:
    python, worker, cache = _voxcpm_paths()
    if not python.is_file() or not worker.is_file():
        return False
    snapshots = cache / "models--openbmb--VoxCPM2" / "snapshots"
    try:
        return any(
            (snapshot / "model.safetensors").is_file()
            and (snapshot / "audiovae.pth").is_file()
            for snapshot in snapshots.iterdir()
        )
    except OSError:
        return False


def _worker_reader(proc: subprocess.Popen[str], responses: queue.Queue[Any]) -> None:
    assert proc.stdout is not None
    try:
        for line in proc.stdout:
            try:
                responses.put(json.loads(line))
            except ValueError:
                responses.put({"ok": False, "error": f"Некорректный ответ VoxCPM2: {line[:300]}"})
    except Exception as exc:  # noqa: BLE001
        responses.put({"ok": False, "error": f"Не удалось прочитать ответ VoxCPM2: {exc}"})
    finally:
        responses.put(None)


def _ensure_voxcpm_worker() -> tuple[subprocess.Popen[str], queue.Queue[Any]]:
    proc = _VOXCPM_STATE.get("process")
    responses = _VOXCPM_STATE.get("responses")
    if proc is not None and proc.poll() is None and responses is not None:
        return proc, responses
    if not _voxcpm_ready():
        raise RuntimeError("VoxCPM2 не установлена: проверьте локальную модель и voxcpm-env")
    python, worker, cache = _voxcpm_paths()
    env = os.environ.copy()
    env["QUANTA_VOXCPM_CACHE"] = str(cache)
    kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.DEVNULL,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
        "cwd": str(ROOT),
        "env": env,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen([str(python), str(worker)], **kwargs)
    responses = queue.Queue()
    _VOXCPM_STATE.update({
        "process": proc, "responses": responses, "loaded": False,
        "resident_device": None, "error": None,
    })
    threading.Thread(target=_worker_reader, args=(proc, responses), daemon=True, name="voxcpm-reader").start()
    return proc, responses


def _call_voxcpm_worker(request: dict[str, Any], timeout: float = 900.0) -> dict[str, Any]:
    proc, responses = _ensure_voxcpm_worker()
    if proc.stdin is None:
        raise RuntimeError("Канал связи с VoxCPM2 закрыт")
    # ASCII JSON escapes keep non-UTF-8 Windows stdio defaults from turning
    # Cyrillic characters into surrogate code points in the worker process.
    proc.stdin.write(json.dumps(request, ensure_ascii=True) + "\n")
    proc.stdin.flush()
    try:
        response = responses.get(timeout=timeout)
    except queue.Empty as exc:
        proc.kill()
        _VOXCPM_STATE.update({"loaded": False, "resident_device": None, "error": "Тайм-аут синтеза VoxCPM2"})
        raise RuntimeError("VoxCPM2 не ответила за отведённое время") from exc
    if response is None:
        _VOXCPM_STATE.update({"loaded": False, "resident_device": None})
        raise RuntimeError("Процесс VoxCPM2 завершился без ответа")
    if not response.get("ok"):
        _VOXCPM_STATE["error"] = str(response.get("error") or "Ошибка VoxCPM2")
        _VOXCPM_STATE["error_trace"] = str(response.get("trace") or "")
        raise RuntimeError(_VOXCPM_STATE["error"])
    _VOXCPM_STATE.update({
        "loaded": bool(response.get("loaded", True)),
        "resident_device": response.get("resident_device"),
        "load_seconds": response.get("load_seconds") or _VOXCPM_STATE.get("load_seconds"),
        "error": None,
        "error_trace": None,
    })
    return response


def _unload_ollama_for_voxcpm(settings: dict[str, Any]) -> str | None:
    if str(settings.get("provider") or "").lower() != "ollama":
        return None
    base = str(settings.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/")
    model = str(settings.get("model") or "").strip()
    if not model:
        return "Модель Ollama не задана"
    payload = json.dumps({"model": model, "prompt": "", "stream": False, "keep_alive": 0}).encode("utf-8")
    req = urllib.request.Request(base + "/api/generate", data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            response.read()
        return None
    except Exception as exc:  # noqa: BLE001
        return str(exc)


def _speak_voxcpm(text: str, settings: dict[str, Any]) -> dict[str, Any]:
    unload_error = _unload_ollama_for_voxcpm(settings)
    path = VOICE_DIR / f"quanta_voxcpm_{uuid.uuid4().hex[:8]}_{int(time.time())}.wav"
    ascii_output_dir = Path(os.environ.get("PUBLIC") or r"C:\Users\Public") / "Quanta" / "voice-output"
    ascii_output_dir.mkdir(parents=True, exist_ok=True)
    worker_path = ascii_output_dir / path.name
    with _TTS_LOCK:
        info = _call_voxcpm_worker({
            "op": "speak",
            "text": text,
            "instruct": str(settings.get("voice_instruct") or "").strip() or DEFAULT_VOXCPM_INSTRUCT,
            "device": str(settings.get("voice_device") or "auto"),
            "steps": DEFAULT_VOXCPM_STEPS,
            "cfg_value": DEFAULT_VOXCPM_CFG,
            "output_path": str(worker_path),
        })
    generated_path = Path(info["path"])
    if generated_path.resolve() != path.resolve():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(generated_path), str(path))
    final_path, applied_speed = _apply_speed(path, float(settings.get("tts_speed") or 1.0))
    duration = float(info.get("duration") or 0.0)
    if applied_speed != 1.0:
        duration /= applied_speed
    return {
        "ok": True,
        "path": str(final_path),
        "url": f"/media/voice/{final_path.name}",
        "engine": "voxcpm",
        "model": "VoxCPM2 2B",
        "device": info.get("device"),
        "resident_device": info.get("resident_device"),
        "duration": duration,
        "sample_rate": info.get("sample_rate"),
        "generation_seconds": info.get("generation_seconds"),
        "load_seconds": info.get("load_seconds"),
        "offload_seconds": info.get("offload_seconds"),
        "rtf": info.get("rtf"),
        "speed": applied_speed,
        "ollama_unload_error": unload_error,
        "text": text,
    }


def _ensure_qwen_talker_worker() -> subprocess.Popen[str]:
    process = _QWEN_TALKER_STATE.get("process")
    if process is not None and process.poll() is None and process.stdin is not None:
        return process

    env = os.environ.copy()
    kwargs: dict[str, Any] = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "bufsize": 1,
        "cwd": str(ROOT),
        "env": env,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        [sys.executable, "-m", "assistant.qwen_talker", "--worker"],
        **kwargs,
    )
    _QWEN_TALKER_STATE.update({
        "process": process,
        "loaded": False,
        "backend": None,
        "error": None,
    })
    return process


def _call_qwen_talker_worker(request: dict[str, Any], timeout: float = 900.0) -> dict[str, Any]:
    process = _ensure_qwen_talker_worker()
    if process.stdin is None:
        raise RuntimeError("Канал связи с Qwen-Talker закрыт")
    response_path = VOICE_DIR / f".qwen_talker_{uuid.uuid4().hex}.json"
    payload = {**request, "response_path": str(response_path)}
    try:
        process.stdin.write(json.dumps(payload, ensure_ascii=True) + "\n")
        process.stdin.flush()
    except (BrokenPipeError, OSError) as exc:
        _QWEN_TALKER_STATE.update({"process": None, "loaded": False, "error": str(exc)})
        raise RuntimeError("Не удалось передать запрос нативному Qwen-Talker worker") from exc

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if response_path.is_file():
            try:
                response = json.loads(response_path.read_text(encoding="utf-8"))
            finally:
                response_path.unlink(missing_ok=True)
            if not response.get("ok"):
                _QWEN_TALKER_STATE.update({
                    "loaded": bool(response.get("loaded")),
                    "backend": response.get("backend"),
                    "load_seconds": response.get("load_seconds") or _QWEN_TALKER_STATE.get("load_seconds"),
                    "error": str(response.get("error") or "Ошибка нативного Qwen-Talker"),
                })
                raise RuntimeError(_QWEN_TALKER_STATE["error"])
            _QWEN_TALKER_STATE.update({
                "loaded": bool(response.get("loaded", True)),
                "backend": response.get("backend"),
                "load_seconds": response.get("load_seconds") or _QWEN_TALKER_STATE.get("load_seconds"),
                "error": None,
            })
            return response

        return_code = process.poll()
        if return_code is not None:
            response_path.unlink(missing_ok=True)
            message = f"Нативный процесс Qwen-Talker завершился с кодом {return_code}; Quanta продолжает работать."
            _QWEN_TALKER_STATE.update({"process": None, "loaded": False, "backend": None, "error": message})
            raise RuntimeError(message)
        time.sleep(0.05)

    response_path.unlink(missing_ok=True)
    process.kill()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    message = "Qwen-Talker не ответила за отведённое время; её отдельный процесс остановлен."
    _QWEN_TALKER_STATE.update({"process": None, "loaded": False, "backend": None, "error": message})
    raise RuntimeError(message)


def _speak_qwen_talker(text: str, settings: dict[str, Any]) -> dict[str, Any]:
    path = VOICE_DIR / f"quanta_qwen_talker_{uuid.uuid4().hex[:8]}_{int(time.time())}.wav"
    instruction = str(settings.get("voice_instruct") or "").strip() or DEFAULT_QWEN_TALKER_INSTRUCT
    device = str(settings.get("voice_device") or "auto").strip().lower()
    info = _call_qwen_talker_worker({
        "op": "speak",
        "text": text,
        "instruction": instruction,
        "output_path": str(path),
        "device": device,
    })
    _QWEN_TALKER_STATE.update({
        "loaded": True,
        "backend": info.get("backend"),
        "load_seconds": info.get("load_seconds"),
        "error": None,
    })
    clean_path, noise_reduction = _apply_tts_noise_reduction(
        path, bool(settings.get("speech_noise_reduction", True))
    )
    final_path, applied_speed = _apply_speed(clean_path, float(settings.get("tts_speed") or 1.0))
    duration = float(info.get("duration") or 0.0)
    if applied_speed != 1.0:
        duration /= applied_speed
    return {
        "ok": True,
        "path": str(final_path),
        "url": f"/media/voice/{final_path.name}",
        "engine": "qwen_talker",
        "model": Path(str(info.get("model") or "qwen-talker-1.7b-voicedesign-Q8_0.gguf")).name,
        "device": info.get("backend"),
        "duration": duration,
        "sample_rate": info.get("sample_rate"),
        "generation_seconds": info.get("generation_seconds"),
        "elapsed_seconds": info.get("elapsed_seconds"),
        "load_seconds": info.get("load_seconds"),
        "rtf": (float(info.get("generation_seconds") or 0) / duration) if duration else None,
        "speed": applied_speed,
        "noise_reduction": noise_reduction,
        "text": text,
    }


def _device_for(settings: dict[str, Any]) -> str:
    requested = str(settings.get("voice_device") or "auto").strip().lower()
    if requested in ("cpu", "cuda"):
        return requested
    try:
        import torch

        return "cuda" if bool(torch.cuda.is_available()) else "cpu"
    except Exception:  # noqa: BLE001
        return "cpu"


def _unload_qwen() -> None:
    """Drop the current TTS model and release its CUDA memory."""
    model = _QWEN_STATE.get("model")
    _QWEN_STATE.update({"loaded": False, "model": None, "model_id": None, "device": None})
    if model is not None:
        del model
        gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass


def _load_qwen(model_id: str, device: str):
    cached = _QWEN_STATE.get("model")
    if cached is not None and _QWEN_STATE.get("model_id") == model_id and _QWEN_STATE.get("device") == device:
        return cached
    if cached is not None:
        # Loading another checkpoint without freeing the previous one crashes
        # CUDA on 8 GB cards ("device-side assert triggered").
        _unload_qwen()

    from qwen_tts import Qwen3TTSModel

    import torch

    kwargs: dict[str, Any] = {"device_map": device if device == "cuda" else "cpu"}
    if device == "cuda":
        kwargs["dtype"] = torch.float16
        kwargs["attn_implementation"] = "sdpa"
    else:
        kwargs["dtype"] = torch.float32
    model = Qwen3TTSModel.from_pretrained(model_id, **kwargs)
    _QWEN_STATE.update({"loaded": True, "model": model, "model_id": model_id, "device": device, "error": None})
    return model


def _resolve_speaker(model, requested: str) -> str:
    """Return a supported speaker name, falling back to the stable default."""
    requested = str(requested or "").strip()
    try:
        supported = {str(name).strip().lower() for name in (model.get_supported_speakers() or [])}
    except Exception:  # noqa: BLE001
        supported = set()
    if requested and (not supported or requested.lower() in supported):
        return requested
    if not supported or DEFAULT_SPEAKER.lower() in supported:
        return DEFAULT_SPEAKER
    return "Vivian" if "vivian" in supported else requested


def _generate_qwen(model, text: str, speaker: str, instruct: str) -> dict[str, Any]:
    import torch

    # The same seed makes generation reproducible; the sampling settings keep
    # the tone steady between phrases.
    torch.manual_seed(20261111)
    wavs, sample_rate = model.generate_custom_voice(
        text=text,
        speaker=_resolve_speaker(model, speaker),
        language="Russian",
        instruct=instruct or None,
        non_streaming_mode=True,
        **_TTS_SAMPLING,
    )
    audio = wavs[0] if isinstance(wavs, list) and wavs else wavs
    path = VOICE_DIR / f"quanta_{uuid.uuid4().hex[:8]}_{int(time.time())}.wav"
    try:
        import soundfile

        soundfile.write(str(path), audio, sample_rate)
    except Exception:  # noqa: BLE001
        # soundfile is a TTS runtime dependency; keep a stable error path if it is missing.
        import numpy as np

        import wave

        with wave.open(str(path), "wb") as fh:
            fh.setnchannels(1)
            fh.setsampwidth(2)
            fh.setframerate(int(sample_rate))
            np.asarray(audio, dtype="float32").astype("float32").tofile(fh.fileno())
    return {
        "ok": True,
        "path": str(path),
        "url": f"/media/voice/{path.name}",
        "engine": "qwen",
        "model": str(_QWEN_STATE.get("model_id") or ""),
        "duration": float(len(audio) / float(sample_rate)) if hasattr(audio, "__len__") else None,
        "sample_rate": int(sample_rate),
    }


def _apply_tts_noise_reduction(path: Path, enabled: bool) -> tuple[Path, str]:
    """Apply a light FFT denoiser to generated Qwen-Talker audio when available."""
    if not enabled:
        return path, "disabled"
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return path, "ffmpeg_unavailable"
    out = path.with_name(f"{path.stem}_denoised{path.suffix}")
    try:
        subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(path),
             "-af", "afftdn=nr=8:nf=-42:tn=1:gs=4:rf=-55", "-c:a", "pcm_s16le", str(out)],
            check=True,
            capture_output=True,
            timeout=60,
        )
        if not out.is_file() or out.stat().st_size <= 44:
            raise RuntimeError("FFmpeg создал пустой WAV")
    except Exception:  # noqa: BLE001
        out.unlink(missing_ok=True)
        return path, "failed"
    path.unlink(missing_ok=True)
    return out, "applied"


def _apply_speed(path: Path, speed: float) -> tuple[Path, float]:
    """Change tempo without changing pitch. Returns (path, actual_speed)."""
    speed = max(0.8, min(1.4, float(speed or 1.0)))
    if abs(speed - 1.0) < 0.01:
        return path, 1.0
    import shutil as _shutil

    ffmpeg = _shutil.which("ffmpeg")
    if not ffmpeg:
        return path, 1.0
    out = path.with_name(f"{path.stem}_speed{path.suffix}")
    try:
        subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(path),
             "-filter:a", f"atempo={speed:.2f}", str(out)],
            check=True,
            capture_output=True,
            timeout=120,
        )
    except Exception:  # noqa: BLE001
        try:
            out.unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass
        return path, 1.0
    try:
        path.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        pass
    return out, speed


def _speak_silero(text: str, settings: dict[str, Any]) -> dict[str, Any]:
    info = _generate_silero(text, str(settings.get("voice_name") or ""))
    info_path, applied_speed = _apply_speed(Path(info["path"]), float(settings.get("tts_speed") or 1.0))
    if applied_speed != 1.0:
        info["path"] = str(info_path)
        info["url"] = f"/media/voice/{info_path.name}"
        if info.get("duration"):
            info["duration"] = float(info["duration"]) / applied_speed
    return {
        **info,
        "device": "cpu",
        "tone_control": False,
        "speed": applied_speed,
        "text": text,
    }


def _sapi_speak(text: str, path: Path) -> dict[str, Any]:
    import pyttsx3

    engine = pyttsx3.init()
    voices = engine.getProperty("voices") or []
    chosen = None
    for voice in voices:
        name = str(getattr(voice, "name", "") or "")
        languages = str(getattr(voice, "languages", []) or [])
        if "Irina" in name or "ru-RU" in languages or "ru_RU" in languages:
            chosen = voice
            break
    if chosen is not None:
        engine.setProperty("voice", chosen)
    engine.setProperty("rate", 150)
    engine.setProperty("volume", 1.0)
    engine.save_to_file(text, str(path))
    engine.runAndWait()
    if not path.exists():
        raise RuntimeError("SAPI не создал WAV-файл")
    return {"ok": True, "path": str(path), "url": f"/media/voice/{path.name}", "engine": "sapi"}


def speak(text: str, store: Store | None = None) -> dict[str, Any]:
    settings = (store.settings if store is not None else None) or Store().settings
    clean = _clean_text(text)
    result: dict[str, Any] = {"ok": True, "engine": None, "model": None, "text": clean}

    def fail(error: Exception) -> dict[str, Any]:
        return {"ok": False, "engine": None, "model": None, "text": clean, "error": str(error)}

    if not clean:
        result["ok"] = True
        result["engine"] = str(settings.get("voice_engine") or "silero")
        return result

    engine = str(settings.get("voice_engine") or "silero").lower()
    speech_text = clean
    normalization: dict[str, Any] = {"numbers": False, "stress": False, "stress_error": None}
    if engine != "sapi":
        # RUAccent marks stress with literal '+' inside words (e.g. "прив+ет").
        # Qwen-TTS treats those characters as input text and can garble Russian;
        # it handles ordinary Russian orthography itself, so skip stress marks.
        apply_stress = bool(settings.get("speech_auto_stress", True)) and engine not in {"qwen", "qwen_talker"}
        speech_text, normalization = normalize_for_speech(
            clean,
            numbers=bool(settings.get("speech_normalize_numbers", True)),
            stress=apply_stress,
        )

    def with_speech_metadata(info: dict[str, Any]) -> dict[str, Any]:
        return {
            **info,
            "text": clean,
            "spoken_text": speech_text,
            "normalization": normalization,
        }

    if engine == "sapi":
        try:
            path = VOICE_DIR / f"quanta_sapi_{uuid.uuid4().hex[:8]}_{int(time.time())}.wav"
            info = _sapi_speak(clean, path)
            return info
        except Exception as exc:  # noqa: BLE001
            return fail(exc)

    if engine == "silero":
        try:
            with _TTS_LOCK:
                return with_speech_metadata(_speak_silero(speech_text, settings))
        except Exception as exc:  # noqa: BLE001
            # Keep the voice working even if the fast model is missing/corrupt:
            # fall through to Qwen, then SAPI.
            _SILERO_STATE["error"] = str(exc)

    if engine == "voxcpm":
        try:
            return with_speech_metadata(_speak_voxcpm(speech_text, settings))
        except Exception as exc:  # noqa: BLE001
            _VOXCPM_STATE["error"] = str(exc)
            return fail(exc)

    if engine == "qwen_talker":
        try:
            with _TTS_LOCK:
                return with_speech_metadata(_speak_qwen_talker(speech_text, settings))
        except Exception as exc:  # noqa: BLE001
            _QWEN_TALKER_STATE.update({
                "loaded": bool(_QWEN_TALKER_STATE["loaded"]),
                "backend": _QWEN_TALKER_STATE["backend"],
                "error": str(exc),
            })
            return fail(exc)

    selected_model = str(settings.get("tts_model") or "1.7b")
    if selected_model == "0.6b":
        # qwen_tts 0.6b triggers "CUDA device-side assert" on Pascal GPUs
        # (GTX 10xx). Keep the stable checkpoint instead of falling back to SAPI.
        selected_model = "1.7b"
    model_id = f"Qwen/Qwen3-TTS-12Hz-{selected_model}-CustomVoice"
    device = _device_for(settings)
    # 0.6b silently drops instruct in qwen_tts; with 1.7b the tone is controlled.
    tone_control = selected_model != "0.6b"
    instruct = ""
    if tone_control:
        instruct = str(settings.get("voice_instruct") or "").strip() or DEFAULT_INSTRUCT
    speaker = str(settings.get("voice_name") or "").strip() or DEFAULT_SPEAKER
    devices = [device, "cpu"] if device == "cuda" else [device]
    for candidate_device in devices:
        try:
            with _TTS_LOCK:
                model = _load_qwen(model_id, candidate_device)
                info = _generate_qwen(model, speech_text, speaker, instruct)
            info_path, applied_speed = _apply_speed(Path(info["path"]), float(settings.get("tts_speed") or 1.0))
            if applied_speed != 1.0:
                info["path"] = str(info_path)
                info["url"] = f"/media/voice/{info_path.name}"
                if info.get("duration"):
                    info["duration"] = float(info["duration"]) / applied_speed
            return with_speech_metadata({
                "ok": True,
                "path": info["path"],
                "url": info["url"],
                "engine": "qwen",
                "model": selected_model,
                "duration": info.get("duration"),
                "sample_rate": info.get("sample_rate"),
                "device": candidate_device,
                "tone_control": tone_control,
                "speed": applied_speed,
            })
        except Exception as exc:  # noqa: BLE001
            _QWEN_STATE["error"] = str(exc)
    try:
        path = VOICE_DIR / f"quanta_sapi_{uuid.uuid4().hex[:8]}_{int(time.time())}.wav"
        info = _sapi_speak(clean, path)
        return with_speech_metadata({**info, "model": str(settings.get("tts_model") or "1.7b"), "device": "sapi"})
    except Exception as exc:  # noqa: BLE001
        return fail(exc)


def warmup(store: Store | None = None) -> dict[str, Any]:
    """Load the selected voice runtime; exercise the short Silero path.

    Silero loads in under a second, but the first apply_tts call pays a few
    seconds of lazy initialization. Qwen-Talker only loads its large weights
    here so startup does not synthesize and discard an extra phrase.
    """
    settings = (store.settings if store is not None else None) or Store().settings
    if not settings.get("voice_enabled"):
        return {"ok": True, "skipped": "voice disabled"}
    engine = str(settings.get("voice_engine") or "silero").lower()
    if engine == "qwen_talker":
        try:
            with _TTS_LOCK:
                info = _call_qwen_talker_worker({
                    "op": "load",
                    "device": str(settings.get("voice_device") or "cpu"),
                })
            _QWEN_TALKER_STATE.update({
                "loaded": True,
                "backend": info.get("backend"),
                "load_seconds": info.get("load_seconds"),
                "error": None,
            })
            return {"ok": True, "engine": engine, **info}
        except Exception as exc:  # noqa: BLE001
            _QWEN_TALKER_STATE.update({
                "loaded": bool(_QWEN_TALKER_STATE["loaded"]),
                "backend": _QWEN_TALKER_STATE["backend"],
                "error": str(exc),
            })
            return {"ok": False, "engine": engine, "error": str(exc)}
    if engine == "voxcpm":
        try:
            unload_error = _unload_ollama_for_voxcpm(settings)
            with _TTS_LOCK:
                info = _call_voxcpm_worker({"op": "warmup", "device": str(settings.get("voice_device") or "auto")})
            return {
                **info,
                "engine": "voxcpm",
                "ollama_unload_error": unload_error,
            }
        except Exception as exc:  # noqa: BLE001
            _VOXCPM_STATE["error"] = str(exc)
            return {"ok": False, "engine": "voxcpm", "error": str(exc)}
    if engine != "silero":
        return {"ok": True, "skipped": f"engine is {engine}"}
    try:
        with _TTS_LOCK:
            model = _load_silero()
            # The first call for each text-length bucket pays several seconds of
            # lazy initialization. Warm both a short and a long phrase so real
            # replies are fast from the very first sentence.
            model.apply_tts(text="Привет.", speaker=DEFAULT_SILERO_SPEAKER, sample_rate=SILERO_SAMPLE_RATE)
            model.apply_tts(
                text=(
                    "Небо голубое потому что молекулы воздуха рассеивают солнечный свет. "
                    "Синий свет имеет короткую волну и рассеивается сильнее красного. "
                    "Поэтому днём мы видим голубое небо, а на закате красное. "
                    "Это явление называется рэлеевским рассеянием."
                ),
                speaker=DEFAULT_SILERO_SPEAKER,
                sample_rate=SILERO_SAMPLE_RATE,
            )
        return {"ok": True, "engine": "silero", "model": "v4_ru", "device": "cpu"}
    except Exception as exc:  # noqa: BLE001
        _SILERO_STATE["error"] = str(exc)
        return {"ok": False, "engine": "silero", "error": str(exc)}


def _ensure_whisper(store: Store, settings_override: dict[str, Any] | None = None) -> Any:
    settings = settings_override or store.settings
    model_size = str(settings.get("stt_model") or "small")
    requested = str(settings.get("stt_device") or "auto").lower()
    with _WHISPER_LOCK:
        if (_WHISPER_STATE["model"] is not None
                and _WHISPER_STATE["model_size"] == model_size
                and _WHISPER_STATE["requested_device"] == requested):
            return _WHISPER_STATE["model"]

        _WHISPER_STATE.update({"loaded": False, "model": None, "error": None, "device": None})
        from faster_whisper import WhisperModel

        candidates = ["cpu"]
        if requested in ("auto", "cuda"):
            try:
                import ctranslate2
                cuda_available = ctranslate2.get_cuda_device_count() > 0
                cuda_int8 = ("int8" in ctranslate2.get_supported_compute_types("cuda", 0)) if cuda_available else False
            except Exception:
                cuda_available = cuda_int8 = False
            if requested == "cuda" and not cuda_available:
                raise RuntimeError("CTranslate2 не видит CUDA-устройство для Whisper")
            if requested == "cuda" and not cuda_int8:
                raise RuntimeError("CTranslate2 не поддерживает int8 на этой CUDA-видеокарте")
            if cuda_available and cuda_int8:
                candidates = ["cuda", "cpu"] if requested == "auto" else ["cuda"]
            elif requested == "cuda":
                raise RuntimeError("Whisper не может запуститься на этой CUDA-видеокарте")

        failures: list[str] = []
        for device in candidates:
            try:
                model = WhisperModel(model_size, device=device, compute_type="int8")
                fallback = ("GPU недоступен; используется CPU: " + failures[0]) if failures else None
                _WHISPER_STATE.update({
                    "loaded": True, "model": model, "error": fallback,
                    "device": device, "model_size": model_size, "requested_device": requested,
                })
                return model
            except Exception as exc:  # CUDA/OOM can vary with other apps using VRAM.
                failures.append(str(exc))
                if device != "cuda" or requested != "auto":
                    _WHISPER_STATE.update({"loaded": False, "model": None, "error": str(exc)[:300]})
                    raise
        _WHISPER_STATE.update({"loaded": False, "model": None, "error": failures[-1] if failures else "Whisper не загрузилась"})
        raise RuntimeError(_WHISPER_STATE["error"])


def transcribe(audio_bytes: bytes, filename: str = "recording.webm", store: Store | None = None) -> dict[str, Any]:
    current_store = store or Store()
    settings = current_store.settings
    if settings.get("resource_profile", "work") == "game":
        settings = {**settings, "stt_device": "cpu"}
    suffix = ".webm"
    name = str(filename or "recording.webm")
    if "." in name and not name.lower().endswith((".webm", ".wav", ".ogg", ".mp3", ".mp4")):
        suffix = "." + name.rsplit(".", 1)[1]
    path = VOICE_DIR / f"quanta_{uuid.uuid4().hex[:8]}_{int(time.time())}{suffix}"
    path.write_bytes(audio_bytes)
    try:
        with _WHISPER_LOCK:
            model = _ensure_whisper(current_store, settings)
            segments, _info = model.transcribe(str(path))
            text = " ".join(str(segment.text or "").strip() for segment in segments).strip()
        return {"ok": True, "text": text, "duration": None, "device": _WHISPER_STATE.get("device")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "text": "", "error": str(exc)}


def release_loaded_models() -> dict[str, Any]:
    """Release resident voice models and workers when a low-resource profile starts."""
    released: list[str] = []
    errors: list[str] = []
    with _TTS_LOCK:
        qwen_talker = _QWEN_TALKER_STATE.get("process")
        if qwen_talker is not None and qwen_talker.poll() is None:
            try:
                _call_qwen_talker_worker({"op": "shutdown"}, timeout=8.0)
            except Exception as exc:  # noqa: BLE001
                errors.append("Qwen-Talker: " + str(exc)[:150])
            if qwen_talker.poll() is None:
                qwen_talker.terminate()
                try:
                    qwen_talker.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    qwen_talker.kill()
            released.append("qwen_talker")
            _QWEN_TALKER_STATE.update({"process": None, "loaded": False, "backend": None})

        voxcpm = _VOXCPM_STATE.get("process")
        if voxcpm is not None and voxcpm.poll() is None:
            try:
                _call_voxcpm_worker({"op": "shutdown"}, timeout=8.0)
            except Exception as exc:  # noqa: BLE001
                errors.append("VoxCPM2: " + str(exc)[:150])
            if voxcpm.poll() is None:
                voxcpm.terminate()
                try:
                    voxcpm.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    voxcpm.kill()
            released.append("voxcpm")
            _VOXCPM_STATE.update({"process": None, "responses": None, "loaded": False, "resident_device": None})

        if _QWEN_STATE.get("model") is not None:
            _unload_qwen()
            released.append("qwen_tts")
        if _SILERO_STATE.get("model") is not None:
            _SILERO_STATE.update({"loaded": False, "model": None, "model_path": None})
            released.append("silero")

    with _WHISPER_LOCK:
        if _WHISPER_STATE.get("model") is not None:
            _WHISPER_STATE.update({
                "loaded": False, "model": None, "device": None,
                "model_size": None, "requested_device": None,
            })
            released.append("whisper")

    try:
        from .text_normalize import _unload_accentizer
        _unload_accentizer()
    except Exception as exc:  # noqa: BLE001
        errors.append("RUAccent: " + str(exc)[:150])
    gc.collect()
    return {"ok": not errors, "released": released, "errors": errors}


def voice_status(store: Store | None = None) -> dict[str, Any]:
    settings = (store.settings if store is not None else None) or Store().settings
    qwen: dict[str, Any] = {"ready": False, "loaded": False, "model_id": None, "device": None, "error": None}
    try:
        qwen = {
            "ready": bool(_qwen_ready()),
            "loaded": bool(_QWEN_STATE["loaded"]),
            "model_id": _QWEN_STATE["model_id"],
            "device": _QWEN_STATE["device"],
            "error": _QWEN_STATE["error"],
        }
    except Exception as exc:  # noqa: BLE001
        qwen = {"ready": False, "loaded": False, "model_id": None, "device": None, "error": str(exc)}
    silero: dict[str, Any] = {"ready": False, "loaded": False, "model": "v4_ru", "error": None}
    try:
        silero = {
            "ready": bool(_silero_ready()),
            "loaded": bool(_SILERO_STATE["loaded"]),
            "model": "v4_ru",
            "error": _SILERO_STATE["error"],
        }
    except Exception as exc:  # noqa: BLE001
        silero = {"ready": False, "loaded": False, "model": "v4_ru", "error": str(exc)}
    whisper: dict[str, Any] = {
        "ready": False, "loaded": False,
        "model_size": str(settings.get("stt_model") or "small"),
        "device": str(_WHISPER_STATE.get("device") or settings.get("stt_device") or "auto"),
        "error": _WHISPER_STATE["error"],
    }
    try:
        whisper = {
            "ready": bool(_whisper_ready()),
            "loaded": bool(_WHISPER_STATE["loaded"]),
            "model_size": str(settings.get("stt_model") or "small"),
            "device": str(_WHISPER_STATE.get("device") or settings.get("stt_device") or "auto"),
            "error": _WHISPER_STATE["error"],
        }
    except Exception as exc:  # noqa: BLE001
        whisper = {"ready": False, "loaded": False, "model_size": str(settings.get("stt_model") or "small"),
                   "device": str(settings.get("stt_device") or "auto"), "error": str(exc)}
    voxcpm = {
        "ready": _voxcpm_ready(),
        "loaded": bool(_VOXCPM_STATE["loaded"]),
        "resident_device": _VOXCPM_STATE["resident_device"],
        "load_seconds": _VOXCPM_STATE["load_seconds"],
        "error": _VOXCPM_STATE["error"],
    }
    try:
        qwen_talker = {
            **qwen_talker_model_status(),
            "loaded": bool(_QWEN_TALKER_STATE["loaded"]),
            "backend": _QWEN_TALKER_STATE["backend"],
            "load_seconds": _QWEN_TALKER_STATE["load_seconds"],
            "error": _QWEN_TALKER_STATE["error"],
        }
    except Exception as exc:  # noqa: BLE001
        qwen_talker = {"ready": False, "loaded": False, "error": str(exc)}
    engine = str(settings.get("voice_engine") or "silero").lower()
    voice_name = str(settings.get("voice_name") or "").strip()
    if engine == "silero" and voice_name.lower() not in SILERO_SPEAKERS:
        voice_name = DEFAULT_SILERO_SPEAKER
    elif engine == "qwen" and voice_name not in FEMALE_SPEAKERS:
        voice_name = DEFAULT_SPEAKER
    elif engine == "voxcpm" and not voice_name:
        voice_name = "Quanta"
    elif engine == "qwen_talker":
        voice_name = "VoiceDesign"
    return {
        "enabled": bool(settings.get("voice_enabled")),
        "engine": engine,
        "tts_model": "1.7b" if str(settings.get("tts_model") or "1.7b") == "0.6b" else str(settings.get("tts_model") or "1.7b"),
        "voice_name": voice_name or DEFAULT_SILERO_SPEAKER,
        "voice_instruct": str(settings.get("voice_instruct") or (
            DEFAULT_QWEN_TALKER_INSTRUCT if engine == "qwen_talker" else DEFAULT_INSTRUCT
        )),
        "voice_device": str(settings.get("voice_device") or "auto"),
        "tone_control": engine in ("qwen", "voxcpm", "qwen_talker"),
        "tts_speed": float(settings.get("tts_speed") or 1.0),
        "speech_normalize_numbers": bool(settings.get("speech_normalize_numbers", True)),
        "speech_auto_stress": bool(settings.get("speech_auto_stress", True)),
        "speech_processing": stress_status(),
        "stt_model": str(settings.get("stt_model") or "small"),
        "voice_auto_send": bool(settings.get("voice_auto_send")),
        "wake_word": bool(settings.get("wake_word")),
        "silero": silero,
        "qwen": qwen,
        "voxcpm": voxcpm,
        "qwen_talker": qwen_talker,
        "whisper": whisper,
    }
