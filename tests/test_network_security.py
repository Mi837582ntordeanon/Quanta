from __future__ import annotations

import socket
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from assistant.network_security import PublicTarget, resolve_public_url, safe_open
from assistant.tools import _curl_get


def _dns_record(address: str):
    parsed = socket.getaddrinfo(address, 443, type=socket.SOCK_STREAM)[0]
    return parsed


class _FakeResponse:
    def __init__(self, status=200, headers=None, body=b"ok"):
        self.status = status
        self.reason = "OK" if status == 200 else "Found"
        self.headers = headers or {}
        self.body = body

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]

    def close(self):
        pass


class _FakeConnection:
    def __init__(self, response):
        self.response = response
        self.closed = False

    def request(self, method, path, headers):
        self.requested = (method, path, headers)

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class NetworkSecurityTests(unittest.TestCase):
    def test_rejects_private_loopback_link_local_and_reserved_ip_literals(self):
        blocked = (
            "127.0.0.1", "10.2.3.4", "192.168.1.20", "169.254.169.254",
            "::1", "::", "fc00::1", "fe80::1", "ff02::1", "::ffff:127.0.0.1",
            "240.0.0.1", "224.0.0.1",
        )
        for address in blocked:
            with self.subTest(address=address), self.assertRaises(ValueError):
                resolve_public_url(f"http://[{address}]/") if ":" in address else resolve_public_url(f"http://{address}/")

    def test_rejects_localhost_names_and_local_aliases(self):
        for hostname in ("localhost", "localhost.", "printer.localhost", "localhost.localdomain",
                         "printer.localdomain", "host.local"):
            with self.subTest(hostname=hostname), self.assertRaises(ValueError):
                resolve_public_url(f"http://{hostname}/")

    def test_rejects_legacy_numeric_ipv4_spellings(self):
        for address in ("2130706433", "0177.0.0.1", "0x7f000001", "127.1", "0x7f.1"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                resolve_public_url(f"http://{address}/")

    def test_rejects_hostname_if_any_dns_answer_is_private(self):
        public = _dns_record("93.184.216.34")
        private = _dns_record("10.0.0.8")
        with patch("assistant.network_security.socket.getaddrinfo", return_value=[public, private]):
            with self.assertRaises(ValueError):
                resolve_public_url("https://mixed.example/path")

    def test_uses_resolved_public_ip_as_pinned_destination(self):
        public = _dns_record("93.184.216.34")
        with patch("assistant.network_security.socket.getaddrinfo", return_value=[public]):
            target = resolve_public_url("https://example.test/page")
        self.assertEqual(target.connect_ip, "93.184.216.34")

    def test_redirect_to_private_address_is_rejected_before_connect(self):
        public = _dns_record("93.184.216.34")
        connection = _FakeConnection(_FakeResponse(302, {"Location": "http://127.0.0.1/admin"}))
        with patch("assistant.network_security.socket.getaddrinfo", return_value=[public]), patch(
            "assistant.network_security._make_connection", return_value=connection
        ) as make_connection:
            with self.assertRaises(ValueError):
                with safe_open("https://example.test/start"):
                    self.fail("redirect should be rejected")
        make_connection.assert_called_once()
        self.assertTrue(connection.closed)

    def test_follows_at_most_five_redirects(self):
        public = _dns_record("93.184.216.34")
        responses = [_FakeConnection(_FakeResponse(302, {"Location": "/next"})) for _ in range(6)]
        with patch("assistant.network_security.socket.getaddrinfo", return_value=[public]), patch(
            "assistant.network_security._make_connection", side_effect=responses
        ) as make_connection:
            with self.assertRaises(ValueError):
                with safe_open("https://example.test/start"):
                    self.fail("sixth redirect should be rejected")
        self.assertEqual(make_connection.call_count, 6)

    def test_curl_fallback_has_protocol_size_and_redirect_limits(self):
        target = PublicTarget(
            url="https://example.com/", scheme="https", host="example.com", port=443,
            connect_ip="93.184.216.34", request_target="/",
        )
        seen = []

        def run(command, **_kwargs):
            seen.extend(command)
            Path(command[command.index("-D") + 1]).write_bytes(
                b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n\r\n"
            )
            return subprocess.CompletedProcess(command, 0, stdout=b"body", stderr=b"")

        with patch("assistant.tools.shutil.which", return_value="curl.exe"), patch(
            "assistant.tools.Path.exists", return_value=True
        ), patch("assistant.tools.resolve_public_url", return_value=target), patch(
            "assistant.tools.subprocess.run", side_effect=run
        ):
            body, content_type = _curl_get("https://example.com/")
        self.assertEqual(body, b"body")
        self.assertEqual(content_type, "text/plain")
        self.assertIn("--resolve", seen)
        self.assertIn("--noproxy", seen)
        self.assertNotIn("-L", seen)
        for option, expected in (
            ("--proto", "=http,https"), ("--proto-redir", "=http,https"),
            ("--max-redirs", "5"), ("--max-filesize", "2000000"),
        ):
            self.assertEqual(seen[seen.index(option) + 1], expected)

    def test_curl_redirect_to_private_address_is_checked_before_second_request(self):
        target = PublicTarget(
            url="https://example.com/", scheme="https", host="example.com", port=443,
            connect_ip="93.184.216.34", request_target="/",
        )
        real_resolve = resolve_public_url
        calls = []

        def resolve(url, **kwargs):
            if url == "https://example.com/":
                return target
            return real_resolve(url, **kwargs)

        def run(command, **_kwargs):
            calls.append(command)
            Path(command[command.index("-D") + 1]).write_bytes(
                b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/admin\r\n\r\n"
            )
            return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

        with patch("assistant.tools.shutil.which", return_value="curl.exe"), patch(
            "assistant.tools.Path.exists", return_value=True
        ), patch("assistant.tools.resolve_public_url", side_effect=resolve), patch(
            "assistant.tools.subprocess.run", side_effect=run
        ):
            with self.assertRaises(ValueError):
                _curl_get("https://example.com/")
        self.assertEqual(len(calls), 1)

    def test_rejects_non_http_schemes_before_connect(self):
        for url in ("file:///C:/Windows/win.ini", "ftp://example.com/file"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                resolve_public_url(url)


if __name__ == "__main__":
    unittest.main()
