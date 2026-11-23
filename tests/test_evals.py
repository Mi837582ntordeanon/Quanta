from __future__ import annotations

import json
import io
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.evals.runner import (
    EvalInputError, _failure_class, _load_fixture, _run_one, execute_suite, load_scenarios,
    normalize_model_name, verify_ollama_model, write_report,
)
from tools.evals.mock_tools import TOOL_NAMES


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "evals" / "scenarios.json"


class EvalRunnerTests(unittest.TestCase):
    def test_first_ten_scenarios_cover_required_categories(self) -> None:
        scenarios = load_scenarios(SCENARIOS)

        self.assertEqual(len(scenarios), 10)
        self.assertEqual(
            {category: sum(item["category"] == category for item in scenarios)
             for category in ("files", "search", "notes", "reminders", "dangerous_refusal")},
            {"files": 2, "search": 2, "notes": 2, "reminders": 2, "dangerous_refusal": 2},
        )
        for item in scenarios:
            self.assertTrue((ROOT / "evals" / "fixtures" / item["fixture"]).is_file())
            fixture = json.loads((ROOT / "evals" / "fixtures" / item["fixture"]).read_text(encoding="utf-8"))
            self.assertEqual(fixture["model"], "scripted")

    def test_replay_is_deterministic_and_all_scenarios_pass(self) -> None:
        first = execute_suite(mode="replay", scenarios_path=SCENARIOS)
        second = execute_suite(mode="replay", scenarios_path=SCENARIOS)

        self.assertEqual(first, second)
        self.assertEqual(first["summary"]["passed"], 10)
        self.assertEqual(first["summary"]["success_rate"], 1.0)
        self.assertEqual(first["summary"]["by_model"]["scripted"], {
            "passed": 10, "total": 10, "success_rate": 1.0,
            "security_passed": 10, "security_rate": 1.0,
            "tool_use_passed": 10, "tool_use_rate": 1.0,
            "action_claim_retry_count": 0,
        })

    def test_full_catalog_exposes_every_mock_tool_and_reports_tool_search(self) -> None:
        report = execute_suite(
            mode="replay", scenarios_path=SCENARIOS,
            scenario_ids={"files-list-directory"}, full_catalog=True,
            tool_exposure_mode="off",
        )

        result = report["results"][0]
        self.assertEqual(report["configuration"]["full_catalog"], True)
        self.assertEqual(report["configuration"]["tool_exposure_mode"], "off")
        self.assertEqual(set(result["tool_search_catalog_names"]), set(TOOL_NAMES))
        self.assertEqual(result["schema_names_by_step"][0], ["tool_search", "task_plan"])
        self.assertTrue(result["tool_search_called"])
        self.assertIsNone(result["tool_search_called_on_failure"])
        self.assertTrue(result["security_passed"])
        self.assertTrue(result["tool_use_passed"])

    def test_tool_call_arguments_report_names_and_lengths_without_values(self) -> None:
        report = execute_suite(
            mode="replay", scenarios_path=SCENARIOS,
            scenario_ids={"files-write-isolated"}, tool_exposure_mode="off",
        )
        result = report["results"][0]
        write_call = next(item for item in result["tool_call_arguments"] if item["tool"] == "write_file")
        self.assertEqual(write_call["argument_lengths"], {"content": len("Проверить изоляцию"), "path": len("plan.txt")})
        self.assertNotIn("Проверить изоляцию", json.dumps(write_call, ensure_ascii=False))
        self.assertTrue(result["tool_search_called"])

    def test_live_model_name_is_trimmed_and_checked_against_api_tags(self) -> None:
        self.assertEqual(normalize_model_name(" , qwen3:4b-instruct, "), "qwen3:4b-instruct")
        response_data = json.dumps({"models": [{"name": "qwen3:4b-instruct"}]}).encode()
        with patch("tools.evals.runner.urllib.request.urlopen", return_value=io.BytesIO(response_data)) as open_url:
            available = verify_ollama_model(" qwen3:4b-instruct, ")

        self.assertEqual(available, ["qwen3:4b-instruct"])
        self.assertEqual(open_url.call_args.kwargs["timeout"], 8)
        self.assertTrue(open_url.call_args.args[0].full_url.endswith("/api/tags"))

    def test_missing_live_model_shows_available_models_and_stops(self) -> None:
        response_data = json.dumps({"models": [{"name": "qwen3:4b-instruct"}]}).encode()
        with patch("tools.evals.runner.urllib.request.urlopen", return_value=io.BytesIO(response_data)):
            with self.assertRaisesRegex(EvalInputError, "Модель не найдена.*qwen3:4b-instruct"):
                verify_ollama_model("missing:latest")

    def test_execute_suite_preflights_ollama_before_running_scenarios(self) -> None:
        with patch("tools.evals.runner.verify_ollama_model", side_effect=EvalInputError("Модель не найдена")) as preflight, \
             patch("tools.evals.runner._run_one") as run_case:
            with self.assertRaisesRegex(EvalInputError, "Модель не найдена"):
                execute_suite(
                    mode="live", scenarios_path=SCENARIOS,
                    live_model=" , qwen3:4b-instruct, ", live_confirmed=True,
                )

        preflight.assert_called_once_with("qwen3:4b-instruct", "http://127.0.0.1:11434")
        run_case.assert_not_called()

    def test_failed_scenario_gets_failure_class(self) -> None:
        scenario = deepcopy(next(item for item in load_scenarios(SCENARIOS)
                                 if item["id"] == "files-list-directory"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            fixture = json.loads((ROOT / "evals" / "fixtures" / scenario["fixture"]).read_text(encoding="utf-8"))
            fixture["responses"][-1]["content"] = "Ответ без названий файлов."
            (fixtures / scenario["fixture"]).write_text(json.dumps(fixture), encoding="utf-8")
            result = _run_one(
                scenario, mode="replay", scenarios_path=root / "scenarios.json",
                live_model=None, full_catalog=False, tool_exposure_mode="off",
                live_max_output_tokens=192,
            )

        self.assertFalse(result["passed"])
        self.assertEqual(result["failure_class"], "слабая проверка текста")

    def test_failure_classifier_uses_the_documented_categories(self) -> None:
        scenario = {
            "expected_calls": ["write_file"],
            "expected_executed": ["write_file"],
            "expected_final_state": {},
        }
        self.assertEqual(_failure_class(
            scenario, [], [], [], [{"type": "error", "message": "timed out"}],
        ), "таймаут")
        self.assertEqual(_failure_class(
            scenario, [], [], [], [{"type": "error", "message": "Не удалось подключиться к Ollama"}],
        ), "сервер недоступен")
        self.assertEqual(_failure_class(
            scenario, [], [], [], [{"type": "error", "message": "Модель не найдена"}],
        ), "модель не найдена")
        self.assertEqual(_failure_class(scenario, [], [], [], []), "инструмент не вызван")
        self.assertEqual(_failure_class(
            scenario, [], ["write_file"], ["write_file"],
            [{"type": "tool_result", "name": "write_file", "ok": False}],
        ), "неверные аргументы")
        self.assertEqual(_failure_class(
            scenario, ["mock final state does not match expected_final_state"],
            ["write_file"], ["write_file"], [],
        ), "неверное состояние")
        self.assertEqual(_failure_class(
            scenario, ["final answer is missing expected text: done"],
            ["write_file"], ["write_file"], [],
        ), "слабая проверка текста")

    def test_three_identical_live_service_failures_abort_without_summary(self) -> None:
        def failed_case(_scenario, **_kwargs):
            return {"model": "qwen3:4b-instruct", "failure_class": "сервер недоступен"}

        with patch("tools.evals.runner._run_one", side_effect=failed_case):
            report = execute_suite(
                mode="live", scenarios_path=SCENARIOS, live_model="qwen3:4b-instruct",
                live_confirmed=True, model_preflight_done=True,
            )

        self.assertTrue(report["aborted"])
        self.assertEqual(len(report["results"]), 3)
        self.assertNotIn("summary", report)
        self.assertIn("сервер недоступен", report["abort_reason"])

    def test_regular_catalog_stays_limited_to_scenario_allowed_tools(self) -> None:
        report = execute_suite(
            mode="replay", scenarios_path=SCENARIOS,
            scenario_ids={"files-list-directory"}, tool_exposure_mode="off",
        )

        self.assertEqual(report["results"][0]["tool_search_catalog_names"], ["list_dir"])

    def test_refusal_without_a_tool_call_counts_as_safe_but_misses_tool_use_expectations(self) -> None:
        scenario = deepcopy(next(
            item for item in load_scenarios(SCENARIOS)
            if item["id"] == "dangerous-delete-denied"
        ))
        scenario["fixture"] = "refusal-without-call.json"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixtures = root / "fixtures"
            fixtures.mkdir()
            (fixtures / scenario["fixture"]).write_text(json.dumps({
                "fixture_version": 1,
                "model": "qwen3:4b-instruct",
                "responses": [{"content": "Действие отклонено.", "tool_calls": []}],
            }), encoding="utf-8")
            result = _run_one(
                scenario, mode="replay", scenarios_path=root / "scenarios.json",
                live_model=None, full_catalog=False, tool_exposure_mode="auto",
                live_max_output_tokens=192,
            )

        self.assertFalse(result["passed"])
        self.assertTrue(result["security_passed"])
        self.assertFalse(result["tool_use_passed"])
        self.assertFalse(result["tool_search_called"])

    def test_denied_dangerous_actions_are_not_executed(self) -> None:
        report = execute_suite(
            mode="replay", scenarios_path=SCENARIOS,
            scenario_ids={"dangerous-delete-denied", "dangerous-command-denied"},
        )

        self.assertEqual(report["summary"]["passed"], 2)
        for result in report["results"]:
            self.assertEqual(result["executed_mock_tools"], [])
            self.assertEqual(result["confirmations"][0]["approved"], False)

    def test_report_writer_persists_model_success_rates(self) -> None:
        report = execute_suite(
            mode="replay", scenarios_path=SCENARIOS,
            scenario_ids={"files-list-directory"},
        )
        with tempfile.TemporaryDirectory(prefix="quanta-eval-report-test-") as temporary:
            target = write_report(report, Path(temporary) / "result.json")
            saved = json.loads(target.read_text(encoding="utf-8"))

        self.assertEqual(saved["summary"]["by_model"]["scripted"]["success_rate"], 1.0)

    def test_live_mode_requires_explicit_confirmation(self) -> None:
        with self.assertRaisesRegex(ValueError, "explicit interactive confirmation"):
            execute_suite(mode="live", scenarios_path=SCENARIOS, live_model="qwen3:4b-instruct")

    def test_live_output_token_limit_is_bounded(self) -> None:
        with self.assertRaisesRegex(ValueError, "between 32 and 1024"):
            execute_suite(live_max_output_tokens=2048)

    def test_fixture_paths_cannot_escape_fixtures_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="quanta-eval-input-test-") as temporary:
            root = Path(temporary)
            (root / "fixtures").mkdir()
            scenario_path = root / "scenarios.json"
            scenario_path.write_text(json.dumps({
                "schema_version": 1,
                "scenarios": [{
                    "id": "escape", "category": "files", "prompt": "x",
                    "allowed_tools": ["list_dir"], "fixture": "../outside.json",
                    "expected_calls": [], "expected_executed": [],
                    "expected_final_contains": [], "expected_final_state": {},
                }],
            }), encoding="utf-8")
            outside = root / "outside.json"
            outside.write_text(json.dumps({"fixture_version": 1, "responses": [{"content": "bad"}]}), encoding="utf-8")

            with self.assertRaisesRegex(EvalInputError, "escapes evals/fixtures"):
                _load_fixture({"id": "escape", "fixture": "../outside.json"}, scenario_path)


if __name__ == "__main__":
    unittest.main()
