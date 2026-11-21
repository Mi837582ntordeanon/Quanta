import tempfile
import unittest
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from assistant import automation, desktop, storage, minecraft_server
from assistant.minecraft_server import MinecraftServerManager, _safe_host_url
from assistant.tools import Toolbox
from assistant.agent import Agent, _direct_music_action, _music_query_from_text, _tool_search_schema
from assistant.vision import VISION_MODELS, VisionError, VisionService, _base_url, _ollama_supported


class _FakeMusicClient:
    def __init__(self, title="Birdbrain", artists=None):
        self.title = title
        self.artists = list(artists or ["Jamie Paige", "OK Glass"])
        self.href = "music-application://desktop/album/track?albumId=1&trackId=2"
        self.closed = False
        self.query_expression = ""

    def evaluate(self, expression):
        if "Boolean(document.querySelector('input[type=search]'))" in expression:
            return True
        if "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')" in expression:
            self.query_expression = expression
            return self.title
        if "const titleNorm=norm(title)" in expression:
            return {"ok": True, "title": self.title, "artists": self.artists,
                    "href": self.href, "clicked": True}
        if "const a=[...document.querySelectorAll('a[href*=" in expression and "const buttons=" in expression:
            return {"href": self.href, "title": self.title, "play": "Пауза"}
        return True

    def close(self):
        self.closed = True


class _FakeWaveClient:
    def __init__(self):
        self.closed = False
        self.wave_clicked = False
        self.play_clicked = False

    def evaluate(self, expression):
        if "const link =" in expression:
            self.wave_clicked = True
            return {"ok": True}
        if "const lines =" in expression:
            return {"play": "Воспроизведение", "track": "Песня недели"}
        if "button.click()" in expression:
            self.play_clicked = True
            return True
        if "VibePlayerControls_playButton" in expression:
            return "Пауза"
        return True

    def close(self):
        self.closed = True


class DesktopFeatureTests(unittest.TestCase):
    def test_application_search_is_case_and_space_insensitive(self):
        entries = [
            {"name": "Yandex Music", "id": "Yandex.Music!App"},
            {"name": "Yandex Music", "id": "Yandex.Music!App"},
            {"name": "Discord", "id": "Discord.Discord"},
        ]
        with patch.object(desktop, "_start_apps", return_value=entries):
            self.assertEqual(desktop.list_applications(" yandex   MUSIC "), [entries[0]])

    def test_ambiguous_application_does_not_launch(self):
        entries = [
            {"name": "Editor Alpha", "id": "Alpha!App"},
            {"name": "Editor Beta", "id": "Beta!App"},
        ]
        with patch.object(desktop, "_start_apps", return_value=entries), patch.object(desktop.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(ValueError, "несколько приложений"):
                desktop.launch_application("Editor")
            launch.assert_not_called()

    def test_ui_automation_rejects_unsupported_action_before_call(self):
        with patch.object(automation, "call") as bridge:
            with self.assertRaises(automation.AutomationError):
                automation.perform_action(100, [0], "type_password")
            bridge.assert_not_called()

    def test_ui_automation_bounds_path_and_value(self):
        with patch.object(automation, "call", return_value={"ok": True}) as bridge:
            with self.assertRaises(automation.AutomationError):
                automation.perform_action(100, [0] * 9, "focus", expected_type="ControlType.Button")
            with self.assertRaises(automation.AutomationError):
                automation.perform_action(100, [0], "set_value", expected_type="ControlType.Edit", value="x" * 2001)
            bridge.assert_not_called()

    def test_ui_action_requires_a_real_window_handle_and_fresh_identity(self):
        with patch.object(automation, "call") as bridge:
            with self.assertRaises(automation.AutomationError):
                automation.perform_action(0, [0], "invoke", expected_type="ControlType.Button")
            with self.assertRaises(automation.AutomationError):
                automation.perform_action(100, [0], "invoke")
            bridge.assert_not_called()

    def test_yandex_search_plays_and_verifies_exact_match(self):
        client = _FakeMusicClient()
        with patch.object(desktop, "_ensure_music_cdp", return_value=client) as connect:
            result = desktop.music_control("search", "Birdbrain")
        connect.assert_called_once_with()
        self.assertTrue(result["ok"])
        self.assertIn("Birdbrain", result["text"])
        self.assertIn("Jamie Paige", result["text"])
        self.assertTrue(client.closed)

    def test_yandex_search_reports_local_client_failure(self):
        with patch.object(desktop, "_ensure_music_cdp", side_effect=RuntimeError("local CDP unavailable")):
            result = desktop.music_control("search", "Birdbrain")
        self.assertFalse(result["ok"])
        self.assertIn("local CDP unavailable", result["text"])

    def test_yandex_wave_uses_the_app_navigation_and_verifies_playback(self):
        client = _FakeWaveClient()
        with patch.object(desktop, "_ensure_music_cdp", return_value=client):
            result = desktop.music_control("wave")
        self.assertTrue(result["ok"])
        self.assertTrue(client.wave_clicked)
        self.assertTrue(client.play_clicked)
        self.assertTrue(client.closed)
        self.assertIn("Плеер подтверждает", result["text"])

    def test_track_and_artist_are_extracted_without_game_context(self):
        request = "Я в Fortnite услышал трек PPPP от Мику и Тето, включи мне его"
        self.assertEqual(_music_query_from_text(request), "PPPP от Мику и Тето")
        self.assertEqual(_direct_music_action(request, [], True),
                         {"action": "search", "query": "PPPP от Мику и Тето"})

    def test_bare_track_name_is_searched_directly(self):
        self.assertEqual(_direct_music_action("Тогда включи Birdbrain", [], False),
                         {"action": "search", "query": "Birdbrain"})

    def test_search_and_play_phrases_keep_the_requested_track_name(self):
        for request in (
            "Найди Birdbrain в Яндекс Музыке",
            "Поищи трек Birdbrain на Яндекс Музыке",
            "Включи мне Birdbrain в музыке",
        ):
            with self.subTest(request=request):
                self.assertEqual(_direct_music_action(request, [], False),
                                 {"action": "search", "query": "Birdbrain"})

    def test_yandex_my_wave_request_is_dispatched_directly(self):
        for request in (
            "Включи в Яндекс Музыке волну",
            "Запусти мою волну в Яндекс Музыке",
            "Включи Мою волну",
        ):
            with self.subTest(request=request):
                self.assertEqual(_direct_music_action(request, [], False),
                                 {"action": "wave", "query": ""})

    def test_agent_runs_wave_instead_of_answering_from_model_guess(self):
        class MemoryStore:
            settings = {"access_mode": "safe", "max_steps": 8}
            def __init__(self): self.messages = []
            def get_chat(self, _chat_id): return {"title": "Музыка", "messages": list(self.messages)}
            def append_message(self, _chat_id, message): self.messages.append(message)
            def record_action(self, _action): pass

        store = MemoryStore()
        agent = Agent(store, Toolbox(SimpleNamespace(settings={"work_dir": ""})), lambda *_args: False)
        events = []
        client = _FakeWaveClient()
        with patch.object(desktop, "_ensure_music_cdp", return_value=client):
            agent.run("chat", "Включи волну в Яндекс Музыке", events.append, threading.Event())
        call = next(event for event in events if event.get("type") == "tool_call")
        self.assertEqual(call["name"], "yandex_music")
        self.assertEqual(call["args"], {"action": "wave", "query": ""})
        result = next(event for event in events if event.get("type") == "tool_result")
        self.assertTrue(result["ok"])
        self.assertIn("Мою волну", result["text"])

    def test_music_followup_reuses_previous_track_context(self):
        prior = [{"role": "user", "content": "Включи песню Paper Planes от M.I.A."}]
        self.assertEqual(_direct_music_action("Включи его", prior, True),
                         {"action": "search", "query": "Paper Planes от M.I.A."})

    def test_agent_forces_named_track_search_instead_of_hallucinating(self):
        class MemoryStore:
            settings = {"access_mode": "safe", "max_steps": 8}
            def __init__(self, messages=None): self.messages = list(messages or [])
            def get_chat(self, _chat_id): return {"title": "Музыка", "messages": list(self.messages)}
            def append_message(self, _chat_id, message): self.messages.append(message)
            def record_action(self, _action): pass

        store = MemoryStore()
        toolbox = Toolbox(SimpleNamespace(settings={"work_dir": ""}))
        agent = Agent(store, toolbox, lambda *_args: False)
        events = []
        request = "В Fortnite услышал трек PPPP от Мику и Тето, включи мне его"
        client = _FakeMusicClient("PPPP", ["Мику", "Тето"])
        with patch.object(desktop, "_ensure_music_cdp", return_value=client):
            agent.run("chat", request, events.append, threading.Event())
        tool_event = next(event for event in events if event.get("type") == "tool_call")
        self.assertEqual(tool_event["name"], "yandex_music")
        self.assertEqual(tool_event["args"], {"action": "search", "query": "PPPP от Мику и Тето"})
        self.assertFalse(tool_event["confirm"])
        self.assertNotIn("minecraft_status", [event.get("name") for event in events if event.get("type") == "tool_call"])
        self.assertTrue(next(event for event in events if event.get("type") == "tool_result")["ok"])
        final = next(event for event in events if event.get("type") == "done")["message"]
        self.assertIn("PPPP", final["content"])

    def test_agent_resolves_short_music_followup_from_recent_chat(self):
        class MemoryStore:
            settings = {"access_mode": "safe", "max_steps": 8}
            def __init__(self): self.messages = [{"role": "user", "content": "Я услышал трек Lullaby от ExampleArtist"}]
            def get_chat(self, _chat_id): return {"title": "Музыка", "messages": list(self.messages)}
            def append_message(self, _chat_id, message): self.messages.append(message)
            def record_action(self, _action): pass

        store = MemoryStore()
        agent = Agent(store, Toolbox(SimpleNamespace(settings={"work_dir": ""})), lambda *_args: False)
        client = _FakeMusicClient("Lullaby", ["ExampleArtist"])
        with patch.object(desktop, "_ensure_music_cdp", return_value=client):
            agent.run("chat", "Включи его", lambda _event: None, threading.Event())
        self.assertTrue(client.closed)
        self.assertIn("Lullaby", store.messages[-1]["content"])


class MinecraftFeatureTests(unittest.TestCase):
    def test_server_creation_requires_explicit_eula_acceptance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            manager = MinecraftServerManager(Path(temp_dir))
            with self.assertRaisesRegex(ValueError, "EULA"):
                manager.create_server(name="Local", version="1.21.1", eula_accepted=False)
            self.assertEqual(manager.list_servers(), [])

    def test_server_properties_default_to_localhost_and_offline_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            instance = {"directory": str(directory), "port": 25565, "max_players": 5,
                        "name": "Local", "lan_access": False}
            MinecraftServerManager._write_eula_and_properties(instance)
            values = dict(line.split("=", 1) for line in (directory / "server.properties").read_text(encoding="utf-8").splitlines() if "=" in line)
            self.assertEqual(values["online-mode"], "false")
            self.assertEqual(values["server-ip"], "127.0.0.1")
            self.assertEqual((directory / "eula.txt").read_text(encoding="utf-8").splitlines()[-1], "eula=true")

    def test_metadata_urls_are_limited_to_mojang_hosts(self):
        self.assertEqual(_safe_host_url("https://piston-data.mojang.com/file.jar", {"piston-data.mojang.com"}),
                         "https://piston-data.mojang.com/file.jar")
        for bad_url in ("http://piston-data.mojang.com/file.jar", "https://example.org/file.jar"):
            with self.assertRaises(ValueError):
                _safe_host_url(bad_url, {"piston-data.mojang.com"})

    def test_java_detection_uses_compatible_minecraft_launcher_runtime(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / ".minecraft" / "runtime"
            candidates = []
            for runtime, major in (("java-runtime-delta", 17), ("java-runtime-gamma", 21)):
                executable = root / runtime / "windows" / runtime / "bin" / "java.exe"
                executable.parent.mkdir(parents=True)
                executable.touch()
                candidates.append((str(executable), major))

            def fake_run(command, **_kwargs):
                major = next(value for path, value in candidates if path == command[0])
                return SimpleNamespace(returncode=0, stderr=f'openjdk version "{major}.0.1"', stdout="")

            with patch.object(minecraft_server, "_JAVA_CACHE", None), patch.dict("os.environ", {"APPDATA": temp_dir}), patch("assistant.minecraft_server.shutil.which", return_value=None), patch(
                "assistant.minecraft_server.subprocess.run", side_effect=fake_run
            ):
                info = MinecraftServerManager.java_info(required_major=21)
            self.assertTrue(info["available"])
            self.assertTrue(info["compatible"])
            self.assertEqual(info["major"], 21)


class VisionFeatureTests(unittest.TestCase):
    def test_vision_only_accepts_loopback_ollama(self):
        self.assertEqual(_base_url({"ollama_url": "http://127.0.0.1:11434"}), "http://127.0.0.1:11434")
        with self.assertRaises(VisionError):
            _base_url({"ollama_url": "https://example.org"})

    def test_qwen3_vl_minimum_ollama_version(self):
        self.assertFalse(_ollama_supported("0.12.6"))
        self.assertTrue(_ollama_supported("0.12.7"))
        self.assertTrue(_ollama_supported("0.13.0"))

    def test_pull_only_accepts_supported_models(self):
        service = VisionService(lambda: {"ollama_url": "http://127.0.0.1:11434", "vision_model": "qwen3-vl:2b"})
        with self.assertRaises(ValueError):
            service.pull("some-remote-model")
        self.assertEqual(VISION_MODELS[0], "qwen3-vl:2b")

    def test_vision_keeps_model_hot_briefly_but_releases_it_in_game_profile(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self, _limit): return json.dumps({"message": {"content": "Экран"}}).encode()

        for profile, expected in (("work", "1m"), ("game", "0")):
            settings = {"ollama_url": "http://127.0.0.1:11434", "vision_model": "qwen3-vl:2b", "resource_profile": profile}
            service = VisionService(lambda: settings)
            with patch.object(service, "_request", return_value=Response()) as request:
                self.assertEqual(service.analyze(b"png"), "Экран")
                self.assertEqual(request.call_args.args[1]["keep_alive"], expected)

    def test_pc_tools_are_registered_in_model_discovery_catalogue(self):
        toolbox = Toolbox(SimpleNamespace(settings={"work_dir": ""}))
        self.assertEqual(toolbox.risk("pc_uia_action"), "high")
        self.assertTrue(toolbox.needs_confirm("smart", "pc_app_open"))
        self.assertEqual(toolbox.risk("yandex_music"), "low")
        self.assertFalse(toolbox.needs_confirm("safe", "yandex_music"))
        smart = _tool_search_schema(toolbox, "smart")["function"]
        self.assertEqual(smart["name"], "tool_search")
        self.assertIn("pc_uia_tree", smart["description"])
        self.assertIn("pc_vision", smart["description"])
        self.assertIn("yandex_music", smart["description"])
        safe = _tool_search_schema(toolbox, "safe")["function"]
        safe_names = safe["parameters"]["properties"]["names"]["items"]["enum"]
        self.assertIn("yandex_music", safe_names)
        self.assertNotIn("pc_app_open", safe_names)
        self.assertNotIn("run_command", safe_names)

        external = [{"function_name": "mcp__notes__find", "description": "Find a note in another app."}]
        smart_external = _tool_search_schema(toolbox, "smart", external)["function"]
        self.assertIn("mcp__notes__find", smart_external["description"])
        smart_names = smart_external["parameters"]["properties"]["names"]["items"]["enum"]
        self.assertIn("mcp__notes__find", smart_names)
        safe_external = _tool_search_schema(toolbox, "safe", external)["function"]
        self.assertNotIn("mcp__notes__find", safe_external["parameters"]["properties"]["names"]["items"]["enum"])

    def test_auto_mode_still_confirms_high_risk_tools(self):
        toolbox = Toolbox(SimpleNamespace(settings={"work_dir": ""}))
        self.assertTrue(toolbox.allowed("auto", "run_command"))
        self.assertTrue(toolbox.needs_confirm("auto", "run_command"))
        self.assertTrue(toolbox.needs_confirm("auto", "pc_uia_action"))
        self.assertFalse(toolbox.needs_confirm("auto", "yandex_music"))

    def test_vision_model_setting_is_validated(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.object(storage, "DATA_DIR", Path(temp_dir)):
            store = storage.Store()
            self.assertEqual(
                store.save_settings({"vision_model": "qwen3-vl:4b", "work_dir": temp_dir})[
                    "vision_model"
                ],
                "qwen3-vl:4b",
            )
            with self.assertRaisesRegex(ValueError, "vision-модель"):
                store.save_settings({"vision_model": "external:vision"})


if __name__ == "__main__":
    unittest.main()
