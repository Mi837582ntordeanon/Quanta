from __future__ import annotations

import tempfile
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant.agent import (
    _direct_tool_names,
    _direct_tools_enabled,
    _select_tools_for_request,
)
from assistant import storage
from assistant.tools import Toolbox


class ToolExposureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.toolbox = Toolbox(SimpleNamespace(settings={"work_dir": "."}))

    def test_auto_profile_enables_direct_tools_for_models_up_to_eight_b(self) -> None:
        self.assertTrue(_direct_tools_enabled({"model": "qwen3:4b-instruct", "tool_exposure_mode": "auto"}))
        self.assertTrue(_direct_tools_enabled({"model": "llama3.1:8b", "tool_exposure_mode": "auto"}))
        self.assertFalse(_direct_tools_enabled({"model": "qwen3:14b", "tool_exposure_mode": "auto"}))
        self.assertFalse(_direct_tools_enabled({"model": "custom-model", "tool_exposure_mode": "auto"}))

    def test_user_override_can_enable_or_disable_direct_exposure(self) -> None:
        self.assertTrue(_direct_tools_enabled({"model": "qwen3:14b", "tool_exposure_mode": "on"}))
        self.assertFalse(_direct_tools_enabled({"model": "qwen3:4b", "tool_exposure_mode": "off"}))

    def test_user_exposure_preference_is_persisted_and_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data_dir = Path(temporary) / "data"
            with patch.object(storage, "DATA_DIR", data_dir), patch.object(storage, "CHATS_DIR", data_dir / "chats"):
                store = storage.Store()
                self.assertEqual(store.public_settings()["tool_exposure_mode"], "auto")
                self.assertEqual(store.save_settings({"tool_exposure_mode": "off"})["tool_exposure_mode"], "off")
                self.assertEqual(store.public_settings()["tool_exposure_mode"], "off")
                with self.assertRaisesRegex(ValueError, "Режим прямого подключения"):
                    store.save_settings({"tool_exposure_mode": "unknown"})

    def test_core_tools_and_all_notes_and_reminders_are_direct(self) -> None:
        names = _direct_tool_names(self.toolbox, "smart")
        self.assertTrue({"list_dir", "read_file", "web_search", "memory_save", "open_path"} <= names)
        self.assertEqual({name for name in names if name.startswith("notes_")}, {
            "notes_create", "notes_list", "notes_update", "notes_delete",
        })
        self.assertEqual({name for name in names if name.startswith("reminder_")}, {
            "reminder_create", "reminder_list", "reminder_delete",
        })

    def test_keyword_selector_returns_a_small_relevant_set(self) -> None:
        selected = _select_tools_for_request(self.toolbox, "smart", "Выполни команду PowerShell")

        self.assertIn("run_command", selected)
        self.assertLessEqual(len(selected), 6)

    def test_selector_uses_available_cached_embedding_scores(self) -> None:
        class FakeEmbeddingModel:
            @staticmethod
            def embed(texts):
                return iter([[1.0, 0.0]] + [
                    [1.0, 0.0] if "run_command." in text else [0.0, 1.0]
                    for text in texts[1:]
                ])

        with patch("assistant.agent._optional_fastembed_model", return_value=FakeEmbeddingModel()):
            selected = _select_tools_for_request(self.toolbox, "smart", "make it happen")

        self.assertEqual(selected[0], "run_command")


if __name__ == "__main__":
    unittest.main()
