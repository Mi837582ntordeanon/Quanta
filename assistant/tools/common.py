# ruff: noqa: F401
"""Shared imports and helpers used by the tool domain modules.

Every capability the model can use is registered here.  Tools return either a
string or a dict ``{"text": ..., "media": [...]}``.  Risk levels are:

* ``read``  – no side effects, always allowed
* ``low``   – internal / harmless side effects (notes, reminders, images, 3D)
* ``high``  – can change files, run code or control the PC; confirmation in
              "smart" mode, blocked in "safe" mode
"""
from __future__ import annotations

import base64
import csv
import ctypes
import ctypes.wintypes as wintypes
import html
import hashlib
import inspect
import ipaddress
import json
import math
import os
import platform
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import zlib
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

from .. import command_sandbox
from ..network_security import resolve_public_url, safe_http_get
from ..storage import DATA_DIR, Store

CREATE_NO_WINDOW = 0x08000000
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


# --------------------------------------------------------------------------- utils
def _truncate(text: str, limit: int = 18000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [обрезано, всего {len(text)} символов]"


def _small_file_digest(path: Path, max_bytes: int = 10_000_000) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > max_bytes:
            return None
        digest = hashlib.sha256()
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _fmt_size(size: float) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if size < 1024 or unit == "ТБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ТБ"


def _decode_console(data: bytes) -> str:
    if not data:
        return ""
    try:
        text = data.decode("utf-8")
        if text.count("\ufffd") <= max(1, len(text) // 50):
            return text
    except UnicodeDecodeError:
        pass
    for encoding in ("cp866", "cp1251"):
        try:
            text = data.decode(encoding)
            if text.count("\ufffd") <= max(1, len(text) // 50):
                return text
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def _strip_tags(value: str) -> str:
    value = re.sub(r"<script.*?</script>", " ", value, flags=re.S | re.I)
    value = re.sub(r"<style.*?</style>", " ", value, flags=re.S | re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def _curl_get(url: str, timeout: float = 25.0, max_bytes: int = 2_000_000) -> tuple[bytes, str]:
    """Certificate-safe fallback with pinned DNS and manually checked redirects."""
    from . import resolve_public_url as package_resolve_public_url

    curl = shutil.which("curl") or r"C:\Windows\System32\curl.exe"
    if not Path(curl).exists():
        raise RuntimeError("curl.exe не найден")
    current = url
    redirects = 0
    while True:
        target = package_resolve_public_url(current)
        resolve_args: list[str] = []
        try:
            ipaddress.ip_address(target.host)
        except ValueError:
            address = f"[{target.connect_ip}]" if ":" in target.connect_ip else target.connect_ip
            resolve_args = ["--resolve", f"{target.host}:{target.port}:{address}"]

        header_file = tempfile.NamedTemporaryFile(prefix="quanta-curl-", suffix=".headers", delete=False)
        header_path = Path(header_file.name)
        header_file.close()
        try:
            proc = subprocess.run(
                [
                    curl, "-sS", "--compressed", "--max-time", str(max(5, int(timeout))),
                    "--proto", "=http,https", "--proto-redir", "=http,https",
                    "--max-redirs", "5", "--max-filesize", "2000000",
                    "--noproxy", "*", *resolve_args, "-D", str(header_path),
                    "-A", USER_AGENT,
                    "-H", "Accept-Language: ru-RU,ru;q=0.9,en;q=0.8",
                    "--", target.url,
                ],
                capture_output=True,
                timeout=timeout + 8,
            )
            if proc.returncode != 0:
                error = proc.stderr.decode("utf-8", "replace").strip()
                raise RuntimeError(error or f"curl вернул код {proc.returncode}")
            raw_headers = header_path.read_bytes()
        finally:
            header_path.unlink(missing_ok=True)

        if len(proc.stdout) > min(max_bytes, 2_000_000):
            raise RuntimeError("Ответ превышает допустимый размер")
        blocks = [part for part in re.split(rb"\r?\n\r?\n", raw_headers) if part.strip()]
        last_block = next((part for part in reversed(blocks) if part.lstrip().startswith(b"HTTP/")), b"")
        lines = last_block.decode("iso-8859-1", "replace").splitlines()
        status_match = re.match(r"HTTP/\S+\s+(\d{3})", lines[0].strip()) if lines else None
        status = int(status_match.group(1)) if status_match else 0
        response_headers: dict[str, str] = {}
        for line in lines[1:]:
            name, separator, value = line.partition(":")
            if separator:
                response_headers[name.strip().lower()] = value.strip()
        location = response_headers.get("location")
        if status in {301, 302, 303, 307, 308} and location:
            if redirects >= 5:
                raise RuntimeError("Превышен лимит перенаправлений")
            current = urllib.parse.urljoin(target.url, location)
            redirects += 1
            continue
        if status >= 400:
            raise RuntimeError(f"curl вернул HTTP {status}")
        return proc.stdout, response_headers.get("content-type", "")


def _safe_get(
    url: str,
    *,
    timeout: float,
    headers: dict[str, str] | None = None,
    max_bytes: int = 2_000_000,
) -> tuple[bytes, str, str]:
    try:
        return safe_http_get(url, headers=headers, timeout=timeout, max_bytes=max_bytes)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
            raise
        raw, content_type = _curl_get(url, timeout=timeout, max_bytes=max_bytes)
        return raw, content_type, url


def _http_json(url: str, timeout: float = 15.0) -> Any:
    raw, _content_type, _final_url = _safe_get(
        url, timeout=timeout, headers={"Accept": "application/json"}
    )
    return json.loads(raw.decode("utf-8", "replace"))


# --------------------------------------------------------------------------- stl
def _stl_write(path: Path, triangles: list[tuple[tuple[float, float, float], ...]], name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as fh:
        header = f"Q.W.A.N.T.A. {name}".encode("utf-8", "replace")[:80]
        fh.write(header.ljust(80, b"\0"))
        fh.write(struct.pack("<I", len(triangles)))
        for tri in triangles:
            a, b, c = tri
            ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            length = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
            nx, ny, nz = nx / length, ny / length, nz / length
            fh.write(struct.pack("<12fH", nx, ny, nz, *a, *b, *c, 0))


def _mesh_box(w: float, d: float, h: float) -> list[tuple[tuple[float, float, float], ...]]:
    x, y = w / 2, d / 2
    v = [
        (-x, -y, 0), (x, -y, 0), (x, y, 0), (-x, y, 0),
        (-x, -y, h), (x, -y, h), (x, y, h), (-x, y, h),
    ]
    f = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
         (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
         (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]
    return [tuple(v[i] for i in tri) for tri in f]


def _mesh_sphere(radius: float, segments: int = 32, rings: int = 20) -> list[tuple]:
    segments = max(8, min(96, segments))
    rings = max(6, min(64, rings))
    verts: list[tuple[float, float, float]] = []
    for i in range(rings + 1):
        theta = math.pi * i / rings
        z = radius * math.cos(theta) + radius
        r = radius * math.sin(theta)
        for j in range(segments):
            phi = 2 * math.pi * j / segments
            verts.append((r * math.cos(phi), r * math.sin(phi), z))
    tris: list[tuple] = []
    for i in range(rings):
        for j in range(segments):
            a = i * segments + j
            b = i * segments + (j + 1) % segments
            c = (i + 1) * segments + j
            d = (i + 1) * segments + (j + 1) % segments
            tris.append((verts[a], verts[c], verts[b]))
            tris.append((verts[b], verts[c], verts[d]))
    return tris


def _mesh_cylinder(radius: float, height: float, segments: int = 40) -> list[tuple]:
    segments = max(8, min(96, segments))
    import math
    bottom: list[tuple[float, float, float]] = []
    top: list[tuple[float, float, float]] = []
    for i in range(segments):
        angle = 2 * math.pi * i / segments
        x, y = radius * math.cos(angle), radius * math.sin(angle)
        bottom.append((x, y, 0))
        top.append((x, y, height))
    tris: list[tuple] = []
    for i in range(segments):
        j = (i + 1) % segments
        tris.append((bottom[i], top[i], bottom[j]))
        tris.append((bottom[j], top[i], top[j]))
    cb, ct = (0, 0, 0), (0, 0, height)
    for i in range(segments):
        j = (i + 1) % segments
        tris.append((cb, bottom[j], bottom[i]))
        tris.append((ct, top[i], top[j]))
    return tris


def _mesh_cone(radius: float, height: float, segments: int = 40) -> list[tuple]:
    segments = max(8, min(96, segments))
    import math
    ring: list[tuple[float, float, float]] = []
    for i in range(segments):
        angle = 2 * math.pi * i / segments
        ring.append((radius * math.cos(angle), radius * math.sin(angle), 0))
    apex = (0.0, 0.0, height)
    base = (0.0, 0.0, 0.0)
    tris: list[tuple] = []
    for i in range(segments):
        j = (i + 1) % segments
        tris.append((ring[i], apex, ring[j]))
        tris.append((base, ring[j], ring[i]))
    return tris


def _mesh_torus(radius: float, tube: float, segments: int = 48, sides: int = 24) -> list[tuple]:
    segments = max(12, min(96, segments))
    sides = max(6, min(48, sides))
    import math
    verts: list[tuple[float, float, float]] = []
    for i in range(segments):
        u = 2 * math.pi * i / segments
        for j in range(sides):
            v = 2 * math.pi * j / sides
            x = (radius + tube * math.cos(v)) * math.cos(u)
            y = (radius + tube * math.cos(v)) * math.sin(u)
            z = tube * math.sin(v) + tube
            verts.append((x, y, z))
    tris: list[tuple] = []
    for i in range(segments):
        for j in range(sides):
            a = i * sides + j
            b = i * sides + (j + 1) % sides
            c = ((i + 1) % segments) * sides + j
            d = ((i + 1) % segments) * sides + (j + 1) % sides
            tris.append((verts[a], verts[c], verts[b]))
            tris.append((verts[b], verts[c], verts[d]))
    return tris


# --------------------------------------------------------------------------- html
class _TextExtractor(HTMLParser):
    block_tags = {
        "p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
        "section", "article", "header", "footer", "pre", "blockquote", "table",
    }
    skip_tags = {"script", "style", "noscript", "svg", "canvas", "template"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self.skip_tags:
            self._skip += 1
            return
        if tag == "title":
            self._in_title = True
        if tag in self.block_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.skip_tags and self._skip:
            self._skip -= 1
            return
        if tag == "title":
            self._in_title = False
        if tag in self.block_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        if self._in_title:
            self.title += data
            return
        if data.strip():
            self.parts.append(data)

    def text(self) -> str:
        text = "".join(self.parts)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text)
        return text.strip()


# --------------------------------------------------------------------------- main


def schema(**properties: Any) -> dict[str, Any]:
    required = [key for key, value in properties.items() if value.pop("_required", False)]
    return {"type": "object", "properties": properties, "required": required}


def prop(kind: str, description: str, *, required: bool = False, **extra: Any) -> dict[str, Any]:
    value = {"type": kind, "description": description}
    value.update(extra)
    value["_required"] = required
    return value


__all__ = [name for name in globals() if not name.startswith("__")]
