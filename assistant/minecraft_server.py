"""Local Vanilla Minecraft Java server manager.

Server jars are fetched directly from Mojang's version metadata and remain in
the user's ignored ``data/minecraft`` directory. No server files are bundled.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import uuid
from collections import deque
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from .storage import DATA_DIR, _read_json, _write_json
from .network_security import safe_open

MANIFEST_URL = "https://piston-meta.mojang.com/mc/game/version_manifest_v2.json"
USER_AGENT = "Quanta/0.1 (local Minecraft server manager)"
MAX_JAR_BYTES = 150 * 1024 * 1024
PORT_MIN, PORT_MAX = 25565, 25575
_HOSTS = {"piston-meta.mojang.com", "launchermeta.mojang.com", "piston-data.mojang.com"}
_JAVA_CACHE_LOCK = threading.Lock()
_JAVA_CACHE: list[dict[str, Any]] | None = None
_JAVA_CACHE_TIME = 0.0


def _stamp() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _safe_host_url(value: str, allowed_hosts: set[str]) -> str:
    parsed = urllib.parse.urlsplit(str(value or ""))
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in allowed_hosts:
        raise ValueError("Ссылка сервера не принадлежит официальному домену Mojang")
    return value


class MinecraftServerManager:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = Path(data_dir or DATA_DIR) / "minecraft"
        self.instances_dir = self.data_dir / "servers"
        self.index_path = self.data_dir / "servers.json"
        self.manifest_path = self.data_dir / "version_manifest_v2.json"
        self.instances_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        stored = _read_json(self.index_path, [])
        self._servers: list[dict[str, Any]] = [item for item in stored if isinstance(item, dict)] if isinstance(stored, list) else []
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._logs: dict[str, deque[str]] = {}
        self._workers: dict[str, threading.Thread] = {}
        self._stop_event = threading.Event()

    def _save(self) -> None:
        _write_json(self.index_path, self._servers)

    def _find_locked(self, server_id: str) -> dict[str, Any]:
        item = next((server for server in self._servers if server.get("id") == server_id), None)
        if not item:
            raise FileNotFoundError("Minecraft-сервер не найден")
        return item

    @staticmethod
    def _json_url(url: str, *, max_bytes: int = 5_000_000) -> dict[str, Any]:
        _safe_host_url(url, _HOSTS)
        with safe_open(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                       timeout=20, allowed_hosts=_HOSTS) as (response, _final_url):
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise ValueError("Метаданные Minecraft неожиданно большие")
            raw = response.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ValueError("Метаданные Minecraft неожиданно большие")
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Официальные метаданные Minecraft имеют неверный формат")
        return data

    def versions(self, *, limit: int = 60) -> dict[str, Any]:
        manifest: dict[str, Any] | None = None
        error = ""
        try:
            manifest = self._json_url(MANIFEST_URL)
            _write_json(self.manifest_path, manifest)
        except (OSError, ValueError, urllib.error.URLError, TimeoutError) as exc:
            cached = _read_json(self.manifest_path, None)
            if isinstance(cached, dict):
                manifest = cached
                error = f"Показываю сохранённый список версий: {exc}"
            else:
                raise RuntimeError(f"Не удалось получить список версий Mojang: {exc}") from exc
        versions = [
            {"id": str(item.get("id") or ""), "type": str(item.get("type") or ""), "release_time": str(item.get("releaseTime") or "")}
            for item in (manifest.get("versions") or [])
            if isinstance(item, dict) and item.get("type") == "release" and item.get("id")
        ]
        return {
            "latest": str((manifest.get("latest") or {}).get("release") or ""),
            "versions": versions[: max(1, min(int(limit), 100))],
            "offline": bool(error), "message": error,
        }

    @staticmethod
    def _validate_name(name: str) -> str:
        name = " ".join(str(name or "").split()).strip()
        if not name or len(name) > 48 or any(ord(char) < 32 or char in "\\/:*?\"<>|" for char in name):
            raise ValueError("Название должно быть от 1 до 48 символов и без символов пути")
        return name

    def create_server(
        self,
        *,
        name: str,
        version: str,
        eula_accepted: bool,
        port: int = 25565,
        memory_mb: int = 2048,
        max_players: int = 10,
        lan_access: bool = False,
    ) -> dict[str, Any]:
        if eula_accepted is not True:
            raise ValueError("Перед созданием сервера нужно прочитать и принять Minecraft EULA")
        name = self._validate_name(name)
        version = str(version or "").strip()
        if not re.fullmatch(r"\d{1,2}\.\d{1,2}(?:\.\d{1,2})?", version):
            raise ValueError("Выбери стабильную версию Minecraft из списка")
        try:
            port = int(port)
            memory_mb = int(memory_mb)
            max_players = int(max_players)
        except (TypeError, ValueError) as exc:
            raise ValueError("Порт, память и число игроков должны быть числами") from exc
        if not 1024 <= port <= 65535:
            raise ValueError("Порт должен быть в диапазоне 1024–65535")
        if not 1024 <= memory_mb <= 8192:
            raise ValueError("Память сервера должна быть от 1024 до 8192 МБ")
        if not 1 <= max_players <= 100:
            raise ValueError("Максимум игроков должен быть от 1 до 100")
        with self._lock:
            if any(int(item.get("port") or 0) == port for item in self._servers):
                raise ValueError(f"Порт {port} уже назначен другому серверу Кванты")
            server_id = uuid.uuid4().hex[:12]
            directory = self.instances_dir / server_id
            directory.mkdir(parents=True, exist_ok=False)
            instance = {
                "id": server_id, "name": name, "version": version,
                "directory": str(directory), "jar_path": str(directory / "server.jar"),
                "port": port, "memory_mb": memory_mb, "max_players": max_players,
                "lan_access": bool(lan_access), "offline_mode": True,
                "eula_accepted": True, "status": "preparing", "progress": 0,
                "downloaded_bytes": 0, "download_total": 0, "java_required": None,
                "error": "", "created": _stamp(), "updated": _stamp(), "pid": None,
            }
            self._servers.append(instance)
            self._save()
            worker = threading.Thread(target=self._prepare_worker, args=(server_id,),
                                      name=f"minecraft-prepare-{server_id}", daemon=True)
            self._workers[server_id] = worker
            worker.start()
            return deepcopy(instance)

    def _resolve_server_metadata(self, version: str) -> tuple[str, str, int, int | None]:
        manifest = self._json_url(MANIFEST_URL)
        _write_json(self.manifest_path, manifest)
        entry = next((item for item in manifest.get("versions") or []
                      if isinstance(item, dict) and item.get("id") == version and item.get("type") == "release"), None)
        if not entry:
            raise ValueError(f"Стабильная версия Minecraft {version} не найдена в списке Mojang")
        metadata_url = _safe_host_url(str(entry.get("url") or ""), {"piston-meta.mojang.com", "launchermeta.mojang.com"})
        metadata = self._json_url(metadata_url)
        server = metadata.get("downloads", {}).get("server") or {}
        jar_url = _safe_host_url(str(server.get("url") or ""), {"piston-data.mojang.com"})
        checksum = str(server.get("sha1") or "").lower()
        if not re.fullmatch(r"[0-9a-f]{40}", checksum):
            raise ValueError("У официального server.jar нет корректной SHA-1 контрольной суммы")
        size = int(server.get("size") or 0)
        if size <= 0 or size > MAX_JAR_BYTES:
            raise ValueError("Размер server.jar вне допустимого диапазона")
        java_version = int((metadata.get("javaVersion") or {}).get("majorVersion") or 0)
        return jar_url, checksum, size, java_version or None

    def _prepare_worker(self, server_id: str) -> None:
        try:
            with self._lock:
                instance = deepcopy(self._find_locked(server_id))
            url, expected_sha1, expected_size, java_required = self._resolve_server_metadata(instance["version"])
            directory = Path(instance["directory"])
            target = Path(instance["jar_path"])
            temp = directory / "server.jar.download"
            digest = hashlib.sha1()
            downloaded = 0
            with safe_open(url, headers={"User-Agent": USER_AGENT}, timeout=60,
                           allowed_hosts={"piston-data.mojang.com"}) as (response, _final_url), temp.open("wb") as output:
                while True:
                    if self._stop_event.is_set():
                        raise RuntimeError("Подготовка сервера остановлена")
                    chunk = response.read(1024 * 512)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > MAX_JAR_BYTES:
                        raise ValueError("server.jar превысил допустимый размер")
                    output.write(chunk)
                    digest.update(chunk)
                    with self._lock:
                        item = self._find_locked(server_id)
                        item["downloaded_bytes"] = downloaded
                        item["download_total"] = expected_size
                        item["progress"] = min(98, int(downloaded * 100 / expected_size))
                        item["java_required"] = java_required
            if downloaded != expected_size:
                raise ValueError(f"Загрузка неполная: получено {downloaded} из {expected_size} байт")
            if digest.hexdigest() != expected_sha1:
                raise ValueError("Контрольная сумма server.jar не совпала; файл удалён")
            os.replace(temp, target)
            self._write_eula_and_properties(instance)
            with self._lock:
                item = self._find_locked(server_id)
                item.update({"status": "stopped", "progress": 100, "error": "", "updated": _stamp()})
                self._save()
        except Exception as exc:  # noqa: BLE001 - job failures are surfaced in the UI
            with self._lock:
                try:
                    item = self._find_locked(server_id)
                    item.update({"status": "error", "error": str(exc)[:600], "updated": _stamp()})
                    self._save()
                except (OSError, FileNotFoundError):
                    pass
            try:
                temp_path = Path(instance["directory"]) / "server.jar.download"
                temp_path.unlink(missing_ok=True)
            except (OSError, UnboundLocalError):
                pass

    @staticmethod
    def _write_eula_and_properties(instance: dict[str, Any]) -> None:
        directory = Path(instance["directory"])
        (directory / "eula.txt").write_text(
            "# Minecraft EULA accepted by the user in Quanta\neula=true\n", encoding="utf-8",
        )
        bind_address = "" if instance.get("lan_access") else "127.0.0.1"
        properties = {
            "enable-jmx-monitoring": "false", "rcon.port": str(int(instance["port"]) + 10),
            "level-seed": "", "gamemode": "survival", "enable-command-block": "false",
            "enable-query": "false", "generator-settings": "{}", "level-name": "world",
            "motd": str(instance["name"])[:59], "query.port": str(instance["port"]),
            "pvp": "true", "generate-structures": "true", "max-chained-neighbor-updates": "1000000",
            "difficulty": "easy", "network-compression-threshold": "256", "max-tick-time": "60000",
            "require-resource-pack": "false", "use-native-transport": "true", "max-players": str(instance["max_players"]),
            "online-mode": "false", "enable-status": "true", "allow-flight": "false",
            "initial-disabled-packs": "", "broadcast-rcon-to-ops": "true", "view-distance": "8",
            "server-ip": bind_address, "resource-pack-prompt": "", "allow-nether": "true",
            "server-port": str(instance["port"]), "enable-rcon": "false", "sync-chunk-writes": "true",
            "op-permission-level": "4", "prevent-proxy-connections": "false", "hide-online-players": "false",
            "simulation-distance": "6", "player-idle-timeout": "0", "force-gamemode": "false",
            "rate-limit": "0", "hardcore": "false", "white-list": "false", "broadcast-console-to-ops": "true",
            "spawn-npcs": "true", "spawn-animals": "true", "function-permission-level": "2",
            "level-type": "minecraft:normal", "text-filtering-config": "", "spawn-monsters": "true",
            "enforce-whitelist": "false", "spawn-protection": "16", "resource-pack-sha1": "",
            "max-world-size": "29999984",
        }
        lines = [f"{key}={value}" for key, value in properties.items()]
        (directory / "server.properties").write_text("\n".join(lines) + "\n", encoding="utf-8")

    @staticmethod
    def java_info(required_major: int | None = None) -> dict[str, Any]:
        global _JAVA_CACHE, _JAVA_CACHE_TIME
        now = time.monotonic()
        with _JAVA_CACHE_LOCK:
            ttl = 30 if _JAVA_CACHE == [] else 120
            if _JAVA_CACHE is None or now - _JAVA_CACHE_TIME > ttl:
                candidates: list[str] = []
                on_path = shutil.which("java")
                if on_path:
                    candidates.append(on_path)
                appdata = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
                runtime_root = appdata / ".minecraft" / "runtime"
                if runtime_root.is_dir():
                    candidates.extend(str(path) for path in runtime_root.glob("*/*/*/bin/java.exe") if path.is_file())
                candidates = list(dict.fromkeys(candidates))
                found: list[dict[str, Any]] = []
                for executable in candidates[:30]:
                    try:
                        result = subprocess.run([executable, "-version"], capture_output=True, text=True,
                                                encoding="utf-8", errors="replace", timeout=8,
                                                creationflags=0x08000000)
                        lines = (result.stderr or result.stdout).splitlines()
                        text = lines[0] if lines else ""
                        match = re.search(r'"(?:1\.)?(\d+)(?:[._+-]|"|$)', text)
                        major = int(match.group(1)) if match else None
                        if result.returncode == 0:
                            found.append({"available": True, "path": executable, "version": text[:120], "major": major})
                    except (OSError, subprocess.TimeoutExpired, IndexError):
                        continue
                _JAVA_CACHE = found
                _JAVA_CACHE_TIME = now
            found = deepcopy(_JAVA_CACHE or [])
        if not found:
            return {"available": False, "compatible": False, "path": "", "version": "Java не найдена", "major": None}
        minimum = int(required_major or 0)
        compatible = [item for item in found if item.get("major") is not None and int(item["major"]) >= minimum]
        if compatible:
            # Prefer the newest for the overview and least-new compatible when starting an older server.
            selector = max if required_major is None else min
            selected = selector(compatible, key=lambda item: int(item.get("major") or 0))
        else:
            selected = max(found, key=lambda item: int(item.get("major") or 0))
        return {**selected, "compatible": bool(compatible), "installed_count": len(found),
                "required_major": minimum or None}

    def _append_log(self, server_id: str, line: str) -> None:
        line = line.rstrip("\r\n")
        if not line:
            return
        with self._lock:
            self._logs.setdefault(server_id, deque(maxlen=400)).append(line[:1000])
            try:
                instance = self._find_locked(server_id)
                path = Path(instance["directory"]) / "server.log"
                with path.open("a", encoding="utf-8", errors="replace") as fh:
                    fh.write(line[:1000] + "\n")
                if path.stat().st_size > 5 * 1024 * 1024:
                    tail = path.read_text(encoding="utf-8", errors="replace").splitlines()[-6000:]
                    path.write_text("\n".join(tail) + "\n", encoding="utf-8")
            except (OSError, FileNotFoundError):
                pass

    def _read_output(self, server_id: str, process: subprocess.Popen[str]) -> None:
        try:
            if process.stdout:
                for line in process.stdout:
                    self._append_log(server_id, line)
        finally:
            with self._lock:
                self._processes.pop(server_id, None)
                try:
                    item = self._find_locked(server_id)
                    item.update({"status": "stopped", "pid": None, "exit_code": process.poll(), "updated": _stamp()})
                    self._save()
                except (OSError, FileNotFoundError):
                    pass

    def start(self, server_id: str) -> dict[str, Any]:
        with self._lock:
            initial = self._find_locked(server_id)
            required_java = int(initial.get("java_required") or 0)
        java = self.java_info(required_java or None)
        with self._lock:
            item = self._find_locked(server_id)
            existing = self._processes.get(server_id)
            if existing and existing.poll() is None:
                return self._public_item(item)
            if item.get("status") in ("preparing", "downloading"):
                raise ValueError("server.jar ещё загружается")
            if item.get("status") == "error":
                raise ValueError(str(item.get("error") or "Подготовка сервера завершилась ошибкой"))
            jar = Path(item.get("jar_path") or "")
            if not jar.is_file():
                raise FileNotFoundError("server.jar не найден; создай сервер заново")
            if not item.get("eula_accepted") or not (jar.parent / "eula.txt").is_file():
                raise ValueError("Minecraft EULA не принята")
            required_java = int(item.get("java_required") or 0)
            if not java["available"]:
                raise RuntimeError("Java не найдена: установи её или запусти Minecraft Launcher, чтобы скачать встроенный runtime.")
            if not java.get("compatible"):
                raise RuntimeError(f"Для Minecraft {item['version']} нужна Java {required_java} или новее; подходящая версия не найдена.")
            memory_mb = int(item["memory_mb"])
            command = [java["path"], f"-Xms{min(512, memory_mb)}M", f"-Xmx{memory_mb}M", "-jar", str(jar), "nogui"]
            log_path = jar.parent / "server.log"
            process = subprocess.Popen(command, cwd=str(jar.parent), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                       bufsize=1, creationflags=0x08000000)
            self._processes[server_id] = process
            item.update({"status": "running", "pid": process.pid, "error": "", "updated": _stamp()})
            self._save()
            thread = threading.Thread(target=self._read_output, args=(server_id, process),
                                      name=f"minecraft-log-{server_id}", daemon=True)
            thread.start()
            self._append_log(server_id, f"[{_stamp()}] server started; log: {log_path.name}")
            return self._public_item(item)

    def stop(self, server_id: str, *, timeout: int = 25) -> dict[str, Any]:
        with self._lock:
            process = self._processes.get(server_id)
            item = self._find_locked(server_id)
            if not process or process.poll() is not None:
                item.update({"status": "stopped", "pid": None, "updated": _stamp()})
                self._save()
                return self._public_item(item)
        try:
            if process.stdin:
                process.stdin.write("stop\n")
                process.stdin.flush()
        except (OSError, BrokenPipeError):
            pass
        try:
            process.wait(timeout=max(5, min(int(timeout), 60)))
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        with self._lock:
            item = self._find_locked(server_id)
            item.update({"status": "stopped", "pid": None, "exit_code": process.returncode, "updated": _stamp()})
            self._processes.pop(server_id, None)
            self._save()
            return self._public_item(item)

    def send_command(self, server_id: str, command: str) -> dict[str, Any]:
        command = str(command or "").strip()
        if not command or len(command) > 200 or any(ord(char) < 32 for char in command):
            raise ValueError("Команда должна занимать 1–200 символов в одной строке")
        with self._lock:
            self._find_locked(server_id)
            process = self._processes.get(server_id)
            if not process or process.poll() is not None or not process.stdin:
                raise RuntimeError("Сервер не запущен")
            process.stdin.write(command + "\n")
            process.stdin.flush()
        return {"ok": True, "text": "Команда передана серверу."}

    def logs(self, server_id: str, *, limit: int = 120) -> list[str]:
        with self._lock:
            item = deepcopy(self._find_locked(server_id))
            cached = list(self._logs.get(server_id, []))
        if cached:
            return cached[-max(1, min(int(limit), 300)):]
        try:
            path = Path(item["directory"]) / "server.log"
            return path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, min(int(limit), 300)):]
        except OSError:
            return []

    def list_servers(self) -> list[dict[str, Any]]:
        with self._lock:
            items = [self._public_item(item) for item in self._servers]
        items.sort(key=lambda item: item.get("created", ""), reverse=True)
        return items

    def has_running(self) -> bool:
        with self._lock:
            return any(process.poll() is None for process in self._processes.values())

    @staticmethod
    def _public_item(item: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(item)
        result["ready"] = Path(str(result.get("jar_path") or "")).is_file()
        host = "127.0.0.1"
        if result.get("lan_access"):
            try:
                candidates = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET, socket.SOCK_STREAM)
                host = next((item[4][0] for item in candidates
                             if ipaddress.ip_address(item[4][0]).is_private
                             and not ipaddress.ip_address(item[4][0]).is_loopback), "IP ПК в локальной сети")
            except OSError:
                host = "IP ПК в локальной сети"
        result["address"] = f"{host}:{result.get('port')}"
        result.pop("directory", None)
        result.pop("jar_path", None)
        return result

    def shutdown(self) -> None:
        self._stop_event.set()
        with self._lock:
            server_ids = list(self._processes)
        for server_id in server_ids:
            try:
                self.stop(server_id)
            except Exception:
                pass
