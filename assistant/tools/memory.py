# ruff: noqa: F403,F405
"""Tool implementations grouped by memory domain."""

from .common import *  # noqa: F403
from .decorators import tool


class MemoryToolsMixin:
    @tool(
        name='memory_save', label='Запись в память',
        risk='low', confirm=False,
        capabilities=('пишет_память',),
        network=False,
        description='Save a short user fact to local persistent memory only when the user explicitly asks to remember it.',
        parameters=schema(text=prop('string', 'One concise fact to remember; do not store passwords, secrets, or sensitive details.', required=True)),
    )
    def _memory_save(self, text: str) -> dict[str, Any]:
        item = self.store.create_memory(text)
        return {"ok": True, "text": "Запись добавлена в локальную память.",
                "_undo": {"kind": "remove_memory", "id": item["id"]}}

    @tool(
        name='memory_list', label='Память',
        risk='read', confirm=False,
        capabilities=('читает_личное',),
        network=False,
        description='List local user memories or search them by a short query.',
        parameters=schema(query=prop('string', 'Optional search query.', default='')),
    )
    def _memory_list(self, query: str = "") -> dict[str, Any]:
        items = self.store.list_memory(query)
        if not items:
            return {"ok": True, "text": "Подходящих записей памяти нет."}
        return {"ok": True, "text": "Локальная память:\n" + "\n".join(f"- {item['id']}: {item['text']}" for item in items[:30])}

    @tool(
        name='memory_delete', label='Удаление из памяти',
        risk='high', confirm=True,
        capabilities=('пишет_память',),
        network=False,
        description='Delete one exact local memory by id or unique exact text. Requires confirmation.',
        parameters=schema(identifier=prop('string', 'Memory id or unique text.', required=True)),
    )
    def _memory_delete(self, identifier: str) -> dict[str, Any]:
        item = self.store.find_memory(identifier)
        if not item:
            return {"ok": False, "text": "Не нашла однозначную запись памяти; уточни текст или id."}
        if not self.store.delete_memory(item["id"]):
            return {"ok": False, "text": "Запись уже отсутствует."}
        return {"ok": True, "text": "Запись удалена из локальной памяти."}

    @staticmethod
    def _project_card_text(project: dict[str, Any]) -> str:
        status = {"active": "в работе", "paused": "на паузе", "done": "завершён", "archived": "архив"}.get(
            str(project.get("status") or "active"), str(project.get("status") or "active")
        )
        lines = [
            f"{project.get('title') or 'Проект'} (id {project.get('id')}, статус: {status})",
            f"Цель: {project.get('goal') or 'не указана'}",
            f"Следующий шаг: {project.get('next_step') or 'не задан'}",
        ]
        if project.get("summary"):
            lines.append(f"Контекст: {str(project['summary'])[:900]}")
        if project.get("decisions"):
            lines.append("Решения: " + "; ".join(str(item) for item in project["decisions"][:8]))
        if project.get("files"):
            lines.append("Файлы: " + "; ".join(str(item) for item in project["files"][:8]))
        return "\n".join(lines)

    @tool(
        name='project_list', label='Список проектов',
        risk='read', confirm=False,
        capabilities=('читает_личное',),
        network=False,
        description='Показывает сохранённые проекты и их цель, статус, решения и следующий шаг.',
        parameters=schema(query=prop('string', 'Необязательная часть названия или текста для поиска.', default=''), include_archived=prop('boolean', 'Включить архивные проекты.', default=False)),
    )
    def _project_list(self, query: str = "", include_archived: bool = False) -> dict[str, Any]:
        projects = self.store.list_projects(include_archived=bool(include_archived), query=query)
        if not projects:
            return {"ok": True, "text": "Сохранённых проектов нет." if not query else f"Проекты по запросу «{query}» не найдены."}
        return {"ok": True, "text": "Проекты:\n\n" + "\n\n".join(self._project_card_text(item) for item in projects[:30])}

    @tool(
        name='project_create', label='Создание проекта',
        risk='low', confirm=False,
        capabilities=('пишет_память',),
        network=False,
        description='Создаёт карточку многосоставного проекта по просьбе пользователя. Текущий чат автоматически привяжется к новому проекту.',
        parameters=schema(title=prop('string', 'Короткое название проекта.', required=True), goal=prop('string', 'Цель проекта.', required=True), next_step=prop('string', 'Подтверждённый ближайший шаг.', default=''), summary=prop('string', 'Краткий текущий контекст.', default=''), decisions=prop('string', 'Подтверждённые решения, по одному на строку.', default=''), files=prop('string', 'Связанные пути к файлам, по одному на строку.', default='')),
    )
    def _project_create(
        self,
        title: str,
        goal: str,
        next_step: str = "",
        summary: str = "",
        decisions: str = "",
        files: str = "",
        _current_chat_id: str = "",
    ) -> dict[str, Any]:
        project = self.store.create_project(
            title, goal, next_step=next_step, summary=summary,
            decisions=decisions, files=files,
        )
        if _current_chat_id:
            self.store.link_chat_to_project(project["id"], _current_chat_id)
            project = self.store.get_project(project["id"])
        return {
            "ok": True,
            "text": "Создала карточку проекта и привязала текущий чат.\n" + self._project_card_text(project),
            "_undo": {"kind": "remove_project", "id": project["id"]},
        }

    @tool(
        name='project_update', label='Обновление проекта',
        risk='low', confirm=False,
        capabilities=('пишет_память',),
        network=False,
        description='Обновляет только переданные пользователем факты проекта. Не выдумывай прогресс; добавляй решения и файлы через add_decision/add_file. Архивирование обратимо.',
        parameters=schema(identifier=prop('string', 'Точное название проекта или его id.', required=True), title=prop('string', 'Новое название.', default=None), goal=prop('string', 'Новая формулировка цели.', default=None), status=prop('string', 'Статус: active, paused, done или archived.', enum=['active', 'paused', 'done', 'archived'], default=None), next_step=prop('string', 'Новый ближайший шаг; пустое значение очищает его.', default=None), summary=prop('string', 'Обновлённый текущий контекст.', default=None), add_decision=prop('string', 'Одно новое подтверждённое решение.', default=None), add_file=prop('string', 'Один связанный путь к файлу.', default=None)),
    )
    def _project_update(
        self,
        identifier: str,
        title: str | None = None,
        goal: str | None = None,
        status: str | None = None,
        next_step: str | None = None,
        summary: str | None = None,
        add_decision: str | None = None,
        add_file: str | None = None,
    ) -> dict[str, Any]:
        project = self.store.find_project(identifier)
        if not project:
            return {"ok": False, "text": "Не нашла однозначный проект. Уточни название или id."}
        changes = {key: value for key, value in {
            "title": title, "goal": goal, "status": status, "next_step": next_step,
            "summary": summary, "add_decision": add_decision, "add_file": add_file,
        }.items() if value is not None}
        updated = self.store.update_project(project["id"], **changes)
        return {"ok": True, "text": "Карточку проекта обновила.\n" + self._project_card_text(updated)}

    @tool(
        name='notes_create', label='Создание заметки',
        risk='low', confirm=False,
        capabilities=('пишет_память',),
        network=False,
        description="Create a note in the user's local notes. Use this when the user says 'запомни', 'запиши', 'создай заметку'.",
        parameters=schema(title=prop('string', 'Note title.', required=True), text=prop('string', 'Note body.', default='')),
    )
    def _notes_create(self, title: str, text: str = "") -> dict[str, Any]:
        note = self.store.create_note(title, text)
        return {"ok": True, "text": f"Заметка создана: «{note['title']}» (id {note['id']})\n\n{note['text']}",
                "_undo": {"kind": "remove_note", "id": note["id"]}}

    @tool(
        name='notes_list', label='Заметки',
        risk='read', confirm=False,
        capabilities=('читает_личное',),
        network=False,
        description="List or search the user's notes.",
        parameters=schema(query=prop('string', 'Optional search query.', default='')),
    )
    def _notes_list(self, query: str = "") -> dict[str, Any]:
        notes = self.store.list_notes(query)
        if not notes:
            return {"ok": True, "text": "Заметок нет." if not query else f"По запросу «{query}» ничего не найдено."}
        lines = [f"Заметки ({len(notes)}):", ""]
        for note in notes:
            preview = re.sub(r"\s+", " ", note.get("text", ""))[:160]
            lines.append(f"• {note['title']} (id {note['id']})\n  {preview}")
        return {"ok": True, "text": "\n".join(lines)}

    @tool(
        name='notes_update', label='Изменение заметки',
        risk='low', confirm=False,
        capabilities=('пишет_память',),
        network=False,
        description='Update an existing note by id or exact title.',
        parameters=schema(identifier=prop('string', 'Note id or title.', required=True), title=prop('string', 'New title.', default=None), text=prop('string', 'New body.', default=None)),
    )
    def _notes_update(self, identifier: str, title: str | None = None, text: str | None = None) -> dict[str, Any]:
        note = self.store.find_note(identifier)
        if not note:
            return {"ok": False, "text": f"Заметка не найдена: {identifier}"}
        updated = self.store.update_note(note["id"], title=title, text=text)
        return {"ok": True, "text": f"Заметка обновлена: «{updated['title']}»"}

    @tool(
        name='notes_delete', label='Удаление заметки',
        risk='high', confirm=True,
        capabilities=('пишет_память',),
        network=False,
        description='Delete a note by id or title. Requires confirmation.',
        parameters=schema(identifier=prop('string', 'Note id or title.', required=True)),
    )
    def _notes_delete(self, identifier: str) -> dict[str, Any]:
        note = self.store.find_note(identifier)
        if not note:
            return {"ok": False, "text": f"Заметка не найдена: {identifier}"}
        self.store.delete_note(note["id"])
        return {"ok": True, "text": f"Заметка удалена: «{note['title']}»"}

    @staticmethod
    def _parse_when(value: str) -> datetime:
        raw = (value or "").strip().lower().replace("ё", "е")
        now = datetime.now()
        match = re.search(r"через\s+(\d+)\s*(минут|мин|час|ч|дн|день|дня|недел|неделю|недели)", raw)
        if match:
            amount = int(match.group(1))
            unit = match.group(2)
            if unit.startswith("мин"):
                return now + timedelta(minutes=amount)
            if unit.startswith("ч"):
                return now + timedelta(hours=amount)
            if unit.startswith("недел"):
                return now + timedelta(weeks=amount)
            return now + timedelta(days=amount)
        if "полчаса" in raw:
            return now + timedelta(minutes=30)
        if re.search(r"через\s+час", raw):
            return now + timedelta(hours=1)
        if re.search(r"через\s+полтора\s+часа", raw):
            return now + timedelta(minutes=90)
        match = re.search(r"(\d{4})-(\d{2})-(\d{2})[ t](\d{1,2}):(\d{2})", raw)
        if match:
            return datetime(*[int(part) for part in match.groups()])
        match = re.search(r"(сегодня|завтра|послезавтра)?\s*(?:в\s*)?(\d{1,2})[:.](\d{2})", raw)
        if match:
            word, hour, minute = match.groups()
            target = now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
            if word == "завтра":
                target += timedelta(days=1)
            elif word == "послезавтра":
                target += timedelta(days=2)
            elif word is None and target <= now:
                target += timedelta(days=1)
            return target
        if "завтра" in raw:
            return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        try:
            return datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(
                "Не понял время. Примеры: «через 30 минут», «завтра в 9:00», "
                "«сегодня в 18:30», «2026-11-12 15:00»."
            ) from exc

    @tool(
        name='reminder_create', label='Создание напоминания',
        risk='low', confirm=False,
        capabilities=('пишет_память',),
        network=False,
        description="Create a local reminder. Accepts human time expressions like 'через 30 минут', 'завтра в 9:00', '2026-11-12 15:00'.",
        parameters=schema(text=prop('string', 'What to remind about.', required=True), when=prop('string', 'When to remind, in Russian or ISO format.', required=True)),
    )
    def _reminder_create(self, text: str, when: str) -> dict[str, Any]:
        due = self._parse_when(when)
        reminder = self.store.create_reminder(text, due)
        return {
            "ok": True,
            "text": f"Напоминание создано: «{reminder['text']}»\nКогда: {due.strftime('%d.%m.%Y %H:%M')}",
            "_undo": {"kind": "remove_reminder", "id": reminder["id"]},
        }

    @tool(
        name='reminder_list', label='Напоминания',
        risk='read', confirm=False,
        capabilities=('читает_личное',),
        network=False,
        description='List active reminders.',
        parameters=schema(),
    )
    def _reminder_list(self) -> dict[str, Any]:
        reminders = self.store.list_reminders()
        if not reminders:
            return {"ok": True, "text": "Активных напоминаний нет."}
        lines = [f"Напоминания ({len(reminders)}):", ""]
        for reminder in reminders:
            try:
                due = datetime.fromisoformat(reminder["due"]).strftime("%d.%m.%Y %H:%M")
            except ValueError:
                due = reminder.get("due", "—")
            lines.append(f"• {due} — {reminder['text']} (id {reminder['id']})")
        return {"ok": True, "text": "\n".join(lines)}

    @tool(
        name='reminder_delete', label='Удаление напоминания',
        risk='low', confirm=False,
        capabilities=('пишет_память',),
        network=False,
        description='Delete a reminder by id.',
        parameters=schema(reminder_id=prop('string', 'Reminder id.', required=True)),
    )
    def _reminder_delete(self, reminder_id: str) -> dict[str, Any]:
        if self.store.delete_reminder(reminder_id):
            return {"ok": True, "text": "Напоминание удалено."}
        return {"ok": False, "text": "Напоминание не найдено."}
