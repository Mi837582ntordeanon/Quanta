"""Optional Windows Sandbox runner for explicitly approved shell commands.

Only the selected work folder and a temporary result folder are mapped into
the disposable guest. Guest networking, clipboard, printers and vGPU are off.
If Windows Sandbox is unavailable, this module fails closed and never retries
the command on the host.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

WORKSPACE_GUEST = r"C:\QuantaWorkspace"
IPC_GUEST = r"C:\QuantaIPC"
MAX_RESULT_BYTES = 14000
SANDBOX_BOOT_ALLOWANCE_SECONDS = 120


def _sandbox_executable() -> Path | None:
    candidates = []
    windir = os.environ.get("WINDIR") or os.environ.get("SystemRoot")
    if windir:
        candidates.append(Path(windir) / "System32" / "WindowsSandbox.exe")
    found = shutil.which("WindowsSandbox.exe")
    if found:
        candidates.append(Path(found))
    return next((path.resolve() for path in candidates if path.is_file()), None)


def status() -> dict[str, Any]:
    executable = _sandbox_executable()
    return {
        "available": executable is not None,
        "executable": str(executable) if executable else "",
        "network_disabled": True,
        "reason": "" if executable else "Компонент Windows Sandbox не установлен или выключен.",
    }


def _xml_text(parent: ET.Element, name: str, value: str) -> ET.Element:
    child = ET.SubElement(parent, name)
    child.text = value
    return child


def build_configuration(workspace: Path, ipc: Path, logon_command: str) -> str:
    """Build a WSB config with only the two selected shared folders."""
    root = ET.Element("Configuration")
    _xml_text(root, "VGpu", "Disable")
    _xml_text(root, "Networking", "Disable")
    _xml_text(root, "ClipboardRedirection", "Disable")
    _xml_text(root, "PrinterRedirection", "Disable")
    _xml_text(root, "AudioInput", "Disable")
    _xml_text(root, "VideoInput", "Disable")
    mapped = ET.SubElement(root, "MappedFolders")
    for host, guest, read_only in (
        (workspace.resolve(), WORKSPACE_GUEST, "false"),
        (ipc.resolve(), IPC_GUEST, "false"),
    ):
        folder = ET.SubElement(mapped, "MappedFolder")
        _xml_text(folder, "HostFolder", str(host))
        _xml_text(folder, "SandboxFolder", guest)
        _xml_text(folder, "ReadOnly", read_only)
    logon = ET.SubElement(root, "LogonCommand")
    _xml_text(logon, "Command", logon_command)
    return ET.tostring(root, encoding="unicode")


def _b64_utf16(value: str) -> str:
    return base64.b64encode(value.encode("utf-16le")).decode("ascii")


def _guest_runner(command: str, work_dir: str, shell: str, timeout: int) -> str:
    command_b64 = base64.b64encode(command.encode("utf-8", "replace")).decode("ascii")
    return f"""$ErrorActionPreference = 'Stop'
$utf8 = [System.Text.Encoding]::UTF8
$command = $utf8.GetString([Convert]::FromBase64String('{command_b64}'))
$workDir = '{work_dir.replace("'", "''")}'
$resultPath = '{IPC_GUEST}\\result.json'
$stdoutPath = '{IPC_GUEST}\\stdout.txt'
$stderrPath = '{IPC_GUEST}\\stderr.txt'
$shellName = '{shell.casefold()}'
$timeoutMs = {timeout * 1000}
try {{
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.WorkingDirectory = $workDir
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = $utf8
    $psi.StandardErrorEncoding = $utf8
    if ($shellName -eq 'powershell') {{
        $inner = "Set-Location -LiteralPath '" + $workDir.Replace("'", "''") + "'`r`n" + $command
        $encoded = [Convert]::ToBase64String([System.Text.Encoding]::Unicode.GetBytes($inner))
        $psi.FileName = [System.IO.Path]::Combine($env:SystemRoot, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
        $psi.Arguments = '-NoLogo -NoProfile -NonInteractive -EncodedCommand ' + $encoded
    }} else {{
        $scriptPath = '{IPC_GUEST}\\run.cmd'
        $body = "@echo off`r`nchcp 65001>nul`r`npushd `"$workDir`"`r`n$command`r`nset `"__QUANTA_EXIT__=%ERRORLEVEL%`"`r`npopd`r`nexit /b %__QUANTA_EXIT__%"
        [System.IO.File]::WriteAllText($scriptPath, $body, [System.Text.UTF8Encoding]::new($false))
        $psi.FileName = [System.IO.Path]::Combine($env:SystemRoot, 'System32', 'cmd.exe')
        $psi.Arguments = '/d /s /c "' + $scriptPath + '"'
    }}
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    [void]$process.Start()
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $finished = $process.WaitForExit($timeoutMs)
    if (-not $finished) {{
        try {{ $process.Kill() }} catch {{}}
        try {{ $process.WaitForExit(5000) | Out-Null }} catch {{}}
        $exitCode = -1
    }} else {{
        $exitCode = $process.ExitCode
    }}
    $stdout = $stdoutTask.Result
    $stderr = $stderrTask.Result
    $result = @{{ ok = ($finished -and $exitCode -eq 0); exit_code = $exitCode; timed_out = (-not $finished); stdout = $stdout; stderr = $stderr }}
}} catch {{
    $result = @{{ ok = $false; exit_code = -1; timed_out = $false; stdout = ''; stderr = $_.Exception.Message }}
}}
try {{
    [System.IO.File]::WriteAllText($resultPath, (ConvertTo-Json -InputObject $result -Compress), [System.Text.UTF8Encoding]::new($false))
    Start-Sleep -Milliseconds 900
    & ([System.IO.Path]::Combine($env:SystemRoot, 'System32', 'shutdown.exe')) /s /t 0
}} catch {{}}
"""


def run(command: str, work_dir: Path, sandbox_root: Path, shell: str = "cmd",
        timeout: int = 60, data_dir: Path | None = None) -> dict[str, Any]:
    executable = _sandbox_executable()
    if executable is None:
        return {"ok": False, "text": "Windows Sandbox недоступен; команда на этом компьютере не запускалась."}

    root = Path(sandbox_root).expanduser().resolve()
    work = Path(work_dir).expanduser().resolve()
    if not root.is_dir() or not work.is_dir() or not work.is_relative_to(root):
        return {"ok": False, "text": "Sandbox отклонён: рабочая папка должна находиться внутри выбранной папки."}
    try:
        timeout = max(1, min(300, int(timeout or 60)))
    except (TypeError, ValueError):
        timeout = 60

    relative = work.relative_to(root)
    guest_work = WORKSPACE_GUEST if str(relative) == "." else str(Path(WORKSPACE_GUEST) / relative)
    base = Path(data_dir) if data_dir else Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "Quanta" / "data"
    runs = base / "sandbox-runs"
    runs.mkdir(parents=True, exist_ok=True)
    result: dict[str, Any] | None = None
    with tempfile.TemporaryDirectory(prefix="run-", dir=runs) as temporary:
        run_dir = Path(temporary).resolve()
        result_path = run_dir / "result.json"
        runner = _guest_runner(str(command), guest_work, "powershell" if shell.casefold() == "powershell" else "cmd", timeout)
        encoded_runner = _b64_utf16(runner)
        logon_command = "powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand " + encoded_runner
        config_path = run_dir / "command.wsb"
        config_path.write_text(build_configuration(root, run_dir, logon_command), encoding="utf-8")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            sandbox = subprocess.Popen(
                [str(executable), str(config_path)], stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, creationflags=creationflags,
            )
        except OSError as exc:
            return {"ok": False, "text": f"Не удалось запустить Windows Sandbox: {exc}"}

        deadline = time.monotonic() + timeout + SANDBOX_BOOT_ALLOWANCE_SECONDS
        while time.monotonic() < deadline:
            if result_path.is_file():
                try:
                    raw = result_path.read_text(encoding="utf-8")
                    result = json.loads(raw)
                except (OSError, ValueError):
                    result = None
                if result is not None:
                    break
            if sandbox.poll() is not None:
                break
            time.sleep(0.25)

        if sandbox.poll() is None:
            try:
                sandbox.wait(timeout=35)
            except subprocess.TimeoutExpired:
                sandbox.terminate()
                try:
                    sandbox.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    sandbox.kill()
                    sandbox.wait(timeout=5)

        if result is None:
            return {"ok": False, "text": "Windows Sandbox не вернул результат. Команда не запускалась на хосте."}

        stdout = str(result.get("stdout") or "").strip()
        stderr = str(result.get("stderr") or "").strip()
        timed_out = bool(result.get("timed_out"))
        parts = [f"Выполнено в Windows Sandbox. Код выхода: {result.get('exit_code', -1)}"]
        if timed_out:
            parts[0] = f"Команда остановлена по тайм-ауту ({timeout} сек.) внутри Windows Sandbox."
        if stdout:
            parts.append("\nSTDOUT:\n" + stdout)
        if stderr:
            parts.append("\nSTDERR:\n" + stderr)
        if not stdout and not stderr:
            parts.append("(вывод пуст)")
        return {"ok": bool(result.get("ok")), "text": "\n".join(parts)[:MAX_RESULT_BYTES]}
