"""Evaluation-only tools. Every side effect is confined to a temp workspace."""

from __future__ import annotations

import ipaddress
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit

from assistant.network_security import resolve_public_url
from assistant.tools import Toolbox


def _schema(properties: dict[str, dict[str, Any]], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or []}


_STRING = {"type": "string"}
_TOOLS: dict[str, dict[str, Any]] = {
    "list_dir": {
        "label": "Просмотр тестовой папки", "risk": "read", "network": False,
        "description": "List entries in the isolated evaluation folder.",
        "parameters": _schema({"path": {**_STRING, "default": "."}}),
    },
    "read_file": {
        "label": "Чтение тестового файла", "risk": "read", "network": False,
        "description": "Read a text file only from the isolated evaluation folder.",
        "parameters": _schema({"path": _STRING}, ["path"]),
    },
    "write_file": {
        "label": "Запись тестового файла", "risk": "high", "network": False,
        "description": "Write a text file only in the isolated evaluation folder; requires a mocked approval.",
        "parameters": _schema({"path": _STRING, "content": _STRING}, ["path", "content"]),
    },
    "search_files": {
        "label": "Поиск тестовых файлов", "risk": "read", "network": False,
        "description": "Search file names inside the isolated evaluation folder.",
        "parameters": _schema({"query": _STRING}, ["query"]),
    },
    "web_search": {
        "label": "Макетный поиск", "risk": "read", "network": True,
        "description": "Return a canned search result; never uses the network.",
        "parameters": _schema({"query": _STRING}, ["query"]),
    },
    "fetch_url": {
        "label": "Макетная загрузка страницы", "risk": "read", "network": True,
        "description": "Return a canned page; never performs a network request. Local targets are checked by the production SSRF validator.",
        "parameters": _schema({"url": _STRING}, ["url"]),
    },
    "screenshot": {
        "label": "Макетный текст экрана", "risk": "read", "network": False,
        "description": "Return canned text from a mock screen; never reads the real screen.",
        "parameters": _schema({}),
    },
    "pc_uia_tree": {
        "label": "Макетное дерево UIA", "risk": "read", "network": False,
        "description": "Return canned UI Automation text; never inspects real applications.",
        "parameters": _schema({}),
    },
    "memory_save": {
        "label": "Макетная запись в память", "risk": "low", "network": False,
        "description": "Save a fact to this evaluation case's in-memory mock state.",
        "parameters": _schema({"text": _STRING}, ["text"]),
    },
    "memory_list": {
        "label": "Макетная память", "risk": "read", "network": False,
        "description": "Search this evaluation case's in-memory mock facts.",
        "parameters": _schema({"query": {**_STRING, "default": ""}}),
    },
    "memory_delete": {
        "label": "Удаление макетной памяти", "risk": "high", "network": False,
        "description": "Delete a matching fact only in this evaluation case's in-memory mock state; requires mocked approval.",
        "parameters": _schema({"identifier": _STRING}, ["identifier"]),
    },
    "notes_create": {
        "label": "Создание тестовой заметки", "risk": "low", "network": False,
        "description": "Create a note in this evaluation case's in-memory mock state.",
        "parameters": _schema({"title": _STRING, "text": {**_STRING, "default": ""}}, ["title"]),
    },
    "notes_list": {
        "label": "Список тестовых заметок", "risk": "read", "network": False,
        "description": "List or filter notes from this evaluation case's mock state.",
        "parameters": _schema({"query": {**_STRING, "default": ""}}),
    },
    "reminder_create": {
        "label": "Создание тестового напоминания", "risk": "low", "network": False,
        "description": "Create a reminder in this evaluation case's in-memory mock state.",
        "parameters": _schema({"text": _STRING, "when": _STRING}, ["text", "when"]),
    },
    "reminder_list": {
        "label": "Список тестовых напоминаний", "risk": "read", "network": False,
        "description": "List reminders from this evaluation case's mock state.",
        "parameters": _schema({}),
    },
    "delete_path": {
        "label": "Макетное удаление", "risk": "high", "network": False,
        "description": "Dangerous-action test double. It never deletes anything and requires mocked approval.",
        "parameters": _schema({"path": _STRING}, ["path"]),
    },
    "run_command": {
        "label": "Макетная команда", "risk": "high", "network": False,
        "description": "Dangerous-action test double. It never starts a process and requires mocked approval.",
        "parameters": _schema({"command": _STRING}, ["command"]),
    },
}


class MockToolbox:
    """Implements only canned/local tools used by the first ten eval cases."""

    def __init__(
        self,
        workspace: Path,
        initial_state: dict[str, Any],
        allowed_tools: list[str],
        *,
        full_catalog: bool = False,
    ) -> None:
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        selected_tools = TOOL_NAMES if full_catalog else allowed_tools
        self.tools = {
            name: {"name": name, **_TOOLS[name]}
            for name in selected_tools
            if name in _TOOLS
        }
        self.notes = [
            {"title": str(item.get("title") or ""), "text": str(item.get("text") or "")}
            for item in initial_state.get("notes", []) if isinstance(item, dict)
        ]
        self.reminders = [
            {"text": str(item.get("text") or ""), "when": str(item.get("when") or "")}
            for item in initial_state.get("reminders", []) if isinstance(item, dict)
        ]
        self.web_result = str(initial_state.get("web_result") or "Макетных результатов не задано.")
        self.fetch_result = str(initial_state.get("fetch_result") or self.web_result)
        self.screen_text = str(initial_state.get("screen_text") or "")
        self.uia_text = str(initial_state.get("uia_text") or self.screen_text)
        self.web_queries: list[str] = []
        self.executed_tools: list[str] = []
        self.network_attempts: list[str] = []
        self.network_guard_rejections: list[str] = []
        self.memories = [str(item) for item in initial_state.get("memories", [])]
        self._sensitivity_toolbox = Toolbox(SimpleNamespace(
            settings={"work_dir": str(self.workspace)},
            settings_path=self.workspace.parent / "data" / "settings.json",
        ))

        files = initial_state.get("files", {})
        if not isinstance(files, dict):
            raise ValueError("initial_state.files must be an object")
        for relative, content in files.items():
            target = self.resolve(str(relative))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8")

    def resolve(self, raw_path: str) -> Path:
        target = (self.workspace / str(raw_path or ".")).resolve()
        try:
            target.relative_to(self.workspace)
        except ValueError as exc:
            raise ValueError("mock tool path escapes isolated workspace") from exc
        return target

    def _resolve(self, path: str | None) -> Path:
        return self.resolve(str(path or "."))

    def sensitive_file_path(self, _path: str | None) -> None:
        return self._sensitivity_toolbox.sensitive_file_path(_path)

    def schemas(self, names: list[str] | set[str] | None = None) -> list[dict[str, Any]]:
        selected = sorted(self.tools) if names is None else sorted(set(names) & set(self.tools))
        return [
            {"type": "function", "function": {
                "name": name, "description": self.tools[name]["description"],
                "parameters": self.tools[name]["parameters"],
            }}
            for name in selected
        ]

    def label(self, name: str) -> str:
        return str(self.tools.get(name, {}).get("label") or name)

    def risk(self, name: str) -> str:
        return str(self.tools.get(name, {}).get("risk") or "high")

    def allowed(self, mode: str, name: str) -> bool:
        if name not in self.tools:
            return False
        return mode != "safe" or self.risk(name) in {"read", "low"}

    def needs_confirm(self, _mode: str, name: str) -> bool:
        return name in self.tools and self.risk(name) == "high"

    def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        if name not in self.tools:
            return {"ok": False, "text": f"Unknown mock tool: {name}"}
        self.executed_tools.append(name)
        try:
            result = getattr(self, f"_tool_{name}")(**args)
        except (OSError, ValueError, TypeError) as exc:
            return {"ok": False, "text": f"Mock tool refused: {exc}"}
        if isinstance(result, dict):
            result.setdefault("ok", True)
            return result
        return {"ok": True, "text": str(result)}

    def _tool_list_dir(self, path: str = ".") -> dict[str, Any]:
        target = self.resolve(path)
        if not target.is_dir():
            return {"ok": False, "text": "Evaluation folder not found."}
        return {"ok": True, "text": "\n".join(sorted(item.name for item in target.iterdir()))}

    def _tool_read_file(self, path: str) -> dict[str, Any]:
        target = self.resolve(path)
        if not target.is_file():
            return {"ok": False, "text": "Evaluation file not found."}
        return {"ok": True, "text": target.read_text(encoding="utf-8")}

    def _tool_write_file(self, path: str, content: str, append: bool = False) -> dict[str, Any]:
        target = self.resolve(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a" if append else "w", encoding="utf-8", newline="\n") as stream:
            stream.write(str(content))
        return {"ok": True, "text": f"Mock file saved: {path}"}

    def _tool_search_files(self, query: str) -> dict[str, Any]:
        needle = str(query).casefold()
        matches = sorted(
            item.relative_to(self.workspace).as_posix()
            for item in self.workspace.rglob("*")
            if item.is_file() and needle in item.name.casefold()
        )
        return {"ok": True, "text": "\n".join(matches) if matches else "No matching files."}

    def _tool_web_search(self, query: str) -> dict[str, Any]:
        self.web_queries.append(str(query))
        return {"ok": True, "text": f"Mock search for {query}: {self.web_result}"}

    def _tool_fetch_url(self, url: str) -> dict[str, Any]:
        """Serve a canned page without DNS or HTTP; validate local IPs with production policy."""
        value = str(url or "").strip()
        try:
            parsed = urlsplit(value)
            host = (parsed.hostname or "").rstrip(".").casefold()
            local_candidate = host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan"))
            if host:
                try:
                    address = ipaddress.ip_address(host)
                    local_candidate = not address.is_global
                except ValueError:
                    local_candidate = local_candidate or all(char in "0123456789abcdefx." for char in host)
            if local_candidate:
                resolve_public_url(value)
        except ValueError as exc:
            self.network_guard_rejections.append("fetch_url")
            return {"ok": False, "text": str(exc)}
        return {"ok": True, "text": self.fetch_result}

    def _tool_screenshot(self) -> dict[str, Any]:
        return {"ok": True, "text": self.screen_text or "Макетный экран пуст."}

    def _tool_pc_uia_tree(self) -> dict[str, Any]:
        return {"ok": True, "text": self.uia_text or "Макетное дерево UIA пусто."}

    def _tool_memory_save(self, text: str) -> dict[str, Any]:
        self.memories.append(str(text))
        return {"ok": True, "text": "Запись добавлена в макетную память."}

    def _tool_memory_list(self, query: str = "") -> dict[str, Any]:
        needle = str(query).casefold()
        matches = [item for item in self.memories if needle in item.casefold()]
        return {"ok": True, "text": "\n".join(matches) if matches else "Подходящих записей памяти нет."}

    def _tool_memory_delete(self, identifier: str) -> dict[str, Any]:
        matches = [item for item in self.memories if identifier.casefold() in item.casefold()]
        if len(matches) != 1:
            return {"ok": False, "text": "Запись памяти не найдена однозначно."}
        self.memories.remove(matches[0])
        return {"ok": True, "text": "Запись удалена из макетной памяти."}

    def _tool_notes_create(self, title: str, text: str = "") -> dict[str, Any]:
        self.notes.append({"title": str(title), "text": str(text)})
        return {"ok": True, "text": f"Mock note created: {title}"}

    def _tool_notes_list(self, query: str = "") -> dict[str, Any]:
        query_folded = str(query).casefold()
        matches = [item for item in self.notes if query_folded in (item["title"] + " " + item["text"]).casefold()]
        text = "\n".join(f"{item['title']}: {item['text']}" for item in matches)
        return {"ok": True, "text": text or "No matching mock notes."}

    def _tool_reminder_create(self, text: str, when: str) -> dict[str, Any]:
        self.reminders.append({"text": str(text), "when": str(when)})
        return {"ok": True, "text": f"Mock reminder created: {text} — {when}"}

    def _tool_reminder_list(self) -> dict[str, Any]:
        text = "\n".join(f"{item['text']} — {item['when']}" for item in self.reminders)
        return {"ok": True, "text": text or "No mock reminders."}

    def _tool_delete_path(self, path: str) -> dict[str, Any]:
        target = self.resolve(path)
        if not target.is_file():
            return {"ok": False, "text": "Evaluation file not found."}
        target.unlink()
        return {"ok": True, "text": "Mock evaluation file deleted."}

    def _tool_run_command(self, command: str) -> dict[str, Any]:
        del command
        return {"ok": False, "text": "Mock command execution is disabled."}

    def snapshot(self) -> dict[str, Any]:
        files = {
            item.relative_to(self.workspace).as_posix(): item.read_text(encoding="utf-8")
            for item in sorted(self.workspace.rglob("*")) if item.is_file()
        }
        return {
            "files": files,
            "notes": list(self.notes),
            "reminders": list(self.reminders),
            "memories": list(self.memories),
            "web_queries": list(self.web_queries),
            "network_attempts": list(self.network_attempts),
            "network_guard_rejections": list(self.network_guard_rejections),
        }


TOOL_NAMES = frozenset(_TOOLS)
