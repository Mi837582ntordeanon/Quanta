"""Local HTTP server for Q.W.A.N.T.A.

Binds strictly to 127.0.0.1.  The browser UI is served from ``ui/``; the agent
streams NDJSON events over ``POST /api/chat``.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import json
import mimetypes
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assistant.agent import Agent  # noqa: E402
from assistant import llm  # noqa: E402
from assistant import voice  # noqa: E402
from assistant.browser import open_app_window  # noqa: E402
from assistant.hotkey import GlobalHotkey  # noqa: E402
from assistant.storage import (  # noqa: E402
    DATA_DIR, MEDIA_DIR, UI_DIR, Store, new_id,
)
from assistant.tools import Toolbox  # noqa: E402

PORT = int(os.environ.get("QUANTA_PORT", "8765"))
VERSION = "0.1.0"
CREATE_NO_WINDOW = 0x08000000

STORE = Store()
TOOLBOX = Toolbox(STORE)
ACTIVE: dict[str, threading.Event] = {}
ACTIVE_LOCK = threading.Lock()
PENDING: dict[str, dict[str, Any]] = {}
PENDING_LOCK = threading.Lock()
STOP = threading.Event()
HOTKEY: GlobalHotkey | None = None
MODEL_WARMUP_LOCK = threading.Lock()
MODEL_WARMUP_STATE: dict[str, Any] = {"status": "waiting", "seconds": None, "message": ""}
RESOURCE_LOCK = threading.RLock()
RESOURCE_OPERATION_LOCK = threading.Lock()
RESOURCE_STATE: dict[str, Any] = {
    "status": "ready", "profile": STORE.settings.get("resource_profile", "work"),
    "message": "Рабочий профиль",
}
LAST_ACTIVITY = time.time()
LOG_LOCK = threading.Lock()
LOG_PATH = DATA_DIR / "quanta.log"

mimetypes.add_type("application/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("model/stl", ".stl")


def log(message: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {message}\n"
    try:
        with LOG_LOCK:
            if LOG_PATH.exists() and LOG_PATH.stat().st_size > 2_000_000:
                LOG_PATH.replace(DATA_DIR / "quanta.old.log")
            with LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except OSError:
        pass


def touch() -> None:
    global LAST_ACTIVITY
    LAST_ACTIVITY = time.time()


# --------------------------------------------------------------------- health
def health_info() -> dict[str, Any]:
    settings = STORE.settings
    provider = settings.get("provider", "ollama")
    result: dict[str, Any] = {
        "ok": True,
        "provider": provider,
        "model": settings.get("model"),
        "ollama": None,
        "version": None,
        "active": len(ACTIVE),
        "hotkey": HOTKEY.status() if HOTKEY else {"enabled": False, "registered": False, "shortcut": "Ctrl+Alt+Space", "message": "Сервер запускается"},
        "loaded_models": [],
    }
    with RESOURCE_LOCK:
        result["resource_profile"] = dict(RESOURCE_STATE)
    with MODEL_WARMUP_LOCK:
        result["model_warmup"] = dict(MODEL_WARMUP_STATE)
    if provider == "ollama":
        base = settings.get("ollama_url") or "http://127.0.0.1:11434"
        try:
            req = urllib.request.Request(base.rstrip("/") + "/api/version", headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            result["ollama"] = True
            result["version"] = data.get("version")
            try:
                ps_req = urllib.request.Request(base.rstrip("/") + "/api/ps", headers={"Accept": "application/json"})
                with urllib.request.urlopen(ps_req, timeout=0.5) as resp:
                    ps_data = json.loads(resp.read().decode("utf-8", "replace"))
                result["loaded_models"] = [
                    {"name": item.get("name"), "size_vram": item.get("size_vram", 0), "context_length": item.get("context_length")}
                    for item in ps_data.get("models", [])
                ]
            except Exception:
                pass
        except Exception:
            result["ollama"] = False
            result["ok"] = False
    return result


# ----------------------------------------------------------------- system stats
class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


class _POWERSTATUS(ctypes.Structure):
    _fields_ = [
        ("ACLineStatus", wintypes.BYTE), ("BatteryFlag", wintypes.BYTE),
        ("BatteryLifePercent", wintypes.BYTE), ("Reserved1", wintypes.BYTE),
        ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD),
    ]


_CPU_LOCK = threading.Lock()
_CPU_LAST: tuple[float, float] | None = None


def _filetime_value(value: _FILETIME) -> float:
    return (value.dwHighDateTime << 32) + value.dwLowDateTime


def cpu_percent() -> float:
    global _CPU_LAST
    kernel32 = ctypes.windll.kernel32
    idle, kernel, user = _FILETIME(), _FILETIME(), _FILETIME()
    if not kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return 0.0
    idle_v = _filetime_value(idle)
    total_v = _filetime_value(kernel) + _filetime_value(user)
    with _CPU_LOCK:
        previous = _CPU_LAST
        _CPU_LAST = (idle_v, total_v)
    if not previous:
        return 0.0
    idle_delta = idle_v - previous[0]
    total_delta = total_v - previous[1]
    if total_delta <= 0:
        return 0.0
    return max(0.0, min(100.0, 100.0 * (1.0 - idle_delta / total_delta)))


def _active_window() -> tuple[str, str]:
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetForegroundWindow.restype = wintypes.HWND
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return "", ""
        length = user32.GetWindowTextLengthW(hwnd)
        title_buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title_buffer, length + 1)
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        kernel32.OpenProcess.restype = wintypes.HANDLE
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        app = ""
        if handle:
            try:
                path_buffer = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                if kernel32.QueryFullProcessImageNameW(handle, 0, path_buffer, ctypes.byref(size)):
                    app = Path(path_buffer.value).name
            finally:
                kernel32.CloseHandle(handle)
        return app, title_buffer.value
    except Exception:
        return "", ""


def system_stats() -> dict[str, Any]:
    mem = _MEMORYSTATUSEX()
    mem.dwLength = ctypes.sizeof(mem)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
    total_ram = mem.ullTotalPhys / (1024 ** 3)
    used_ram = (mem.ullTotalPhys - mem.ullAvailPhys) / (1024 ** 3)
    try:
        disk = shutil.disk_usage(str(Path.home().anchor or "C:\\"))
        disk_used = disk.used / (1024 ** 3)
        disk_total = disk.total / (1024 ** 3)
        disk_percent = 100.0 * disk.used / disk.total if disk.total else 0.0
    except OSError:
        disk_used = disk_total = disk_percent = 0.0
    power = _POWERSTATUS()
    battery = None
    plugged = None
    if ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(power)):
        battery = int(power.BatteryLifePercent)
        if battery < 0 or battery > 100 or (int(power.BatteryFlag) & 128):
            battery = None
        plugged = power.ACLineStatus == 1
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.GetTickCount64.restype = ctypes.c_ulonglong
        uptime = int(kernel32.GetTickCount64() // 1000)
    except Exception:
        uptime = 0
    app, title = _active_window()
    return {
        "ok": True,
        "time": datetime.now().strftime("%H:%M:%S"),
        "date": datetime.now().strftime("%d.%m.%Y"),
        "cpu": round(cpu_percent(), 1),
        "ram_percent": round(100.0 * (mem.ullTotalPhys - mem.ullAvailPhys) / max(1, mem.ullTotalPhys), 1),
        "ram_used": round(used_ram, 1),
        "ram_free": round(mem.ullAvailPhys / (1024 ** 3), 1),
        "ram_total": round(total_ram, 1),
        "disk_percent": round(disk_percent, 1),
        "disk_used": round(disk_used, 1),
        "disk_free": round(max(0.0, disk_total - disk_used), 1),
        "disk_total": round(disk_total, 1),
        "battery": battery,
        "plugged": plugged,
        "uptime": uptime,
        "active_app": app,
        "active_title": title,
    }


def _set_model_warmup(**values: Any) -> None:
    with MODEL_WARMUP_LOCK:
        MODEL_WARMUP_STATE.update(values)


def _initialize_models_locked() -> None:
    settings = STORE.settings
    if settings.get("resource_profile", "work") == "game":
        _set_model_warmup(status="skipped", seconds=None, message="Игровой профиль освобождает ресурсы")
        return

    voice_result: dict[str, Any] = {"ok": True, "skipped": "voice disabled"}
    try:
        voice_result = voice.warmup(STORE)
        log("voice warmup: " + str(voice_result))
    except Exception:  # noqa: BLE001
        log("voice warmup error: " + traceback.format_exc())
        voice_result = {"ok": False, "error": "Ошибка прогрева голосового движка"}

    settings = STORE.settings
    if settings.get("resource_profile", "work") == "game":
        status = {"status": "skipped", "seconds": None, "message": "Переключён игровой профиль"}
    elif not settings.get("model_warmup_on_start", True):
        status = {"status": "disabled", "seconds": None, "message": "Прогрев выключен в настройках"}
    elif settings.get("provider", "ollama") != "ollama":
        status = {"status": "skipped", "seconds": None, "message": "Выбран внешний API"}
    elif str(settings.get("voice_engine", "")).lower() == "voxcpm":
        status = {"status": "skipped", "seconds": None, "message": "VoxCPM2 освобождает память Ollama"}
    else:
        available_ram = float(system_stats().get("ram_free", 0) or 0)
        if available_ram < 2.5:
            status = {"status": "skipped", "seconds": None,
                      "message": f"Мало свободной RAM ({available_ram:.1f} ГБ)"}
        else:
            _set_model_warmup(status="loading", seconds=None, message="Загружаю выбранную модель")
            try:
                result = llm.warmup_model(llm.effective_runtime_settings(settings))
                status = {"status": "ready", "seconds": result.get("seconds"),
                          "message": "Модель прогрета в фоне"}
            except Exception as exc:  # noqa: BLE001
                status = {"status": "error", "seconds": None, "message": str(exc)[:220]}
    _set_model_warmup(**status)
    log("chat model warmup: " + str(status) + "; voice_ready=" + str(bool(voice_result.get("ok"))))


def _initialize_models() -> None:
    with RESOURCE_OPERATION_LOCK:
        _initialize_models_locked()


def _apply_resource_profile(profile: str) -> None:
    try:
        if profile == "game":
            with RESOURCE_OPERATION_LOCK:
                voice_result = voice.release_loaded_models()
                model_result = llm.unload_model(STORE.settings)
            _set_model_warmup(status="skipped", seconds=None, message="Игровой профиль освобождает ресурсы")
            parts = ["Игровой режим активен"]
            parts.append("голосовые модели выгружены" if voice_result.get("ok") else "голосовые модели выгружены с ошибкой")
            parts.append(str(model_result.get("message") or model_result.get("error") or "модель Ollama обработана"))
            if voice_result.get("errors"):
                parts.extend(str(error) for error in voice_result["errors"][:2])
            ready = bool(voice_result.get("ok")) and bool(model_result.get("ok", True))
            status = {"status": "ready" if ready else "error", "profile": profile, "message": "; ".join(parts)}
        else:
            _initialize_models()
            warmup = dict(MODEL_WARMUP_STATE)
            detail = "Модели подготавливаются в фоне" if warmup.get("status") == "loading" else str(warmup.get("message") or "Настройки производительности восстановлены")
            status = {"status": "ready", "profile": profile, "message": "Рабочий режим активен; " + detail}
    except Exception as exc:  # noqa: BLE001
        status = {"status": "error", "profile": profile, "message": str(exc)[:220]}
        log("resource profile error: " + traceback.format_exc())
    with RESOURCE_LOCK:
        RESOURCE_STATE.update(status)


def request_resource_profile(profile: str) -> tuple[dict[str, Any] | None, int | None, str | None]:
    profile = str(profile or "").strip().lower()
    if profile not in ("work", "game"):
        return None, 400, "Неизвестный профиль ресурсов"
    with RESOURCE_LOCK:
        with ACTIVE_LOCK:
            if ACTIVE:
                return None, 409, "Сначала дождись завершения текущего ответа"
        if RESOURCE_STATE.get("status") == "switching":
            return None, 409, "Предыдущая смена профиля ещё выполняется"
        previous = STORE.settings.get("resource_profile", "work")
        if previous == profile and RESOURCE_STATE.get("status") == "ready":
            return STORE.settings, None, None
        try:
            settings = STORE.save_settings({"resource_profile": profile})
        except ValueError as exc:
            return None, 400, str(exc)
        RESOURCE_STATE.update({
            "status": "switching", "profile": profile,
            "message": "Выгружаю модели" if profile == "game" else "Восстанавливаю рабочие ресурсы",
        })
    threading.Thread(target=_apply_resource_profile, args=(profile,), daemon=True,
                     name=f"quanta-profile-{profile}").start()
    return settings, None, None


# ----------------------------------------------------------------- confirmations
def confirm_callback(
    chat_id: str,
    call_id: str,
    name: str,
    args: dict[str, Any],
    emit,
    stop_event: threading.Event,
) -> bool:
    event = threading.Event()
    with PENDING_LOCK:
        PENDING[call_id] = {"event": event, "allow": False, "chat_id": chat_id}
    try:
        emit({
            "type": "confirm",
            "id": call_id,
            "name": name,
            "label": TOOLBOX.label(name),
            "args": args,
        })
        deadline = time.time() + 300
        while time.time() < deadline:
            if event.wait(0.4):
                break
            if stop_event.is_set():
                return False
        with PENDING_LOCK:
            info = PENDING.pop(call_id, None)
        return bool(info and info.get("allow"))
    finally:
        with PENDING_LOCK:
            PENDING.pop(call_id, None)


def deny_pending_for_chat(chat_id: str) -> None:
    with PENDING_LOCK:
        for info in PENDING.values():
            if info.get("chat_id") == chat_id:
                info["allow"] = False
                info["event"].set()


# --------------------------------------------------------------------- reminders
def reminder_loop() -> None:
    while not STOP.is_set():
        try:
            for reminder in STORE.due_reminders():
                STORE.add_event({"type": "reminder", "reminder": reminder})
                try:
                    import winsound
                    winsound.MessageBeep(winsound.MB_ICONASTERISK)
                except Exception:
                    pass
                log(f"reminder due: {reminder.get('text')}")
        except Exception:
            log("reminder loop error: " + traceback.format_exc())
        STOP.wait(10)


def idle_loop(server: ThreadingHTTPServer) -> None:
    while not STOP.is_set():
        STOP.wait(60)
        if STOP.is_set():
            return
        with ACTIVE_LOCK:
            active = bool(ACTIVE)
        if not active and time.time() - LAST_ACTIVITY > 1800:
            log("idle shutdown")
            try:
                server.shutdown()
            except Exception:
                pass
            return


# ---------------------------------------------------------------------- handler
class Handler(BaseHTTPRequestHandler):
    server_version = "Quanta/" + VERSION
    sys_version = ""

    def log_message(self, fmt: str, *args: Any) -> None:
        log(f"{self.client_address[0] if self.client_address else '?'} {fmt % args}")

    # ------------------------------------------------------------------ plumbing
    def _touch(self) -> None:
        touch()

    def _send_bytes(self, data: bytes, content_type: str, status: int = 200, cache: str = "no-store") -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            # The browser may close a tab while a large status response is
            # being assembled. A disconnect is not an application failure.
            pass

    def _json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send_bytes(body, "application/json; charset=utf-8", status)

    def _error(self, status: int, message: str) -> None:
        self._json({"ok": False, "error": message}, status)

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            raise ValueError("Некорректный Content-Length")
        if length > 25_000_000:
            raise ValueError("Слишком большой запрос")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("Некорректный JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("Ожидался JSON-объект")
        return data

    def _check_host(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].strip().lower()
        if host in ("127.0.0.1", "localhost", "::1", "[::1]"):
            return True
        self._error(403, "Доступ разрешён только с этого компьютера")
        return False

    def _check_origin(self) -> bool:
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        try:
            parsed = urllib.parse.urlsplit(origin)
        except ValueError:
            self._error(403, "Некорректный Origin")
            return False
        if parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1"):
            return True
        self._error(403, "Запросы разрешены только из локального интерфейса")
        return False

    def _serve_file(self, path: Path, download_name: str | None = None) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            self._error(404, "Файл не найден")
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self._send_bytes(data, content_type, cache="no-cache")

    # ----------------------------------------------------------------------- GET
    def do_GET(self) -> None:
        self._touch()
        if not self._check_host():
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)
        try:
            if path in ("/", "/index.html"):
                self._serve_file(UI_DIR / "index.html")
                return
            if path.startswith("/ui/"):
                rel = path[len("/ui/"):]
                target = (UI_DIR / rel).resolve()
                if not str(target).startswith(str(UI_DIR.resolve()) + os.sep):
                    self._error(403, "Запрещено")
                    return
                self._serve_file(target)
                return
            if path.startswith("/media/"):
                rel = path[len("/media/"):]
                target = (MEDIA_DIR / rel).resolve()
                if not str(target).startswith(str(MEDIA_DIR.resolve()) + os.sep):
                    self._error(403, "Запрещено")
                    return
                self._serve_file(target)
                return
            if path == "/api/bootstrap":
                settings = STORE.settings
                self._json({
                    "ok": True,
                    "version": VERSION,
                    "settings": settings,
                    "chats": STORE.list_chats(),
                    "notes": STORE.list_notes(),
                    "reminders": STORE.list_reminders(),
                    "health": health_info(),
                    "system": system_stats(),
                    "server_time": datetime.now().isoformat(),
                    "data_dir": str(DATA_DIR),
                })
                return
            if path == "/api/health":
                self._json({"ok": True, "health": health_info(), "system": system_stats(), "data_dir": str(DATA_DIR)})
                return
            if path == "/api/system":
                self._json({"ok": True, **system_stats()})
                return
            if path == "/api/weather":
                city = (query.get("city") or [""])[0]
                info = TOOLBOX.weather_info(city)
                self._json(info)
                return
            if path == "/api/models":
                try:
                    models = llm.list_models(STORE.settings)
                except llm.LLMError as exc:
                    self._error(400, str(exc))
                    return
                self._json({"ok": True, "models": models, "current": STORE.settings.get("model")})
                return
            if path == "/api/voice/status":
                self._json({"ok": True, "voice": voice.voice_status(STORE)})
                return
            if path == "/api/memory":
                query_text = (query.get("query") or [""])[0]
                self._json({"ok": True, "memory": STORE.list_memory(query_text)})
                return
            if path == "/api/workflows":
                self._json({"ok": True, "workflows": STORE.list_workflows()})
                return
            if path == "/api/projects":
                include_archived = (query.get("all") or ["0"])[0] in ("1", "true", "yes")
                project_query = (query.get("query") or [""])[0]
                self._json({"ok": True, "projects": STORE.list_projects(
                    include_archived=include_archived, query=project_query,
                )})
                return
            if path == "/api/actions":
                self._json({"ok": True, "actions": STORE.list_actions()})
                return
            if path == "/api/chats":
                self._json({"ok": True, "chats": STORE.list_chats()})
                return
            if path.startswith("/api/chats/"):
                chat_id = path.split("/")[3]
                try:
                    chat = STORE.get_chat(chat_id)
                except (FileNotFoundError, ValueError) as exc:
                    self._error(404, str(exc))
                    return
                self._json({"ok": True, "chat": chat})
                return
            if path == "/api/notes":
                q = (query.get("query") or [""])[0]
                self._json({"ok": True, "notes": STORE.list_notes(q)})
                return
            if path == "/api/reminders":
                include_done = (query.get("all") or ["0"])[0] in ("1", "true", "yes")
                self._json({"ok": True, "reminders": STORE.list_reminders(include_done)})
                return
            if path == "/api/events":
                self._json({"ok": True, "events": STORE.drain_events()})
                return
            if path == "/api/window":
                view = (query.get("view") or ["hud"])[0]
                width, height = (480, 720) if view == "mini" else (1360, 900)
                ok = open_app_window(f"http://127.0.0.1:{PORT}/?view={view}", width, height)
                self._json({"ok": ok})
                return
            self._error(404, "Не найдено")
        except Exception:
            log("GET error: " + traceback.format_exc())
            self._error(500, "Внутренняя ошибка сервера")

    # ---------------------------------------------------------------------- POST
    def do_POST(self) -> None:
        self._touch()
        if not self._check_host():
            return
        if not self._check_origin():
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        try:
            if path == "/api/chat":
                self._handle_chat()
                return
            if path == "/api/resource-profile":
                data = self._read_json()
                settings, status, error = request_resource_profile(str(data.get("profile") or ""))
                if error:
                    self._error(status or 400, error)
                    return
                with RESOURCE_LOCK:
                    transition = dict(RESOURCE_STATE)
                self._json({"ok": True, "settings": settings, "transition": transition})
                return
            if path == "/api/voice/tts":
                data = self._read_json()
                text = str(data.get("text") or "").strip()
                if not text:
                    self._error(400, "Пустой текст для синтеза речи")
                    return
                with RESOURCE_OPERATION_LOCK:
                    with RESOURCE_LOCK:
                        switching = RESOURCE_STATE.get("status") == "switching"
                    if switching:
                        self._error(409, "Подожди завершения переключения профиля ресурсов")
                        return
                    info = voice.speak(text, STORE)
                if not info.get("ok"):
                    self._error(500, str(info.get("error") or "Синтез речи недоступен"))
                    return
                self._json({"ok": True, **info})
                return
            if path == "/api/voice/transcribe":
                length_header = self.headers.get("Content-Length")
                try:
                    length = max(0, int(length_header or "0"))
                except ValueError:
                    self._error(400, "Неверный Content-Length")
                    return
                audio = self.rfile.read(length) if length else b""
                if not audio:
                    self._error(400, "Пустой аудиофайл")
                    return
                with RESOURCE_OPERATION_LOCK:
                    with RESOURCE_LOCK:
                        switching = RESOURCE_STATE.get("status") == "switching"
                    if switching:
                        self._error(409, "Подожди завершения переключения профиля ресурсов")
                        return
                    info = voice.transcribe(audio, "recording.webm", STORE)
                if not info.get("ok"):
                    self._error(500, str(info.get("error") or "Распознавание речи недоступно"))
                    return
                self._json({"ok": True, **info})
                return
            if path == "/api/confirm":
                data = self._read_json()
                confirm_id = str(data.get("id") or "")
                allow = bool(data.get("allow"))
                with PENDING_LOCK:
                    info = PENDING.get(confirm_id)
                if info:
                    info["allow"] = allow
                    info["event"].set()
                self._json({"ok": True})
                return
            if path == "/api/stop":
                data = self._read_json()
                chat_id = str(data.get("chat_id") or "")
                with ACTIVE_LOCK:
                    event = ACTIVE.get(chat_id)
                if event:
                    event.set()
                deny_pending_for_chat(chat_id)
                self._json({"ok": True})
                return
            if path == "/api/chats":
                data = self._read_json()
                chat = STORE.create_chat(str(data.get("title") or "Новый чат"))
                self._json({"ok": True, "chat": chat})
                return
            if path.startswith("/api/chats/") and path.endswith("/rename"):
                chat_id = path.split("/")[3]
                data = self._read_json()
                result = STORE.update_chat_title(chat_id, str(data.get("title") or "Новый чат"))
                self._json({"ok": True, "chat": result})
                return
            if path == "/api/settings":
                data = self._read_json()
                if "resource_profile" in data and data["resource_profile"] != STORE.settings.get("resource_profile", "work"):
                    self._error(400, "Переключай профиль ресурсов через селектор Работа / Игра")
                    return
                try:
                    settings = STORE.save_settings(data)
                    if HOTKEY:
                        HOTKEY.set_enabled(bool(settings.get("global_hotkey_enabled", True)))
                except ValueError as exc:
                    self._error(400, str(exc))
                    return
                self._json({"ok": True, "settings": settings})
                return
            if path == "/api/memory":
                data = self._read_json()
                item = STORE.create_memory(str(data.get("text") or ""))
                self._json({"ok": True, "item": item})
                return
            if path == "/api/workflows":
                data = self._read_json()
                item = STORE.create_workflow(str(data.get("title") or ""), str(data.get("prompt") or ""))
                self._json({"ok": True, "item": item})
                return
            if path == "/api/projects":
                data = self._read_json()
                try:
                    chat_id = str(data.get("chat_id") or "").strip()
                    if chat_id:
                        # Validate the optional relationship before creating a card,
                        # so a stale chat id cannot leave behind an orphan project.
                        STORE.get_chat(chat_id)
                    project = STORE.create_project(
                        str(data.get("title") or ""),
                        str(data.get("goal") or ""),
                        next_step=str(data.get("next_step") or ""),
                        summary=str(data.get("summary") or ""),
                        decisions=data.get("decisions") or [],
                        files=data.get("files") or [],
                    )
                    if chat_id:
                        project = STORE.link_chat_to_project(project["id"], chat_id)
                except (ValueError, FileNotFoundError) as exc:
                    self._error(400 if isinstance(exc, ValueError) else 404, str(exc))
                    return
                self._json({"ok": True, "project": project})
                return
            if path.startswith("/api/projects/") and path.endswith("/link"):
                project_id = path.split("/")[3]
                data = self._read_json()
                try:
                    project = STORE.link_chat_to_project(project_id, str(data.get("chat_id") or ""))
                except FileNotFoundError as exc:
                    self._error(404, str(exc))
                    return
                except ValueError as exc:
                    self._error(400, str(exc))
                    return
                self._json({"ok": True, "project": project})
                return
            if path.startswith("/api/projects/") and path.endswith("/unlink"):
                project_id = path.split("/")[3]
                data = self._read_json()
                try:
                    project = STORE.unlink_chat_from_project(project_id, str(data.get("chat_id") or ""))
                except FileNotFoundError as exc:
                    self._error(404, str(exc))
                    return
                self._json({"ok": True, "project": project})
                return
            if path.startswith("/api/projects/") and (path.endswith("/archive") or path.endswith("/restore")):
                project_id = path.split("/")[3]
                try:
                    project = STORE.archive_project(project_id, archived=path.endswith("/archive"))
                except FileNotFoundError as exc:
                    self._error(404, str(exc))
                    return
                except ValueError as exc:
                    self._error(400, str(exc))
                    return
                self._json({"ok": True, "project": project})
                return
            if path == "/api/actions/clear":
                STORE.clear_actions()
                self._json({"ok": True})
                return
            if path.startswith("/api/actions/") and path.endswith("/undo"):
                action_id = path.split("/")[3]
                self._json(TOOLBOX.undo_action(action_id))
                return
            if path == "/api/notes":
                data = self._read_json()
                note = STORE.create_note(str(data.get("title") or "Без названия"), str(data.get("text") or ""))
                self._json({"ok": True, "note": note})
                return
            if path == "/api/reminders":
                data = self._read_json()
                try:
                    due = TOOLBOX._parse_when(str(data.get("when") or ""))
                except ValueError as exc:
                    self._error(400, str(exc))
                    return
                reminder = STORE.create_reminder(str(data.get("text") or "Напоминание"), due)
                self._json({"ok": True, "reminder": reminder})
                return
            if path.startswith("/api/reminders/") and path.endswith("/done"):
                reminder_id = path.split("/")[3]
                data = self._read_json()
                reminder = STORE.set_reminder_done(reminder_id, bool(data.get("done", True)))
                self._json({"ok": True, "reminder": reminder})
                return
            if path == "/api/open":
                data = self._read_json()
                target = str(data.get("path") or "")
                result = TOOLBOX._open_path(target)
                self._json(result)
                return
            if path == "/api/reveal":
                data = self._read_json()
                target = Path(str(data.get("path") or "")).resolve()
                if target.exists():
                    subprocess.Popen(["explorer", "/select,", str(target)], close_fds=True)
                self._json({"ok": target.exists()})
                return
            if path == "/api/shutdown":
                self._json({"ok": True})
                STOP.set()
                threading.Thread(target=SERVER.shutdown, daemon=True).start()  # type: ignore[name-defined]
                return
            self._error(404, "Не найдено")
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception:
            log("POST error: " + traceback.format_exc())
            self._error(500, "Внутренняя ошибка сервера")

    def _handle_chat(self) -> None:
        data = self._read_json()
        text = str(data.get("text") or "").strip()
        tone_mode = str(data.get("tone_mode") or "default").strip().lower()
        if tone_mode != "serious":
            tone_mode = "default"
        if not text:
            self._error(400, "Пустое сообщение")
            return
        if len(text) > 30000:
            self._error(400, "Сообщение слишком длинное")
            return
        chat_id = str(data.get("chat_id") or "").strip()
        if chat_id:
            try:
                chat = STORE.get_chat(chat_id)
            except (FileNotFoundError, ValueError) as exc:
                self._error(404, str(exc))
                return
        with RESOURCE_LOCK:
            if RESOURCE_STATE.get("status") == "switching":
                self._error(409, "Подожди завершения переключения профиля ресурсов")
                return
            if not chat_id:
                chat = STORE.create_chat(text)
                chat_id = chat["id"]
            with ACTIVE_LOCK:
                if chat_id in ACTIVE:
                    self._error(409, "Кванта уже отвечает в этом чате. Дождись ответа или останови генерацию.")
                    return
                stop_event = threading.Event()
                ACTIVE[chat_id] = stop_event

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def emit(event: dict[str, Any]) -> None:
            nonlocal stop_event
            if self.wfile.closed:
                stop_event.set()
                return
            try:
                line = json.dumps(event, ensure_ascii=False) + "\n"
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                stop_event.set()

        emit({"type": "start", "chat_id": chat_id, "title": chat.get("title") or "Новый чат"})
        agent = Agent(STORE, TOOLBOX, confirm_callback)
        try:
            agent.run(chat_id, text, emit, stop_event, tone_mode=tone_mode)
        except Exception:
            log("agent error: " + traceback.format_exc())
            emit({"type": "error", "message": "Внутренняя ошибка агента. Подробности в data/quanta.log."})
        finally:
            with ACTIVE_LOCK:
                ACTIVE.pop(chat_id, None)
            deny_pending_for_chat(chat_id)
            try:
                self.wfile.flush()
            except Exception:
                pass

    # -------------------------------------------------------------------- others
    def do_DELETE(self) -> None:
        self._touch()
        if not self._check_host():
            return
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        try:
            if path.startswith("/api/chats/"):
                chat_id = path.split("/")[3]
                STORE.delete_chat(chat_id)
                self._json({"ok": True})
                return
            if path.startswith("/api/notes/"):
                note_id = path.split("/")[3]
                self._json({"ok": STORE.delete_note(note_id)})
                return
            if path.startswith("/api/reminders/"):
                reminder_id = path.split("/")[3]
                self._json({"ok": STORE.delete_reminder(reminder_id)})
                return
            if path.startswith("/api/projects/"):
                project_id = path.split("/")[3]
                self._json({"ok": STORE.delete_project(project_id)})
                return
            if path.startswith("/api/memory/"):
                memory_id = path.split("/")[3]
                self._json({"ok": STORE.delete_memory(memory_id)})
                return
            if path.startswith("/api/workflows/"):
                workflow_id = path.split("/")[3]
                self._json({"ok": STORE.delete_workflow(workflow_id)})
                return
            if path == "/api/actions":
                STORE.clear_actions()
                self._json({"ok": True})
                return
            self._error(404, "Не найдено")
        except Exception:
            log("DELETE error: " + traceback.format_exc())
            self._error(500, "Внутренняя ошибка сервера")

    def do_PUT(self) -> None:
        self._touch()
        if not self._check_host():
            return
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        try:
            if path.startswith("/api/notes/"):
                note_id = path.split("/")[3]
                data = self._read_json()
                note = STORE.update_note(note_id, title=data.get("title"), text=data.get("text"))
                self._json({"ok": True, "note": note})
                return
            if path.startswith("/api/projects/"):
                project_id = path.split("/")[3]
                data = self._read_json()
                allowed = {"title", "goal", "status", "next_step", "summary", "decisions", "files"}
                changes = {key: data[key] for key in allowed if key in data}
                project = STORE.update_project(project_id, **changes)
                self._json({"ok": True, "project": project})
                return
            self._error(404, "Не найдено")
        except FileNotFoundError as exc:
            self._error(404, str(exc))
        except ValueError as exc:
            self._error(400, str(exc))
        except Exception:
            log("PUT error: " + traceback.format_exc())
            self._error(500, "Внутренняя ошибка сервера")


SERVER: ThreadingHTTPServer | None = None


def _port_free(port: int) -> bool:
    """A listening socket owns the port. We deliberately use connect() instead
    of bind(): Windows lets SO_REUSEADDR steal a port, and TIME_WAIT would make
    a plain bind fail after a normal restart."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.25)
        return probe.connect_ex(("127.0.0.1", port)) != 0


def _create_server() -> ThreadingHTTPServer:
    """Bind the HTTP server, falling back to the next free port if needed."""
    global PORT
    # When the launcher passes QUANTA_PORT it has already picked a free port.
    # A manual `python server.py` gets an automatic fallback instead of dying
    # because CharacterAIHybrid or another app took 8765 at boot.
    explicit = "QUANTA_PORT" in os.environ
    candidates = [PORT] if explicit else list(range(PORT, PORT + 20))
    last_error: OSError | None = None
    for candidate in candidates:
        if not _port_free(candidate):
            last_error = OSError(f"port {candidate} is already in use")
            log(f"port {candidate} unavailable: already in use")
            continue
        try:
            server = ThreadingHTTPServer(("127.0.0.1", candidate), Handler)
            if candidate != PORT:
                log(f"port {PORT} is busy, using {candidate}")
            PORT = candidate
            return server
        except OSError as exc:
            last_error = exc
            log(f"port {candidate} unavailable: {exc}")
    first, last = candidates[0], candidates[-1]
    raise OSError(f"no free port in {first}-{last}") from last_error


def main() -> None:
    global SERVER, HOTKEY
    server = _create_server()
    server.daemon_threads = True
    SERVER = server
    HOTKEY = GlobalHotkey(PORT, STORE)
    HOTKEY.set_enabled(bool(STORE.settings.get("global_hotkey_enabled", True)))
    try:
        (DATA_DIR / "port.txt").write_text(str(PORT), encoding="utf-8")
    except OSError:
        pass
    log(f"Q.W.A.N.T.A. {VERSION} starting on 127.0.0.1:{PORT}")
    threading.Thread(target=reminder_loop, daemon=True).start()
    threading.Thread(target=idle_loop, args=(server,), daemon=True).start()

    threading.Thread(target=_initialize_models, daemon=True, name="quanta-model-warmup").start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    except Exception:
        log("server error: " + traceback.format_exc())
    finally:
        STOP.set()
        if HOTKEY:
            HOTKEY.stop()
        try:
            with RESOURCE_OPERATION_LOCK:
                release_result = voice.release_loaded_models()
            log("voice cleanup: " + str(release_result))
        except Exception:  # noqa: BLE001
            log("voice cleanup error: " + traceback.format_exc())
        try:
            server.server_close()
        except Exception:
            pass
        log("server stopped")


if __name__ == "__main__":
    main()
