"""Local-only Ollama vision model management and screenshot analysis."""
from __future__ import annotations

import base64
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

VISION_MODELS = ("qwen3-vl:2b", "qwen3-vl:4b", "qwen3-vl:8b")
MIN_OLLAMA_VERSION = (0, 12, 7)


class VisionError(RuntimeError):
    pass


def _ollama_supported(version: str) -> bool:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", str(version or ""))
    return not match or tuple(map(int, match.groups())) >= MIN_OLLAMA_VERSION


def _base_url(settings: dict[str, Any]) -> str:
    value = str(settings.get("ollama_url") or "http://127.0.0.1:11434").strip().rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1"):
        raise VisionError("Для зрения разрешён только локальный Ollama на этом компьютере")
    if parsed.username or parsed.password:
        raise VisionError("Адрес локального Ollama не должен содержать логин или пароль")
    return value


class VisionService:
    def __init__(self, settings: Callable[[], dict[str, Any]]) -> None:
        self._settings = settings
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {"status": "idle", "model": "", "completed": 0, "total": 0, "message": ""}

    def _request(self, path: str, payload: dict[str, Any] | None = None, *, timeout: int = 30):
        url = _base_url(self._settings()) + path
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"} if data is not None else {})
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read(1000).decode("utf-8", "replace")
            raise VisionError(f"Ollama ответила {exc.code}: {detail[:500]}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise VisionError(f"Не удалось подключиться к локальной Ollama: {exc}") from exc

    def status(self) -> dict[str, Any]:
        settings = self._settings()
        model = str(settings.get("vision_model") or VISION_MODELS[0])
        if model not in VISION_MODELS:
            model = VISION_MODELS[0]
        with self._lock:
            state = dict(self._state)
        try:
            version = ""
            try:
                with self._request("/api/version", timeout=5) as response:
                    version_data = json.loads(response.read(100_000).decode("utf-8", "replace"))
                version = str(version_data.get("version") or "")
            except Exception:
                pass
            with self._request("/api/tags", timeout=5) as response:
                data = json.loads(response.read(2_000_000).decode("utf-8", "replace"))
            installed = {str(item.get("name") or item.get("model") or "") for item in data.get("models", [])}
            supported = _ollama_supported(version)
            state.update({"available": True, "model": model, "installed": any(
                name == model or name.startswith(model + "-") for name in installed
            ), "models": sorted(installed), "ollama_version": version,
                "supported": supported, "error": "" if supported else "Qwen3-VL требует Ollama 0.12.7 или новее"})
        except Exception as exc:  # status should still render when Ollama is off
            state.update({"available": False, "model": model, "installed": False, "models": [],
                          "supported": False, "ollama_version": "", "error": str(exc)[:350]})
        return state

    def pull(self, model: str) -> dict[str, Any]:
        model = str(model or "").strip()
        if model not in VISION_MODELS:
            raise ValueError("Можно загрузить только одну из поддерживаемых моделей Qwen3-VL")
        with self._lock:
            if self._state.get("status") == "pulling":
                raise ValueError("Загрузка vision-модели уже идёт")
            self._state = {"status": "pulling", "model": model, "completed": 0, "total": 0, "message": "Подключаюсь к Ollama"}
        thread = threading.Thread(target=self._pull_worker, args=(model,), daemon=True, name="quanta-vision-pull")
        thread.start()
        return {"ok": True, "status": "pulling", "model": model}

    def _pull_worker(self, model: str) -> None:
        try:
            with self._request("/api/pull", {"name": model, "stream": True}, timeout=1800) as response:
                for raw in response:
                    if not raw.strip():
                        continue
                    item = json.loads(raw.decode("utf-8", "replace"))
                    if item.get("error"):
                        raise VisionError(str(item["error"]))
                    with self._lock:
                        self._state.update({
                            "status": "pulling", "model": model,
                            "completed": int(item.get("completed") or self._state.get("completed") or 0),
                            "total": int(item.get("total") or self._state.get("total") or 0),
                            "message": str(item.get("status") or "Загрузка модели")[:120],
                        })
            with self._lock:
                self._state.update({"status": "ready", "completed": 0, "total": 0, "message": "Модель готова"})
        except Exception as exc:  # noqa: BLE001 - surface Ollama errors in the UI
            with self._lock:
                self._state.update({"status": "error", "message": str(exc)[:400]})

    def analyze(self, image: bytes, prompt: str = "Опиши, что видно на экране. Отвечай по-русски, коротко и по делу.") -> str:
        if not image or len(image) > 15_000_000:
            raise ValueError("Скриншот пустой или слишком большой для локального анализа")
        settings = self._settings()
        model = str(settings.get("vision_model") or VISION_MODELS[0])
        if model not in VISION_MODELS:
            model = VISION_MODELS[0]
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": str(prompt or "")[:1200],
                          "images": [base64.b64encode(image).decode("ascii")]}],
            "stream": False, "keep_alive": "0" if settings.get("resource_profile") == "game" else "1m",
            "options": {"temperature": 0.2, "num_ctx": 8192},
        }
        with self._request("/api/chat", payload, timeout=180) as response:
            data = json.loads(response.read(8_000_000).decode("utf-8", "replace"))
        answer = str((data.get("message") or {}).get("content") or "").strip()
        if not answer:
            raise VisionError("Vision-модель вернула пустой ответ")
        return answer[:12000]
