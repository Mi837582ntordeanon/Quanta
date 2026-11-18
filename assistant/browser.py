"""Find a Chromium browser and open Q.W.A.N.T.A. as a standalone app window."""
from __future__ import annotations

import os
import subprocess
import webbrowser
from pathlib import Path

from .storage import DATA_DIR

PROFILE_DIR = DATA_DIR / "browser_profile"


def find_browser() -> str | None:
    local = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        os.path.join(local, r"Microsoft\Edge\Application\msedge.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(local, r"Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        os.path.join(local, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return None


def open_app_window(url: str, width: int = 1360, height: int = 900) -> bool:
    browser = find_browser()
    if not browser:
        webbrowser.open(url)
        return False
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    args = [
        browser,
        f"--app={url}",
        f"--window-size={int(width)},{int(height)}",
        f"--user-data-dir={PROFILE_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--disable-features=Translate",
        "--autoplay-policy=no-user-gesture-required",
    ]
    try:
        subprocess.Popen(args, close_fds=True)
        return True
    except OSError:
        webbrowser.open(url)
        return False
