"""Windows UI Automation bridge backed by the inbox PowerShell UIA assemblies."""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

CREATE_NO_WINDOW = 0x08000000
BRIDGE = Path(__file__).with_name("uia_bridge.ps1")


class AutomationError(RuntimeError):
    pass


def _powershell() -> str:
    executable = shutil.which("powershell.exe") or shutil.which("powershell")
    if executable:
        return executable
    fallback = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if fallback.is_file():
        return str(fallback)
    raise AutomationError("Windows PowerShell не найден")


def call(request: dict[str, Any], *, timeout: int = 15) -> dict[str, Any]:
    if os.name != "nt":
        raise AutomationError("UI Automation доступна только в Windows")
    if not BRIDGE.is_file():
        raise AutomationError("Не найден мост Windows UI Automation")
    payload = base64.b64encode(json.dumps(request, ensure_ascii=False).encode("utf-8")).decode("ascii")
    command = [
        _powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(BRIDGE), "-RequestBase64", payload,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        raise AutomationError("Windows UI Automation не ответила за отведённое время") from exc
    except OSError as exc:
        raise AutomationError(f"Не удалось запустить Windows UI Automation: {exc}") from exc
    raw = result.stdout.strip()
    try:
        response = json.loads(raw) if raw else {}
    except json.JSONDecodeError as exc:
        detail = result.stderr.strip() or raw[:500] or f"код {result.returncode}"
        raise AutomationError(f"UI Automation вернула некорректный ответ: {detail}") from exc
    if result.returncode != 0 or not response.get("ok"):
        detail = str(response.get("error") or result.stderr.strip() or f"код {result.returncode}")
        raise AutomationError(detail[:600])
    return response


def list_windows(limit: int = 80) -> list[dict[str, Any]]:
    response = call({"op": "windows", "limit": max(1, min(int(limit), 120))})
    return response.get("windows") or []


def control_tree(hwnd: int = 0, *, max_depth: int = 5, limit: int = 160) -> dict[str, Any]:
    return call({
        "op": "tree", "hwnd": int(hwnd or 0),
        "max_depth": max(1, min(int(max_depth), 8)),
        "limit": max(10, min(int(limit), 300)),
    }, timeout=20)


def perform_action(
    hwnd: int,
    path: list[int],
    operation: str,
    *,
    expected_name: str = "",
    expected_type: str = "",
    expected_automation_id: str = "",
    value: str = "",
) -> dict[str, Any]:
    if int(hwnd or 0) <= 0:
        raise AutomationError("Сначала прочитай дерево нужного окна и используй его HWND")
    if operation not in {"invoke", "set_value", "toggle", "select", "focus"}:
        raise AutomationError("Неизвестное действие UI Automation")
    if not any((expected_name, expected_type, expected_automation_id)):
        raise AutomationError("Для действия нужна хотя бы одна контрольная характеристика из свежего дерева")
    if len(path) > 8 or any(not isinstance(index, int) or index < 0 or index > 500 for index in path):
        raise AutomationError("Некорректный путь к элементу интерфейса")
    if len(value) > 2000:
        raise AutomationError("Текст для ввода слишком длинный")
    return call({
        "op": "act", "hwnd": int(hwnd), "path": path, "action": operation,
        "expected_name": str(expected_name)[:300],
        "expected_type": str(expected_type)[:80],
        "expected_automation_id": str(expected_automation_id)[:200],
        "value": value,
    }, timeout=15)
