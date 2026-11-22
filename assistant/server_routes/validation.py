"""Validation for JSON request bodies."""

from __future__ import annotations

import json
from typing import Any, BinaryIO, Mapping


def read_json_object(headers: Mapping[str, str], stream: BinaryIO, max_bytes: int = 25_000_000) -> dict[str, Any]:
    try:
        length = int(headers.get("Content-Length", "0") or "0")
    except ValueError as exc:
        raise ValueError("Некорректный Content-Length") from exc
    if length > max_bytes:
        raise ValueError("Слишком большой запрос")
    raw = stream.read(length) if length else b"{}"
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Некорректный JSON") from exc
    if not isinstance(data, dict):
        raise ValueError("Ожидался JSON-объект")
    return data
