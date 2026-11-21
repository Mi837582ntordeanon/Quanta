"""Optional stdio MCP client for user-configured local tool servers.

Servers are launched without a shell, only when selected tools are discovered
or called. MCP tools are treated as high-risk by the agent and require an
explicit confirmation before each invocation.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import Any

from .storage import DATA_DIR

CONFIG_PATH = DATA_DIR / "mcp_servers.json"
MAX_SERVERS = 8
MAX_TOOLS = 80
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"stamp": None, "expires": 0.0, "tools": [], "warnings": []}


class MCPClientError(RuntimeError):
    pass


def _read_servers() -> list[dict[str, Any]]:
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        raise MCPClientError(f"Не удалось прочитать {CONFIG_PATH.name}: {exc}") from exc
    servers = raw.get("servers", []) if isinstance(raw, dict) else []
    if not isinstance(servers, list):
        raise MCPClientError("В конфигурации MCP поле servers должно быть списком")
    return [item for item in servers[:MAX_SERVERS] if isinstance(item, dict) and item.get("enabled", True)]


def _server_parameters(config: dict[str, Any]):
    name = str(config.get("name") or "").strip()
    command = str(config.get("command") or "").strip()
    args = config.get("args", [])
    env = config.get("env", {})
    if not name or len(name) > 48:
        raise MCPClientError("У MCP-сервера должно быть короткое имя (до 48 символов)")
    if not command or len(command) > 500 or not Path(command).name:
        raise MCPClientError(f"Некорректная команда MCP-сервера: {name}")
    if not isinstance(args, list) or len(args) > 80 or any(not isinstance(arg, str) or len(arg) > 2000 for arg in args):
        raise MCPClientError(f"Некорректные аргументы MCP-сервера: {name}")
    if not isinstance(env, dict) or len(env) > 40:
        raise MCPClientError(f"Некорректное окружение MCP-сервера: {name}")
    env_map = {}
    for key, value in env.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", str(key)) or not isinstance(value, str) or len(value) > 8000:
            raise MCPClientError(f"Некорректная переменная окружения MCP-сервера: {name}")
        env_map[str(key)] = value
    cwd = str(config.get("cwd") or "").strip()
    if cwd and (len(cwd) > 1000 or not Path(cwd).expanduser().is_dir()):
        raise MCPClientError(f"Рабочая папка MCP-сервера не найдена: {name}")
    try:
        from mcp import StdioServerParameters
    except ImportError as exc:
        raise MCPClientError("MCP SDK не установлен. Выполни: py -3.11 -m pip install -r requirements.txt") from exc
    return name, StdioServerParameters(
        command=command,
        args=list(args),
        env=env_map or None,
        cwd=str(Path(cwd).expanduser()) if cwd else None,
    )


def _asyncio_run(awaitable):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)
    raise MCPClientError("MCP-клиент вызван из уже работающего async event loop")


def _items(result: Any) -> list[Any]:
    values = getattr(result, "tools", result)
    return values if isinstance(values, list) else []


def _schema_from_tool(tool: Any) -> dict[str, Any] | None:
    raw_schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
    if not isinstance(raw_schema, dict):
        raw_schema = {"type": "object", "properties": {}}
    if len(json.dumps(raw_schema, ensure_ascii=False)) > 12000:
        return None
    params = {key: value for key, value in raw_schema.items()
              if key in {"type", "properties", "required", "additionalProperties"}}
    params.setdefault("type", "object")
    if not isinstance(params.get("properties"), dict):
        params["properties"] = {}
    params["properties"] = dict(list(params["properties"].items())[:40])
    if isinstance(params.get("required"), list):
        params["required"] = params["required"][:40]
    name = str(getattr(tool, "name", "") or "").strip()
    if not name or len(name) > 80 or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        return None
    description = str(getattr(tool, "description", "") or getattr(tool, "title", "") or "MCP tool")[:600]
    return {
        "name": name,
        "description": description,
        "parameters": params,
    }


async def _list_server_tools(server_name: str, parameters: Any) -> list[dict[str, Any]]:
    from mcp import Client

    async with Client(parameters) as client:
        result = await asyncio.wait_for(client.list_tools(), timeout=20)
    tools = []
    safe_server = re.sub(r"[^A-Za-z0-9_-]", "_", server_name)[:14]
    for item in _items(result):
        schema = _schema_from_tool(item)
        if not schema:
            continue
        server_hash = hashlib.sha1(server_name.encode("utf-8")).hexdigest()[:6]
        tool_hash = hashlib.sha1(schema["name"].encode("utf-8")).hexdigest()[:6]
        safe_tool = re.sub(r"[^A-Za-z0-9_-]", "_", schema["name"])[:29]
        public_name = f"mcp__{safe_server}_{server_hash}__{safe_tool}_{tool_hash}"
        tools.append({
            **schema,
            "function_name": public_name,
            "server_name": server_name,
            "tool_name": schema["name"],
            "risk": "high",
        })
    return tools


def list_tools() -> tuple[list[dict[str, Any]], list[str]]:
    """Return MCP schemas and isolated configuration/connection warnings."""
    try:
        stat = CONFIG_PATH.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
    except FileNotFoundError:
        return [], []
    except OSError as exc:
        return [], [f"Конфигурация MCP: {str(exc)[:180]}"]
    with _CACHE_LOCK:
        if _CACHE["stamp"] == stamp and time.monotonic() < _CACHE["expires"]:
            return copy.deepcopy(_CACHE["tools"]), list(_CACHE["warnings"])
    result: list[dict[str, Any]] = []
    warnings: list[str] = []
    for config in _read_servers():
        name = str(config.get("name") or "MCP")[:48]
        try:
            server_name, parameters = _server_parameters(config)
            tools = _asyncio_run(_list_server_tools(server_name, parameters))
            result.extend(tools[:max(0, MAX_TOOLS - len(result))])
        except Exception as exc:  # keep built-in Quanta tools available on MCP failure
            warnings.append(f"{name}: {str(exc)[:220]}")
        if len(result) >= MAX_TOOLS:
            break
    seen: set[str] = set()
    unique = []
    for item in result:
        key = item["function_name"]
        if key not in seen:
            seen.add(key)
            unique.append(item)
    with _CACHE_LOCK:
        _CACHE.update({"stamp": stamp, "expires": time.monotonic() + 20,
                       "tools": copy.deepcopy(unique), "warnings": list(warnings)})
    return unique, warnings


async def _call_server_tool(config: dict[str, Any], tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    from mcp import Client

    server_name, parameters = _server_parameters(config)
    async with Client(parameters) as client:
        result = await asyncio.wait_for(client.call_tool(tool_name, arguments), timeout=90)
    parts = []
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            parts.append(str(text))
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False, default=str))
    return {
        "ok": not bool(getattr(result, "is_error", False)),
        "text": "\n".join(parts)[:12000] or ("MCP tool completed." if not getattr(result, "is_error", False) else "MCP tool failed."),
    }


def call_tool(server_name: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Reconnect to one configured server and execute one confirmed action."""
    config = next((item for item in _read_servers() if item.get("name") == server_name), None)
    if not config:
        return {"ok": False, "text": "MCP-сервер отключён или удалён из конфигурации."}
    try:
        return _asyncio_run(_call_server_tool(config, tool_name, arguments if isinstance(arguments, dict) else {}))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "text": f"MCP-инструмент завершился ошибкой: {str(exc)[:500]}"}
