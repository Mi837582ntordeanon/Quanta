"""Start Q.W.A.N.T.A. and open its window.

Run this with pythonw (or via the .bat file) so no console window appears.
The HTTP server is started as a detached child process; the launcher itself
exits after the app window is open.
"""
from __future__ import annotations

import ctypes
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from assistant.browser import open_app_window  # noqa: E402
from assistant.storage import DATA_DIR, Store  # noqa: E402

PREFERRED_PORT = int(os.environ.get("QUANTA_PORT", "8765"))
PORT_SPAN = 20
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


def log(message: str) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with (DATA_DIR / "launcher.log").open("a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
    except OSError:
        pass


def server_up(port: int) -> bool:
    expected_data = os.path.normcase(os.path.abspath(str(DATA_DIR)))
    for endpoint in ("health", "bootstrap"):
        try:
            url = f"http://127.0.0.1:{port}/api/{endpoint}"
            with urllib.request.urlopen(url, timeout=0.5) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
            if not data.get("ok"):
                return False
            if endpoint == "health" and "health" not in data:
                return False
            data_dir = data.get("data_dir")
            if data_dir:
                actual_data = os.path.normcase(os.path.abspath(str(data_dir)))
                return actual_data == expected_data
        except Exception:
            return False
    return False


def find_running() -> int | None:
    for port in range(PREFERRED_PORT, PREFERRED_PORT + PORT_SPAN):
        if server_up(port):
            return port
    return None


def pick_free_port() -> int | None:
    for port in range(PREFERRED_PORT, PREFERRED_PORT + PORT_SPAN):
        # connect() detects a real listener without being fooled by TIME_WAIT.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                continue
        return port
    return None


def start_server(port: int) -> bool:
    python = sys.executable
    if not python:
        return False
    log_path = DATA_DIR / "server.out.log"
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        out = open(log_path, "a", encoding="utf-8")
        env = dict(os.environ)
        env["QUANTA_PORT"] = str(port)
        subprocess.Popen(
            [python, str(ROOT / "server.py")],
            cwd=str(ROOT),
            stdout=out,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=DETACHED_PROCESS | CREATE_NO_WINDOW,
            env=env,
            close_fds=True,
        )
    except OSError as exc:
        log(f"cannot start server: {exc}")
        return False
    for _ in range(80):
        if server_up(port):
            return True
        time.sleep(0.25)
    return False


def show_error(message: str) -> None:
    try:
        ctypes.windll.user32.MessageBoxW(None, message, "Q.W.A.N.T.A.", 0x10)
    except Exception:
        pass


def main() -> None:
    log("launcher start")
    port = find_running()
    if port is None:
        port = pick_free_port()
        if port is None:
            show_error(
                "Не удалось найти свободный порт для Q.W.A.N.T.A.\n\n"
                f"Порты {PREFERRED_PORT}–{PREFERRED_PORT + PORT_SPAN - 1} заняты."
            )
            return
        if not start_server(port):
            show_error(
                "Не удалось запустить Q.W.A.N.T.A.\n\n"
                "Проверь data/server.out.log и data/quanta.log.\n"
                f"Порт {port} мог освободиться или занять его другая программа."
            )
            return
    url = f"http://127.0.0.1:{port}"
    log(f"using port {port}")
    try:
        settings = Store().settings
        view = settings.get("ui_mode", "standard")
    except Exception:
        view = "standard"
    if view == "mini":
        opened = open_app_window(url + "/?view=mini", 480, 740)
    else:
        opened = open_app_window(url + f"/?view={view}", 1360, 900)
    if not opened:
        log("browser not found, fallback to default browser")
        import webbrowser
        webbrowser.open(url)
    log("launcher done")


if __name__ == "__main__":
    main()
