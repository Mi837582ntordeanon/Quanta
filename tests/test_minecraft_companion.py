import io
import json
import unittest
from unittest.mock import patch

from assistant.minecraft_companion import MinecraftCompanionManager


class FakeServerManager:
    def __init__(self, server):
        self.server = server

    def list_servers(self):
        return [dict(self.server)]


class FakeProcess:
    def __init__(self, *_args, **_kwargs):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO()
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = 0


class NoopThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass


class MinecraftCompanionTests(unittest.TestCase):
    def make_server(self, **overrides):
        server = {
            "id": "abcdef123456", "name": "Local", "port": 25565,
            "status": "running", "offline_mode": True, "eula_accepted": True,
        }
        server.update(overrides)
        return server

    def test_bot_connects_only_to_managed_local_offline_server(self):
        manager = MinecraftCompanionManager(FakeServerManager(self.make_server()), ".")
        process = FakeProcess()
        with patch.object(manager, "_node_path", return_value="node"), \
             patch("assistant.minecraft_companion.socket.create_connection"), \
             patch("assistant.minecraft_companion.subprocess.Popen", return_value=process) as popen, \
             patch("assistant.minecraft_companion.threading.Thread", NoopThread):
            started = manager.start("abcdef123456", "Quanta_Test")
            self.assertEqual(started["status"], "connecting")
            args = popen.call_args.args[0]
            self.assertEqual(args[args.index(str(manager.script)) + 1:args.index(str(manager.script)) + 4],
                             ["127.0.0.1", "25565", "Quanta_Test"])
            manager._record_event("abcdef123456", {"type": "status", "status": "online"})
            manager._record_event("abcdef123456", {"type": "state", "health": 18, "food": 19,
                                                       "position": {"x": 1, "y": 64, "z": 2}})
            self.assertEqual(manager.status("abcdef123456")["health"], 18)
            manager.say("abcdef123456", "Всем привет")
            self.assertEqual(json.loads(process.stdin.getvalue().splitlines()[0])["text"], "Всем привет")
            manager.stop("abcdef123456")
            self.assertEqual(manager.status("abcdef123456")["status"], "stopped")

    def test_bot_rejects_remote_or_online_servers(self):
        manager = MinecraftCompanionManager(FakeServerManager(self.make_server(offline_mode=False)), ".")
        with self.assertRaisesRegex(ValueError, "offline-mode"):
            manager.start("abcdef123456", "Quanta")

    def test_game_chat_cannot_send_server_commands(self):
        manager = MinecraftCompanionManager(FakeServerManager(self.make_server()), ".")
        with self.assertRaisesRegex(ValueError, "Команды сервера"):
            manager.say("abcdef123456", "/op Quanta")


if __name__ == "__main__":
    unittest.main()
