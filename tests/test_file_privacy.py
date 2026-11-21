from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant import storage
from assistant.agent import Agent
from assistant.tools import Toolbox


class SensitiveFileTests(unittest.TestCase):
    def make_store(self, root):
        data_dir = Path(root) / "data"
        chats_dir = data_dir / "chats"
        with patch.object(storage, "DATA_DIR", data_dir), patch.object(storage, "CHATS_DIR", chats_dir):
            store = storage.Store()
            store.save_settings({"work_dir": str(root), "access_mode": "auto"})
            chat = store.create_chat("Private files")
        return store, chat, data_dir, chats_dir

    def test_sensitive_file_detector_covers_secrets_profiles_and_quanta_data(self):
        with tempfile.TemporaryDirectory() as root:
            store, _chat, _data_dir, _chats_dir = self.make_store(root)
            toolbox = Toolbox(store)
            sensitive = (
                ".env", ".env.production", ".ssh/id_ed25519",
                "Browser/Google/Chrome/User Data/Default/Login Data",
                "Browser/Mozilla/Firefox/Profiles/profile/cookies.sqlite",
                "data/settings.json", "data/api_secrets.json", "data/chats/abcdef0123456789.json",
            )
            for value in sensitive:
                with self.subTest(path=value):
                    self.assertIsNotNone(toolbox.sensitive_file_path(value))
            self.assertIsNone(toolbox.sensitive_file_path("notes/ordinary.txt"))

    def test_sensitive_read_in_auto_requires_warning_confirmation_before_read(self):
        with tempfile.TemporaryDirectory() as root:
            store, chat, data_dir, chats_dir = self.make_store(root)
            agent = Agent(store, Toolbox(store), lambda *_args: False)
            replies = [
                [{"id": "discover", "name": "tool_search", "arguments": {"names": ["read_file"]}}],
                [{"id": "read", "name": "read_file", "arguments": {"path": ".env.production"}}],
            ]
            read_calls = []
            confirmation_args = []
            events = []

            def fake_stream(_settings, _messages, _tools):
                calls = replies.pop(0)
                yield {"type": "final", "message": {
                    "role": "assistant", "content": "", "tool_calls": calls,
                }}

            def confirm(_chat_id, _call_id, _name, args, _emit, _stop):
                confirmation_args.append(dict(args))
                return False

            with patch.object(storage, "DATA_DIR", data_dir), \
                 patch.object(storage, "CHATS_DIR", chats_dir), \
                 patch("assistant.agent.mcp_client.CONFIG_PATH", data_dir / "mcp_servers.json"), \
                 patch.object(agent.toolbox, "execute", side_effect=lambda *args: read_calls.append(args)), \
                 patch("assistant.agent.llm.stream_chat", side_effect=fake_stream):
                agent.confirm_fn = confirm
                agent.run(chat["id"], "Прочитай файл настроек", events.append, threading.Event())

            self.assertEqual(read_calls, [])
            self.assertEqual(len(confirmation_args), 1)
            details = confirmation_args[0]["_quanta_security_confirmation"]
            self.assertIn("API-ключи", details["warning"])
            self.assertTrue(details["destinations"][0].endswith("\\.env.production"))
            self.assertTrue(any(event.get("type") == "tool_call" and event.get("name") == "read_file"
                                and event.get("confirm") for event in events))


if __name__ == "__main__":
    unittest.main()
