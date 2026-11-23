import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant import storage
from assistant.agent import Agent
from assistant.tools import Toolbox


class PersistentTaskTests(unittest.TestCase):
    def make_store(self, root):
        data_dir = Path(root) / "data"
        chats_dir = data_dir / "chats"
        with patch.object(storage, "DATA_DIR", data_dir), patch.object(storage, "CHATS_DIR", chats_dir):
            store = storage.Store()
            chat = store.create_chat("Сценарий")
            return store, chat, data_dir, chats_dir

    def test_plan_and_checkpoint_survive_store_restart(self):
        with tempfile.TemporaryDirectory() as root:
            store, chat, data_dir, chats_dir = self.make_store(root)
            task = store.create_task(chat["id"], "Сделай резервную копию проекта", status="running")
            store.update_task(task["id"], plan=["Найти проект", "Создать архив"], checkpoint="Папка проекта определена.", step=1)
            with patch.object(storage, "DATA_DIR", data_dir), patch.object(storage, "CHATS_DIR", chats_dir):
                restarted = storage.Store()
            recovered = restarted.get_task(task["id"])
            self.assertEqual(recovered["status"], "interrupted")
            self.assertEqual([item["title"] for item in recovered["plan"]], ["Найти проект", "Создать архив"])
            self.assertEqual(recovered["checkpoint"], "Процесс остановился вместе с приложением. Можно продолжить из истории чата.")
            self.assertGreaterEqual(len(recovered["checkpoint_history"]), 2)

    def test_invalid_task_status_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            store, chat, _data_dir, _chats_dir = self.make_store(root)
            task = store.create_task(chat["id"], "Тест")
            with self.assertRaisesRegex(ValueError, "статус"):
                store.update_task(task["id"], status="launched")

    def test_memory_search_ranks_relevant_facts_instead_of_returning_all(self):
        with tempfile.TemporaryDirectory() as root:
            store, _chat, _data_dir, _chats_dir = self.make_store(root)
            store.create_memory("Предпочитаю русский интерфейс и короткие ответы")
            store.create_memory("Для голоса нужен молодой женский тембр и быстрый темп")
            store.create_memory("В Minecraft сервере включать localhost режим")
            results = store.search_memory("молодой голос женский тембр")
            self.assertEqual(len(results), 1)
            self.assertIn("голоса", results[0]["text"])

    def test_model_gets_common_tools_directly_without_a_discovery_turn(self):
        with tempfile.TemporaryDirectory() as root:
            data_dir = Path(root) / "data"
            chats_dir = data_dir / "chats"
            with patch.object(storage, "DATA_DIR", data_dir), patch.object(storage, "CHATS_DIR", chats_dir):
                store = storage.Store()
                chat = store.create_chat("Сценарий")
                toolbox = Toolbox(store)
                agent = Agent(store, toolbox, lambda *_args: False)
                seen_tool_sets = []

                def fake_stream(_settings, _messages, tools):
                    names = [item["function"]["name"] for item in tools]
                    seen_tool_sets.append(names)
                    if len(seen_tool_sets) == 1:
                        self.assertIn("notes_list", names)
                        yield {"type": "final", "message": {"role": "assistant", "content": "", "tool_calls": [
                            {"id": "notes", "name": "notes_list", "arguments": {"query": ""}},
                        ]}}
                    else:
                        yield {"type": "final", "message": {"role": "assistant", "content": "Заметок пока нет.", "tool_calls": []}}

                with patch("assistant.agent.llm.stream_chat", side_effect=fake_stream):
                    agent.run(chat["id"], "Покажи мои заметки, пожалуйста", lambda _event: None, threading.Event())

                self.assertEqual(len(seen_tool_sets), 2)
                self.assertIn("tool_search", seen_tool_sets[0])
                self.assertIn("task_plan", seen_tool_sets[0])
                tasks = store.list_tasks(chat_id=chat["id"])
                self.assertEqual(tasks[0]["status"], "completed")

    def test_mcp_tool_call_requires_confirmation_and_uses_configured_server(self):
        with tempfile.TemporaryDirectory() as root:
            data_dir = Path(root) / "data"
            chats_dir = data_dir / "chats"
            config_path = data_dir / "mcp_servers.json"
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text('{"servers": []}', encoding="utf-8")
            with patch.object(storage, "DATA_DIR", data_dir), patch.object(storage, "CHATS_DIR", chats_dir):
                store = storage.Store()
                chat = store.create_chat("MCP")
                confirm_calls = []
                agent = Agent(store, Toolbox(store), lambda *args: confirm_calls.append(args) or True)
                external = {
                    "function_name": "mcp__notes__find",
                    "server_name": "notes-server",
                    "tool_name": "find_note",
                    "description": "Find a note by its title.",
                    "parameters": {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
                }
                tool_sets = []

                def fake_stream(_settings, _messages, tools):
                    names = [item["function"]["name"] for item in tools]
                    tool_sets.append(names)
                    if len(tool_sets) == 1:
                        yield {"type": "final", "message": {"role": "assistant", "content": "", "tool_calls": [
                            {"id": "search", "name": "tool_search", "arguments": {"names": [external["function_name"]]}},
                        ]}}
                    elif len(tool_sets) == 2:
                        yield {"type": "final", "message": {"role": "assistant", "content": "", "tool_calls": [
                            {"id": "mcp", "name": external["function_name"], "arguments": {"title": "Кванта"}},
                        ]}}
                    else:
                        yield {"type": "final", "message": {"role": "assistant", "content": "Нашла заметку.", "tool_calls": []}}

                with patch("assistant.agent.mcp_client.CONFIG_PATH", config_path), \
                     patch("assistant.agent.mcp_client.list_tools", return_value=([external], [])), \
                     patch("assistant.agent.mcp_client.call_tool", return_value={"ok": True, "text": "Заметка найдена"}) as call_tool, \
                     patch("assistant.agent.llm.stream_chat", side_effect=fake_stream):
                    agent.run(chat["id"], "Найди заметку Кванта", lambda _event: None, threading.Event())

                self.assertIn(external["function_name"], tool_sets[1])
                self.assertEqual(len(confirm_calls), 1)
                call_tool.assert_called_once_with("notes-server", "find_note", {"title": "Кванта"})
                task = store.list_tasks(chat_id=chat["id"])[0]
                self.assertEqual(task["status"], "completed")


if __name__ == "__main__":
    unittest.main()
