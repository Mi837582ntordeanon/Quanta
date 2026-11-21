import tempfile
import json
import re
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer

from server import Handler, SESSION_TOKEN, _show_path_in_explorer
from assistant.tools import Toolbox


class LocalApiSecurityTests(unittest.TestCase):
    def make_handler(self, headers):
        handler = Handler.__new__(Handler)
        handler.headers = headers
        handler.server = SimpleNamespace(server_port=8765)
        handler.errors = []
        handler._error = lambda status, message: handler.errors.append((status, message))
        return handler

    def test_host_must_be_loopback_on_the_actual_server_port(self):
        self.assertTrue(self.make_handler({"Host": "127.0.0.1:8765"})._check_host())
        self.assertTrue(self.make_handler({"Host": "localhost:8765"})._check_host())
        for host in ("127.0.0.1:9999", "example.com:8765", "localhost", "127.0.0.1:8765.evil"):
            with self.subTest(host=host):
                handler = self.make_handler({"Host": host})
                self.assertFalse(handler._check_host())
                self.assertEqual(handler.errors[0][0], 403)

    def test_origin_must_match_host_and_server_port(self):
        good = self.make_handler({"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765"})
        self.assertTrue(good._check_origin())
        for origin in ("http://localhost:9999", "http://evil.test:8765", "https://127.0.0.1:8765", "null"):
            with self.subTest(origin=origin):
                handler = self.make_handler({"Host": "127.0.0.1:8765", "Origin": origin})
                self.assertFalse(handler._check_origin())

    def test_api_requires_session_capability_but_static_files_do_not(self):
        handler = self.make_handler({"Host": "127.0.0.1:8765", "X-Quanta-Session": SESSION_TOKEN})
        self.assertTrue(handler._authorize_api("/api/bootstrap"))
        self.assertTrue(handler._authorize_api("/ui/app.js"))
        handler = self.make_handler({"Host": "127.0.0.1:8765"})
        self.assertFalse(handler._authorize_api("/api/bootstrap"))
        self.assertEqual(handler.errors[0][0], 403)

    def test_live_local_api_requires_token_injected_into_served_interface(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=httpd.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{httpd.server_port}"
        try:
            with patch("server.log"):
                with urlopen(base + "/", timeout=3) as response:
                    csp = response.headers.get("Content-Security-Policy", "")
                    page = response.read().decode("utf-8")
                self.assertIn("default-src 'self'", csp)
                self.assertIn("object-src 'none'", csp)
                self.assertIn("base-uri 'none'", csp)
                self.assertIn("frame-ancestors 'none'", csp)
                self.assertNotIn("unsafe-inline", csp)
                for static_path in ("/ui/app.js", "/ui/style.css"):
                    with urlopen(base + static_path, timeout=3) as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.headers.get("Content-Security-Policy"), csp)
                        self.assertGreater(int(response.headers.get("Content-Length", "0")), 0)
                match = re.search(r'<meta name="quanta-session-token" content="([^"]+)"', page)
                self.assertIsNotNone(match)
                token = match.group(1)
                self.assertEqual(token, SESSION_TOKEN)
                with self.assertRaises(HTTPError) as denied:
                    urlopen(base + "/api/events", timeout=3)
                self.assertEqual(denied.exception.code, 403)
                denied.exception.close()
                request = Request(base + "/api/events", headers={"X-Quanta-Session": token})
                with urlopen(request, timeout=3) as response:
                    body = json.loads(response.read().decode("utf-8"))
                self.assertTrue(body["ok"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            worker.join(timeout=2)

    def test_open_endpoint_only_navigates_to_files_in_explorer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "payload.exe"
            executable.write_bytes(b"not actually executable")
            with patch("server.subprocess.Popen") as launch:
                result = _show_path_in_explorer(str(executable))
            self.assertTrue(result["ok"])
            command = launch.call_args.args[0]
            self.assertEqual(command[:2], ["explorer.exe", "/select,"])
            self.assertEqual(command[-1], str(executable.resolve()))

    def test_open_endpoint_opens_directories_in_explorer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("server.subprocess.Popen") as launch:
                result = _show_path_in_explorer(temp_dir)
            self.assertTrue(result["ok"])
            self.assertEqual(launch.call_args.args[0], ["explorer.exe", str(Path(temp_dir).resolve())])

    def test_file_tools_cannot_escape_the_configured_workspace(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as outside:
            toolbox = Toolbox(SimpleNamespace(settings={"work_dir": root}))
            inside = toolbox._resolve("notes\\readme.txt")
            self.assertTrue(inside.is_relative_to(Path(root).resolve()))
            with self.assertRaisesRegex(ValueError, "за пределами рабочей папки"):
                toolbox._resolve(outside)

    def test_powershell_no_longer_uses_execution_policy_bypass(self):
        with tempfile.TemporaryDirectory() as root:
            toolbox = Toolbox(SimpleNamespace(settings={"work_dir": root}))
            completed = SimpleNamespace(stdout=b"ok", stderr=b"", returncode=0)
            with patch("assistant.tools.subprocess.run", return_value=completed) as run:
                result = toolbox._run_command("Write-Output ok", cwd=".", shell="powershell")
            self.assertTrue(result["ok"])
            command = run.call_args.args[0]
            self.assertNotIn("-ExecutionPolicy", command)
            self.assertNotIn("Bypass", command)


if __name__ == "__main__":
    unittest.main()
