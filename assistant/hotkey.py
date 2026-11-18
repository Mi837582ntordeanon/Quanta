from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import threading
from typing import Any

HOTKEY_ID = 0x5141
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_NOREPEAT = 0x4000
VK_SPACE = 0x20


def _user32() -> Any:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
    user32.RegisterHotKey.restype = wintypes.BOOL
    user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.UnregisterHotKey.restype = wintypes.BOOL
    user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
    user32.PeekMessageW.restype = wintypes.BOOL
    user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = ctypes.c_int
    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostThreadMessageW.restype = wintypes.BOOL
    user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    return user32


class GlobalHotkey:
    """Register Ctrl+Alt+Space and focus Quanta's standalone browser window."""

    def __init__(self, port: int, store: Any) -> None:
        self.port = int(port)
        self.store = store
        self._enabled = False
        self._registered = False
        self._message = "Ожидает запуска"
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._lock = threading.RLock()

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self._enabled,
                "registered": self._registered,
                "shortcut": "Ctrl+Alt+Space",
                "message": self._message,
            }

    def set_enabled(self, enabled: bool) -> None:
        if os.name != "nt":
            with self._lock:
                self._enabled = bool(enabled)
                self._registered = False
                self._message = "Глобальная клавиша доступна только в Windows"
            return
        enabled = bool(enabled)
        with self._lock:
            if enabled == self._enabled and (not enabled or (self._thread and self._thread.is_alive())):
                return
            self._enabled = enabled
            thread_id = self._thread_id
            thread = self._thread
        if not enabled:
            if thread_id:
                _user32().PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
            if thread and thread.is_alive():
                thread.join(timeout=1.0)
            with self._lock:
                self._registered = False
                self._thread_id = 0
                self._message = "Выключена"
            return
        thread = threading.Thread(target=self._run, name="QuantaHotkey", daemon=True)
        with self._lock:
            self._thread = thread
        thread.start()

    def stop(self) -> None:
        self.set_enabled(False)

    def _run(self) -> None:
        user32 = _user32()
        kernel32 = ctypes.windll.kernel32
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD
        thread_id = int(kernel32.GetCurrentThreadId())
        with self._lock:
            self._thread_id = thread_id
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        ctypes.set_last_error(0)
        registered = bool(user32.RegisterHotKey(None, HOTKEY_ID, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_SPACE))
        with self._lock:
            self._registered = registered
            self._message = "Ctrl+Alt+Space готова" if registered else (
                f"Сочетание уже занято Windows (код {ctypes.get_last_error()})"
            )
        if not registered:
            with self._lock:
                self._thread_id = 0
            return

        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result <= 0:
                    break
                if msg.message == WM_HOTKEY and int(msg.wParam) == HOTKEY_ID:
                    self._focus_or_open()
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)
            with self._lock:
                self._registered = False
                self._thread_id = 0
                self._message = "Выключена" if not self._enabled else "Остановлена"

    def _focus_or_open(self) -> None:
        user32 = _user32()
        found: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @callback_type
        def visit(hwnd: int, _lparam: int) -> bool:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            title = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, title, length + 1)
            normalized = title.value.casefold()
            if "q.w.a.n.t.a." in normalized or "кванта" in normalized:
                found.append(int(hwnd))
                return False
            return True

        user32.EnumWindows(visit, 0)
        if found:
            hwnd = found[0]
            if user32.IsIconic(hwnd):
                user32.ShowWindow(hwnd, 9)
            user32.SetForegroundWindow(hwnd)
            return

        try:
            settings = self.store.settings
            view = settings.get("ui_mode", "mini")
            width, height = (480, 740) if view == "mini" else (1360, 900)
            from .browser import open_app_window
            open_app_window(f"http://127.0.0.1:{self.port}/?view={view}", width, height)
        except Exception:
            pass
