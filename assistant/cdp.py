"""Small localhost-only Chrome DevTools Protocol client for Chromium apps."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import socket
import struct
import urllib.request
from typing import Any
from urllib.parse import urlsplit


class CDPError(RuntimeError):
    pass


class WebSocket:
    def __init__(self, url: str, timeout: float = 5.0) -> None:
        parsed = urlsplit(url)
        if parsed.scheme != "ws" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise CDPError("Разрешён только локальный CDP websocket")
        host, port = parsed.hostname, parsed.port or 80
        self.socket = socket.create_connection((host, port), timeout=timeout)
        self.socket.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {parsed.path or '/'}{('?' + parsed.query) if parsed.query else ''} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n"
        )
        self.socket.sendall(request.encode("ascii"))
        response = self._read_headers()
        expected = base64.b64encode(hashlib.sha1(
            (key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
        ).digest()).decode("ascii")
        headers = response.decode("latin-1", "replace").split("\r\n")
        if not headers or " 101 " not in f" {headers[0]} ":
            self.socket.close()
            raise CDPError(f"CDP отклонил websocket: {headers[0] if headers else 'пустой ответ'}")
        actual = next((line.split(":", 1)[1].strip() for line in headers[1:]
                       if line.lower().startswith("sec-websocket-accept:")), "")
        if actual != expected:
            self.socket.close()
            raise CDPError("Некорректный websocket handshake CDP")

    def _read_headers(self) -> bytes:
        data = bytearray()
        while b"\r\n\r\n" not in data:
            chunk = self.socket.recv(1)
            if not chunk or len(data) > 16384:
                raise CDPError("CDP не завершил websocket handshake")
            data.extend(chunk)
        return bytes(data).split(b"\r\n\r\n", 1)[0]

    def _read_exact(self, size: int) -> bytes:
        data = bytearray()
        while len(data) < size:
            chunk = self.socket.recv(size - len(data))
            if not chunk:
                raise CDPError("Соединение с CDP закрыто")
            data.extend(chunk)
        return bytes(data)

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        mask = os.urandom(4)
        size = len(payload)
        if size < 126:
            header = bytes((0x80 | opcode, 0x80 | size))
        elif size <= 0xFFFF:
            header = bytes((0x80 | opcode, 0x80 | 126)) + struct.pack("!H", size)
        else:
            header = bytes((0x80 | opcode, 0x80 | 127)) + struct.pack("!Q", size)
        masked = bytes(value ^ mask[index & 3] for index, value in enumerate(payload))
        self.socket.sendall(header + mask + masked)

    def send_json(self, value: dict[str, Any]) -> None:
        self._send_frame(1, json.dumps(value, ensure_ascii=False).encode("utf-8"))

    def recv_json(self) -> dict[str, Any]:
        message = bytearray()
        while True:
            first, second = self._read_exact(2)
            opcode = first & 0x0F
            size = second & 0x7F
            if size == 126:
                size = struct.unpack("!H", self._read_exact(2))[0]
            elif size == 127:
                size = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if second & 0x80 else b""
            payload = self._read_exact(size)
            if mask:
                payload = bytes(value ^ mask[index & 3] for index, value in enumerate(payload))
            if opcode == 8:
                raise CDPError("CDP закрыл соединение")
            if opcode == 9:
                self._send_frame(10, payload)
                continue
            if opcode in (1, 2, 0):
                message.extend(payload)
                if first & 0x80:
                    try:
                        value = json.loads(message.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise CDPError("CDP вернул некорректное сообщение") from exc
                    if isinstance(value, dict):
                        return value
                    raise CDPError("CDP вернул неожиданное сообщение")

    def close(self) -> None:
        try:
            self._send_frame(8, b"")
        except OSError:
            pass
        self.socket.close()


class Client:
    def __init__(self, port: int = 9222, timeout: float = 5.0) -> None:
        if not 1 <= int(port) <= 65535:
            raise CDPError("Некорректный порт CDP")
        self.port = int(port)
        self.timeout = timeout
        self._next_id = 0
        self._ws: WebSocket | None = None

    def connect(self) -> None:
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/json/list",
            headers={"Host": f"127.0.0.1:{self.port}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                targets = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise CDPError(f"Локальный порт управления Яндекс Музыкой не отвечает: {exc}") from exc
        target = next((item for item in targets if item.get("type") == "page"
                       and "yandexmusic" in str(item.get("userAgent", "")).casefold()), None)
        if target is None:
            target = next((item for item in targets if item.get("type") == "page"
                           and "music-application://desktop" in str(item.get("url", ""))), None)
        if target is None:
            raise CDPError("В окне Яндекс Музыки не нашла страницу плеера")
        self._ws = WebSocket(str(target.get("webSocketDebuggerUrl") or ""), self.timeout)

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._ws is None:
            self.connect()
        assert self._ws is not None
        self._next_id += 1
        call_id = self._next_id
        self._ws.send_json({"id": call_id, "method": method, "params": params or {}})
        while True:
            response = self._ws.recv_json()
            if response.get("id") == call_id:
                if "error" in response:
                    raise CDPError(str(response["error"].get("message") or response["error"]))
                return response.get("result") or {}

    def evaluate(self, expression: str) -> Any:
        result = self.call("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
            "userGesture": True,
        })
        details = result.get("exceptionDetails")
        if details:
            description = details.get("exception", {}).get("description") or details.get("text") or "ошибка JavaScript"
            raise CDPError(str(description)[:400])
        remote = result.get("result") or {}
        if remote.get("subtype") == "error":
            raise CDPError(str(remote.get("description") or "ошибка JavaScript")[:400])
        return remote.get("value")

    def close(self) -> None:
        if self._ws is not None:
            self._ws.close()
            self._ws = None
