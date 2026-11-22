"""Characterization tests for the Stage 3 refactor boundaries."""

import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

import server as server_app
from assistant import storage
from assistant.agent import Agent
from assistant.tools import Toolbox
from server import Handler, SESSION_TOKEN


class ToolboxContractTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.toolbox = Toolbox(SimpleNamespace(settings={"work_dir": self.temp_dir.name}))

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_tool_discovery_metadata_and_schemas_keep_the_public_contract(self):
        self.assertIn("read_file", self.toolbox.tools)
        self.assertEqual(self.toolbox.label("read_file"), "Чтение файла")
        self.assertEqual(self.toolbox.risk("read_file"), "read")
        self.assertEqual(self.toolbox.risk("write_file"), "high")
        schemas = self.toolbox.schemas(["read_file", "missing_tool"])
        self.assertEqual([item["function"]["name"] for item in schemas], ["read_file"])
        self.assertEqual(schemas[0]["function"]["parameters"]["required"], ["path"])

    def test_access_modes_and_confirmation_preserve_high_risk_guard(self):
        self.assertTrue(self.toolbox.allowed("safe", "read_file"))
        self.assertFalse(self.toolbox.allowed("safe", "write_file"))
        self.assertTrue(self.toolbox.needs_confirm("smart", "write_file"))
        self.assertTrue(self.toolbox.needs_confirm("auto", "write_file"))
        self.assertFalse(self.toolbox.needs_confirm("smart", "read_file"))
        self.assertFalse(self.toolbox.allowed("smart", "missing_tool"))

    def test_execute_normalizes_success_and_unknown_tool_results(self):
        self.toolbox.add(
            "characterization_echo", "Echo", {"type": "object", "properties": {}},
            lambda value="ok": value, label="Echo", risk="read",
        )
        self.assertEqual(
            self.toolbox.execute("characterization_echo", {"value": "hello", "ignored": True}),
            {"ok": True, "text": "hello"},
        )
        result = self.toolbox.execute("missing_tool", {})
        self.assertFalse(result["ok"])
        self.assertIn("Неизвестный инструмент", result["text"])

class AgentEventContractTests(unittest.TestCase):
    def test_run_emits_existing_user_step_delta_end_and_done_events(self):
        with tempfile.TemporaryDirectory() as root:
            data_dir = Path(root) / "data"
            with patch.object(storage, "DATA_DIR", data_dir), patch.object(storage, "CHATS_DIR", data_dir / "chats"):
                store = storage.Store()
                store.save_settings({"access_mode": "safe", "memory_enabled": False,
                                     "max_steps": 2, "work_dir": root})
                chat = store.create_chat("Характеризация Agent.run")
                agent = Agent(store, Toolbox(store), lambda *_args: True)
                events = []

                def scripted_model(*_args):
                    yield {"type": "delta", "text": "Готово."}
                    yield {"type": "final", "message": {
                        "role": "assistant", "content": "Готово.", "tool_calls": [],
                    }}

                with patch("assistant.agent.llm.stream_chat", side_effect=scripted_model):
                    agent.run(chat["id"], "Ответь кратко", events.append, threading.Event())

        event_types = [event.get("type") for event in events]
        self.assertEqual(event_types, ["user_saved", "step_start", "delta", "step_end", "done"])
        self.assertEqual(events[-1]["message"]["content"], "Готово.")


class ServerRouteContractTests(unittest.TestCase):
    def start_server(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        return httpd, worker, f"http://127.0.0.1:{httpd.server_port}"

    def test_json_body_requires_a_utf8_json_object(self):
        def read_body(raw):
            handler = Handler.__new__(Handler)
            handler.headers = {"Content-Length": str(len(raw))}
            handler.rfile = io.BytesIO(raw)
            return handler._read_json()

        self.assertEqual(read_body(b'{"name":"Quanta"}'), {"name": "Quanta"})
        for body in (b"not-json", b"[]", b"null"):
            with self.subTest(body=body), self.assertRaises(ValueError):
                read_body(body)

    def test_health_route_is_authenticated_and_unknown_api_route_is_404(self):
        httpd, worker, base = self.start_server()
        headers = {"X-Quanta-Session": SESSION_TOKEN}
        try:
            with patch("server.health_info", return_value={"ready": True}), \
                 patch("server.system_stats", return_value={"cpu": 0}):
                with urlopen(Request(base + "/api/health", headers=headers), timeout=3) as response:
                    body = json.loads(response.read().decode("utf-8"))
                self.assertTrue(body["ok"])
                self.assertEqual(body["health"], {"ready": True})
                self.assertEqual(body["system"], {"cpu": 0})

            with self.assertRaises(HTTPError) as missing:
                urlopen(Request(base + "/api/no-such-route", headers=headers), timeout=3)
            self.assertEqual(missing.exception.code, 404)
            missing.exception.close()
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join(timeout=2)

    def test_get_post_put_and_delete_dispatch_through_route_tables(self):
        httpd, worker, base = self.start_server()
        headers = {"X-Quanta-Session": SESSION_TOKEN, "Content-Type": "application/json"}

        def read_json(request):
            with urlopen(request, timeout=3) as response:
                return json.loads(response.read().decode("utf-8"))

        try:
            with patch.object(server_app.STORE, "list_notes", return_value=[{"id": "note-1"}]) as list_notes, \
                 patch.object(server_app.STORE, "create_note", return_value={"id": "created", "title": "T", "text": "Body"}) as create_note, \
                 patch.object(server_app.STORE, "update_note", return_value={"id": "note-1", "title": "Updated", "text": "Body"}) as update_note, \
                 patch.object(server_app.STORE, "delete_note", return_value=True) as delete_note:
                get_body = read_json(Request(base + "/api/notes", headers=headers))
                self.assertEqual(get_body["notes"], [{"id": "note-1"}])
                list_notes.assert_called_once_with("")

                post_body = read_json(Request(
                    base + "/api/notes", data=b'{"title":"T","text":"Body"}',
                    headers=headers, method="POST",
                ))
                self.assertEqual(post_body["note"]["id"], "created")
                create_note.assert_called_once_with("T", "Body")

                put_body = read_json(Request(
                    base + "/api/notes/note-1", data=b'{"title":"Updated","text":"Body"}',
                    headers=headers, method="PUT",
                ))
                self.assertEqual(put_body["note"]["title"], "Updated")
                update_note.assert_called_once_with("note-1", title="Updated", text="Body")

                delete_body = read_json(Request(
                    base + "/api/notes/note-1", headers=headers, method="DELETE",
                ))
                self.assertTrue(delete_body["ok"])
                delete_note.assert_called_once_with("note-1")
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
