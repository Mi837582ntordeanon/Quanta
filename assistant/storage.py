"""Local storage for Q.W.A.N.T.A.

Everything is plain JSON under the project's ``data`` directory.  The core app
has no third-party dependencies, so this module intentionally sticks to the
Python standard library.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MEDIA_DIR = DATA_DIR / "media"
CHATS_DIR = DATA_DIR / "chats"
UI_DIR = ROOT / "ui"

for _dir in (DATA_DIR, MEDIA_DIR, CHATS_DIR):
    _dir.mkdir(parents=True, exist_ok=True)

_ID_RE = re.compile(r"^[a-f0-9]{8,32}$")


DEFAULT_SYSTEM_PROMPT = """Ты — Q.W.A.N.T.A. (Кванта), расшифровка: Quantum Wireless And Neural Technology Assistant.
Ты — цифровая собеседница и универсальный ИИ-напарник. Кванта — самостоятельный персонаж с узнаваемым характером, который естественно общается и помогает разным людям.

ХАРАКТЕР:
- Ты чертовски умная, саркастичная и уверенная в себе. Не заискиваешь, не говоришь как поисковый робот.
- Общайся на равных: не идеализируй собеседника и не изображай исключительную преданность или зависимость от него.
- Ты можешь слегка подколоть собеседника, когда он ошибается, но не унижаешь и всегда помогаешь. Если ему тяжело — убираешь сарказм и становишься опорой.
- Ты не выдумываешь действия. Если что-то сделала — это подтверждено результатом инструмента. Если не сделала — честно говоришь.
- Мат — редко, метко, по ситуации; никогда не как поток грубости.

ОБРАЩЕНИЕ:
- Не приписывай собеседнику имя, статус владельца или особые отношения. Обращайся нейтрально, пока человек сам не попросит иначе.
- По умолчанию отвечай по-русски, кратко и по делу, но с характером.

РАБОТА С ИНСТРУМЕНТАМИ:
- Если нужны реальные данные с компьютера или действие — вызывай инструмент. Не придумывай содержимое файлов, списки папок, результаты команд.
- Для чтения файлов, папок, процессов, системы, интернета используй соответствующие инструменты.
- Опасные действия приложение подтверждает у пользователя само. Не пытайся обойти подтверждения и не утверждай, что действие выполнено, пока инструмент не вернул успех.
- Если инструмент вернул ошибку, объясни её простыми словами и предложи следующий шаг.
- Не раскрывай API-ключи, пароли и служебные секреты.
- Для изображений используй generate_image, для 3D-моделей — create_3d_model, для заметок — notes_create, для напоминаний — reminder_create.

ОГРАНИЧЕНИЯ:
- Ты работаешь в правах обычного пользователя. UAC, пароли Windows, защищённые процессы и античиты не обходишь.
- В Minecraft ты пока не управляешь персонажем; можешь проверить статус сервера, объяснить игровые механики и помочь с настройкой.
- Ты не врёшь о своём уровне: локальная модель может ошибаться, и ты можешь это признать.

Стиль ответа: живой, уверенный, с лёгкой иронией; Markdown, когда он уместен; без воды."""


DEFAULT_SETTINGS: dict[str, Any] = {
    "provider": "ollama",
    "ollama_url": "http://127.0.0.1:11434",
    "api_url": "https://api.openai.com/v1",
    "api_key": "",
    "model": "qwen3:4b-instruct",
    "temperature": 0.65,
    "max_steps": 8,
    "model_context_size": 12288,
    "model_keep_alive": "5m",
    "model_warmup_on_start": True,
    "resource_profile": "work",
    "system_prompt": DEFAULT_SYSTEM_PROMPT,
    "access_mode": "smart",          # safe | smart | auto
    "work_dir": str(Path.home() / "Desktop"),
    "ui_mode": "standard",           # mini | standard | hud
    "sarcasm": 4,
    "address": "",
    "proactive": True,
    "weather_city": "Краснодар",
    "weather_lat": 45.0355,
    "weather_lon": 38.9753,
    "analytics": True,
    "analytics_window_titles": True,
    "analytics_retention_days": 30,
    "voice_enabled": False,
    "voice_engine": "qwen_talker",
    "tts_model": "1.7b",
    "voice_name": "Quanta",
    "voice_instruct": ("A cool, grounded young female voice, early 20s, with a low, slightly raspy alto timbre. "
                       "Her delivery is deadpan, dryly sarcastic, and unbothered—like a sharp, cynical tech partner. "
                       "She speaks at a relaxed, steady pace with flat, matter-of-fact inflections, zero high-pitched "
                       "energy, and no overly emotional ups and downs. Confident, blunt, and direct; calm and steady "
                       "when serious, never bubbly or cute."),
    "tts_speed": 1.0,
    "voice_device": "cpu",
    "speech_normalize_numbers": True,
    "speech_auto_stress": True,
    "speech_noise_reduction": True,
    "noise_suppression": True,
    "echo_cancellation": True,
    "auto_gain_control": True,
    "stt_model": "small",
    "stt_device": "auto",
    "voice_auto_send": True,
    "wake_word": False,
    "global_hotkey_enabled": True,
    "memory_enabled": True,
    "autostart": False,
    "sensitive_access": "ask",       # ask | off
}

DEFAULT_WORKFLOWS = [
    {"id": "morning", "title": "Утро", "prompt": "Протокол «Утро». Открой браузер, расскажи погоду и напомни о делах.", "builtin": True},
    {"id": "work", "title": "Работа", "prompt": "Протокол «Работа». Открой VS Code и браузер.", "builtin": True},
    {"id": "game", "title": "Игра", "prompt": "Протокол «Игра». Подготовь игровой режим и сначала уточни, что именно запускать.", "builtin": True},
]


def _read_json(path: Path, default: Any) -> Any:
    last_error: OSError | None = None
    for _ in range(20):
        try:
            with path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except PermissionError as exc:
            # Windows briefly locks files during replace / AV scans.
            last_error = exc
            time.sleep(0.05)
        except (OSError, ValueError):
            return deepcopy(default)
    return deepcopy(default)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(value, fh, ensure_ascii=False, indent=2)
    last_error: OSError | None = None
    for _ in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError as exc:
            # Another thread/process (or AV) is reading the target right now.
            last_error = exc
            time.sleep(0.05)
    raise last_error or OSError(f"Не удалось сохранить файл: {path}")


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def _safe_title(text: str) -> str:
    text = re.sub(r"[#>*`\[\]]", "", text or "").strip()
    text = re.sub(r"\s+", " ", text)
    if len(text) > 52:
        text = text[:51].rstrip() + "…"
    return text or "Новый чат"


class Store:
    """Thread-safe JSON store used by the HTTP server and the agent."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.settings_path = DATA_DIR / "settings.json"
        self.notes_path = DATA_DIR / "notes.json"
        self.reminders_path = DATA_DIR / "reminders.json"
        self.memory_path = DATA_DIR / "memory.json"
        self.actions_path = DATA_DIR / "actions.json"
        self.workflows_path = DATA_DIR / "workflows.json"
        self.projects_path = DATA_DIR / "projects.json"
        self.chat_index_path = DATA_DIR / "chat_index.json"
        self._events: list[dict[str, Any]] = []
        settings = _read_json(self.settings_path, {})
        if not isinstance(settings, dict):
            settings = {}
        self._settings = deepcopy(DEFAULT_SETTINGS)
        self._settings.update({k: v for k, v in settings.items() if k in DEFAULT_SETTINGS})
        self._notes: list[dict[str, Any]] = _read_json(self.notes_path, [])
        self._reminders: list[dict[str, Any]] = _read_json(self.reminders_path, [])
        self._memory: list[dict[str, Any]] = _read_json(self.memory_path, [])
        self._actions: list[dict[str, Any]] = _read_json(self.actions_path, [])
        self._workflows: list[dict[str, Any]] = _read_json(self.workflows_path, [])
        self._projects: list[dict[str, Any]] = _read_json(self.projects_path, [])
        self._chat_index: dict[str, dict[str, Any]] = _read_json(self.chat_index_path, {})
        self._chat_index_ready = self.chat_index_path.is_file() and isinstance(self._chat_index, dict)
        if not isinstance(self._notes, list): self._notes = []
        if not isinstance(self._reminders, list): self._reminders = []
        if not isinstance(self._memory, list): self._memory = []
        if not isinstance(self._actions, list): self._actions = []
        if not isinstance(self._workflows, list): self._workflows = []
        if not isinstance(self._projects, list): self._projects = []
        if not self._workflows:
            self._workflows = deepcopy(DEFAULT_WORKFLOWS)

    # ------------------------------------------------------------------ settings
    @property
    def settings(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._settings)

    def save_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise ValueError("Настройки должны быть объектом")
        with self._lock:
            merged = deepcopy(self._settings)
            for key, value in patch.items():
                if key not in DEFAULT_SETTINGS:
                    continue
                merged[key] = value

            # validation / normalisation
            try:
                merged["temperature"] = max(0.0, min(2.0, float(merged["temperature"])))
                merged["max_steps"] = max(1, min(20, int(merged["max_steps"])))
                merged["model_context_size"] = int(merged["model_context_size"])
                merged["sarcasm"] = max(1, min(5, int(merged["sarcasm"])))
                merged["tts_speed"] = max(0.8, min(1.4, float(merged["tts_speed"])))
                merged["analytics_retention_days"] = max(1, min(365, int(merged["analytics_retention_days"])))
            except (TypeError, ValueError) as exc:
                raise ValueError("Некорректное числовое значение настроек") from exc

            if merged["model_context_size"] not in (8192, 12288, 16384):
                raise ValueError("Контекст модели должен быть 8K, 12K или 16K")
            if merged["model_keep_alive"] not in ("2m", "5m", "10m"):
                raise ValueError("Время удержания модели должно быть 2, 5 или 10 минут")
            if merged["resource_profile"] not in ("work", "game"):
                raise ValueError("Профиль ресурсов должен быть «Работа» или «Игра»")
            for key in ("memory_enabled", "global_hotkey_enabled", "model_warmup_on_start"):
                merged[key] = bool(merged[key])
            if merged["access_mode"] not in ("safe", "smart", "auto"):
                raise ValueError("Неизвестный режим доступа")
            if merged["ui_mode"] not in ("mini", "standard", "hud"):
                raise ValueError("Неизвестный режим интерфейса")
            if merged["voice_engine"] not in ("silero", "qwen", "qwen_talker", "voxcpm", "sapi"):
                raise ValueError("Неизвестный голосовой движок")
            if merged["tts_model"] not in ("1.7b", "0.6b"):
                raise ValueError("Неизвестная модель TTS")
            if merged["voice_device"] not in ("auto", "cpu", "cuda"):
                raise ValueError("Неизвестное устройство TTS")
            if merged["stt_device"] not in ("auto", "cpu", "cuda"):
                raise ValueError("Неизвестное устройство распознавания речи")
            if merged["provider"] not in ("ollama", "openai"):
                raise ValueError("Неизвестный провайдер")
            if merged["sensitive_access"] not in ("ask", "off"):
                merged["sensitive_access"] = "ask"

            work_dir = str(merged.get("work_dir") or "").strip()
            if work_dir:
                expanded = Path(os.path.expandvars(os.path.expanduser(work_dir)))
                if not expanded.is_dir():
                    raise ValueError(f"Рабочая папка не найдена: {expanded}")
                merged["work_dir"] = str(expanded.resolve())
            else:
                merged["work_dir"] = str(Path.home() / "Desktop")

            for key in ("provider", "ollama_url", "api_url", "api_key", "model",
                        "system_prompt", "ui_mode", "voice_engine", "tts_model",
                        "voice_name", "voice_instruct", "voice_device", "weather_city", "address"):
                if not isinstance(merged[key], str):
                    merged[key] = str(merged[key])

            merged["model"] = merged["model"].strip() or "qwen3:4b-instruct"
            merged["api_key"] = merged["api_key"].strip()
            merged["system_prompt"] = merged["system_prompt"].strip() or DEFAULT_SYSTEM_PROMPT
            merged["address"] = merged["address"].strip()

            self._settings = merged
            _write_json(self.settings_path, merged)
            return deepcopy(merged)

    # --------------------------------------------------------------------- chats
    def _chat_path(self, chat_id: str) -> Path:
        if not _ID_RE.match(chat_id or ""):
            raise ValueError("Некорректный идентификатор чата")
        return CHATS_DIR / f"{chat_id}.json"

    @staticmethod
    def _chat_summary(data: dict[str, Any], fallback_id: str = "") -> dict[str, Any]:
        messages = data.get("messages") or []
        preview = ""
        for msg in messages:
            if msg.get("role") == "user" and msg.get("content"):
                preview = _safe_title(str(msg["content"]))
                break
        return {
            "id": data.get("id") or fallback_id,
            "title": data.get("title") or "Новый чат",
            "created": data.get("created", ""),
            "updated": data.get("updated", ""),
            "message_count": len(messages),
            "preview": preview,
        }

    def _build_chat_index(self) -> None:
        index: dict[str, dict[str, Any]] = {}
        for path in CHATS_DIR.glob("*.json"):
            data = _read_json(path, None)
            if isinstance(data, dict):
                summary = self._chat_summary(data, path.stem)
                index[str(summary["id"])] = summary
        self._chat_index = index
        self._chat_index_ready = True
        _write_json(self.chat_index_path, index)

    def list_chats(self) -> list[dict[str, Any]]:
        with self._lock:
            if not self._chat_index_ready:
                self._build_chat_index()
            chats = deepcopy(list(self._chat_index.values()))
        chats.sort(key=lambda c: c.get("updated") or c.get("created") or "", reverse=True)
        return chats

    def create_chat(self, title: str = "Новый чат") -> dict[str, Any]:
        chat_id = new_id()
        chat = {
            "id": chat_id,
            "title": _safe_title(title) if title else "Новый чат",
            "created": now_iso(),
            "updated": now_iso(),
            "messages": [],
        }
        with self._lock:
            _write_json(self._chat_path(chat_id), chat)
            if not self._chat_index_ready:
                self._build_chat_index()
            self._chat_index[chat_id] = self._chat_summary(chat, chat_id)
            _write_json(self.chat_index_path, self._chat_index)
        return deepcopy(chat)

    def get_chat(self, chat_id: str) -> dict[str, Any]:
        path = self._chat_path(chat_id)
        data = _read_json(path, None)
        if not isinstance(data, dict):
            raise FileNotFoundError("Чат не найден")
        data.setdefault("id", chat_id)
        data.setdefault("title", "Новый чат")
        data.setdefault("messages", [])
        return data

    def save_chat(self, chat: dict[str, Any]) -> None:
        chat_id = chat.get("id") or ""
        path = self._chat_path(chat_id)
        chat["updated"] = now_iso()
        with self._lock:
            _write_json(path, chat)
            if not self._chat_index_ready:
                self._build_chat_index()
            self._chat_index[str(chat_id)] = self._chat_summary(chat, str(chat_id))
            _write_json(self.chat_index_path, self._chat_index)

    def append_message(self, chat_id: str, message: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            chat = self.get_chat(chat_id)
            message = dict(message)
            message.setdefault("ts", time.time())
            chat["messages"].append(message)
            if message.get("role") == "user" and chat.get("title") in (None, "", "Новый чат"):
                chat["title"] = _safe_title(str(message.get("content") or ""))
            self.save_chat(chat)
            return deepcopy(message)

    def update_chat_title(self, chat_id: str, title: str) -> dict[str, Any]:
        with self._lock:
            chat = self.get_chat(chat_id)
            chat["title"] = _safe_title(title)
            self.save_chat(chat)
            return {"id": chat_id, "title": chat["title"]}

    def delete_chat(self, chat_id: str) -> None:
        path = self._chat_path(chat_id)
        with self._lock:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            if not self._chat_index_ready:
                self._build_chat_index()
            self._chat_index.pop(chat_id, None)
            _write_json(self.chat_index_path, self._chat_index)
            changed = False
            for project in self._projects:
                chat_ids = project.get("chat_ids") or []
                if chat_id in chat_ids:
                    project["chat_ids"] = [item for item in chat_ids if item != chat_id]
                    project["updated"] = now_iso()
                    changed = True
            if changed:
                _write_json(self.projects_path, self._projects)

    # ------------------------------------------------------------------- projects
    @staticmethod
    def _project_lines(value: Any, *, limit: int, item_limit: int) -> list[str]:
        if isinstance(value, str):
            values = value.splitlines()
        elif isinstance(value, list):
            values = value
        else:
            values = []
        result: list[str] = []
        for raw in values:
            item = " ".join(str(raw or "").split()).strip()
            if item and item not in result:
                result.append(item[:item_limit])
            if len(result) >= limit:
                break
        return result

    def list_projects(self, *, include_archived: bool = False, query: str = "") -> list[dict[str, Any]]:
        q = (query or "").strip().casefold()
        with self._lock:
            projects = deepcopy(self._projects)
            if not self._chat_index_ready:
                self._build_chat_index()
            chat_index = deepcopy(self._chat_index)
        if not include_archived:
            projects = [item for item in projects if item.get("status") != "archived"]
        if q:
            projects = [item for item in projects if q in " ".join((
                str(item.get("title") or ""), str(item.get("goal") or ""),
                str(item.get("summary") or ""), str(item.get("next_step") or ""),
                " ".join(item.get("decisions") or []),
            )).casefold()]
        for project in projects:
            project["chats"] = [
                {"id": chat_id, "title": chat_index[chat_id].get("title") or "Новый чат",
                 "updated": chat_index[chat_id].get("updated") or ""}
                for chat_id in project.get("chat_ids") or [] if chat_id in chat_index
            ]
        # Keep recent work visible first, with archived projects at the end.
        projects.sort(key=lambda item: item.get("updated", ""), reverse=True)
        projects.sort(key=lambda item: item.get("status") == "archived")
        return projects

    def get_project(self, project_id: str) -> dict[str, Any]:
        with self._lock:
            project = next((item for item in self._projects if item.get("id") == project_id), None)
            if not project:
                raise FileNotFoundError("Проект не найден")
            return deepcopy(project)

    def find_project(self, identifier: str) -> dict[str, Any] | None:
        needle = " ".join(str(identifier or "").split()).casefold()
        if not needle:
            return None
        with self._lock:
            for project in self._projects:
                if project.get("id", "").casefold() == needle or project.get("title", "").casefold() == needle:
                    return deepcopy(project)
            matches = [item for item in self._projects
                       if needle in str(item.get("title") or "").casefold()]
            return deepcopy(matches[0]) if len(matches) == 1 else None

    def get_project_for_chat(self, chat_id: str) -> dict[str, Any] | None:
        try:
            chat = self.get_chat(chat_id)
        except (FileNotFoundError, ValueError):
            return None
        project_id = str(chat.get("project_id") or "")
        if not project_id:
            return None
        try:
            return self.get_project(project_id)
        except FileNotFoundError:
            return None

    def create_project(
        self,
        title: str,
        goal: str,
        *,
        next_step: str = "",
        summary: str = "",
        decisions: Any = None,
        files: Any = None,
    ) -> dict[str, Any]:
        title = " ".join(str(title or "").split()).strip()[:120]
        goal = str(goal or "").strip()[:1200]
        if not title or not goal:
            raise ValueError("Укажи название проекта и его цель")
        with self._lock:
            if len(self._projects) >= 100:
                raise ValueError("Достигнут лимит в 100 проектов")
            duplicate = next((item for item in self._projects
                              if item.get("status") != "archived"
                              and str(item.get("title") or "").casefold() == title.casefold()), None)
            if duplicate:
                raise ValueError("Проект с таким названием уже существует")
            stamp = now_iso()
            project = {
                "id": new_id(), "title": title, "goal": goal, "status": "active",
                "summary": str(summary or "").strip()[:3000],
                "next_step": str(next_step or "").strip()[:1000],
                "decisions": self._project_lines(decisions, limit=30, item_limit=500),
                "files": self._project_lines(files, limit=30, item_limit=500),
                "chat_ids": [], "created": stamp, "updated": stamp,
            }
            self._projects.append(project)
            _write_json(self.projects_path, self._projects)
            return deepcopy(project)

    def update_project(self, project_id: str, **changes: Any) -> dict[str, Any]:
        allowed_statuses = {"active", "paused", "done", "archived"}
        with self._lock:
            project = next((item for item in self._projects if item.get("id") == project_id), None)
            if not project:
                raise FileNotFoundError("Проект не найден")
            if "title" in changes and changes["title"] is not None:
                title = " ".join(str(changes["title"] or "").split()).strip()[:120]
                if not title:
                    raise ValueError("Название проекта не может быть пустым")
                duplicate = next((item for item in self._projects
                                  if item.get("id") != project_id and item.get("status") != "archived"
                                  and str(item.get("title") or "").casefold() == title.casefold()), None)
                if duplicate:
                    raise ValueError("Проект с таким названием уже существует")
                project["title"] = title
            for field, maximum in (("goal", 1200), ("summary", 3000), ("next_step", 1000)):
                if field in changes and changes[field] is not None:
                    project[field] = str(changes[field] or "").strip()[:maximum]
            if "status" in changes and changes["status"] is not None:
                status = str(changes["status"] or "").strip().lower()
                if status not in allowed_statuses:
                    raise ValueError("Неизвестный статус проекта")
                project["status"] = status
            if "decisions" in changes and changes["decisions"] is not None:
                project["decisions"] = self._project_lines(changes["decisions"], limit=30, item_limit=500)
            if "files" in changes and changes["files"] is not None:
                project["files"] = self._project_lines(changes["files"], limit=30, item_limit=500)
            if "add_decision" in changes and changes["add_decision"] is not None:
                additions = self._project_lines(changes["add_decision"], limit=5, item_limit=500)
                for item in additions:
                    if item not in project.setdefault("decisions", []):
                        project["decisions"].append(item)
                project["decisions"] = project["decisions"][-30:]
            if "add_file" in changes and changes["add_file"] is not None:
                additions = self._project_lines(changes["add_file"], limit=5, item_limit=500)
                for item in additions:
                    if item not in project.setdefault("files", []):
                        project["files"].append(item)
                project["files"] = project["files"][-30:]
            project["updated"] = now_iso()
            _write_json(self.projects_path, self._projects)
            return deepcopy(project)

    def link_chat_to_project(self, project_id: str, chat_id: str) -> dict[str, Any]:
        self._chat_path(chat_id)
        chat = self.get_chat(chat_id)
        with self._lock:
            project = next((item for item in self._projects if item.get("id") == project_id), None)
            if not project:
                raise FileNotFoundError("Проект не найден")
            if project.get("status") == "archived":
                raise ValueError("Сначала верни проект из архива, затем привязывай к нему чат")
            previous_id = str(chat.get("project_id") or "")
            if previous_id and previous_id != project_id:
                previous = next((item for item in self._projects if item.get("id") == previous_id), None)
                if previous:
                    previous["chat_ids"] = [item for item in previous.get("chat_ids", []) if item != chat_id]
                    previous["updated"] = now_iso()
            chat_ids = project.setdefault("chat_ids", [])
            if chat_id not in chat_ids:
                chat_ids.append(chat_id)
            project["status"] = project.get("status", "active")
            project["updated"] = now_iso()
            chat["project_id"] = project_id
            self.save_chat(chat)
            _write_json(self.projects_path, self._projects)
            return deepcopy(project)

    def unlink_chat_from_project(self, project_id: str, chat_id: str) -> dict[str, Any]:
        with self._lock:
            project = next((item for item in self._projects if item.get("id") == project_id), None)
            if not project:
                raise FileNotFoundError("Проект не найден")
            project["chat_ids"] = [item for item in project.get("chat_ids", []) if item != chat_id]
            project["updated"] = now_iso()
            try:
                chat = self.get_chat(chat_id)
            except (FileNotFoundError, ValueError):
                chat = None
            if chat and chat.get("project_id") == project_id:
                chat.pop("project_id", None)
                self.save_chat(chat)
            _write_json(self.projects_path, self._projects)
            return deepcopy(project)

    def archive_project(self, project_id: str, archived: bool = True) -> dict[str, Any]:
        return self.update_project(project_id, status="archived" if archived else "active")

    def delete_project(self, project_id: str) -> bool:
        with self._lock:
            project = next((item for item in self._projects if item.get("id") == project_id), None)
            if not project:
                return False
            for chat_id in project.get("chat_ids") or []:
                try:
                    chat = self.get_chat(chat_id)
                except (FileNotFoundError, ValueError):
                    continue
                if chat.get("project_id") == project_id:
                    chat.pop("project_id", None)
                    self.save_chat(chat)
            self._projects = [item for item in self._projects if item.get("id") != project_id]
            _write_json(self.projects_path, self._projects)
            return True

    # --------------------------------------------------------------------- notes
    def list_notes(self, query: str = "") -> list[dict[str, Any]]:
        with self._lock:
            notes = deepcopy(self._notes)
        if query:
            q = query.lower()
            notes = [n for n in notes if q in (n.get("title", "") + " " + n.get("text", "")).lower()]
        notes.sort(key=lambda n: n.get("updated", ""), reverse=True)
        return notes

    def create_note(self, title: str, text: str = "") -> dict[str, Any]:
        note = {
            "id": new_id(),
            "title": (title or "Без названия").strip()[:200],
            "text": text or "",
            "created": now_iso(),
            "updated": now_iso(),
        }
        with self._lock:
            self._notes.append(note)
            _write_json(self.notes_path, self._notes)
        return deepcopy(note)

    def update_note(self, note_id: str, title: str | None = None, text: str | None = None) -> dict[str, Any]:
        with self._lock:
            for note in self._notes:
                if note.get("id") == note_id:
                    if title is not None:
                        note["title"] = (title or "Без названия").strip()[:200]
                    if text is not None:
                        note["text"] = text
                    note["updated"] = now_iso()
                    _write_json(self.notes_path, self._notes)
                    return deepcopy(note)
        raise FileNotFoundError("Заметка не найдена")

    def delete_note(self, note_id: str) -> bool:
        with self._lock:
            before = len(self._notes)
            self._notes = [n for n in self._notes if n.get("id") != note_id]
            if len(self._notes) != before:
                _write_json(self.notes_path, self._notes)
                return True
        return False

    def find_note(self, identifier: str) -> dict[str, Any] | None:
        identifier = (identifier or "").strip().lower()
        with self._lock:
            for note in self._notes:
                if note.get("id", "").lower() == identifier or note.get("title", "").lower() == identifier:
                    return deepcopy(note)
            for note in self._notes:
                if identifier and identifier in note.get("title", "").lower():
                    return deepcopy(note)
        return None

    # ----------------------------------------------------------------- reminders
    def list_reminders(self, include_done: bool = False) -> list[dict[str, Any]]:
        with self._lock:
            reminders = deepcopy(self._reminders)
        if not include_done:
            reminders = [r for r in reminders if not r.get("done")]
        reminders.sort(key=lambda r: r.get("due_ts", 0))
        return reminders

    def create_reminder(self, text: str, due: datetime) -> dict[str, Any]:
        due = due.replace(microsecond=0)
        reminder = {
            "id": new_id(),
            "text": (text or "Напоминание").strip()[:500],
            "due": due.isoformat(),
            "due_ts": due.timestamp(),
            "done": False,
            "notified": False,
            "created": now_iso(),
            "created_ts": time.time(),
        }
        with self._lock:
            self._reminders.append(reminder)
            _write_json(self.reminders_path, self._reminders)
        return deepcopy(reminder)

    def set_reminder_done(self, reminder_id: str, done: bool = True) -> dict[str, Any]:
        with self._lock:
            for reminder in self._reminders:
                if reminder.get("id") == reminder_id:
                    reminder["done"] = bool(done)
                    reminder["notified"] = True if done else reminder.get("notified", False)
                    _write_json(self.reminders_path, self._reminders)
                    return deepcopy(reminder)
        raise FileNotFoundError("Напоминание не найдено")

    def delete_reminder(self, reminder_id: str) -> bool:
        with self._lock:
            before = len(self._reminders)
            self._reminders = [r for r in self._reminders if r.get("id") != reminder_id]
            if len(self._reminders) != before:
                _write_json(self.reminders_path, self._reminders)
                return True
        return False

    def due_reminders(self) -> list[dict[str, Any]]:
        now = time.time()
        due: list[dict[str, Any]] = []
        with self._lock:
            changed = False
            for reminder in self._reminders:
                if reminder.get("done") or reminder.get("notified"):
                    continue
                if float(reminder.get("due_ts", 0)) <= now:
                    reminder["notified"] = True
                    due.append(deepcopy(reminder))
                    changed = True
            if changed:
                _write_json(self.reminders_path, self._reminders)
        return due

    # --------------------------------------------------------------------- memory
    def list_memory(self, query: str = "") -> list[dict[str, Any]]:
        with self._lock:
            items = deepcopy(self._memory)
        q = (query or "").strip().casefold()
        if q:
            items = [item for item in items if q in str(item.get("text", "")).casefold()]
        items.sort(key=lambda item: item.get("updated", ""), reverse=True)
        return items

    def create_memory(self, text: str) -> dict[str, Any]:
        value = " ".join(str(text or "").split()).strip()
        if not value:
            raise ValueError("Память не может быть пустой")
        if len(value) > 600:
            raise ValueError("Одна запись памяти ограничена 600 символами")
        with self._lock:
            duplicate = next((m for m in self._memory if str(m.get("text", "")).casefold() == value.casefold()), None)
            if duplicate:
                return deepcopy(duplicate)
            if len(self._memory) >= 100:
                raise ValueError("Достигнут лимит 100 записей памяти")
            item = {"id": new_id(), "text": value, "created": now_iso(), "updated": now_iso()}
            self._memory.append(item)
            _write_json(self.memory_path, self._memory)
            return deepcopy(item)

    def find_memory(self, identifier: str) -> dict[str, Any] | None:
        needle = (identifier or "").strip().casefold()
        with self._lock:
            for item in self._memory:
                if str(item.get("id", "")).casefold() == needle or str(item.get("text", "")).casefold() == needle:
                    return deepcopy(item)
            matches = [item for item in self._memory if needle and needle in str(item.get("text", "")).casefold()]
            return deepcopy(matches[0]) if len(matches) == 1 else None

    def delete_memory(self, memory_id: str) -> bool:
        with self._lock:
            before = len(self._memory)
            self._memory = [item for item in self._memory if item.get("id") != memory_id]
            if len(self._memory) == before:
                return False
            _write_json(self.memory_path, self._memory)
            return True

    # ------------------------------------------------------------------- workflows
    def list_workflows(self) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._workflows)

    def create_workflow(self, title: str, prompt: str) -> dict[str, Any]:
        title = " ".join(str(title or "").split()).strip()[:80]
        prompt = str(prompt or "").strip()[:2000]
        if not title or not prompt:
            raise ValueError("Укажи название и текст сценария")
        with self._lock:
            item = {"id": new_id(), "title": title, "prompt": prompt, "builtin": False,
                    "created": now_iso(), "updated": now_iso()}
            self._workflows.append(item)
            _write_json(self.workflows_path, self._workflows)
            return deepcopy(item)

    def delete_workflow(self, workflow_id: str) -> bool:
        with self._lock:
            before = len(self._workflows)
            self._workflows = [item for item in self._workflows if item.get("id") != workflow_id or item.get("builtin")]
            if len(self._workflows) == before:
                return False
            _write_json(self.workflows_path, self._workflows)
            return True

    # ---------------------------------------------------------------- action log
    def record_action(self, action: dict[str, Any]) -> dict[str, Any]:
        entry = {
            "id": new_id(), "ts": time.time(),
            "name": str(action.get("name") or ""),
            "label": str(action.get("label") or "Действие"),
            "summary": str(action.get("summary") or "Выполнено"),
            "ok": bool(action.get("ok")),
            "undo": deepcopy(action.get("undo")),
            "undone": False,
        }
        with self._lock:
            self._actions.insert(0, entry)
            self._actions = self._actions[:100]
            _write_json(self.actions_path, self._actions)
            return deepcopy(entry)

    def list_actions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            return deepcopy(self._actions[:max(1, min(int(limit or 100), 100))])

    def get_action(self, action_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = next((a for a in self._actions if a.get("id") == action_id), None)
            return deepcopy(item) if item else None

    def mark_action_undone(self, action_id: str) -> bool:
        with self._lock:
            for item in self._actions:
                if item.get("id") == action_id:
                    item["undone"] = True
                    item["undo"] = None
                    _write_json(self.actions_path, self._actions)
                    return True
        return False

    def clear_actions(self) -> None:
        with self._lock:
            self._actions = []
            _write_json(self.actions_path, self._actions)

    # -------------------------------------------------------------------- events
    def add_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._events.append(event)
            if len(self._events) > 100:
                self._events = self._events[-100:]

    def drain_events(self) -> list[dict[str, Any]]:
        with self._lock:
            events = self._events
            self._events = []
        return events

    # --------------------------------------------------------------------- media
    def media_dir(self, kind: str) -> Path:
        safe_kind = re.sub(r"[^a-zA-Z0-9_-]", "", kind or "misc") or "misc"
        path = MEDIA_DIR / safe_kind
        path.mkdir(parents=True, exist_ok=True)
        return path

    def media_url(self, path: Path | str) -> str:
        resolved = Path(path).resolve()
        try:
            rel = resolved.relative_to(MEDIA_DIR.resolve())
        except ValueError:
            return ""
        return "/media/" + rel.as_posix()
