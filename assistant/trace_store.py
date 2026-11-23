"""Privacy-limited JSONL traces for agent turns."""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from urllib.parse import urlsplit


_TRACE_FILE = re.compile(r"^traces(?:\.\d+)?\.jsonl$")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|"
    r"secret|authorization|cookie|private[_-]?key|client[_-]?secret)[\"']?\s*(?:[:=]|\bis\b)\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\r\n,;}\]]+)"
)
_CREDENTIAL = re.compile(
    r"(?i)\b(?:sk-[a-z0-9_-]{16,}|gh[pousr]_[a-z0-9_]{16,}|github_pat_[a-z0-9_]{16,}|"
    r"AIza[0-9a-z_-]{30,})\b"
)
_BEARER = re.compile(r"(?i)\bBearer\s+[a-z0-9._~+/-]+=*")
_JWT = re.compile(r"\b[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b")
_ERROR_PATTERNS = (
    ("таймаут", re.compile(r"timeout|timed out|deadline exceeded|таймаут|время ожидания", re.I)),
    ("отказано", re.compile(r"denied|permission|forbidden|not allowed|reject|отказ|запрещ|отклон", re.I)),
    ("не найдено", re.compile(r"not found|no such|missing|не найден|отсутств", re.I)),
    ("сеть", re.compile(r"network|connection|dns|socket|http|unreachable|сеть|соединени", re.I)),
    ("некорректные данные", re.compile(r"invalid|malformed|bad request|некоррект|неверн", re.I)),
)


def _mask_text(value: str) -> str:
    value = _SECRET_ASSIGNMENT.sub(r"\1[СКРЫТО]", value)
    value = _BEARER.sub("Bearer [СКРЫТО]", value)
    value = _CREDENTIAL.sub("[СКРЫТО]", value)
    return _JWT.sub("[СКРЫТО]", value)


def _error_class(value: Any) -> str:
    text = str(value or "")
    for label, pattern in _ERROR_PATTERNS:
        if pattern.search(text):
            return label
    return "ошибка"


def _value_length(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, str):
        return len(value)
    if isinstance(value, bytes):
        return len(value)
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str))
    except (TypeError, ValueError, RecursionError):
        return len(str(value))


def _bounded_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return None


def _url_host(value: Any) -> str | None:
    try:
        parsed = urlsplit(str(value or "").strip())
        if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
            return None
        return parsed.hostname.casefold()[:253]
    except (TypeError, ValueError):
        return None


def _file_extension(value: Any) -> str | None:
    text = str(value or "").strip().split("?", 1)[0].split("#", 1)[0]
    name = text.replace("\\", "/").rsplit("/", 1)[-1]
    if name.startswith(".") and name.count(".") == 1:
        return None
    extension = PurePosixPath(name).suffix.casefold()
    if not re.fullmatch(r"\.[a-z0-9]{1,16}", extension):
        return None
    return extension


def _safe_tool_fields(
    tool_name: str,
    args: dict[str, Any],
    clean_text: Callable[[Any, int], str],
) -> dict[str, str]:
    fields: dict[str, str] = {}
    if tool_name == "pc_app_open":
        app_name = args.get("application_name")
        if isinstance(app_name, str) and app_name:
            fields["application_name"] = clean_text(app_name, 120)
    for key, value in args.items():
        folded = str(key).casefold().replace("-", "_")
        if folded == "url":
            host = _url_host(value)
            if host:
                fields["url_host"] = host
        elif folded in {"path", "src", "dst", "file", "file_path", "source", "destination"}:
            extension = _file_extension(value)
            if extension:
                fields["file_extension"] = extension
    return fields


class TraceStore:
    """Append privacy-limited JSONL records and prune oldest files at fixed limits."""

    def __init__(
        self,
        directory: Path,
        *,
        max_bytes: int = 2_000_000,
        max_total_bytes: int = 20 * 1024 * 1024,
        max_files: int = 10,
        secret_redactor: Callable[[str], str] | None = None,
    ) -> None:
        self.directory = Path(directory)
        self.max_total_bytes = max(1024, int(max_total_bytes))
        self.max_bytes = min(max(1024, int(max_bytes)), self.max_total_bytes)
        self.max_files = max(1, int(max_files))
        self.secret_redactor = secret_redactor
        self._lock = threading.RLock()

    def start_turn(
        self,
        *,
        chat_id: str,
        task_id: str | None,
        provider: str,
        model: str,
    ) -> "TraceSession":
        return TraceSession(self, chat_id, task_id, provider, model)

    def _clean_text(self, value: Any, limit: int = 1200) -> str:
        text = str(value or "")
        if self.secret_redactor:
            try:
                text = self.secret_redactor(text)
            except Exception:
                return "[СКРЫТО: фильтрация не удалась]"
        return _mask_text(text)[:limit]

    def _sanitize_record(self, record: dict[str, Any]) -> dict[str, Any] | None:
        event = record.get("event")
        if not isinstance(event, str) or event not in {
            "turn_start", "step_start", "step_end", "tool_call", "tool_result",
            "confirmation_requested", "confirmation_decision", "planner_status",
            "provider_fallback", "error", "turn_done", "turn_stopped", "turn_end",
        }:
            return None
        stamp = record.get("ts")
        base: dict[str, Any] = {
            "ts": stamp if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) else 0,
            "trace_id": self._clean_text(record.get("trace_id"), 64),
            "chat_id": self._clean_text(record.get("chat_id"), 64),
            "task_id": self._clean_text(record.get("task_id"), 64),
            "event": event,
        }

        if event == "turn_start":
            return {**base, "provider": self._clean_text(record.get("provider")),
                    "model": self._clean_text(record.get("model"))}
        if event == "step_start":
            return {**base, "step": _bounded_int(record.get("step")) or 0}
        if event == "step_end":
            return {
                **base, "step": _bounded_int(record.get("step")) or 0,
                "duration_ms": _bounded_int(record.get("duration_ms")),
                "stopped": bool(record.get("stopped")),
            }
        if event == "tool_call":
            name = self._clean_text(record.get("name"), 120)
            legacy_args = record.get("args")
            if isinstance(legacy_args, dict):
                arguments = [
                    {"name": self._clean_text(key, 100), "length": _value_length(value)}
                    for key, value in legacy_args.items()
                ]
                safe_fields = _safe_tool_fields(name, legacy_args, self._clean_text)
            else:
                raw_arguments = record.get("arguments")
                arguments = []
                if isinstance(raw_arguments, list):
                    for item in raw_arguments:
                        if not isinstance(item, dict):
                            continue
                        length = _bounded_int(item.get("length"))
                        if length is not None:
                            arguments.append({"name": self._clean_text(item.get("name"), 100), "length": length})
                safe_fields = self._sanitize_safe_fields(record.get("safe_fields"))
            return {
                **base, "name": name, "arguments": arguments, "safe_fields": safe_fields,
                "allowed": bool(record.get("allowed")),
                "requires_confirmation": bool(record.get("requires_confirmation", record.get("confirm"))),
            }
        if event == "tool_result":
            ok = bool(record.get("ok"))
            legacy_text = record.get("text", record.get("summary", ""))
            length = _bounded_int(record.get("text_length"))
            if length is None:
                length = len(str(legacy_text or ""))
            result = {
                **base, "ok": ok,
                "duration_ms": _bounded_int(record.get("duration_ms")),
                "text_length": length,
            }
            if not ok:
                result["error_class"] = _error_class(record.get("error_class") or legacy_text)
            return result
        if event in {"confirmation_requested", "confirmation_decision"}:
            fields: dict[str, Any] = {
                "call_id": self._clean_text(record.get("call_id", record.get("id")), 128),
                "name": self._clean_text(record.get("name"), 120),
            }
            if event == "confirmation_requested":
                fields["kind"] = self._clean_text(record.get("kind"), 40)
            else:
                fields.update({
                    "approved": bool(record.get("approved")),
                    "duration_ms": _bounded_int(record.get("duration_ms")) or 0,
                })
            return {**base, **fields}
        if event == "planner_status":
            status = self._clean_text(record.get("status"), 40)
            fields = {
                "status": status,
                "provider": self._clean_text(record.get("provider")),
                "model": self._clean_text(record.get("model")),
            }
            if status.casefold() in {"failed", "error"}:
                fields["error_class"] = _error_class(record.get("error_class") or record.get("message"))
            return {**base, **fields}
        if event == "provider_fallback":
            return {
                **base, "provider": self._clean_text(record.get("provider")),
                "model": self._clean_text(record.get("model")),
                "error_class": _error_class(record.get("error_class") or record.get("reason")),
            }
        if event == "error":
            return {**base, "error_class": _error_class(record.get("error_class") or record.get("message"))}
        if event == "turn_done":
            return {**base, "steps": _bounded_int(record.get("steps")) or 0}
        if event == "turn_stopped":
            return base
        status = record.get("status")
        if not isinstance(status, str) or status not in {"completed", "failed", "stopped", "unknown"}:
            status = "unknown"
        return {
            **base, "status": status,
            "duration_ms": _bounded_int(record.get("duration_ms")) or 0,
        }

    def _sanitize_safe_fields(self, value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        fields: dict[str, str] = {}
        app_name = value.get("application_name")
        if isinstance(app_name, str) and app_name:
            fields["application_name"] = self._clean_text(app_name, 120)
        raw_host = value.get("url_host")
        if isinstance(raw_host, str):
            host = _url_host("https://" + raw_host)
            if host:
                fields["url_host"] = host
        extension = value.get("file_extension")
        if isinstance(extension, str) and re.fullmatch(r"\.[a-z0-9]{1,16}", extension.casefold()):
            fields["file_extension"] = extension.casefold()
        return fields

    def _trace_files(self) -> list[Path]:
        if not self.directory.is_dir() or self.directory.is_symlink():
            return []
        result = []
        for path in self.directory.iterdir():
            if _TRACE_FILE.fullmatch(path.name) and path.is_file() and not path.is_symlink():
                result.append(path)
        return result

    @staticmethod
    def _age(path: Path) -> tuple[int, str]:
        try:
            return path.stat().st_mtime_ns, path.name
        except OSError:
            return 0, path.name

    def _prune_unlocked(self) -> None:
        files = sorted(self._trace_files(), key=self._age)
        total = sum(path.stat().st_size for path in files)
        while files and (len(files) > self.max_files or total > self.max_total_bytes):
            oldest = files.pop(0)
            try:
                size = oldest.stat().st_size
                oldest.unlink()
                total -= size
            except OSError:
                continue

    def _append(self, record: dict[str, Any]) -> None:
        line = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(line) > min(self.max_bytes, self.max_total_bytes):
            raise OSError("trace record exceeds configured storage limit")
        with self._lock:
            if self.directory.is_symlink():
                raise OSError("trace directory must not be a symlink")
            self.directory.mkdir(parents=True, exist_ok=True)
            active = self.directory / "traces.jsonl"
            if active.is_symlink():
                raise OSError("trace file must not be a symlink")
            if active.exists() and active.stat().st_size and active.stat().st_size + len(line) > self.max_bytes:
                indices = [
                    int(match.group(1)) for path in self.directory.iterdir()
                    if (match := re.fullmatch(r"traces\.(\d+)\.jsonl", path.name))
                ]
                archive = self.directory / f"traces.{max(indices, default=0) + 1}.jsonl"
                active.replace(archive)
            with active.open("ab") as stream:
                stream.write(line)
            self._prune_unlocked()

    def read_recent(self, limit: int = 300) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        with self._lock:
            files = sorted(self._trace_files(), key=self._age)
            records: list[dict[str, Any]] = []
            for path in files:
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except (OSError, UnicodeError):
                    continue
                for line in lines:
                    try:
                        record = json.loads(line)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if isinstance(record, dict):
                        safe_record = self._sanitize_record(record)
                        if safe_record is not None:
                            records.append(safe_record)
            return records[-limit:]

    def clear(self) -> int:
        """Delete only trace JSONL files; leave unrelated files untouched."""
        with self._lock:
            if not self.directory.is_dir() or self.directory.is_symlink():
                return 0
            removed = 0
            for path in self.directory.iterdir():
                if not _TRACE_FILE.fullmatch(path.name):
                    continue
                if path.is_symlink() or path.is_file():
                    path.unlink()
                    removed += 1
            return removed


class TraceSession:
    def __init__(
        self,
        store: TraceStore,
        chat_id: str,
        task_id: str | None,
        provider: str,
        model: str,
    ) -> None:
        self.store = store
        self.trace_id = uuid.uuid4().hex
        self.chat_id = chat_id
        self.task_id = task_id
        self.started = time.monotonic()
        self._step_started: dict[int, float] = {}
        self._call_started: dict[str, float] = {}
        self._lock = threading.RLock()
        self._record("turn_start", provider=self._clean_text(provider), model=self._clean_text(model))

    def _record(self, event: str, **fields: Any) -> None:
        record = {
            "ts": time.time(),
            "trace_id": self.trace_id,
            "chat_id": self.chat_id,
            "task_id": self.task_id,
            "event": event,
            **fields,
        }
        self.store._append(record)

    def _clean_text(self, value: Any) -> str:
        return self.store._clean_text(value)

    def _safe_tool_fields(self, tool_name: str, args: dict[str, Any]) -> dict[str, str]:
        return _safe_tool_fields(tool_name, args, self.store._clean_text)

    def record_agent_event(self, event: dict[str, Any]) -> None:
        kind = str(event.get("type") or "")
        now = time.monotonic()
        with self._lock:
            if kind == "step_start":
                step = int(event.get("step") or 0)
                self._step_started[step] = now
                self._record("step_start", step=step)
            elif kind == "step_end":
                step = int(event.get("step") or max(self._step_started, default=0))
                start = self._step_started.pop(step, None)
                self._record(
                    "step_end", step=step,
                    duration_ms=round((now - start) * 1000) if start is not None else None,
                    stopped=bool(event.get("stopped")),
                )
            elif kind == "tool_call":
                call_id = self._clean_text(event.get("id"))[:128]
                name = self._clean_text(event.get("name"))[:120]
                self._call_started[call_id] = now
                args = event.get("args") if isinstance(event.get("args"), dict) else {}
                arguments = [
                    {"name": self._clean_text(key)[:100], "length": _value_length(value)}
                    for key, value in args.items()
                ]
                self._record(
                    "tool_call", name=name, arguments=arguments,
                    safe_fields=self._safe_tool_fields(name, args),
                    allowed=bool(event.get("allowed")),
                    requires_confirmation=bool(event.get("confirm")),
                )
            elif kind == "tool_result":
                call_id = self._clean_text(event.get("id"))[:128]
                start = self._call_started.pop(call_id, None)
                ok = bool(event.get("ok"))
                result_text = str(event.get("text") or "")
                result_fields: dict[str, Any] = {
                    "ok": ok,
                    "duration_ms": round((now - start) * 1000) if start is not None else None,
                    "text_length": len(result_text),
                }
                if not ok:
                    result_fields["error_class"] = _error_class(result_text)
                self._record("tool_result", **result_fields)
            elif kind == "confirm":
                self._record(
                    "confirmation_requested", call_id=self._clean_text(event.get("id"))[:128],
                    name=self._clean_text(event.get("name"))[:120],
                    kind=self._clean_text(event.get("kind"))[:40],
                )
            elif kind == "planner_status":
                status = self._clean_text(event.get("status"))[:40]
                fields: dict[str, Any] = {
                    "status": status,
                    "provider": self._clean_text(event.get("provider")),
                    "model": self._clean_text(event.get("model")),
                }
                if status.casefold() in {"failed", "error"}:
                    fields["error_class"] = _error_class(event.get("message"))
                self._record("planner_status", **fields)
            elif kind == "provider_fallback":
                self._record(
                    "provider_fallback", provider=self._clean_text(event.get("provider")),
                    model=self._clean_text(event.get("model")),
                    error_class=_error_class(event.get("reason")),
                )
            elif kind == "error":
                self._record("error", error_class=_error_class(event.get("message")))
            elif kind == "done":
                self._record("turn_done", steps=int(event.get("steps") or 0))
            elif kind == "stopped":
                self._record("turn_stopped")

    def record_confirmation(self, call_id: str, name: str, decision: Any, duration_ms: int) -> None:
        approved = bool(decision) and (not isinstance(decision, dict) or bool(decision.get("allow", True)))
        with self._lock:
            self._record(
                "confirmation_decision", call_id=self._clean_text(call_id)[:128],
                name=self._clean_text(name)[:120], approved=approved,
                duration_ms=max(0, int(duration_ms)),
            )

    def finish(self, status: str) -> None:
        allowed_status = status if status in {"completed", "failed", "stopped"} else "unknown"
        with self._lock:
            self._record(
                "turn_end", status=allowed_status,
                duration_ms=round((time.monotonic() - self.started) * 1000),
            )
