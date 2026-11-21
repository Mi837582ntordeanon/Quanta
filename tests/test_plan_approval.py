import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant import storage
from assistant.agent import Agent, _approval_allows_tool
from assistant.tools import Toolbox


class PlanApprovalTests(unittest.TestCase):
    def make_store(self, root):
        data_dir = Path(root) / "data"
        chats_dir = data_dir / "chats"
        with patch.object(storage, "DATA_DIR", data_dir), patch.object(storage, "CHATS_DIR", chats_dir):
            store = storage.Store()
            store.save_settings({"work_dir": str(root)})
            chat = store.create_chat("Plan approval")
            return store, chat, data_dir, chats_dir

    def test_approved_write_is_confined_to_selected_folder(self):
        with tempfile.TemporaryDirectory() as root:
            store, chat, _data_dir, _chats_dir = self.make_store(root)
            confirm_calls = []
            approval = {"root": root, "allow_write": True, "commands": []}
            agent = Agent(store, Toolbox(store), lambda *args: confirm_calls.append(args[2]) or approval)
            replies = [
                [{"id": "plan", "name": "task_plan", "arguments": {"steps": ["Подготовить папку", "Записать результат"]}}],
                [{"id": "search", "name": "tool_search", "arguments": {"names": ["write_file"]}}],
                [{"id": "write", "name": "write_file", "arguments": {"path": "backup.txt", "content": "ok"}}],
                [],
            ]
            calls = []

            def fake_stream(_settings, _messages, _tools):
                tool_calls = replies.pop(0)
                yield {"type": "final", "message": {"role": "assistant", "content": "Готово." if not tool_calls else "", "tool_calls": tool_calls}}

            toolbox = agent.toolbox
            with patch.object(storage, "DATA_DIR", Path(root) / "data"), \
                 patch.object(storage, "CHATS_DIR", Path(root) / "data" / "chats"), \
                 patch.object(toolbox, "execute", return_value={"ok": True, "text": "Файл записан"}) as execute, \
                 patch("assistant.agent.llm.stream_chat", side_effect=fake_stream):
                agent.run(chat["id"], "Создай файл", lambda event: calls.append(event), threading.Event())
                task = store.list_tasks(chat_id=chat["id"])[0]
                self.assertEqual(task["approval"]["status"], "approved")
                self.assertTrue(Path(task["approval"]["root"]).is_relative_to(Path(root).resolve()),
                                f"approval={task['approval']!r}, expected root={root!r}")
                self.assertEqual(confirm_calls, ["task_plan"])
                execute.assert_called_once_with("write_file", {"path": "backup.txt", "content": "ok"})

    def test_denied_plan_blocks_other_tools_in_the_same_model_response(self):
        with tempfile.TemporaryDirectory() as root:
            store, chat, _data_dir, _chats_dir = self.make_store(root)
            agent = Agent(store, Toolbox(store), lambda *_args: False)
            emitted = []

            def fake_stream(_settings, _messages, _tools):
                yield {"type": "final", "message": {"role": "assistant", "content": "", "tool_calls": [
                    {"id": "plan", "name": "task_plan", "arguments": {"steps": ["Проверить", "Запустить"]}},
                    {"id": "command", "name": "run_command", "arguments": {"command": "whoami", "cwd": "."}},
                ]}}

            with patch.object(storage, "DATA_DIR", Path(root) / "data"), \
                 patch.object(storage, "CHATS_DIR", Path(root) / "data" / "chats"), \
                 patch.object(agent.toolbox, "execute") as execute, \
                 patch("assistant.agent.llm.stream_chat", side_effect=fake_stream):
                agent.run(chat["id"], "Сделай задачу", emitted.append, threading.Event())
                execute.assert_not_called()
                task = store.list_tasks(chat_id=chat["id"])[0]
                self.assertEqual(task["status"], "paused")
                self.assertEqual(task["approval"]["status"], "denied")
                self.assertTrue(any(event.get("type") == "done" for event in emitted))

    def test_approval_requires_exact_commands_and_rooted_working_directory(self):
        with tempfile.TemporaryDirectory() as root:
            work = Path(root)
            scoped = work / "project"
            scoped.mkdir()
            approval = {"status": "approved", "root": str(scoped), "allow_write": True,
                        "sandbox_commands": True, "commands": ["python -m unittest"]}
            self.assertTrue(_approval_allows_tool("write_file", {"path": "project/output.txt"}, approval, str(work)))
            self.assertFalse(_approval_allows_tool("write_file", {"path": "../outside.txt"}, approval, str(work)))
            with patch("assistant.agent.command_sandbox._sandbox_executable",
                       return_value=work / "WindowsSandbox.exe"):
                self.assertTrue(_approval_allows_tool("run_command", {
                    "command": "python -m unittest", "cwd": str(scoped),
                }, approval, str(work)))
            self.assertFalse(_approval_allows_tool("run_command", {
                "command": "python -m unittest; whoami", "cwd": str(scoped),
            }, approval, str(work)))
            self.assertFalse(_approval_allows_tool("run_command", {
                "command": "python -m unittest", "cwd": str(work),
            }, approval, str(work)))

    def test_exact_plan_command_is_not_auto_approved_without_windows_sandbox(self):
        with tempfile.TemporaryDirectory() as root:
            approval = {"status": "approved", "root": root, "allow_write": False,
                        "sandbox_commands": True, "commands": ["python -m unittest"]}
            with patch("assistant.agent.command_sandbox._sandbox_executable", return_value=None):
                self.assertFalse(_approval_allows_tool("run_command", {
                    "command": "python -m unittest", "cwd": ".",
                }, approval, root))

    def test_denied_high_risk_action_pauses_task_and_skips_remaining_calls(self):
        with tempfile.TemporaryDirectory() as root:
            store, chat, _data_dir, _chats_dir = self.make_store(root)
            store.save_settings({"access_mode": "smart"})
            agent = Agent(store, Toolbox(store), lambda *_args: False)
            events = []
            model_steps = []

            def fake_stream(_settings, _messages, _tools):
                model_steps.append(True)
                if len(model_steps) == 1:
                    calls = [{"id": "discover", "name": "tool_search",
                              "arguments": {"names": ["run_command", "notes_create"]}}]
                else:
                    calls = [
                        {"id": "command", "name": "run_command",
                         "arguments": {"command": "whoami", "cwd": "."}},
                        {"id": "note", "name": "notes_create",
                         "arguments": {"title": "Не создавать", "text": "Отменено"}},
                    ]
                yield {"type": "final", "message": {"role": "assistant", "content": "",
                                                          "tool_calls": calls}}

            with patch.object(storage, "DATA_DIR", Path(root) / "data"), \
                 patch.object(storage, "CHATS_DIR", Path(root) / "data" / "chats"), \
                 patch.object(agent.toolbox, "execute") as execute, \
                 patch("assistant.agent.llm.stream_chat", side_effect=fake_stream):
                agent.run(chat["id"], "Сделай это", events.append, threading.Event())
                execute.assert_not_called()
                self.assertEqual(len(model_steps), 2)
                self.assertEqual(store.list_tasks(chat_id=chat["id"])[0]["status"], "paused")
                tool_results = [item for item in store.get_chat(chat["id"])["messages"]
                                if item.get("role") == "tool"]
                self.assertTrue(any(item.get("tool_call_id") == "note" and not item.get("ok")
                                    for item in tool_results))
                self.assertTrue(any(item.get("type") == "done" for item in events))

    def test_model_pool_and_planner_settings_are_validated(self):
        with tempfile.TemporaryDirectory() as root:
            store, _chat, _data_dir, _chats_dir = self.make_store(root)
            saved = store.save_settings({
                "fallback_enabled": True,
                "planner_enabled": True,
                "fallback_routes": [{"provider": "ollama", "model": "qwen3:8b"}],
            })
            self.assertTrue(saved["planner_enabled"])
            self.assertEqual(saved["fallback_routes"][0]["model"], "qwen3:8b")
            with self.assertRaisesRegex(ValueError, "модель"):
                store.save_settings({"fallback_routes": [{"provider": "openai", "model": ""}]})


if __name__ == "__main__":
    unittest.main()
