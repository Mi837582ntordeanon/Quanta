"""ctypes bridge to the Qwen-TTS Studio native Qwen3-TTS engine."""
from __future__ import annotations

import array
import ctypes
import json
import os
import subprocess
import sys
import wave
from pathlib import Path
from typing import Any


class _Params(ctypes.Structure):
    _fields_ = [
        ("max_audio_tokens", ctypes.c_int32),
        ("temperature", ctypes.c_float),
        ("top_p", ctypes.c_float),
        ("top_k", ctypes.c_int32),
        ("n_threads", ctypes.c_int32),
        ("print_progress", ctypes.c_int32),
        ("print_timing", ctypes.c_int32),
        ("repetition_penalty", ctypes.c_float),
        ("language_id", ctypes.c_int32),
        ("instruction", ctypes.c_char_p),
        ("speaker", ctypes.c_char_p),
        ("vocoder_left_context_sec", ctypes.c_float),
    ]


class _Result(ctypes.Structure):
    _fields_ = [
        ("audio", ctypes.POINTER(ctypes.c_float)),
        ("audio_len", ctypes.c_int32),
        ("sample_rate", ctypes.c_int32),
        ("success", ctypes.c_int32),
        ("error_msg", ctypes.c_void_p),
        ("t_total_ms", ctypes.c_int64),
    ]


class _Capabilities(ctypes.Structure):
    _fields_ = [
        ("loaded", ctypes.c_int32),
        ("supports_voice_clone", ctypes.c_int32),
        ("supports_named_speakers", ctypes.c_int32),
        ("supports_instruction", ctypes.c_int32),
        ("speaker_embedding_dim", ctypes.c_int32),
        ("speaker_count", ctypes.c_int32),
        ("model_kind", ctypes.c_int32),
    ]


class NativeQwenTalker:
    """Keep a native Qwen-TTS context resident for low-latency synthesis."""

    def __init__(self) -> None:
        self._dll: Any = None
        self._context: int | None = None
        self._dll_directories: list[Any] = []
        self._library_path: Path | None = None
        self._model_path: Path | None = None
        self._device_preference: str | None = None
        self.backend: str | None = None
        self.error: str | None = None
        self.load_seconds: float | None = None

    @staticmethod
    def model_directory() -> Path:
        override = str(os.environ.get("QUANTA_QWEN_TTS_MODELS") or "").strip()
        if override:
            return Path(override).expanduser()
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return local / "Speech Studio" / "models" / "qwen3-tts"

    @staticmethod
    def engine_directory() -> Path:
        override = str(os.environ.get("QUANTA_QWEN_TTS_ENGINE") or "").strip()
        if override:
            return Path(override).expanduser()
        return Path(os.environ.get("ProgramFiles") or r"C:\Program Files") / "qwen-tts-studio"

    @classmethod
    def model_path(cls) -> Path:
        override = str(os.environ.get("QUANTA_QWEN_TTS_MODEL") or "").strip()
        if override:
            return Path(override).expanduser()
        model_dir = cls.model_directory()
        preferred = model_dir / "qwen-talker-1.7b-voicedesign-Q8_0.gguf"
        if preferred.is_file():
            return preferred
        return model_dir / "qwen-talker-1.7b-voicedesign-Q4_K_M.gguf"

    @staticmethod
    def _short_ascii_path(path: Path) -> bytes:
        """Return a Windows 8.3 path because this C API accepts char* paths."""
        if os.name != "nt":
            return os.fsencode(path)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        get_short = kernel32.GetShortPathNameW
        get_short.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        get_short.restype = ctypes.c_uint32
        buffer = ctypes.create_unicode_buffer(32768)
        length = get_short(str(path.resolve()), buffer, len(buffer))
        candidate = buffer.value if length else str(path.resolve())
        try:
            return candidate.encode("ascii")
        except UnicodeEncodeError as exc:
            raise RuntimeError(
                f"У пути к Qwen-TTS нет ASCII-алиаса для нативной библиотеки: {path}"
            ) from exc

    @staticmethod
    def _available_memory_gb() -> float | None:
        if os.name != "nt":
            return None

        class _MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_uint32),
                ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64),
                ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64),
                ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64),
                ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64),
            ]

        status = _MemoryStatus()
        status.dwLength = ctypes.sizeof(status)
        if not ctypes.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx(ctypes.byref(status)):
            return None
        return status.ullAvailPhys / (1024 ** 3)

    @staticmethod
    def studio_is_running() -> bool:
        if os.name != "nt":
            return False
        try:
            result = subprocess.run(
                ["tasklist.exe", "/FI", "IMAGENAME eq qwen-tts-studio.exe", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=3,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return "qwen-tts-studio.exe" in result.stdout.lower()
        except (OSError, subprocess.SubprocessError):
            return False

    @staticmethod
    def _bind(library: Any) -> None:
        library.qwen3_tts_init.argtypes = []
        library.qwen3_tts_init.restype = ctypes.c_void_p
        library.qwen3_tts_free.argtypes = [ctypes.c_void_p]
        library.qwen3_tts_free.restype = None
        library.qwen3_tts_set_backend_preference.argtypes = [ctypes.c_int32]
        library.qwen3_tts_set_backend_preference.restype = ctypes.c_int32
        library.qwen3_tts_set_cpu_threads.argtypes = [ctypes.c_int32]
        library.qwen3_tts_set_cpu_threads.restype = ctypes.c_int32
        library.qwen3_tts_get_active_backend_name.argtypes = []
        library.qwen3_tts_get_active_backend_name.restype = ctypes.c_void_p
        library.qwen3_tts_free_string.argtypes = [ctypes.c_void_p]
        library.qwen3_tts_free_string.restype = None
        library.qwen3_tts_load_models_with_name.argtypes = [
            ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p
        ]
        library.qwen3_tts_load_models_with_name.restype = ctypes.c_int32
        library.qwen3_tts_get_last_error.argtypes = [ctypes.c_void_p]
        library.qwen3_tts_get_last_error.restype = ctypes.c_void_p
        library.qwen3_tts_get_model_capabilities.argtypes = [ctypes.c_void_p]
        library.qwen3_tts_get_model_capabilities.restype = _Capabilities
        library.qwen3_tts_synthesize.argtypes = [ctypes.c_void_p, ctypes.c_char_p, _Params]
        library.qwen3_tts_synthesize.restype = _Result
        library.qwen3_tts_free_result.argtypes = [_Result]
        library.qwen3_tts_free_result.restype = None

    def _native_message(self, function: Any, *args: Any) -> str:
        value = function(*args)
        if not value:
            return ""
        try:
            return ctypes.string_at(value).decode("utf-8", errors="replace")
        finally:
            self._dll.qwen3_tts_free_string(value)

    def _last_error(self) -> str:
        if self._dll is None or not self._context:
            return "Не удалось создать контекст Qwen-TTS"
        return self._native_message(self._dll.qwen3_tts_get_last_error, self._context)

    def load(self, device: str = "auto") -> dict[str, Any]:
        device = str(device or "auto").lower()
        if self._context and self._dll:
            if self._device_preference == device:
                return {
                    "model": str(self._model_path),
                    "backend": self.backend,
                    "load_seconds": self.load_seconds,
                }
            self.close()

        model_dir = self.model_directory()
        model_path = self.model_path()
        tokenizer = model_dir / "qwen-tokenizer-12hz-Q8_0.gguf"
        install_dir = self.engine_directory()
        library_path = install_dir / "qwen3_tts.dll"
        if not library_path.is_file():
            raise RuntimeError(f"Нативный движок Qwen-TTS не найден: {library_path}")
        if not model_path.is_file():
            raise RuntimeError(f"Модель Qwen-Talker VoiceDesign не найдена: {model_path}")
        if not tokenizer.is_file():
            raise RuntimeError(f"Токенизатор Qwen-TTS не найден: {tokenizer}")

        available_gb = self._available_memory_gb()
        if available_gb is not None and available_gb < 4.0:
            studio = " Закрой Qwen-TTS Studio, чтобы не загружать вторую копию модели." if self.studio_is_running() else ""
            raise RuntimeError(
                f"Не загружаю {model_path.name}: свободно {available_gb:.1f} ГБ RAM, "
                f"нужно оставить не меньше 4 ГБ.{studio}"
            )

        if os.name == "nt":
            for directory in (install_dir, model_dir):
                if directory.is_dir():
                    self._dll_directories.append(os.add_dll_directory(str(directory)))
        library = ctypes.CDLL(str(library_path))
        self._bind(library)

        backend_preference = {"auto": 0, "cpu": 1, "cuda": 2}.get(str(device).lower(), 0)
        if not library.qwen3_tts_set_backend_preference(backend_preference):
            if backend_preference == 2:
                raise RuntimeError("Нативный движок Qwen-TTS не поддерживает CUDA")
            raise RuntimeError("Не удалось настроить нативный движок Qwen-TTS")
        library.qwen3_tts_set_cpu_threads(max(2, min(8, os.cpu_count() or 4)))

        started = __import__("time").perf_counter()
        context = library.qwen3_tts_init()
        if not context:
            raise RuntimeError("Qwen-TTS не создал контекст модели")
        model_dir_bytes = self._short_ascii_path(model_dir)
        model_name = model_path.name.encode("ascii")
        if not library.qwen3_tts_load_models_with_name(context, model_dir_bytes, model_name):
            message = self._native_message(library.qwen3_tts_get_last_error, context)
            library.qwen3_tts_free(context)
            raise RuntimeError(message or "Qwen-Talker не загрузилась")

        self._dll = library
        self._context = context
        self._library_path = library_path
        self._model_path = model_path
        self._device_preference = device
        self.load_seconds = __import__("time").perf_counter() - started
        backend_ptr = library.qwen3_tts_get_active_backend_name()
        self.backend = (
            ctypes.string_at(backend_ptr).decode("utf-8", errors="replace") if backend_ptr else "unknown"
        )
        if backend_ptr:
            library.qwen3_tts_free_string(backend_ptr)
        capabilities = library.qwen3_tts_get_model_capabilities(context)
        if not capabilities.loaded or capabilities.model_kind != 3 or not capabilities.supports_instruction:
            self.close()
            raise RuntimeError("Загруженная модель не является Qwen-Talker VoiceDesign с поддержкой промпта")
        self.error = None
        return {
            "model": str(model_path),
            "backend": self.backend,
            "load_seconds": self.load_seconds,
        }

    def synthesize(
        self, text: str, instruction: str, output_path: Path, device: str = "auto"
    ) -> dict[str, Any]:
        import time

        request_started = time.perf_counter()
        self.load(device=device)
        assert self._context and self._dll
        params = _Params(
            max_audio_tokens=4096,
            temperature=0.72,
            top_p=0.9,
            top_k=50,
            n_threads=max(2, min(8, os.cpu_count() or 4)),
            print_progress=0,
            print_timing=0,
            repetition_penalty=1.05,
            language_id=2069,  # Russian
            instruction=instruction.encode("utf-8"),
            speaker=None,
            vocoder_left_context_sec=0.0,
        )
        result = self._dll.qwen3_tts_synthesize(self._context, text.encode("utf-8"), params)
        try:
            if not result.success or not result.audio or result.audio_len <= 0:
                message = (
                    ctypes.string_at(result.error_msg).decode("utf-8", errors="replace")
                    if result.error_msg
                    else self._last_error()
                )
                raise RuntimeError(message or "Qwen-Talker не вернула аудио")
            samples = ctypes.cast(
                result.audio, ctypes.POINTER(ctypes.c_float * result.audio_len)
            ).contents
            pcm = array.array(
                "h",
                (
                    max(-32768, min(32767, int(float(sample) * 32767.0)))
                    for sample in samples
                ),
            )
            if sys.byteorder != "little":
                pcm.byteswap()
            output_path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(output_path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(result.sample_rate)
                wav.writeframes(pcm.tobytes())
            duration = result.audio_len / float(result.sample_rate)
            return {
                "path": str(output_path),
                "duration": duration,
                "sample_rate": int(result.sample_rate),
                "generation_seconds": float(result.t_total_ms) / 1000.0,
                "elapsed_seconds": time.perf_counter() - request_started,
                "backend": self.backend,
                "load_seconds": self.load_seconds,
                "model": str(self._model_path),
            }
        finally:
            self._dll.qwen3_tts_free_result(result)

    def close(self) -> None:
        if self._context and self._dll:
            self._dll.qwen3_tts_free(self._context)
        self._context = None
        self._dll = None
        self._model_path = None
        self._device_preference = None
        self.backend = None


def model_status() -> dict[str, Any]:
    model_dir = NativeQwenTalker.model_directory()
    model = NativeQwenTalker.model_path()
    engine = NativeQwenTalker.engine_directory() / "qwen3_tts.dll"
    tokenizer = model_dir / "qwen-tokenizer-12hz-Q8_0.gguf"
    available_gb = NativeQwenTalker._available_memory_gb()
    studio_running = NativeQwenTalker.studio_is_running()
    ready = bool(model.is_file() and tokenizer.is_file() and engine.is_file())
    resource_warning = None
    if ready and available_gb is not None and available_gb < 4.0:
        suffix = " Закрой Qwen-TTS Studio перед загрузкой второй копии." if studio_running else ""
        resource_warning = (
            f"Сейчас свободно {available_gb:.1f} ГБ RAM; для {model.name} нужно оставить минимум 4 ГБ."
            f"{suffix}"
        )
    return {
        "ready": ready,
        "available_ram_gb": round(available_gb, 1) if available_gb is not None else None,
        "studio_running": studio_running,
        "resource_warning": resource_warning,
        "model": model.name,
        "model_path": str(model),
        "engine_path": str(engine),
        "error": None,
    }


def _worker_main() -> None:
    """Serve line-oriented requests; isolate native crashes from Quanta."""
    engine = NativeQwenTalker()
    for line in sys.stdin:
        response_path: Path | None = None
        operation = ""
        request: dict[str, Any] = {}
        try:
            request = json.loads(line)
            response_path = Path(str(request["response_path"]))
            operation = str(request.get("op") or "")
            if operation == "load":
                info = engine.load(device=str(request.get("device") or "cpu"))
                response = {"ok": True, "loaded": True, **info}
            elif operation == "speak":
                info = engine.synthesize(
                    str(request.get("text") or ""),
                    str(request.get("instruction") or ""),
                    Path(str(request["output_path"])),
                    device=str(request.get("device") or "cpu"),
                )
                response = {"ok": True, "loaded": True, **info}
            elif operation == "shutdown":
                engine.close()
                response = {"ok": True, "loaded": False}
            else:
                response = {"ok": False, "error": f"Неизвестная команда Qwen-Talker: {operation}"}
        except Exception as exc:  # noqa: BLE001
            raw_response_path = str(request.get("response_path") or "")
            response_path = Path(raw_response_path) if raw_response_path else None
            response = {
                "ok": False,
                "loaded": bool(engine._context),
                "backend": engine.backend,
                "load_seconds": engine.load_seconds,
                "error": str(exc),
            }
        if response_path is not None:
            try:
                response_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = response_path.with_name(response_path.name + ".part")
                temporary.write_text(json.dumps(response, ensure_ascii=True), encoding="utf-8")
                os.replace(temporary, response_path)
            except OSError:
                pass
        if operation == "shutdown":
            break
    engine.close()


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    _worker_main()
