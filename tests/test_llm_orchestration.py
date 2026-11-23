import json
import unittest
from unittest.mock import patch

from assistant import llm
from assistant.agent import Agent


class ModelOrchestrationTests(unittest.TestCase):
    def test_live_eval_token_cap_is_added_only_when_requested(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def __iter__(self):
                return iter([b'{"done": true, "message": {"content": ""}}\n'])

        with patch("assistant.llm.urllib.request.urlopen", return_value=FakeResponse()) as request:
            list(llm._stream_ollama({"model": "test-model"}, [], None))
            default_payload = json.loads(request.call_args.args[0].data)

        with patch("assistant.llm.urllib.request.urlopen", return_value=FakeResponse()) as request:
            list(llm._stream_ollama({"model": "test-model", "_quanta_eval_max_tokens": 96}, [], None))
            bounded_payload = json.loads(request.call_args.args[0].data)

        self.assertNotIn("num_predict", default_payload["options"])
        self.assertEqual(bounded_payload["options"]["num_predict"], 96)

    def test_live_eval_temperature_and_seed_are_passed_to_ollama(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def __iter__(self):
                return iter([b'{"done": true, "message": {"content": ""}}\n'])

        settings = {
            "model": "test-model",
            "temperature": 0.25,
            "_quanta_eval_max_tokens": 128,
            "_quanta_eval_seed": 73,
        }
        with patch("assistant.llm.urllib.request.urlopen", return_value=FakeResponse()) as request:
            list(llm._stream_ollama(settings, [], None))
        payload = json.loads(request.call_args.args[0].data)

        self.assertEqual(payload["options"]["temperature"], 0.25)
        self.assertEqual(payload["options"]["num_predict"], 128)
        self.assertEqual(payload["options"]["seed"], 73)

    def test_falls_back_before_any_output(self):
        settings = {
            "provider": "ollama", "model": "qwen3:4b-instruct",
            "fallback_enabled": True, "fallback_provider": "openai",
            "fallback_model": "free-model", "fallback_api_url": "https://api.example/v1",
            "fallback_api_key": "secret",
        }
        requests = []

        def fake_provider(current, _messages, _tools):
            requests.append(current)
            if current["provider"] == "ollama":
                raise llm.LLMError("Не удалось подключиться к Ollama")
            yield {"type": "final", "message": {"role": "assistant", "content": "резерв"}}

        with patch("assistant.llm._stream_provider", side_effect=fake_provider):
            events = list(llm.stream_chat(settings, [], None))

        self.assertEqual(events[0]["type"], "provider_fallback")
        self.assertEqual(events[-1]["message"]["content"], "резерв")
        self.assertEqual(requests[1]["api_url"], "https://api.example/v1")
        self.assertEqual(requests[1]["api_key"], "secret")

    def test_auth_errors_do_not_switch_providers(self):
        settings = {
            "provider": "openai", "model": "primary",
            "fallback_enabled": True, "fallback_provider": "ollama", "fallback_model": "local",
        }
        calls = []

        def fake_provider(current, _messages, _tools):
            calls.append(current)
            raise llm.LLMError("Доступ запрещён (401). Проверьте API-ключ.")
            yield  # pragma: no cover

        with patch("assistant.llm._stream_provider", side_effect=fake_provider):
            with self.assertRaisesRegex(llm.LLMError, "401"):
                list(llm.stream_chat(settings, [], None))
        self.assertEqual(len(calls), 1)

    def test_stream_failure_after_first_token_does_not_restart_model(self):
        settings = {
            "provider": "ollama", "model": "primary",
            "fallback_enabled": True, "fallback_provider": "openai", "fallback_model": "remote",
        }
        calls = []

        def fake_provider(current, _messages, _tools):
            calls.append(current)
            yield {"type": "delta", "text": "часть"}
            raise llm.LLMError("Ошибка API (503)")

        with patch("assistant.llm._stream_provider", side_effect=fake_provider):
            stream = llm.stream_chat(settings, [], None)
            self.assertEqual(next(stream), {"type": "delta", "text": "часть"})
            with self.assertRaisesRegex(llm.LLMError, "503"):
                next(stream)
        self.assertEqual(len(calls), 1)

    def test_fallback_pool_skips_rate_limited_route_and_uses_the_next(self):
        settings = {
            "provider": "ollama", "model": "primary", "fallback_enabled": True,
            "fallback_routes": [
                {"provider": "openai", "api_url": "https://one.example/v1", "model": "first", "api_key": "one"},
                {"provider": "openai", "api_url": "https://two.example/v1", "model": "second", "api_key": "two"},
            ],
        }
        calls = []

        def fake_provider(current, _messages, _tools):
            calls.append(current)
            if current["provider"] == "ollama" or current["model"] == "first":
                raise llm.LLMError("Ошибка API (503)")
            yield {"type": "final", "message": {"role": "assistant", "content": "из пула"}}

        with patch("assistant.llm._stream_provider", side_effect=fake_provider):
            events = list(llm.stream_chat(settings, [], None))

        self.assertEqual(events[0]["type"], "provider_fallback")
        self.assertEqual(events[0]["model"], "second")
        self.assertEqual(events[-1]["message"]["content"], "из пула")
        self.assertEqual([route["model"] for route in calls], ["primary", "first", "second"])

    def test_planner_uses_first_reserve_and_keeps_remaining_pool(self):
        settings = {
            "provider": "ollama", "model": "small", "fallback_enabled": True,
            "fallback_provider": "openai", "fallback_model": "big-planner",
            "fallback_api_url": "https://planner.example/v1", "fallback_api_key": "key",
            "fallback_routes": [{"provider": "ollama", "model": "qwen3:8b"}],
            "planner_enabled": True,
        }
        route = llm.planner_route_settings(settings)
        self.assertEqual(route["provider"], "openai")
        self.assertEqual(route["model"], "big-planner")
        self.assertTrue(route["fallback_enabled"])
        self.assertEqual(route["fallback_routes"][0]["model"], "qwen3:8b")

    def test_complex_plan_is_refined_as_json_before_user_approval(self):
        settings = {
            "provider": "ollama", "model": "small", "fallback_enabled": True,
            "fallback_provider": "openai", "fallback_model": "planner",
            "fallback_api_url": "https://planner.example/v1", "planner_enabled": True,
        }
        agent = Agent(None, None, None)

        def fake_stream(route, messages, tools):
            self.assertEqual(route["model"], "planner")
            self.assertIsNone(tools)
            self.assertIn("draft_plan", messages[1]["content"])
            yield {"type": "final", "message": {"content": '{"steps":["Осмотреть проект","Сохранить результат"]}'}}

        with patch("assistant.agent.llm.stream_chat", side_effect=fake_stream):
            plan = agent._refine_plan(settings, "Сделай резервную копию", ["Сделать план", "Выполнить"])
        self.assertEqual(plan, ["Осмотреть проект", "Сохранить результат"])


if __name__ == "__main__":
    unittest.main()
