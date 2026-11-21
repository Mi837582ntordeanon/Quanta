import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.request import Request, urlopen

import server
from assistant.secret_store import DPAPISecretStore, SECRET_MASK, _dpapi_protect, _dpapi_unprotect
from assistant.storage import Store


def _test_protect(data):
    return bytes(value ^ 0x5A for value in data)


def _test_unprotect(data):
    return bytes(value ^ 0x5A for value in data)


class SecretStoreTests(unittest.TestCase):
    def make_store_factory(self):
        return lambda path: DPAPISecretStore(path, protect=_test_protect, unprotect=_test_unprotect)

    def test_protected_envelope_roundtrips_without_plaintext(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "api_secrets.json"
            vault = DPAPISecretStore(path, protect=_test_protect, unprotect=_test_unprotect)
            secrets = {"api_key": "primary-secret", "fallback_api_key": "fallback-secret"}
            vault.save(secrets)
            self.assertEqual(vault.load(), secrets)
            envelope = path.read_text(encoding="utf-8")
            self.assertNotIn("primary-secret", envelope)
            self.assertNotIn("fallback-secret", envelope)
            self.assertIn('"version":1', envelope)

    def test_store_migrates_plaintext_keys_and_preserves_masked_updates(self):
        primary = "primary-secret-test"
        fallback = "fallback-secret-test"
        route_key = "route-secret-test"
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            settings_path = root_path / "settings.json"
            settings_path.write_text(json.dumps({
                "api_key": primary,
                "fallback_api_key": fallback,
                "fallback_routes": [{
                    "provider": "openai", "model": "route-model",
                    "api_url": "https://provider.example/v1", "api_key": route_key,
                }],
            }), encoding="utf-8")

            with patch("assistant.storage.DATA_DIR", root_path), patch(
                "assistant.storage.DPAPISecretStore", side_effect=self.make_store_factory(),
            ):
                store = Store()
                self.assertEqual(store.settings["api_key"], primary)
                self.assertEqual(store.settings["fallback_api_key"], fallback)
                self.assertEqual(store.settings["fallback_routes"][0]["api_key"], route_key)

                public = store.public_settings()
                self.assertEqual(public["api_key"], SECRET_MASK)
                self.assertEqual(public["fallback_api_key"], SECRET_MASK)
                self.assertEqual(public["fallback_routes"][0]["api_key"], SECRET_MASK)
                on_disk = json.loads(settings_path.read_text(encoding="utf-8"))
                self.assertNotIn("api_key", on_disk)
                self.assertNotIn("fallback_api_key", on_disk)
                self.assertNotIn("api_key", on_disk["fallback_routes"][0])
                vault_text = (root_path / "api_secrets.json").read_text(encoding="utf-8")
                for value in (primary, fallback, route_key):
                    self.assertNotIn(value, vault_text)
                    self.assertNotIn(value, settings_path.read_text(encoding="utf-8"))

                updated = store.save_settings({
                    "work_dir": root,
                    "api_key": SECRET_MASK,
                    "fallback_api_key": SECRET_MASK,
                    "fallback_routes": public["fallback_routes"],
                })
                self.assertEqual(updated["api_key"], primary)
                self.assertEqual(updated["fallback_api_key"], fallback)
                self.assertEqual(updated["fallback_routes"][0]["api_key"], route_key)

                restarted = Store()
                self.assertEqual(restarted.settings["api_key"], primary)
                self.assertEqual(restarted.settings["fallback_api_key"], fallback)
                self.assertEqual(restarted.settings["fallback_routes"][0]["api_key"], route_key)

    @unittest.skipUnless(os.name == "nt", "DPAPI доступен только в Windows")
    def test_windows_dpapi_roundtrip(self):
        value = b"Quanta-DPAPI-test-credential"
        self.assertEqual(_dpapi_unprotect(_dpapi_protect(value)), value)

    def test_api_settings_responses_mask_credentials_and_logs_redact_them(self):
        primary = "primary-secret-api"
        fallback = "fallback-secret-api"
        route_key = "route-secret-api"
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            settings_path = root_path / "settings.json"
            settings_path.write_text(json.dumps({
                "api_key": primary,
                "fallback_api_key": fallback,
                "fallback_routes": [{
                    "provider": "openai", "model": "route-model",
                    "api_url": "https://provider.example/v1", "api_key": route_key,
                }],
            }), encoding="utf-8")
            with patch("assistant.storage.DATA_DIR", root_path), patch(
                "assistant.storage.DPAPISecretStore", side_effect=self.make_store_factory(),
            ):
                store = Store()
                httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
                worker = threading.Thread(target=httpd.serve_forever, daemon=True)
                worker.start()
                base = f"http://127.0.0.1:{httpd.server_port}"
                try:
                    with patch("server.STORE", store), patch("server.health_info", return_value={}), patch(
                        "server.system_stats", return_value={},
                    ), patch("server.command_sandbox.status", return_value={}):
                        headers = {"X-Quanta-Session": server.SESSION_TOKEN}
                        with urlopen(Request(base + "/api/bootstrap", headers=headers), timeout=3) as response:
                            bootstrap_text = response.read().decode("utf-8")
                        bootstrap = json.loads(bootstrap_text)
                        self.assertEqual(bootstrap["settings"]["api_key"], SECRET_MASK)
                        self.assertEqual(bootstrap["settings"]["fallback_api_key"], SECRET_MASK)
                        self.assertEqual(bootstrap["settings"]["fallback_routes"][0]["api_key"], SECRET_MASK)
                        for value in (primary, fallback, route_key):
                            self.assertNotIn(value, bootstrap_text)

                        payload = {
                            "work_dir": root,
                            "api_key": SECRET_MASK,
                            "fallback_api_key": SECRET_MASK,
                            "fallback_routes": bootstrap["settings"]["fallback_routes"],
                        }
                        body = json.dumps(payload).encode("utf-8")
                        save_request = Request(
                            base + "/api/settings", data=body, method="POST",
                            headers={**headers, "Content-Type": "application/json"},
                        )
                        with urlopen(save_request, timeout=3) as response:
                            saved_text = response.read().decode("utf-8")
                        saved = json.loads(saved_text)
                        self.assertEqual(saved["settings"]["api_key"], SECRET_MASK)
                        self.assertEqual(saved["settings"]["fallback_api_key"], SECRET_MASK)
                        self.assertEqual(saved["settings"]["fallback_routes"][0]["api_key"], SECRET_MASK)
                        for value in (primary, fallback, route_key):
                            self.assertNotIn(value, saved_text)

                        with patch("server.request_resource_profile", return_value=(store.settings, None, None)), patch(
                            "server.RESOURCE_STATE", {"status": "ready", "profile": "work"},
                        ):
                            profile_request = Request(
                                base + "/api/resource-profile",
                                data=json.dumps({"profile": "work"}).encode("utf-8"),
                                method="POST",
                                headers={**headers, "Content-Type": "application/json"},
                            )
                            with urlopen(profile_request, timeout=3) as response:
                                profile_text = response.read().decode("utf-8")
                        profile = json.loads(profile_text)
                        self.assertEqual(profile["settings"]["api_key"], SECRET_MASK)
                        for value in (primary, fallback, route_key):
                            self.assertNotIn(value, profile_text)

                        store.save_settings({
                            "work_dir": root,
                            "api_key": "",
                            "fallback_api_key": "",
                            "fallback_routes": [],
                        })
                        log_path = root_path / "test.log"
                        with patch("server.LOG_PATH", log_path), patch("server.LOG_LOCK", threading.Lock()):
                            server.log(f"provider error: {primary}; route: {route_key}")
                        log_text = log_path.read_text(encoding="utf-8")
                        self.assertNotIn(primary, log_text)
                        self.assertNotIn(route_key, log_text)
                        self.assertIn(SECRET_MASK, log_text)
                finally:
                    httpd.shutdown()
                    httpd.server_close()
                    worker.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
