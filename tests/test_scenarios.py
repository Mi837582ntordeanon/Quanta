"""Acceptance scenarios for tool selection, execution, and truthful reporting."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant import desktop, storage
from assistant.agent import Agent
from assistant.tools import Toolbox


class ActionAcceptanceScenarios(unittest.TestCase):
    def _run_open_app(self, outcomes, final_text="Калькулятор открыт."):
        with tempfile.TemporaryDirectory() as root:
            data_dir = Path(root) / "data"
            chats_dir = data_dir / "chats"
            with patch.object(storage, "DATA_DIR", data_dir), patch.object(storage, "CHATS_DIR", chats_dir):
                store = storage.Store()
                store.save_settings({
                    "access_mode": "smart",
                    "memory_enabled": False,
                    "max_steps": 8,
                    "work_dir": root,
                })
                chat = store.create_chat("Проверка действия")
                toolbox = Toolbox(store)
                responses = iter(outcomes)
                toolbox.tools["pc_app_open"]["func"] = lambda application_name: next(responses)
                agent = Agent(store, toolbox, lambda *_args: True)
                stream_turn = 0
                events = []

                def scripted_model(_settings, _messages, _tools):
                    nonlocal stream_turn
                    stream_turn += 1
                    if stream_turn == 1:
                        yield {"type": "final", "message": {"role": "assistant", "content": "", "tool_calls": [
                            {"id": "discover", "name": "tool_search", "arguments": {
                                "query": "открыть Калькулятор", "names": ["pc_app_open"]}},
                        ]}}
                    elif stream_turn <= len(outcomes) + 1:
                        yield {"type": "final", "message": {"role": "assistant", "content": "", "tool_calls": [
                            {"id": f"open_{stream_turn}", "name": "pc_app_open", "arguments": {
                                "application_name": "Калькулятор"}},
                        ]}}
                    else:
                        # Simulate a model that ignores a failed tool and confidently lies.
                        yield {"type": "delta", "text": final_text}
                        yield {"type": "final", "message": {
                            "role": "assistant", "content": final_text, "tool_calls": [],
                        }}

                with patch("assistant.agent.llm.stream_chat", side_effect=scripted_model):
                    agent.run(chat["id"], "Открой Калькулятор", events.append, threading.Event())
                final = next(event["message"] for event in reversed(events) if event.get("type") == "done")
                return final["content"], events, stream_turn

    def test_failed_pc_action_cannot_be_reported_as_success_or_spoken_as_one(self):
        final, events, _turns = self._run_open_app([
            {"ok": False, "text": "Приложение «Калькулятор» не найдено в меню Пуск."},
        ])

        self.assertIn("не выполнено", final)
        self.assertIn("не найдено", final)
        self.assertNotIn("Калькулятор открыт", final)
        self.assertFalse(any(event.get("type") == "delta" for event in events))

    def test_recovery_attempts_are_reported_separately(self):
        final, events, _turns = self._run_open_app([
            {"ok": False, "text": "Первый запуск не сработал."},
            {"ok": True, "text": "Windows приняла запрос на запуск Калькулятора."},
        ])

        self.assertIn("не выполнено", final)
        self.assertIn("Первый запуск не сработал", final)
        self.assertIn("выполнено", final)
        self.assertIn("Windows приняла запрос", final)
        self.assertNotIn("Калькулятор открыт", final)
        self.assertFalse(any(event.get("type") == "delta" for event in events))

    def test_successful_action_keeps_the_natural_model_reply(self):
        final, events, _turns = self._run_open_app([
            {"ok": True, "text": "Windows приняла запрос на запуск Калькулятора."},
        ], final_text="Запрос на запуск Калькулятора отправлен.")

        self.assertEqual(final, "Запрос на запуск Калькулятора отправлен.")
        self.assertTrue(any(event.get("type") == "delta" for event in events))


class MusicAcceptanceScenarios(unittest.TestCase):
    def test_missing_exact_track_is_not_replaced_with_a_guess(self):
        class NoMatchClient:
            closed = False

            def evaluate(self, expression):
                if "Boolean(document.querySelector('input[type=search]'))" in expression:
                    return True
                if "Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')" in expression:
                    return "Несуществующий трек"
                if "const titleNorm=norm(title)" in expression:
                    return {"ok": False, "reason": "not-found"}
                return True

            def close(self):
                self.closed = True

        client = NoMatchClient()
        with patch.object(desktop, "_ensure_music_cdp", return_value=client), \
             patch.object(desktop.time, "sleep", return_value=None):
            result = desktop.music_control("search", "Несуществующий трек")

        self.assertFalse(result["ok"])
        self.assertIn("не нашла точный трек", result["text"])
        self.assertIn("Похожий не запускала", result["text"])
        self.assertTrue(client.closed)


if __name__ == "__main__":
    unittest.main()
