"""Small Mineflayer companion restricted to Quanta-managed offline servers."""
from __future__ import annotations

import json
import re
import shutil
import socket
import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

_USERNAME = re.compile(r"^[A-Za-z0-9_]{1,16}$")
_SERVER_ID = re.compile(r"^[a-f0-9]{8,32}$")


class MinecraftCompanionManager:
    def __init__(self, servers: Any, project_root: Path) -> None:
        self.servers = servers
        self.project_root = Path(project_root).resolve()
        self.script = self.project_root / "assistant" / "minecraft_bot.js"
        self._lock = threading.RLock()
        self._start_lock = threading.Lock()
        self._bots: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _stamp() -> str:
        return datetime.now().replace(microsecond=0).isoformat()

    def _find_server(self, server_id: str) -> dict[str, Any]:
        if not _SERVER_ID.fullmatch(str(server_id or "")):
            raise ValueError("Некорректный идентификатор Minecraft-сервера")
        server = next((item for item in self.servers.list_servers() if item.get("id") == server_id), None)
        if not server:
            raise FileNotFoundError("Minecraft-сервер не найден")
        if server.get("status") != "running":
            raise ValueError("Сначала запусти этот Minecraft-сервер в панели Кванты")
        if server.get("offline_mode") is not True or server.get("eula_accepted") is not True:
            raise ValueError("Компаньон работает только на локальном сервере Кванты с принятым EULA и offline-mode")
        return server

    def _node_path(self) -> str:
        node = shutil.which("node")
        if not node:
            raise RuntimeError("Для Minecraft-компаньона нужен Node.js 22 или новее")
        try:
            result = subprocess.run([node, "--version"], capture_output=True, text=True,
                                    timeout=4, check=False, creationflags=0x08000000)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("Не удалось проверить Node.js") from exc
        match = re.search(r"v(\d+)", result.stdout or result.stderr)
        if result.returncode or not match or int(match.group(1)) < 22:
            raise RuntimeError("Для Minecraft-компаньона требуется Node.js 22 или новее")
        if not (self.project_root / "node_modules" / "mineflayer" / "package.json").is_file():
            raise RuntimeError("Mineflayer не установлен. Выполни `npm install` в папке Quanta")
        if not self.script.is_file():
            raise RuntimeError("Скрипт Minecraft-компаньона не найден")
        return node

    def start(self, server_id: str, username: str = "Quanta") -> dict[str, Any]:
        with self._start_lock:
            return self._start(server_id, username)

    def _start(self, server_id: str, username: str) -> dict[str, Any]:
        username = str(username or "").strip()
        if not _USERNAME.fullmatch(username):
            raise ValueError("Имя бота должно содержать 1–16 латинских букв, цифр или подчёркиваний")
        server = self._find_server(server_id)
        port = int(server.get("port") or 0)
        if not 1024 <= port <= 65535:
            raise ValueError("Некорректный порт сервера")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1.0):
                pass
        except OSError as exc:
            raise RuntimeError("Сервер запускается, но ещё не принимает соединения. Подожди несколько секунд и попробуй снова.") from exc
        node = self._node_path()
        with self._lock:
            other_active = next((item for key, item in self._bots.items()
                                 if key != server_id and item["process"].poll() is None), None)
            if other_active:
                raise RuntimeError("Компаньон уже подключён к другому Minecraft-серверу; сначала отключи его")
            old = self._bots.get(server_id)
            if old and old["process"].poll() is None:
                if old.get("status") not in {"error", "disconnected"}:
                    return self._snapshot(server_id, old)
        if old and old["process"].poll() is None:
            self.stop(server_id)
        with self._lock:
            command = [node, str(self.script), "127.0.0.1", str(port), username]
            process = subprocess.Popen(
                command, cwd=str(self.project_root), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=0x08000000,
            )
            record: dict[str, Any] = {
                "process": process, "server_id": server_id, "server_name": str(server.get("name") or "Minecraft"),
                "username": username, "status": "connecting", "health": None, "food": None,
                "position": None, "players": [], "nearby": [], "events": [], "error": "",
                "updated": self._stamp(),
            }
            self._bots[server_id] = record
            threading.Thread(target=self._read_output, args=(server_id, process),
                             name=f"quanta-minecraft-bot-{server_id}", daemon=True).start()
            return self._snapshot(server_id, record)

    def _snapshot(self, server_id: str, record: dict[str, Any]) -> dict[str, Any]:
        result = {key: value for key, value in record.items() if key != "process"}
        process = record.get("process")
        if process and process.poll() is not None and result.get("status") not in {"stopped", "error"}:
            result["status"] = "disconnected"
        result["events"] = list(result.get("events") or [])[-24:]
        result["server_id"] = server_id
        return result

    def list_status(self) -> list[dict[str, Any]]:
        with self._lock:
            return [self._snapshot(key, value) for key, value in self._bots.items()]

    def status(self, server_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._bots.get(server_id)
            if not record:
                return {"server_id": server_id, "status": "stopped", "events": []}
            return self._snapshot(server_id, record)

    def _record_event(self, server_id: str, event: dict[str, Any]) -> None:
        kind = str(event.get("type") or "event")[:32]
        with self._lock:
            record = self._bots.get(server_id)
            if not record:
                return
            if kind == "state":
                for key in ("health", "food", "position", "players", "nearby"):
                    if key in event:
                        record[key] = event[key]
            elif kind == "status":
                record["status"] = str(event.get("status") or "connecting")[:32]
                if event.get("error"):
                    record["error"] = str(event["error"])[:400]
            text = str(event.get("text") or "").replace("\x00", "")[:320]
            if text:
                record.setdefault("events", []).append({"type": kind, "text": text, "at": self._stamp()})
                record["events"] = record["events"][-24:]
            record["updated"] = self._stamp()

    def _read_output(self, server_id: str, process: subprocess.Popen[str]) -> None:
        try:
            if process.stdout:
                for line in process.stdout:
                    try:
                        event = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if isinstance(event, dict):
                        self._record_event(server_id, event)
        finally:
            with self._lock:
                record = self._bots.get(server_id)
                if record and record.get("process") is process and record.get("status") != "stopped":
                    if process.poll() not in (None, 0) and not record.get("error"):
                        record["error"] = "Компаньон завершился; проверь Node.js и логи приложения."
                    record["status"] = "disconnected" if process.poll() not in (0,) else "stopped"
                    record["updated"] = self._stamp()

    def say(self, server_id: str, text: str) -> dict[str, Any]:
        message = " ".join(str(text or "").split()).strip()
        if not message or len(message) > 200 or any(ord(char) < 32 for char in message):
            raise ValueError("Сообщение должно содержать от 1 до 200 символов в одну строку")
        if message.startswith(("/", "\\")):
            raise ValueError("Команды сервера через игровой чат отключены")
        with self._lock:
            record = self._bots.get(server_id)
            if not record or record.get("status") != "online":
                raise ValueError("Компаньон ещё не подключён к серверу")
            process = record["process"]
            if not process.stdin or process.poll() is not None:
                raise RuntimeError("Соединение компаньона завершилось")
            process.stdin.write(json.dumps({"action": "say", "text": message}, ensure_ascii=False) + "\n")
            process.stdin.flush()
            return self._snapshot(server_id, record)

    def stop(self, server_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._bots.get(server_id)
            if not record:
                return {"server_id": server_id, "status": "stopped", "events": []}
            process = record["process"]
            record["status"] = "stopping"
            try:
                if process.stdin and process.poll() is None:
                    process.stdin.write('{"action":"stop"}\n')
                    process.stdin.flush()
            except (OSError, BrokenPipeError):
                pass
        try:
            process.wait(timeout=6)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)
        with self._lock:
            record = self._bots.get(server_id)
            if record:
                record["status"] = "stopped"
                record["updated"] = self._stamp()
                return self._snapshot(server_id, record)
        return {"server_id": server_id, "status": "stopped", "events": []}

    def stop_for_server(self, server_id: str) -> None:
        with self._lock:
            active = server_id in self._bots and self._bots[server_id]["process"].poll() is None
        if active:
            self.stop(server_id)

    def shutdown(self) -> None:
        with self._lock:
            ids = list(self._bots)
        for server_id in ids:
            try:
                self.stop(server_id)
            except Exception:
                pass
