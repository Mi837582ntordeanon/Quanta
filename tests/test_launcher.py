import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from launcher import server_up


class FakeResponse:
    def __init__(self, body: str):
        self.body = body.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


class LauncherHealthTests(unittest.TestCase):
    def test_server_up_uses_the_session_token_injected_into_the_ui(self):
        token = "launcher-test-token"
        page = f'<meta name="quanta-session-token" content="{token}">'

        with tempfile.TemporaryDirectory() as data_dir:
            def open_url(request, timeout):
                self.assertEqual(timeout, 0.5)
                if isinstance(request, str):
                    self.assertTrue(request.endswith("/"))
                    return FakeResponse(page)
                self.assertTrue(request.full_url.endswith("/api/health"))
                self.assertEqual(request.get_header("X-quanta-session"), token)
                return FakeResponse(json.dumps({
                    "ok": True,
                    "health": {"status": "ok"},
                    "data_dir": data_dir,
                }))

            with patch("launcher.DATA_DIR", Path(data_dir)), patch("launcher.urllib.request.urlopen", side_effect=open_url):
                self.assertTrue(server_up(8765))

    def test_server_up_rejects_a_page_without_a_session_token(self):
        with tempfile.TemporaryDirectory() as data_dir:
            with patch("launcher.DATA_DIR", Path(data_dir)), patch(
                "launcher.urllib.request.urlopen", return_value=FakeResponse("<html></html>")
            ) as open_url:
                self.assertFalse(server_up(8765))
                open_url.assert_called_once()


if __name__ == "__main__":
    unittest.main()
