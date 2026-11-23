import json
import tempfile
import unittest
from pathlib import Path

from assistant.trace_store import TraceStore


class TraceStoreTests(unittest.TestCase):
    def _store(self, directory: Path, **kwargs) -> TraceStore:
        return TraceStore(directory, **kwargs)

    def test_tool_call_records_only_argument_names_lengths_and_safe_fields(self):
        baits = (
            "type-text-password-bait",
            "private-note-content-bait",
            "private-search-query-bait",
            "password=command-secret-bait",
            "clipboard-private-content-bait",
            "fetched-page-private-content-bait",
            "url-path-secret-bait",
            "url-query-secret-bait",
        )
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "traces"
            store = self._store(directory)
            trace = store.start_turn(chat_id="chat-id", task_id="task-id", provider="ollama", model="local")
            calls = (
                ("type_text", {"text": baits[0]}),
                ("write_file", {"path": r"C:\Users\tester\notes.txt", "content": baits[1]}),
                ("web_search", {"query": baits[2]}),
                ("run_command", {"command": baits[3]}),
                ("clipboard_get", {}),
                ("fetch_url", {"url": f"https://example.test/private/{baits[6]}?token={baits[7]}"}),
                ("pc_app_open", {"application_name": "Notepad"}),
            )
            for index, (name, args) in enumerate(calls):
                trace.record_agent_event({
                    "type": "tool_call", "id": f"call-{index}", "name": name,
                    "label": "ignored label", "args": args, "allowed": True, "confirm": False,
                })
            trace.record_agent_event({
                "type": "tool_result", "id": "call-4", "name": "clipboard_get",
                "ok": True, "text": baits[4],
            })
            trace.record_agent_event({
                "type": "tool_result", "id": "call-5", "name": "fetch_url",
                "ok": True, "text": baits[5],
            })
            raw = (directory / "traces.jsonl").read_text(encoding="utf-8")
            records = [json.loads(line) for line in raw.splitlines()]

        for bait in baits:
            self.assertNotIn(bait, raw)
        tool_calls = [record for record in records if record["event"] == "tool_call"]
        self.assertNotIn("call_id", tool_calls[0])
        self.assertEqual(tool_calls[0]["arguments"], [{"name": "text", "length": len(baits[0])}])
        self.assertEqual(
            {item["name"]: item["length"] for item in tool_calls[1]["arguments"]},
            {"path": len(r"C:\Users\tester\notes.txt"), "content": len(baits[1])},
        )
        self.assertEqual(tool_calls[1]["safe_fields"], {"file_extension": ".txt"})
        self.assertEqual(tool_calls[5]["safe_fields"], {"url_host": "example.test"})
        self.assertEqual(tool_calls[6]["safe_fields"], {"application_name": "Notepad"})
        result_records = [record for record in records if record["event"] == "tool_result"]
        base_fields = {"ts", "trace_id", "chat_id", "task_id", "event"}
        for record in result_records:
            self.assertEqual(set(record) - base_fields, {"ok", "duration_ms", "text_length"})
        self.assertEqual(result_records[0]["text_length"], len(baits[4]))
        self.assertEqual(result_records[1]["text_length"], len(baits[5]))

    def test_tool_errors_store_only_a_fixed_error_class(self):
        raw_error = "Timed out connecting to https://private.example/?token=do-not-store"
        raw_model_error = "permission denied api_key=sk-abcdefghijklmnopqrstuvwxyz"
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "traces"
            trace = self._store(directory).start_turn(
                chat_id="chat", task_id=None, provider="ollama", model="local",
            )
            trace.record_agent_event({
                "type": "tool_result", "id": "call-1", "name": "fetch_url",
                "ok": False, "text": raw_error,
            })
            trace.record_agent_event({"type": "error", "message": raw_model_error})
            raw = (directory / "traces.jsonl").read_text(encoding="utf-8")
            records = [json.loads(line) for line in raw.splitlines()]

        self.assertNotIn(raw_error, raw)
        self.assertNotIn(raw_model_error, raw)
        self.assertNotIn("do-not-store", raw)
        tool_result = next(record for record in records if record["event"] == "tool_result")
        self.assertEqual(tool_result["error_class"], "таймаут")
        model_error = next(record for record in records if record["event"] == "error")
        self.assertEqual(model_error["error_class"], "отказано")
        self.assertNotIn("message", model_error)

    def test_legacy_records_are_sanitized_before_they_are_returned_by_api(self):
        private_file_text = "legacy-private-file-content-bait"
        private_result_text = "legacy-private-tool-result-bait"
        private_error_text = "permission denied api_key=sk-abcdefghijklmnopqrstuvwxyz"
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "traces"
            directory.mkdir()
            trace_file = directory / "traces.jsonl"
            legacy_records = [
                {
                    "ts": 1, "trace_id": "legacy-trace", "chat_id": "legacy-chat", "task_id": None,
                    "event": "tool_call", "id": "call-old", "name": "write_file", "label": "old label",
                    "args": {"path": r"C:\Users\tester\private.txt", "content": private_file_text},
                    "allowed": True, "confirm": True,
                },
                {
                    "ts": 2, "trace_id": "legacy-trace", "chat_id": "legacy-chat", "task_id": None,
                    "event": "tool_result", "id": "call-old", "name": "write_file",
                    "ok": True, "text": private_result_text, "summary": private_result_text,
                    "duration_ms": 12,
                },
                {
                    "ts": 3, "trace_id": "legacy-trace", "chat_id": "legacy-chat", "task_id": None,
                    "event": "error", "message": private_error_text,
                },
            ]
            trace_file.write_text(
                "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in legacy_records),
                encoding="utf-8",
            )

            returned = self._store(directory).read_recent()
            returned_json = json.dumps(returned, ensure_ascii=False)
            legacy_file_contents = trace_file.read_text(encoding="utf-8")

        for bait in (private_file_text, private_result_text, private_error_text, "old label"):
            self.assertNotIn(bait, returned_json)
        self.assertIn(private_file_text, legacy_file_contents)
        legacy_call = next(record for record in returned if record["event"] == "tool_call")
        self.assertEqual(legacy_call["safe_fields"], {"file_extension": ".txt"})
        self.assertEqual([argument["name"] for argument in legacy_call["arguments"]], ["path", "content"])
        legacy_result = next(record for record in returned if record["event"] == "tool_result")
        self.assertEqual(legacy_result["text_length"], len(private_result_text))
        legacy_error = next(record for record in returned if record["event"] == "error")
        self.assertEqual(legacy_error["error_class"], "отказано")

    def test_operational_events_keep_steps_calls_confirmations_and_safe_errors(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "traces"
            trace = self._store(directory).start_turn(chat_id="chat", task_id=None, provider="ollama", model="local")
            trace.record_agent_event({"type": "step_start", "step": 1})
            trace.record_agent_event({"type": "step_end", "step": 1})
            trace.record_agent_event({
                "type": "tool_call", "id": "call-1", "name": "web_search",
                "args": {"query": "private request"}, "allowed": True, "confirm": True,
            })
            trace.record_agent_event({
                "type": "confirm", "id": "call-1", "name": "web_search", "label": "secret label",
                "kind": "tool", "args": {"password": "private"},
            })
            trace.record_confirmation("call-1", "web_search", True, 321)
            trace.record_agent_event({
                "type": "tool_result", "id": "call-1", "name": "web_search",
                "ok": False, "text": "permission denied: private diagnostic detail",
            })
            trace.record_agent_event({"type": "error", "message": "model failure detail"})
            raw = (directory / "traces.jsonl").read_text(encoding="utf-8")
            records = [json.loads(line) for line in raw.splitlines()]

        self.assertNotIn("private request", raw)
        self.assertNotIn("secret label", raw)
        self.assertNotIn("private diagnostic detail", raw)
        self.assertNotIn("model failure detail", raw)
        event_names = [record["event"] for record in records]
        self.assertIn("step_start", event_names)
        self.assertIn("step_end", event_names)
        self.assertIn("confirmation_requested", event_names)
        decision = next(record for record in records if record["event"] == "confirmation_decision")
        self.assertTrue(decision["approved"])
        self.assertEqual(decision["duration_ms"], 321)
        result = next(record for record in records if record["event"] == "tool_result")
        self.assertEqual(result["error_class"], "отказано")

    def test_trace_rotation_preserves_recent_records_without_overwriting_archives(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "traces"
            store = self._store(directory, max_bytes=1024)
            trace = store.start_turn(chat_id="chat", task_id=None, provider="ollama", model="local")
            for index in range(20):
                trace.record_agent_event({"type": "error", "message": f"message {index} " + "x" * 100})
            archives = sorted(directory.glob("traces.*.jsonl"))
            records = store.read_recent(100)

        self.assertGreater(len(archives), 0)
        self.assertEqual(sum(record["event"] == "error" for record in records), 20)
        self.assertTrue(all(record["error_class"] == "ошибка" for record in records if record["event"] == "error"))

    def test_storage_limits_total_bytes_and_file_count_by_pruning_oldest(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "traces"
            store = self._store(directory, max_bytes=1_000_000, max_total_bytes=2048, max_files=100)
            trace = store.start_turn(chat_id="chat", task_id=None, provider="ollama", model="local")
            for index in range(40):
                trace.record_agent_event({
                    "type": "tool_call", "id": f"call-{index}", "name": "list_dir",
                    "args": {f"arg_{index}": "x" * 120}, "allowed": True, "confirm": False,
                })
            files = list(directory.glob("traces*.jsonl"))
            total_bytes = sum(path.stat().st_size for path in files)
            records = store.read_recent(100)

        self.assertLessEqual(total_bytes, 2048)
        argument_names = {
            argument["name"]
            for record in records if record["event"] == "tool_call"
            for argument in record["arguments"]
        }
        self.assertNotIn("arg_0", argument_names)
        self.assertIn("arg_39", argument_names)

    def test_storage_limit_prunes_oldest_files_when_file_count_is_higher(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "traces"
            store = self._store(directory, max_bytes=1024, max_total_bytes=20_000_000, max_files=2)
            trace = store.start_turn(chat_id="chat", task_id=None, provider="ollama", model="local")
            for index in range(40):
                trace.record_agent_event({
                    "type": "tool_call", "id": f"call-{index}", "name": "list_dir",
                    "args": {f"arg_{index}": "x" * 120}, "allowed": True, "confirm": False,
                })
            files = list(directory.glob("traces*.jsonl"))
            records = store.read_recent(100)

        self.assertLessEqual(len(files), 2)
        argument_names = {
            argument["name"]
            for record in records if record["event"] == "tool_call"
            for argument in record["arguments"]
        }
        self.assertNotIn("arg_0", argument_names)
        self.assertIn("arg_39", argument_names)

    def test_clear_removes_only_trace_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "traces"
            store = self._store(directory, max_bytes=1024)
            trace = store.start_turn(chat_id="chat", task_id=None, provider="ollama", model="local")
            for index in range(20):
                trace.record_agent_event({
                    "type": "tool_call", "id": f"call-{index}", "name": "list_dir",
                    "args": {"path": "x" * 100}, "allowed": True, "confirm": False,
                })
            unrelated = directory / "keep.txt"
            unrelated.write_text("keep", encoding="utf-8")
            expected_count = len(list(directory.glob("traces*.jsonl")))

            removed = store.clear()

            self.assertEqual(removed, expected_count)
            self.assertEqual(store.read_recent(), [])
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
