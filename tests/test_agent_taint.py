from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import quote_plus
from unittest.mock import patch

from assistant import storage
from assistant.agent import Agent, _NETWORK_TAINT_TOOLS, _READ_TAINT_TOOLS
from assistant.tools import Toolbox


class AgentTaintTests(unittest.TestCase):
    def make_store(self, root, mode="auto"):
        data_dir = Path(root) / "data"
        chats_dir = data_dir / "chats"
        with patch.object(storage, "DATA_DIR", data_dir), patch.object(storage, "CHATS_DIR", chats_dir):
            store = storage.Store()
            store.save_settings({"work_dir": str(root), "access_mode": mode})
            chat = store.create_chat("Taint")
        return store, chat, data_dir, chats_dir

    def test_read_then_network_requires_confirm_in_auto_with_full_url_and_warning(self):
        with tempfile.TemporaryDirectory() as root:
            store, chat, data_dir, chats_dir = self.make_store(root)
            agent = Agent(store, Toolbox(store), lambda *_args: False)
            replies = [
                [{"id": "discover", "name": "tool_search", "arguments": {
                    "names": ["read_file", "web_search"],
                }}],
                [{"id": "read", "name": "read_file", "arguments": {"path": "private.txt"}}],
                [{"id": "search", "name": "web_search", "arguments": {"query": "секретный проект"}}],
            ]
            executed = []
            confirmations = []
            events = []

            def fake_stream(_settings, _messages, _tools):
                calls = replies.pop(0) if replies else []
                yield {"type": "final", "message": {
                    "role": "assistant", "content": "", "tool_calls": calls,
                }}

            def execute(name, args):
                executed.append((name, dict(args)))
                return {"ok": True, "text": "Содержимое локального файла"}

            def confirm(_chat_id, _call_id, name, args, _emit, _stop):
                confirmations.append((name, dict(args)))
                return False

            with patch.object(storage, "DATA_DIR", data_dir), \
                 patch.object(storage, "CHATS_DIR", chats_dir), \
                 patch("assistant.agent.mcp_client.CONFIG_PATH", data_dir / "mcp_servers.json"), \
                 patch.object(agent.toolbox, "execute", side_effect=execute), \
                 patch("assistant.agent.llm.stream_chat", side_effect=fake_stream):
                agent.confirm_fn = confirm
                agent.run(chat["id"], "Прочитай файл и найди упоминания в интернете", events.append, threading.Event())

            self.assertEqual([name for name, _args in executed], ["read_file"])
            self.assertEqual(len(confirmations), 1)
            name, confirm_args = confirmations[0]
            self.assertEqual(name, "web_search")
            metadata = confirm_args["_quanta_security_confirmation"]
            self.assertIn("Чтение файла", metadata["warning"])
            self.assertEqual(
                metadata["destinations"],
                ["https://html.duckduckgo.com/html/?q=" + quote_plus("секретный проект")],
            )
            search_event = next(event for event in events if event.get("type") == "tool_call" and event.get("name") == "web_search")
            self.assertTrue(search_event["confirm"])

    def test_auto_mode_keeps_untainted_web_search_without_confirmation(self):
        with tempfile.TemporaryDirectory() as root:
            store, chat, data_dir, chats_dir = self.make_store(root)
            confirmation_calls = []
            agent = Agent(store, Toolbox(store), lambda *args: confirmation_calls.append(args) or True)
            replies = [
                [{"id": "discover", "name": "tool_search", "arguments": {"names": ["web_search"]}}],
                [{"id": "search", "name": "web_search", "arguments": {"query": "погода"}}],
                [],
            ]
            executed = []

            def fake_stream(_settings, _messages, _tools):
                calls = replies.pop(0)
                yield {"type": "final", "message": {
                    "role": "assistant", "content": "Готово.", "tool_calls": calls,
                }}

            with patch.object(storage, "DATA_DIR", data_dir), \
                 patch.object(storage, "CHATS_DIR", chats_dir), \
                 patch("assistant.agent.mcp_client.CONFIG_PATH", data_dir / "mcp_servers.json"), \
                 patch.object(agent.toolbox, "execute", side_effect=lambda name, args: executed.append(name) or {"ok": True, "text": "Результат"}), \
                 patch("assistant.agent.llm.stream_chat", side_effect=fake_stream):
                agent.run(chat["id"], "Найди погоду", lambda _event: None, threading.Event())

            self.assertEqual(executed, ["web_search"])
            self.assertEqual(confirmation_calls, [])

    def test_taint_catalogue_covers_file_clipboard_screen_and_uia_reads(self):
        self.assertTrue({"read_file", "clipboard_get", "screenshot", "pc_vision", "pc_uia_tree"} <= _READ_TAINT_TOOLS)
        self.assertTrue({"web_search", "fetch_url", "weather", "generate_image"} <= _NETWORK_TAINT_TOOLS)


if __name__ == "__main__":
    unittest.main()
