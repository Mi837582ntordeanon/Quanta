"""Local HTTP server for Q.W.A.N.T.A.

Binds strictly to 127.0.0.1.  The browser UI is served from ``ui/``; the agent
streams NDJSON events over ``POST /api/chat``.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
from concurrent.futures import ThreadPoolExecutor
import hmac
import json
import mimetypes
import os
import secrets
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
from assistant.server_routes.context import RouteContext  # noqa: E402
from assistant import llm  # noqa: E402
from assistant import voice  # noqa: E402
from assistant import command_sandbox  # noqa: E402
from assistant.browser import open_app_window  # noqa: E402
from assistant.hotkey import GlobalHotkey  # noqa: E402
from assistant.minecraft_server import MinecraftServerManager  # noqa: E402
from assistant.minecraft_companion import MinecraftCompanionManager  # noqa: E402
from assistant.storage import (  # noqa: E402
    DATA_DIR, MEDIA_DIR, UI_DIR, Store,
)
from assistant.tools import Toolbox  # noqa: E402
from assistant.trace_store import TraceStore  # noqa: E402
from assistant.vision import VisionService  # noqa: E402

PORT = int(os.environ.get("QUANTA_PORT", "8765"))
VERSION = "0.1.0"
CREATE_NO_WINDOW = 0x08000000
SESSION_TOKEN = secrets.token_urlsafe(32)

STORE = Store()
MINECRAFT = MinecraftServerManager()
MINECRAFT_COMPANION = MinecraftCompanionManager(MINECRAFT, ROOT)
VISION = VisionService(lambda: STORE.settings)
TOOLBOX = Toolbox(STORE, minecraft=MINECRAFT, vision=VISION)
TRACE_STORE = TraceStore(
    DATA_DIR / "traces",
    secret_redactor=STORE.redact_secrets,
)
ACTIVE: dict[str, threading.Event] = {}
ACTIVE_LOCK = threading.Lock()
TASK_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="quanta-task")
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
    try:
        safe_message = STORE.redact_secrets(str(message))
    except Exception:
        safe_message = "Сообщение скрыто: не удалось отфильтровать секреты"
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {safe_message}\n"
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


def _show_path_in_explorer(raw_path: str) -> dict[str, Any]:
    """Navigate to a path without handing files to their registered handler."""
    target = Path(str(raw_path or "")).expanduser().resolve()
    if not target.exists():
        return {"ok": False, "text": "Путь не найден."}
    command = ["explorer.exe", str(target)] if target.is_dir() else ["explorer.exe", "/select,", str(target)]
    subprocess.Popen(command, close_fds=True)
    return {"ok": True, "text": "Путь показан в Проводнике."}


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
) -> bool | dict[str, Any]:
    event = threading.Event()
    with PENDING_LOCK:
        PENDING[call_id] = {"event": event, "allow": False, "chat_id": chat_id,
                            "kind": "plan" if name == "task_plan" else "tool", "approval": None}
    try:
        label = "План задачи" if name == "task_plan" else TOOLBOX.label(name)
        if name.startswith("mcp__"):
            label = "MCP-инструмент · " + name
        emit({
            "type": "confirm",
            "id": call_id,
            "name": name,
            "label": label,
            "args": args,
            "kind": "plan" if name == "task_plan" else "tool",
        })
        deadline = time.time() + 300
        while time.time() < deadline:
            if event.wait(0.4):
                break
            if stop_event.is_set():
                return False
        with PENDING_LOCK:
            info = PENDING.pop(call_id, None)
        if not info or not info.get("allow"):
            return False
        if info.get("kind") == "plan" or isinstance(info.get("approval"), dict):
            return info.get("approval") or {}
        return True
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
        if not active and not MINECRAFT.has_running() and time.time() - LAST_ACTIVITY > 1800:
            log("idle shutdown")
            try:
                server.shutdown()
            except Exception:
                pass
            return


# ---------------------------------------------------------------------- handler
def _route_context() -> RouteContext:
    return RouteContext(
        active=ACTIVE,
        active_lock=ACTIVE_LOCK,
        data_dir=DATA_DIR,
        hotkey=HOTKEY,
        media_dir=MEDIA_DIR,
        minecraft=MINECRAFT,
        minecraft_companion=MINECRAFT_COMPANION,
        pending=PENDING,
        pending_lock=PENDING_LOCK,
        port=PORT,
        resource_lock=RESOURCE_LOCK,
        resource_operation_lock=RESOURCE_OPERATION_LOCK,
        resource_state=RESOURCE_STATE,
        server=SERVER,
        stop=STOP,
        store=STORE,
        toolbox=TOOLBOX,
        ui_dir=UI_DIR,
        version=VERSION,
        vision=VISION,
        show_path_in_explorer=_show_path_in_explorer,
        command_sandbox=command_sandbox,
        deny_pending_for_chat=deny_pending_for_chat,
        health_info=health_info,
        llm=llm,
        open_app_window=open_app_window,
        request_resource_profile=request_resource_profile,
        system_stats=system_stats,
        trace_store=TRACE_STORE,
        voice=voice,
        log=log,
    )


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
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
                "object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            )
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
        from assistant.server_routes.validation import read_json_object
        return read_json_object(self.headers, self.rfile)

    def _check_host(self) -> bool:
        raw_host = (self.headers.get("Host") or "").strip()
        try:
            parsed = urllib.parse.urlsplit("//" + raw_host)
            expected_port = int(getattr(self.server, "server_port", PORT))
            valid = (
                parsed.hostname in ("127.0.0.1", "localhost", "::1")
                and parsed.port == expected_port
                and parsed.username is None and parsed.password is None
                and not parsed.path
            )
        except ValueError:
            valid = False
        if valid:
            return True
        self._error(403, "Доступ разрешён только с этого компьютера")
        return False

    def _check_origin(self) -> bool:
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        try:
            parsed = urllib.parse.urlsplit(origin)
            host_header = urllib.parse.urlsplit("//" + (self.headers.get("Host") or "").strip())
            expected_port = int(getattr(self.server, "server_port", PORT))
            valid = (
                parsed.scheme == "http"
                and parsed.hostname in ("127.0.0.1", "localhost", "::1")
                and parsed.port == expected_port
                and parsed.hostname == host_header.hostname
                and parsed.port == host_header.port
                and parsed.username is None and parsed.password is None
                and parsed.path == "" and not parsed.query and not parsed.fragment
            )
        except ValueError:
            self._error(403, "Некорректный Origin")
            return False
        if valid:
            return True
        self._error(403, "Запросы разрешены только из локального интерфейса")
        return False

    def _authorize_api(self, path: str) -> bool:
        """Require the UI's per-process capability token for every API route."""
        if not path.startswith("/api/"):
            return True
        if not self._check_origin():
            return False
        supplied = self.headers.get("X-Quanta-Session", "")
        if not hmac.compare_digest(str(supplied), SESSION_TOKEN):
            self._error(403, "Сессия интерфейса истекла. Обнови страницу Кванты.")
            return False
        return True

    def _serve_file(self, path: Path, download_name: str | None = None) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            self._error(404, "Файл не найден")
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self._send_bytes(data, content_type, cache="no-cache")

    def _serve_index(self) -> None:
        try:
            html = (UI_DIR / "index.html").read_text(encoding="utf-8")
        except OSError:
            self._error(404, "Интерфейс не найден")
            return
        html = html.replace("__QUANTA_SESSION_TOKEN__", SESSION_TOKEN)
        self._send_bytes(html.encode("utf-8"), "text/html; charset=utf-8", cache="no-store")

    # ----------------------------------------------------------------------- GET
    def do_GET(self) -> None:
        self._touch()
        if not self._check_host():
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        query = urllib.parse.parse_qs(parsed.query)
        if not self._authorize_api(path):
            return
        from assistant.server_routes.get import dispatch_get
        dispatch_get(_route_context(), self, path, query, parsed)

    # ---------------------------------------------------------------------- POST
    def do_POST(self) -> None:
        self._touch()
        if not self._check_host():
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        if not self._authorize_api(path):
            return
        from assistant.server_routes.post import dispatch_post
        dispatch_post(_route_context(), self, path)

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
        task_id = str(data.get("task_id") or "").strip() or None
        if chat_id:
            try:
                chat = STORE.get_chat(chat_id)
            except (FileNotFoundError, ValueError) as exc:
                self._error(404, str(exc))
                return
        if task_id:
            try:
                task = STORE.get_task(task_id)
            except (FileNotFoundError, ValueError) as exc:
                self._error(404, str(exc))
                return
            if task.get("chat_id") != chat_id:
                self._error(400, "Задача привязана к другому чату")
                return
            if task.get("status") not in {"interrupted", "paused", "failed"}:
                self._error(409, "Эта задача сейчас не ожидает возобновления")
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

        try:
            if task_id:
                STORE.update_task(task_id, status="queued", checkpoint="Задача повторно поставлена в очередь.",
                                  checkpoint_type="queued", error="")
            else:
                task_id = STORE.create_task(chat_id, text, status="queued")["id"]
        except (FileNotFoundError, ValueError) as exc:
            with ACTIVE_LOCK:
                ACTIVE.pop(chat_id, None)
            self._error(400, str(exc))
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        settings = STORE.settings
        try:
            trace = TRACE_STORE.start_turn(
                chat_id=chat_id,
                task_id=task_id,
                provider=str(settings.get("provider") or ""),
                model=str(settings.get("model") or ""),
            )
        except Exception:
            trace = None
            log("agent trace initialization failed")
        trace_status = "completed"

        def emit(event: dict[str, Any]) -> None:
            nonlocal stop_event, trace_status
            if trace is not None:
                try:
                    trace.record_agent_event(event)
                except Exception:
                    log("agent trace write failed")
            if event.get("type") == "error":
                trace_status = "failed"
            elif event.get("type") == "stopped":
                trace_status = "stopped"
            elif event.get("type") == "done":
                trace_status = "completed"
            if self.wfile.closed:
                stop_event.set()
                return
            try:
                line = json.dumps(event, ensure_ascii=False) + "\n"
                self.wfile.write(line.encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, OSError):
                stop_event.set()

        emit({"type": "start", "chat_id": chat_id, "title": chat.get("title") or "Новый чат", "task_id": task_id})
        def traced_confirm(*args, **kwargs):
            if trace is None:
                return confirm_callback(*args, **kwargs)
            call_id = str(args[1]) if len(args) > 1 else str(kwargs.get("call_id") or "")
            name = str(args[2]) if len(args) > 2 else str(kwargs.get("name") or "")
            started = time.monotonic()
            try:
                decision = confirm_callback(*args, **kwargs)
            except Exception:
                try:
                    trace.record_confirmation(call_id, name, False, round((time.monotonic() - started) * 1000))
                except Exception:
                    log("agent trace confirmation write failed")
                raise
            try:
                trace.record_confirmation(call_id, name, decision, round((time.monotonic() - started) * 1000))
            except Exception:
                log("agent trace confirmation write failed")
            return decision

        agent = Agent(STORE, TOOLBOX, traced_confirm)
        try:
            # Serialize model work so two chats cannot load models at once and
            # exhaust system memory. Pending runs are durable task records.
            future = TASK_EXECUTOR.submit(agent.run, chat_id, text, emit, stop_event, tone_mode, task_id)
            future.result()
        except Exception:
            try:
                STORE.update_task(task_id, status="failed", checkpoint="Задача аварийно завершилась.",
                                  checkpoint_type="error", error="См. data/quanta.log")
            except Exception:
                pass
            log("agent error: " + traceback.format_exc())
            emit({"type": "error", "message": "Внутренняя ошибка агента. Подробности в data/quanta.log."})
        finally:
            if trace is not None:
                try:
                    trace.finish(trace_status)
                except Exception:
                    log("agent trace finalization failed")
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
        if not self._authorize_api(path):
            return
        from assistant.server_routes.delete import dispatch_delete
        dispatch_delete(_route_context(), self, path)

    def do_PUT(self) -> None:
        self._touch()
        if not self._check_host():
            return
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        if not self._authorize_api(path):
            return
        from assistant.server_routes.put import dispatch_put
        dispatch_put(_route_context(), self, path)


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

    if os.environ.get("QUANTA_SKIP_MODEL_WARMUP") == "1":
        log("model warmup skipped by QUANTA_SKIP_MODEL_WARMUP")
    else:
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
            MINECRAFT_COMPANION.shutdown()
        except Exception:
            log("Minecraft companion cleanup error: " + traceback.format_exc())
        try:
            MINECRAFT.shutdown()
        except Exception:
            log("Minecraft cleanup error: " + traceback.format_exc())
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
