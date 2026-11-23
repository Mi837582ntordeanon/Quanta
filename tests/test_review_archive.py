from __future__ import annotations

import unittest

from tools.review_archive import forbidden_archive_entries


class ReviewArchiveTests(unittest.TestCase):
    def test_forbidden_secret_and_local_data_paths_are_rejected(self) -> None:
        names = [
            "api_secrets.json",
            "config/API_SECRETS-backup.json",
            "data/settings.json",
            "traces.jsonl",
            "data/traces-0001.jsonl",
            "assistant/__pycache__/module.pyc",
            ".env",
            "certificates/client.pem",
            "keys/private.key",
        ]

        rejected = forbidden_archive_entries(names)

        self.assertEqual(len(rejected), len(names))
        self.assertTrue(all(any(name.casefold() in item.casefold() for item in rejected) for name in names))

    def test_safe_source_docs_and_env_example_are_allowed(self) -> None:
        names = [
            "AGENTS.md",
            "docs/PROGRESS.md",
            "assistant/tools.py",
            ".env.example",
            "evals/results/replay-scripted.json",
        ]

        self.assertEqual(forbidden_archive_entries(names), [])

    def test_unsafe_archive_paths_are_rejected(self) -> None:
        self.assertEqual(
            forbidden_archive_entries(["../outside.txt", "/absolute/file.txt", "C:/outside.txt"]),
            [
                "../outside.txt: unsafe archive path",
                "/absolute/file.txt: unsafe archive path",
                "C:/outside.txt: unsafe archive path",
            ],
        )


if __name__ == "__main__":
    unittest.main()
