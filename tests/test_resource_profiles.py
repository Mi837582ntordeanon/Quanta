import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from assistant import llm, storage, voice


class ResourceProfileTests(unittest.TestCase):
    def test_game_runtime_uses_low_resource_overrides(self):
        settings = {
            "resource_profile": "game",
            "model_context_size": 16384,
            "model_keep_alive": "10m",
            "stt_device": "cuda",
        }

        effective = llm.effective_runtime_settings(settings)

        self.assertEqual(effective["model_context_size"], 8192)
        self.assertEqual(effective["model_keep_alive"], "2m")
        self.assertEqual(effective["stt_device"], "cpu")
        self.assertEqual(settings["model_context_size"], 16384)

    def test_work_runtime_preserves_user_settings(self):
        settings = {
            "resource_profile": "work",
            "model_context_size": 16384,
            "model_keep_alive": "10m",
            "stt_device": "cuda",
        }

        self.assertEqual(llm.effective_runtime_settings(settings), settings)

    def test_storage_accepts_only_known_resource_profiles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(storage, "DATA_DIR", Path(temp_dir)):
                store = storage.Store()
                self.assertEqual(
                    store.save_settings({"resource_profile": "game", "work_dir": temp_dir})[
                        "resource_profile"
                    ],
                    "game",
                )
                with self.assertRaises(ValueError):
                    store.save_settings({"resource_profile": "turbo"})

    def test_game_profile_forces_whisper_to_cpu(self):
        captured = {}
        fake_model = SimpleNamespace(
            transcribe=lambda _path: ([SimpleNamespace(text="ok")], None)
        )

        def fake_ensure(_store, settings_override=None):
            captured.update(settings_override or {})
            return fake_model

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.object(voice, "VOICE_DIR", Path(temp_dir)), patch.object(
                voice, "_ensure_whisper", side_effect=fake_ensure
            ):
                store = SimpleNamespace(settings={"resource_profile": "game", "stt_device": "auto"})
                result = voice.transcribe(b"test", "test.wav", store)

        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "ok")
        self.assertEqual(captured["stt_device"], "cpu")


if __name__ == "__main__":
    unittest.main()
