"""Stage Mineflayer for the packaged Java-only Minecraft companion.

The upstream minecraft-data package also ships several hundred MB of Bedrock
metadata. Quanta only manages Java servers, so the bundle omits Bedrock data,
tests, docs, source maps, and TypeScript declarations while retaining Java
version data and all package licenses.
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    source = root / "node_modules"
    destination = root / "build" / "minecraft-runtime" / "node_modules"
    if not (source / "mineflayer" / "package.json").is_file():
        print("Mineflayer is missing. Run npm ci first.", file=sys.stderr)
        return 2
    try:
        destination.resolve().relative_to((root / "build").resolve())
    except ValueError:
        print("Refusing to stage Minecraft files outside build/.", file=sys.stderr)
        return 2

    def ignore(directory: str, names: list[str]) -> set[str]:
        current = Path(directory)
        ignored = {name for name in names if name in {".git", ".github", ".bin", ".cache", "doc", "test", "tests"}}
        if current.name == "data" and current.parent.name == "minecraft-data":
            if "bedrock" in names:
                ignored.add("bedrock")
        ignored.update(name for name in names if name.endswith((".map", ".ts")) or name.lower().endswith(".md"))
        return ignored

    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True, ignore=ignore)
    bedrock = destination / "minecraft-data" / "minecraft-data" / "data" / "bedrock"
    if bedrock.exists():
        shutil.rmtree(bedrock)
    required = (
        destination / "mineflayer" / "package.json",
        destination / "minecraft-data" / "minecraft-data" / "data" / "dataPaths.json",
    )
    if not all(path.is_file() for path in required):
        print("Staged Mineflayer bundle failed its required-file check.", file=sys.stderr)
        return 3
    total = sum(file.stat().st_size for file in destination.rglob("*") if file.is_file())
    print(f"Staged Java Mineflayer runtime: {total / 1024 / 1024:.1f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
