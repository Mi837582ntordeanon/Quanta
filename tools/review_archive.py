"""Validate review archives against the repository's secret-file denylist."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from typing import Iterable
from zipfile import BadZipFile, ZipFile


def forbidden_archive_entries(names: Iterable[str]) -> list[str]:
    """Return archive paths that must never be included in a review bundle."""
    forbidden: list[str] = []
    for raw_name in names:
        name = str(raw_name).replace("\\", "/")
        normalized = name
        while normalized.startswith("./"):
            normalized = normalized[2:]
        parts = [part for part in normalized.split("/") if part]
        folded_parts = [part.casefold() for part in parts]
        filename = folded_parts[-1] if folded_parts else ""
        reasons: list[str] = []

        if any(part.startswith("api_secrets") for part in folded_parts):
            reasons.append("api_secrets*")
        if "data" in folded_parts:
            reasons.append("data/")
        if re.fullmatch(r"traces.*\.jsonl", filename):
            reasons.append("traces*.jsonl")
        if "__pycache__" in folded_parts:
            reasons.append("__pycache__/")
        if filename == ".env" or (filename.startswith(".env.") and filename != ".env.example"):
            reasons.append(".env")
        if filename.endswith((".pem", ".key")):
            reasons.append("*.pem / *.key")
        if ".." in parts or name.startswith("/") or re.match(r"^[a-z]:/", name, re.IGNORECASE):
            reasons.append("unsafe archive path")

        if reasons:
            forbidden.append(f"{raw_name}: {', '.join(reasons)}")
    return forbidden


def check_zip(path: Path) -> list[str]:
    """Return forbidden entries from a ZIP archive, or raise for invalid input."""
    with ZipFile(Path(path)) as archive:
        return forbidden_archive_entries(archive.namelist())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reject secret and local-data paths in a review ZIP.")
    parser.add_argument("--check-zip", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        forbidden = check_zip(args.check_zip)
    except (OSError, BadZipFile) as exc:
        print(f"Archive check failed: {exc}", file=sys.stderr)
        return 2
    if forbidden:
        print("Forbidden archive entries:", file=sys.stderr)
        for item in forbidden:
            print(f"- {item}", file=sys.stderr)
        return 1
    print("Archive file-list security check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
