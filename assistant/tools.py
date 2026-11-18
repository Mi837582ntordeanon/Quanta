"""Toolbox for Q.W.A.N.T.A.

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
import json
import math
import os
import platform
import re
import shutil
import socket
import struct
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable

from .storage import DATA_DIR, MEDIA_DIR, Store

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


def _curl_get(url: str, timeout: float = 25.0) -> tuple[bytes, str]:
    """Certificate-safe fallback: Windows curl uses the system certificate store."""
    curl = shutil.which("curl") or r"C:\Windows\System32\curl.exe"
    if not Path(curl).exists():
        raise RuntimeError("curl.exe не найден")
    proc = subprocess.run(
        [
            curl, "-sS", "-L", "--compressed", "--max-time", str(max(5, int(timeout))),
            "-A", USER_AGENT,
            "-H", "Accept-Language: ru-RU,ru;q=0.9,en;q=0.8",
            url,
        ],
        capture_output=True,
        timeout=timeout + 8,
    )
    if proc.returncode != 0:
        error = proc.stderr.decode("utf-8", "replace").strip()
        raise RuntimeError(error or f"curl вернул код {proc.returncode}")
    return proc.stdout, ""


def _http_json(url: str, timeout: float = 15.0) -> Any:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, TimeoutError) as exc:
        if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
            raise
        raw, _ = _curl_get(url, timeout=timeout)
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
class Toolbox:
    def __init__(self, store: Store) -> None:
        self.store = store
        self.tools: dict[str, dict[str, Any]] = {}
        self._weather_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._register()

    # ------------------------------------------------------------------ registry
    def add(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        func: Callable[..., Any],
        *,
        label: str,
        risk: str = "read",
        confirm: bool = False,
    ) -> None:
        self.tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "func": func,
            "label": label,
            "risk": risk,
            "confirm": confirm,
        }

    def schemas(self, names: list[str] | set[str] | None = None) -> list[dict[str, Any]]:
        result = []
        selected_names = None if names is None else set(names)
        selected = (tool for name, tool in self.tools.items() if selected_names is None or name in selected_names)
        for tool in selected:
            result.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["parameters"],
                },
            })
        return result

    def label(self, name: str) -> str:
        return self.tools.get(name, {}).get("label", name)

    def risk(self, name: str) -> str:
        return self.tools.get(name, {}).get("risk", "high")

    def allowed(self, mode: str, name: str) -> bool:
        if name not in self.tools:
            return False
        if mode == "safe":
            return self.risk(name) in ("read", "low")
        return True

    def needs_confirm(self, mode: str, name: str) -> bool:
        if name not in self.tools:
            return False
        if mode == "auto":
            return False
        if mode == "safe":
            return False
        return bool(self.tools[name].get("confirm"))

    def execute(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        tool = self.tools.get(name)
        if not tool:
            return {"ok": False, "text": f"Неизвестный инструмент: {name}"}
        args = args if isinstance(args, dict) else {}
        func = tool["func"]
        try:
            signature = inspect.signature(func)
            accepted = {k: v for k, v in args.items() if k in signature.parameters}
            result = func(**accepted)
        except TypeError as exc:
            return {"ok": False, "text": f"Неверные аргументы инструмента {name}: {exc}"}
        except Exception as exc:  # noqa: BLE001 - tools must never crash the agent
            return {"ok": False, "text": f"Ошибка инструмента {name}: {exc}"}

        if isinstance(result, dict):
            result.setdefault("ok", True)
            if "text" not in result:
                result["text"] = json.dumps(result, ensure_ascii=False)[:4000]
            result["text"] = _truncate(str(result["text"]))
            return result
        return {"ok": True, "text": _truncate(str(result))}

    # -------------------------------------------------------------------- helpers
    def _resolve(self, path: str | None) -> Path:
        raw = str(path or "").strip().strip('"')
        if not raw:
            raw = self.store.settings.get("work_dir") or str(Path.home())
        raw = os.path.expandvars(os.path.expanduser(raw))
        p = Path(raw)
        if not p.is_absolute():
            p = Path(self.store.settings.get("work_dir") or str(Path.home())) / p
        return p.resolve()

    # ---------------------------------------------------------------------- files
    def _list_dir(self, path: str = ".", limit: int = 200) -> dict[str, Any]:
        p = self._resolve(path)
        if not p.exists():
            return {"ok": False, "text": f"Папка не найдена: {p}"}
        if not p.is_dir():
            return {"ok": False, "text": f"Это не папка: {p}"}
        entries = []
        try:
            with os.scandir(p) as it:
                for entry in it:
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        entries.append({
                            "name": entry.name,
                            "dir": entry.is_dir(follow_symlinks=False),
                            "size": stat.st_size,
                            "mtime": stat.st_mtime,
                        })
                    except OSError:
                        continue
        except OSError as exc:
            return {"ok": False, "text": f"Не удалось прочитать папку: {exc}"}
        entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
        total = len(entries)
        entries = entries[: max(1, min(int(limit or 200), 1000))]
        lines = [f"Папка: {p}", f"Элементов: {total}" + (f" (показаны первые {len(entries)})" if total > len(entries) else ""), ""]
        for entry in entries:
            try:
                stamp = datetime.fromtimestamp(entry["mtime"]).strftime("%d.%m.%Y %H:%M")
            except (OSError, ValueError):
                stamp = "—"
            if entry["dir"]:
                lines.append(f"[ПАПКА] {entry['name']}/  (изменено {stamp})")
            else:
                lines.append(f"        {entry['name']}  ({_fmt_size(entry['size'])}, {stamp})")
        return {"ok": True, "text": "\n".join(lines)}

    def _read_file(self, path: str, max_chars: int = 20000) -> dict[str, Any]:
        p = self._resolve(path)
        if not p.exists():
            return {"ok": False, "text": f"Файл не найден: {p}"}
        if not p.is_file():
            return {"ok": False, "text": f"Это не файл: {p}"}
        try:
            size = p.stat().st_size
            with p.open("rb") as fh:
                raw = fh.read(2_000_000)
        except OSError as exc:
            return {"ok": False, "text": f"Не удалось прочитать файл: {exc}"}
        if b"\x00" in raw[:4096]:
            return {"ok": False, "text": f"Похоже, это двоичный файл ({_fmt_size(size)}): {p}"}
        text = ""
        for encoding in ("utf-8-sig", "utf-8", "cp1251"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if not text:
            text = raw.decode("utf-8", "replace")
        note = ""
        if len(text) > max_chars:
            note = f"\n… [файл обрезан: показано {max_chars} из {len(text)} символов]"
            text = text[:max_chars]
        return {
            "ok": True,
            "text": f"Файл: {p}\nРазмер: {_fmt_size(size)}\n\n{text}{note}",
        }

    def _write_file(self, path: str, content: str, append: bool = False) -> dict[str, Any]:
        p = self._resolve(path)
        if p.exists() and p.is_dir():
            return {"ok": False, "text": f"Это папка, а не файл: {p}"}
        content = "" if content is None else str(content)
        created_new = not p.exists()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            mode = "a" if append else "w"
            with p.open(mode, encoding="utf-8", newline="\n") as fh:
                fh.write(content)
            undo = None
            digest = _small_file_digest(p) if created_new else None
            if digest:
                undo = {"kind": "remove_file", "path": str(p), "sha256": digest}
            return {"ok": True, "text": f"Файл {'дописан' if append else 'сохранён'}: {p}\nСимволов: {len(content)}", "_undo": undo}
        except OSError as exc:
            return {"ok": False, "text": f"Не удалось записать файл: {exc}"}

    def _search_files(self, query: str, root: str = ".", limit: int = 50) -> dict[str, Any]:
        base = self._resolve(root)
        if not base.is_dir():
            return {"ok": False, "text": f"Папка не найдена: {base}"}
        needle = (query or "").lower().strip()
        if not needle:
            return {"ok": False, "text": "Пустой поисковый запрос"}
        skip = {".git", "node_modules", "__pycache__", ".venv", "venv", "$recycle.bin",
                "windows", "winsxs", "system32", "appdata\\local\\temp"}
        found: list[Path] = []
        started = time.time()
        try:
            for dirpath, dirnames, filenames in os.walk(base):
                if time.time() - started > 12:
                    break
                dirnames[:] = [d for d in dirnames if d.lower() not in skip and not d.startswith(".")]
                for filename in filenames:
                    if needle in filename.lower() or needle in dirpath.lower():
                        found.append(Path(dirpath) / filename)
                        if len(found) >= max(1, min(int(limit or 50), 200)):
                            break
                if len(found) >= max(1, min(int(limit or 50), 200)):
                    break
        except OSError as exc:
            return {"ok": False, "text": f"Ошибка поиска: {exc}"}
        if not found:
            return {"ok": True, "text": f"Ничего не найдено по запросу «{query}» в {base}"}
        lines = [f"Найдено: {len(found)}", ""]
        for item in found:
            try:
                size = _fmt_size(item.stat().st_size)
            except OSError:
                size = "—"
            lines.append(f"- {item}  ({size})")
        return {"ok": True, "text": "\n".join(lines)}

    def _move_path(self, src: str, dst: str) -> dict[str, Any]:
        source, target = self._resolve(src), self._resolve(dst)
        if not source.exists():
            return {"ok": False, "text": f"Источник не найден: {source}"}
        if source == target:
            return {"ok": False, "text": "Источник и цель совпадают"}
        target_existed = target.exists()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            result = shutil.move(str(source), str(target))
            moved_to = Path(result).resolve()
            digest = _small_file_digest(moved_to) if not target_existed else None
            undo = ({"kind": "move_file", "source": str(moved_to), "destination": str(source), "sha256": digest}
                    if digest else None)
            return {"ok": True, "text": f"Перемещено:\n{source}\n→ {result}", "_undo": undo}
        except OSError as exc:
            return {"ok": False, "text": f"Не удалось переместить: {exc}"}

    def _copy_path(self, src: str, dst: str) -> dict[str, Any]:
        source, target = self._resolve(src), self._resolve(dst)
        if not source.exists():
            return {"ok": False, "text": f"Источник не найден: {source}"}
        target_existed = target.exists()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, target, dirs_exist_ok=True)
            else:
                shutil.copy2(source, target)
            digest = _small_file_digest(target) if not target_existed and source.is_file() else None
            undo = ({"kind": "remove_file", "path": str(target), "sha256": digest} if digest else None)
            return {"ok": True, "text": f"Скопировано:\n{source}\n→ {target}", "_undo": undo}
        except OSError as exc:
            return {"ok": False, "text": f"Не удалось скопировать: {exc}"}

    def _recycle(self, path: Path) -> bool:
        class SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND),
                ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR),
                ("fFlags", ctypes.c_uint16),
                ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", wintypes.LPCWSTR),
            ]

        FO_DELETE = 3
        FOF_SILENT = 0x0004
        FOF_NOCONFIRMATION = 0x0010
        FOF_ALLOWUNDO = 0x0040
        FOF_NOERRORUI = 0x0400
        FOF_NOCONFIRMMKDIR = 0x0200

        op = SHFILEOPSTRUCTW()
        op.wFunc = FO_DELETE
        buffer = ctypes.create_unicode_buffer(str(path) + "\0")
        op.pFrom = ctypes.cast(buffer, wintypes.LPCWSTR)
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI | FOF_NOCONFIRMMKDIR
        try:
            result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
            return result == 0
        except Exception:
            return False

    def undo_action(self, action_id: str) -> dict[str, Any]:
        action = self.store.get_action(action_id)
        if not action:
            return {"ok": False, "text": "Действие не найдено"}
        if not action.get("ok") or action.get("undone"):
            return {"ok": False, "text": "Это действие уже отменено или не выполнено"}
        undo = action.get("undo") or {}
        kind = undo.get("kind")
        try:
            if kind == "remove_note":
                ok = self.store.delete_note(str(undo.get("id") or ""))
                if not ok: raise ValueError("Заметка уже отсутствует")
            elif kind == "remove_reminder":
                ok = self.store.delete_reminder(str(undo.get("id") or ""))
                if not ok: raise ValueError("Напоминание уже отсутствует")
            elif kind == "remove_memory":
                ok = self.store.delete_memory(str(undo.get("id") or ""))
                if not ok: raise ValueError("Запись памяти уже отсутствует")
            elif kind == "remove_project":
                ok = self.store.delete_project(str(undo.get("id") or ""))
                if not ok: raise ValueError("Проект уже отсутствует")
            elif kind == "remove_file":
                path = Path(str(undo.get("path") or "")).resolve()
                if not path.is_file() or _small_file_digest(path) != undo.get("sha256"):
                    raise ValueError("Файл изменился после создания; отмену не выполняю")
                if not self._recycle(path): raise OSError("Не удалось отправить новый файл в Корзину")
            elif kind == "move_file":
                source = Path(str(undo.get("source") or "")).resolve()
                destination = Path(str(undo.get("destination") or "")).resolve()
                if not source.is_file() or _small_file_digest(source) != undo.get("sha256"):
                    raise ValueError("Перемещённый файл изменился; отмену не выполняю")
                if destination.exists(): raise ValueError("Исходный путь уже занят; файл не перезаписан")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
            else:
                raise ValueError("Для этого действия безопасная отмена недоступна")
        except (OSError, ValueError) as exc:
            return {"ok": False, "text": str(exc)}
        self.store.mark_action_undone(action_id)
        return {"ok": True, "text": "Действие отменено"}

    def _delete_path(self, path: str) -> dict[str, Any]:
        p = self._resolve(path)
        if not p.exists():
            return {"ok": False, "text": f"Путь не найден: {p}"}
        if p == Path(p.anchor) or p == Path.home():
            return {"ok": False, "text": "Отказано: нельзя удалять корень диска или домашнюю папку"}
        if self._recycle(p):
            return {"ok": True, "text": f"Перемещено в Корзину: {p}"}
        return {"ok": False, "text": f"Не удалось переместить в Корзину: {p} (файл не удалён)"}

    # ------------------------------------------------------------------- commands
    def _run_command(self, command: str, cwd: str = ".", shell: str = "cmd", timeout: int = 60) -> dict[str, Any]:
        work = self._resolve(cwd)
        if not work.is_dir():
            return {"ok": False, "text": f"Рабочая папка не найдена: {work}"}
        try:
            timeout = max(1, min(300, int(timeout or 60)))
        except (TypeError, ValueError):
            timeout = 60
        if shell == "powershell":
            args = [
                "powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-Command", "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + str(command),
            ]
        else:
            args = ["cmd", "/c", "chcp 65001>nul & " + str(command)]
        try:
            proc = subprocess.run(
                args, cwd=str(work), capture_output=True, timeout=timeout,
                creationflags=CREATE_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            return {"ok": False, "text": f"Команда не завершилась за {timeout} сек. и была остановлена."}
        except OSError as exc:
            return {"ok": False, "text": f"Не удалось запустить команду: {exc}"}
        stdout = _decode_console(proc.stdout)
        stderr = _decode_console(proc.stderr)
        parts = [f"Команда: {command}", f"Код выхода: {proc.returncode}"]
        if stdout.strip():
            parts.append("\nSTDOUT:\n" + stdout.strip())
        if stderr.strip():
            parts.append("\nSTDERR:\n" + stderr.strip())
        if not stdout.strip() and not stderr.strip():
            parts.append("(вывод пуст)")
        return {"ok": proc.returncode == 0, "text": _truncate("\n".join(parts), 14000)}

    def _open_path(self, path: str) -> dict[str, Any]:
        raw = (path or "").strip()
        if raw.startswith(("http://", "https://")):
            import webbrowser
            webbrowser.open(raw)
            return {"ok": True, "text": f"Открыто в браузере: {raw}"}
        p = self._resolve(raw)
        if not p.exists():
            return {"ok": False, "text": f"Путь не найден: {p}"}
        try:
            os.startfile(str(p))  # type: ignore[attr-defined]
            return {"ok": True, "text": f"Открыто: {p}"}
        except OSError as exc:
            return {"ok": False, "text": f"Не удалось открыть: {exc}"}

    # ------------------------------------------------------------------- screen
    def _screenshot(self) -> dict[str, Any]:
        try:
            width, height, rows = self._capture_screen_rgb()
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "text": f"Не удалось сделать скриншот: {exc}"}
        png = self._png_encode(width, height, rows)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.store.media_dir("screenshots") / f"screen_{stamp}.png"
        path.write_bytes(png)
        return {
            "ok": True,
            "text": f"Скриншот сохранён: {path}\nРазмер: {width}×{height}",
            "media": [{"type": "image", "url": self.store.media_url(path)}],
        }

    def _capture_screen_rgb(self) -> tuple[int, int, list[bytes]]:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
        x = user32.GetSystemMetrics(76)
        y = user32.GetSystemMetrics(77)
        width = user32.GetSystemMetrics(78)
        height = user32.GetSystemMetrics(79)
        if width <= 0 or height <= 0:
            raise RuntimeError("не удалось определить размер экрана")

        user32.GetDC.argtypes = [wintypes.HWND]
        user32.GetDC.restype = wintypes.HDC
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteObject.restype = wintypes.BOOL
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        gdi32.DeleteDC.restype = wintypes.BOOL
        gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
        gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT,
                                    ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]

        hdc = user32.GetDC(None)
        mem = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, width, height)
        old = gdi32.SelectObject(mem, bmp)
        try:
            gdi32.BitBlt(mem, 0, 0, width, height, hdc, x, y, 0x00CC0020)
            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [
                    ("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                    ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                    ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                    ("biClrImportant", wintypes.DWORD),
                ]
            bi = BITMAPINFOHEADER()
            bi.biSize = ctypes.sizeof(bi)
            bi.biWidth = width
            bi.biHeight = -height
            bi.biPlanes = 1
            bi.biBitCount = 32
            bi.biCompression = 0
            buffer = ctypes.create_string_buffer(width * height * 4)
            gdi32.GetDIBits(mem, bmp, 0, height, buffer, ctypes.byref(bi), 0)
            raw = memoryview(buffer)
            stride = width * 4
            rows: list[bytes] = []
            for row_index in range(height):
                row = raw[row_index * stride:(row_index + 1) * stride]
                rgb = bytearray(width * 3)
                rgb[0::3] = row[2::4]
                rgb[1::3] = row[1::4]
                rgb[2::3] = row[0::4]
                rows.append(bytes(rgb))
            return width, height, rows
        finally:
            gdi32.SelectObject(mem, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(None, hdc)

    @staticmethod
    def _png_encode(width: int, height: int, rows: list[bytes]) -> bytes:
        def chunk(tag: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        raw = b"".join(b"\x00" + row for row in rows)
        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6))
                + chunk(b"IEND", b""))

    # -------------------------------------------------------------------- input
    def _mouse_click(self, x: int, y: int, button: str = "left", double: bool = False) -> dict[str, Any]:
        user32 = ctypes.windll.user32
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.05)
        flags = {
            "left": (0x0002, 0x0004),
            "right": (0x0008, 0x0010),
            "middle": (0x0020, 0x0040),
        }.get(str(button).lower(), (0x0002, 0x0004))
        count = 2 if double else 1
        for _ in range(count):
            user32.mouse_event(flags[0], 0, 0, 0, 0)
            time.sleep(0.02)
            user32.mouse_event(flags[1], 0, 0, 0, 0)
            time.sleep(0.08)
        return {"ok": True, "text": f"Клик {button}{' (двойной)' if double else ''} в ({x}, {y})"}

    def _type_text(self, text: str) -> dict[str, Any]:
        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]
        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]
        class INPUTUNION(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]
        class INPUT(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", wintypes.DWORD), ("u", INPUTUNION)]

        user32 = ctypes.windll.user32
        user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
        user32.SendInput.restype = wintypes.UINT
        KEYEVENTF_UNICODE = 0x0004
        KEYEVENTF_KEYUP = 0x0002

        def send(vk: int, scan: int, flags: int) -> None:
            inp = INPUT()
            inp.type = 1  # INPUT_KEYBOARD
            inp.ki = KEYBDINPUT(wVk=vk, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))

        value = str(text or "")
        units = value.encode("utf-16-le")
        for offset in range(0, len(units), 2):
            unit = struct.unpack_from("<H", units, offset)[0]
            if unit == 0x000D:
                send(0x0D, 0, 0)
                send(0x0D, 0, KEYEVENTF_KEYUP)
            elif unit == 0x000A:
                continue
            else:
                send(0, unit, KEYEVENTF_UNICODE)
                send(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)
        return {"ok": True, "text": f"Введено символов: {len(value)}"}

    def _press_keys(self, keys: str) -> dict[str, Any]:
        vk_map = {
            "ctrl": 0x11, "control": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B,
            "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B, "escape": 0x1B,
            "space": 0x20, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D,
            "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
            "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
            "minus": 0xBD, "equal": 0xBB, "plus": 0xBB,
            "comma": 0xBC, "period": 0xBE, "slash": 0xBF,
            "semicolon": 0xBA, "quote": 0xDE, "backtick": 0xC0,
            "lbracket": 0xDB, "rbracket": 0xDD, "backslash": 0xDC,
        }
        for i in range(1, 13):
            vk_map[f"f{i}"] = 0x6F + i
        tokens = [token.strip().lower() for token in str(keys or "").split("+") if token.strip()]
        if not tokens:
            return {"ok": False, "text": "Не указаны клавиши"}
        codes: list[int] = []
        for token in tokens:
            if len(token) == 1 and token.isalnum():
                code = ord(token.upper())
            elif token in vk_map:
                code = vk_map[token]
            else:
                return {"ok": False, "text": f"Неизвестная клавиша: {token}"}
            codes.append(code)
        user32 = ctypes.windll.user32
        user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
        for code in codes:
            user32.keybd_event(code, 0, 0, 0)
        time.sleep(0.05)
        for code in reversed(codes):
            user32.keybd_event(code, 0, 0x0002, 0)
        return {"ok": True, "text": f"Нажато: {keys}"}

    def _volume(self, action: str = "up", steps: int = 2) -> dict[str, Any]:
        vk = {"up": 0xAF, "down": 0xAE, "mute": 0xAD}.get(str(action).lower(), 0xAF)
        steps = max(1, min(10, int(steps or 2)))
        user32 = ctypes.windll.user32
        count = 1 if vk == 0xAD else steps
        for _ in range(count):
            user32.keybd_event(vk, 0, 0, 0)
            user32.keybd_event(vk, 0, 0x0002, 0)
            time.sleep(0.03)
        return {"ok": True, "text": f"Громкость: {action} × {count}"}

    # ----------------------------------------------------------------- clipboard
    def _clipboard_get(self) -> dict[str, Any]:
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                 "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-Clipboard -Raw"],
                capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "text": f"Не удалось прочитать буфер обмена: {exc}"}
        text = _decode_console(proc.stdout)
        if not text.strip():
            return {"ok": True, "text": "Буфер обмена пуст (или содержит не текст)."}
        return {"ok": True, "text": _truncate(text, 12000)}

    def _clipboard_set(self, text: str) -> dict[str, Any]:
        encoded = base64.b64encode(str(text or "").encode("utf-8")).decode("ascii")
        command = (
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
            f"$t=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{encoded}')); "
            "Set-Clipboard -Value $t"
        )
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "text": f"Не удалось записать в буфер обмена: {exc}"}
        if proc.returncode != 0:
            return {"ok": False, "text": "Не удалось записать в буфер обмена."}
        return {"ok": True, "text": f"В буфер обмена записано символов: {len(str(text or ''))}"}

    # ------------------------------------------------------------------ internet
    def _web_search(self, query: str, limit: int = 5) -> dict[str, Any]:
        query = (query or "").strip()
        if not query:
            return {"ok": False, "text": "Пустой поисковый запрос"}
        limit = max(1, min(10, int(limit or 5)))
        url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote_plus(query)
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                },
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                page = resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            try:
                page = _curl_get(url, timeout=20)[0].decode("utf-8", "replace")
            except Exception:
                return {"ok": False, "text": f"Поиск недоступен: {exc}"}

        links = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.S)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', page, re.S)
        if not links:
            lite_url = "https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote_plus(query)
            try:
                req = urllib.request.Request(
                    lite_url,
                    headers={"User-Agent": USER_AGENT, "Accept-Language": "ru-RU,ru;q=0.9"},
                )
                with urllib.request.urlopen(req, timeout=20) as resp:
                    lite_page = resp.read().decode("utf-8", "replace")
                links = re.findall(
                    r'<a[^>]+href="([^"]+)"[^>]+class=["\']result-link["\'][^>]*>(.*?)</a>',
                    lite_page, re.S,
                )
                snippets = re.findall(r'<td[^>]+class=["\']result-snippet["\'][^>]*>(.*?)</td>', lite_page, re.S)
            except Exception:
                pass
        results = []
        for index, (href, title) in enumerate(links):
            href = html.unescape(href)
            if href.startswith("//"):
                href = "https:" + href
            if "uddg=" in href:
                try:
                    parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                    href = parsed.get("uddg", [href])[0]
                except Exception:
                    pass
            snippet = _strip_tags(snippets[index]) if index < len(snippets) else ""
            results.append((_strip_tags(title), href, snippet))
            if len(results) >= limit:
                break
        if not results:
            return {
                "ok": False,
                "text": "DuckDuckGo не вернул результаты (возможно, временно блокирует запросы). "
                        "Попробуй открыть страницу напрямую через fetch_url.",
            }
        lines = [f"Результаты поиска: {query}", ""]
        for index, (title, href, snippet) in enumerate(results, 1):
            lines.append(f"{index}. {title}\n   {href}")
            if snippet:
                lines.append(f"   {snippet[:300]}")
        return {"ok": True, "text": "\n".join(lines)}

    def _fetch_url(self, url: str, max_chars: int = 12000) -> dict[str, Any]:
        url = (url or "").strip()
        if not url.startswith(("http://", "https://")):
            return {"ok": False, "text": "URL должен начинаться с http:// или https://"}
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain,*/*"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                content_type = resp.headers.get("Content-Type", "")
                raw = resp.read(2_000_000)
        except (urllib.error.URLError, TimeoutError) as exc:
            if "CERTIFICATE_VERIFY_FAILED" not in str(exc):
                return {"ok": False, "text": f"Не удалось загрузить страницу: {exc}"}
            try:
                raw, content_type = _curl_get(url, timeout=25)
            except Exception as curl_exc:
                return {"ok": False, "text": f"Не удалось загрузить страницу: {curl_exc}"}
        if b"\0" in raw[:4096]:
            return {"ok": False, "text": "Это не текстовая страница."}
        if content_type and not any(token in content_type for token in ("text", "html", "json", "xml")):
            return {"ok": False, "text": f"Это не текстовая страница ({content_type})."}
        text = ""
        for encoding in ("utf-8", "cp1251"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if not text:
            text = raw.decode("utf-8", "replace")
        if "html" in content_type or text.lstrip().startswith("<"):
            parser = _TextExtractor()
            try:
                parser.feed(text)
                title = parser.title.strip()
                text = parser.text()
            except Exception:
                text = _strip_tags(text)
            header = f"Страница: {url}"
            if title:
                header += f"\nЗаголовок: {title}"
            text = header + "\n\n" + text
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n… [обрезано, всего {len(text)} символов]"
        return {"ok": True, "text": text}

    # -------------------------------------------------------------------- memory
    def _memory_save(self, text: str) -> dict[str, Any]:
        item = self.store.create_memory(text)
        return {"ok": True, "text": "Запись добавлена в локальную память.",
                "_undo": {"kind": "remove_memory", "id": item["id"]}}

    def _memory_list(self, query: str = "") -> dict[str, Any]:
        items = self.store.list_memory(query)
        if not items:
            return {"ok": True, "text": "Подходящих записей памяти нет."}
        return {"ok": True, "text": "Локальная память:\n" + "\n".join(f"- {item['id']}: {item['text']}" for item in items[:30])}

    def _memory_delete(self, identifier: str) -> dict[str, Any]:
        item = self.store.find_memory(identifier)
        if not item:
            return {"ok": False, "text": "Не нашла однозначную запись памяти; уточни текст или id."}
        if not self.store.delete_memory(item["id"]):
            return {"ok": False, "text": "Запись уже отсутствует."}
        return {"ok": True, "text": "Запись удалена из локальной памяти."}

    # ------------------------------------------------------------------- projects
    @staticmethod
    def _project_card_text(project: dict[str, Any]) -> str:
        status = {"active": "в работе", "paused": "на паузе", "done": "завершён", "archived": "архив"}.get(
            str(project.get("status") or "active"), str(project.get("status") or "active")
        )
        lines = [
            f"{project.get('title') or 'Проект'} (id {project.get('id')}, статус: {status})",
            f"Цель: {project.get('goal') or 'не указана'}",
            f"Следующий шаг: {project.get('next_step') or 'не задан'}",
        ]
        if project.get("summary"):
            lines.append(f"Контекст: {str(project['summary'])[:900]}")
        if project.get("decisions"):
            lines.append("Решения: " + "; ".join(str(item) for item in project["decisions"][:8]))
        if project.get("files"):
            lines.append("Файлы: " + "; ".join(str(item) for item in project["files"][:8]))
        return "\n".join(lines)

    def _project_list(self, query: str = "", include_archived: bool = False) -> dict[str, Any]:
        projects = self.store.list_projects(include_archived=bool(include_archived), query=query)
        if not projects:
            return {"ok": True, "text": "Сохранённых проектов нет." if not query else f"Проекты по запросу «{query}» не найдены."}
        return {"ok": True, "text": "Проекты:\n\n" + "\n\n".join(self._project_card_text(item) for item in projects[:30])}

    def _project_create(
        self,
        title: str,
        goal: str,
        next_step: str = "",
        summary: str = "",
        decisions: str = "",
        files: str = "",
        _current_chat_id: str = "",
    ) -> dict[str, Any]:
        project = self.store.create_project(
            title, goal, next_step=next_step, summary=summary,
            decisions=decisions, files=files,
        )
        if _current_chat_id:
            self.store.link_chat_to_project(project["id"], _current_chat_id)
            project = self.store.get_project(project["id"])
        return {
            "ok": True,
            "text": "Создала карточку проекта и привязала текущий чат.\n" + self._project_card_text(project),
            "_undo": {"kind": "remove_project", "id": project["id"]},
        }

    def _project_update(
        self,
        identifier: str,
        title: str | None = None,
        goal: str | None = None,
        status: str | None = None,
        next_step: str | None = None,
        summary: str | None = None,
        add_decision: str | None = None,
        add_file: str | None = None,
    ) -> dict[str, Any]:
        project = self.store.find_project(identifier)
        if not project:
            return {"ok": False, "text": "Не нашла однозначный проект. Уточни название или id."}
        changes = {key: value for key, value in {
            "title": title, "goal": goal, "status": status, "next_step": next_step,
            "summary": summary, "add_decision": add_decision, "add_file": add_file,
        }.items() if value is not None}
        updated = self.store.update_project(project["id"], **changes)
        return {"ok": True, "text": "Карточку проекта обновила.\n" + self._project_card_text(updated)}

    # -------------------------------------------------------------------- notes
    def _notes_create(self, title: str, text: str = "") -> dict[str, Any]:
        note = self.store.create_note(title, text)
        return {"ok": True, "text": f"Заметка создана: «{note['title']}» (id {note['id']})\n\n{note['text']}",
                "_undo": {"kind": "remove_note", "id": note["id"]}}

    def _notes_list(self, query: str = "") -> dict[str, Any]:
        notes = self.store.list_notes(query)
        if not notes:
            return {"ok": True, "text": "Заметок нет." if not query else f"По запросу «{query}» ничего не найдено."}
        lines = [f"Заметки ({len(notes)}):", ""]
        for note in notes:
            preview = re.sub(r"\s+", " ", note.get("text", ""))[:160]
            lines.append(f"• {note['title']} (id {note['id']})\n  {preview}")
        return {"ok": True, "text": "\n".join(lines)}

    def _notes_update(self, identifier: str, title: str | None = None, text: str | None = None) -> dict[str, Any]:
        note = self.store.find_note(identifier)
        if not note:
            return {"ok": False, "text": f"Заметка не найдена: {identifier}"}
        updated = self.store.update_note(note["id"], title=title, text=text)
        return {"ok": True, "text": f"Заметка обновлена: «{updated['title']}»"}

    def _notes_delete(self, identifier: str) -> dict[str, Any]:
        note = self.store.find_note(identifier)
        if not note:
            return {"ok": False, "text": f"Заметка не найдена: {identifier}"}
        self.store.delete_note(note["id"])
        return {"ok": True, "text": f"Заметка удалена: «{note['title']}»"}

    # ---------------------------------------------------------------- reminders
    @staticmethod
    def _parse_when(value: str) -> datetime:
        raw = (value or "").strip().lower().replace("ё", "е")
        now = datetime.now()
        match = re.search(r"через\s+(\d+)\s*(минут|мин|час|ч|дн|день|дня|недел|неделю|недели)", raw)
        if match:
            amount = int(match.group(1))
            unit = match.group(2)
            if unit.startswith("мин"):
                return now + timedelta(minutes=amount)
            if unit.startswith("ч"):
                return now + timedelta(hours=amount)
            if unit.startswith("недел"):
                return now + timedelta(weeks=amount)
            return now + timedelta(days=amount)
        if "полчаса" in raw:
            return now + timedelta(minutes=30)
        if re.search(r"через\s+час", raw):
            return now + timedelta(hours=1)
        if re.search(r"через\s+полтора\s+часа", raw):
            return now + timedelta(minutes=90)
        match = re.search(r"(\d{4})-(\d{2})-(\d{2})[ t](\d{1,2}):(\d{2})", raw)
        if match:
            return datetime(*[int(part) for part in match.groups()])
        match = re.search(r"(сегодня|завтра|послезавтра)?\s*(?:в\s*)?(\d{1,2})[:.](\d{2})", raw)
        if match:
            word, hour, minute = match.groups()
            target = now.replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
            if word == "завтра":
                target += timedelta(days=1)
            elif word == "послезавтра":
                target += timedelta(days=2)
            elif word is None and target <= now:
                target += timedelta(days=1)
            return target
        if "завтра" in raw:
            return (now + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
        try:
            return datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(
                "Не понял время. Примеры: «через 30 минут», «завтра в 9:00», "
                "«сегодня в 18:30», «2026-11-12 15:00»."
            ) from exc

    def _reminder_create(self, text: str, when: str) -> dict[str, Any]:
        due = self._parse_when(when)
        reminder = self.store.create_reminder(text, due)
        return {
            "ok": True,
            "text": f"Напоминание создано: «{reminder['text']}»\nКогда: {due.strftime('%d.%m.%Y %H:%M')}",
            "_undo": {"kind": "remove_reminder", "id": reminder["id"]},
        }

    def _reminder_list(self) -> dict[str, Any]:
        reminders = self.store.list_reminders()
        if not reminders:
            return {"ok": True, "text": "Активных напоминаний нет."}
        lines = [f"Напоминания ({len(reminders)}):", ""]
        for reminder in reminders:
            try:
                due = datetime.fromisoformat(reminder["due"]).strftime("%d.%m.%Y %H:%M")
            except ValueError:
                due = reminder.get("due", "—")
            lines.append(f"• {due} — {reminder['text']} (id {reminder['id']})")
        return {"ok": True, "text": "\n".join(lines)}

    def _reminder_delete(self, reminder_id: str) -> dict[str, Any]:
        if self.store.delete_reminder(reminder_id):
            return {"ok": True, "text": "Напоминание удалено."}
        return {"ok": False, "text": "Напоминание не найдено."}

    # --------------------------------------------------------------------- 3d
    def _create_3d_model(
        self,
        name: str,
        shape: str,
        width: float = 50.0,
        depth: float = 50.0,
        height: float = 50.0,
        radius: float = 25.0,
        tube_radius: float = 8.0,
        segments: int = 32,
    ) -> dict[str, Any]:
        shape = (shape or "box").lower()
        safe_name = re.sub(r"[^\w\-. ]", "_", (name or "model").strip(), flags=re.UNICODE)
        safe_name = safe_name.strip(". ") or "model"
        segments = max(8, min(96, int(segments or 32)))
        try:
            if shape == "box":
                w = max(0.5, min(2000, float(width)))
                d = max(0.5, min(2000, float(depth)))
                h = max(0.5, min(2000, float(height)))
                triangles = _mesh_box(w, d, h)
                label = f"куб {w}×{d}×{h} мм"
            elif shape == "sphere":
                r = max(0.5, min(1000, float(radius)))
                triangles = _mesh_sphere(r, segments=segments, rings=max(8, segments // 2))
                label = f"сфера R{r} мм"
            elif shape == "cylinder":
                r = max(0.5, min(1000, float(radius)))
                h = max(0.5, min(2000, float(height)))
                triangles = _mesh_cylinder(r, h, segments=segments)
                label = f"цилиндр R{r}, H{h} мм"
            elif shape == "cone":
                r = max(0.5, min(1000, float(radius)))
                h = max(0.5, min(2000, float(height)))
                triangles = _mesh_cone(r, h, segments=segments)
                label = f"конус R{r}, H{h} мм"
            elif shape == "torus":
                r = max(1.0, min(1000, float(radius)))
                tube = max(0.5, min(r * 2, float(tube_radius)))
                triangles = _mesh_torus(r, tube, segments=max(16, segments), sides=max(8, segments // 2))
                label = f"тор R{r}, r{tube} мм"
            else:
                return {"ok": False, "text": "Неизвестная фигура. Доступно: box, sphere, cylinder, cone, torus."}
        except (TypeError, ValueError) as exc:
            return {"ok": False, "text": f"Некорректные размеры: {exc}"}

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.store.media_dir("models") / f"{safe_name}_{stamp}.stl"
        _stl_write(path, triangles, safe_name)
        return {
            "ok": True,
            "text": (f"3D-модель создана: {label}\nФайл: {path}\n"
                     f"Треугольников: {len(triangles)}\nФормат: STL (бинарный), единицы: мм"),
            "media": [{"type": "file", "url": self.store.media_url(path), "name": path.name}],
        }

    # ------------------------------------------------------------------ images
    def _generate_image(self, prompt: str, width: int = 1024, height: int = 1024) -> dict[str, Any]:
        prompt = (prompt or "").strip()
        if not prompt:
            return {"ok": False, "text": "Пустой запрос на изображение"}
        width = max(256, min(1536, int(width or 1024)))
        height = max(256, min(1536, int(height or 1024)))
        encoded = urllib.parse.quote(prompt, safe="")
        url = (f"https://image.pollinations.ai/prompt/{encoded}"
               f"?width={width}&height={height}&nologo=true&model=flux&seed={int(time.time()) % 1000000}")
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                content_type = resp.headers.get("Content-Type", "image/jpeg")
                data = resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {"ok": False, "text": f"Не удалось сгенерировать изображение: {exc}"}
        if len(data) < 1000 or not content_type.startswith("image"):
            return {"ok": False, "text": "Сервис генерации вернул не изображение. Попробуй позже."}
        ext = "png" if "png" in content_type else "webp" if "webp" in content_type else "jpg"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.store.media_dir("images") / f"quanta_{stamp}.{ext}"
        path.write_bytes(data)
        return {
            "ok": True,
            "text": f"Изображение сгенерировано: {path}\nПромпт: {prompt}",
            "media": [{"type": "image", "url": self.store.media_url(path)}],
        }

    # ------------------------------------------------------------------- system
    def _system_info(self) -> dict[str, Any]:
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]
        mem = MEMORYSTATUSEX()
        mem.dwLength = ctypes.sizeof(mem)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
        total_ram = mem.ullTotalPhys / (1024 ** 3)
        used_ram = (mem.ullTotalPhys - mem.ullAvailPhys) / (1024 ** 3)
        try:
            disk = shutil.disk_usage(str(Path.home().anchor or "C:\\"))
            disk_text = f"{disk.used / 1024**3:.1f} ГБ из {disk.total / 1024**3:.1f} ГБ"
        except OSError:
            disk_text = "—"
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.GetTickCount64.restype = ctypes.c_ulonglong
            uptime_seconds = kernel32.GetTickCount64() / 1000
            uptime = f"{int(uptime_seconds // 86400)} д {int((uptime_seconds % 86400) // 3600)} ч"
        except Exception:
            uptime = "—"
        lines = [
            f"ОС: {platform.platform()}",
            f"Компьютер: {platform.node()}",
            f"Пользователь: {os.getenv('USERNAME', '—')}",
            f"Процессор: {platform.processor() or '—'} ({os.cpu_count()} логических ядер)",
            f"ОЗУ: {used_ram:.1f} / {total_ram:.1f} ГБ ({mem.dwMemoryLoad}%)",
            f"Диск {Path.home().anchor}: {disk_text}",
            f"Время работы: {uptime}",
            f"Текущее время: {datetime.now():%H:%M}",
            f"Python: {platform.python_version()}",
        ]
        return {"ok": True, "text": "\n".join(lines)}

    def _list_processes(self, filter_text: str = "", limit: int = 50) -> dict[str, Any]:
        try:
            proc = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True, timeout=20, creationflags=CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "text": f"Не удалось получить список процессов: {exc}"}
        text = _decode_console(proc.stdout)
        needle = (filter_text or "").lower().strip()
        rows = []
        for row in csv.reader(text.splitlines()):
            if len(row) < 4:
                continue
            if needle and needle not in row[0].lower():
                continue
            rows.append(row)
        rows = rows[: max(1, min(int(limit or 50), 200))]
        if not rows:
            return {"ok": True, "text": "Процессы не найдены."}
        lines = ["Имя | PID | Память", ""]
        for row in rows:
            lines.append(f"{row[0]} | {row[1]} | {row[4] if len(row) > 4 else '—'}")
        return {"ok": True, "text": "\n".join(lines)}

    def _kill_process(self, pid_or_name: str, force: bool = False) -> dict[str, Any]:
        target = str(pid_or_name or "").strip()
        if not target:
            return {"ok": False, "text": "Укажи PID или имя процесса"}
        args = ["taskkill", "/PID" if target.isdigit() else "/IM", target]
        if force:
            args.append("/F")
        try:
            proc = subprocess.run(args, capture_output=True, timeout=20, creationflags=CREATE_NO_WINDOW)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "text": f"Не удалось завершить процесс: {exc}"}
        output = _decode_console(proc.stdout + proc.stderr).strip()
        return {"ok": proc.returncode == 0, "text": output or f"Код выхода: {proc.returncode}"}

    # ----------------------------------------------------------------- minecraft
    @staticmethod
    def _mc_varint(value: int) -> bytes:
        value &= 0xFFFFFFFF
        result = bytearray()
        while True:
            byte = value & 0x7F
            value >>= 7
            if value:
                result.append(byte | 0x80)
            else:
                result.append(byte)
                return bytes(result)

    @staticmethod
    def _mc_string(value: str) -> bytes:
        data = value.encode("utf-8")
        return Toolbox._mc_varint(len(data)) + data

    @staticmethod
    def _mc_read_varint(sock: socket.socket) -> int:
        value = 0
        shift = 0
        while True:
            raw = sock.recv(1)
            if not raw:
                raise ConnectionError("соединение закрыто")
            byte = raw[0]
            value |= (byte & 0x7F) << shift
            if not byte & 0x80:
                return value
            shift += 7
            if shift > 35:
                raise ValueError("некорректный varint")

    @staticmethod
    def _mc_read_exact(sock: socket.socket, count: int) -> bytes:
        chunks = []
        while count > 0:
            chunk = sock.recv(count)
            if not chunk:
                raise ConnectionError("соединение закрыто")
            chunks.append(chunk)
            count -= len(chunk)
        return b"".join(chunks)

    @staticmethod
    def _flatten_motd(description: Any) -> str:
        if isinstance(description, str):
            return description
        if isinstance(description, dict):
            text = str(description.get("text", ""))
            for extra in description.get("extra", []) or []:
                text += Toolbox._flatten_motd(extra)
            return text
        return str(description or "")

    def _minecraft_status(self, host: str = "127.0.0.1", port: int = 25565) -> dict[str, Any]:
        host = (host or "127.0.0.1").strip()
        if ":" in host and port == 25565:
            host, port_text = host.rsplit(":", 1)
            try:
                port = int(port_text)
            except ValueError:
                pass
        try:
            port = int(port or 25565)
        except (TypeError, ValueError):
            port = 25565
        started = time.time()
        try:
            with socket.create_connection((host, port), timeout=8) as sock:
                payload = (Toolbox._mc_varint(0x00) + Toolbox._mc_varint(0xFFFFFFFF)
                           + Toolbox._mc_string(host) + struct.pack(">H", port) + Toolbox._mc_varint(1))
                sock.sendall(Toolbox._mc_varint(len(payload)) + payload)
                sock.sendall(Toolbox._mc_varint(1) + Toolbox._mc_varint(0x00))
                length = Toolbox._mc_read_varint(sock)
                packet = Toolbox._mc_read_exact(sock, length)
                offset = 0
                # packet id
                while True:
                    byte = packet[offset]
                    offset += 1
                    if not byte & 0x80:
                        break
                str_length = 0
                shift = 0
                while True:
                    byte = packet[offset]
                    offset += 1
                    str_length |= (byte & 0x7F) << shift
                    if not byte & 0x80:
                        break
                    shift += 7
                data = json.loads(packet[offset:offset + str_length].decode("utf-8", "replace"))
        except (OSError, TimeoutError, ValueError, ConnectionError) as exc:
            return {"ok": False, "text": f"Сервер Minecraft {host}:{port} недоступен: {exc}"}
        latency = int((time.time() - started) * 1000)
        version = (data.get("version") or {}).get("name", "?")
        players = data.get("players") or {}
        motd = self._flatten_motd(data.get("description"))
        lines = [
            f"Minecraft-сервер: {host}:{port}",
            f"Версия: {version}",
            f"Игроки: {players.get('online', 0)} / {players.get('max', 0)}",
            f"MOTD: {motd}",
            f"Задержка: {latency} мс",
        ]
        sample = players.get("sample") or []
        if sample:
            names = ", ".join(str(item.get("name", "")) for item in sample[:8])
            lines.append(f"Пример игроков: {names}")
        return {"ok": True, "text": "\n".join(lines)}

    # -------------------------------------------------------------------- weather
    WEATHER_CODES = {
        0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "облачно",
        45: "туман", 48: "изморозь и туман", 51: "лёгкая морось", 53: "морось",
        55: "сильная морось", 56: "лёгкая ледяная морось", 57: "ледяная морось",
        61: "лёгкий дождь", 63: "дождь", 65: "сильный дождь",
        66: "лёгкий ледяной дождь", 67: "ледяной дождь",
        71: "лёгкий снег", 73: "снег", 75: "сильный снег", 77: "снежные зёрна",
        80: "лёгкий ливень", 81: "ливень", 82: "сильный ливень",
        85: "снегопад", 86: "сильный снегопад", 95: "гроза",
        96: "гроза с лёгким градом", 99: "гроза с сильным градом",
    }

    @staticmethod
    def _condition_code(condition: str) -> int:
        value = (condition or "").lower()
        if "гроза" in value:
            return 95
        if "снег" in value or "метел" in value:
            return 71
        if "ливень" in value:
            return 80
        if "дождь" in value:
            return 61
        if "морос" in value:
            return 51
        if "туман" in value or "дымка" in value:
            return 45
        if "пасмур" in value or "облач" in value:
            return 3
        if "ясно" in value or "солнеч" in value:
            return 0
        return -1

    def _yandex_weather(self, city: str) -> dict[str, Any]:
        """Yandex is a reliable fallback when Open-Meteo is unreachable."""
        slug = "krasnodar" if city.lower().replace("ё", "е") == "краснодар" else urllib.parse.quote(city.lower().replace(" ", "-"))
        req = urllib.request.Request(
            "https://yandex.ru/pogoda/" + slug,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
        )
        with urllib.request.urlopen(req, timeout=9) as resp:
            page = resp.read(2_000_000).decode("utf-8", "replace")
        hidden = re.search(r"погода сейчас:\s*(.*?)</p>", page, re.S)
        if not hidden:
            raise ValueError("Яндекс не отдал текущую погоду")
        text = re.sub(r"<[^>]+>", " ", hidden.group(1))
        text = html.unescape(text)
        temp_match = re.search(r"Температура воздуха\s*([+-]?\d+)°", text)
        if not temp_match:
            raise ValueError("не найдена температура")
        feels_match = re.search(r"ощущается как\s*([+-]?\d+)°", text)
        wind_match = re.search(r"Скорость ветра\s*([\d.,]+)\s*м/с", text)
        humidity_match = re.search(r"Влажность\s*(\d+)%", text)
        condition_match = re.search(r"^\s*([^.]+)\.", text)
        condition = (condition_match.group(1).strip() if condition_match else "—").strip(" ,")
        condition = re.sub(r"\s+", " ", condition)
        day_match = re.search(r"Днём.*?AppForecastDayPart_temp[^>]*>\s*([+-]?\d+)°", page, re.S)
        night_match = re.search(r"Ночью.*?AppForecastDayPart_temp[^>]*>\s*([+-]?\d+)°", page, re.S)
        temp = int(temp_match.group(1))
        feels = int(feels_match.group(1)) if feels_match else None
        wind = wind_match.group(1).replace(",", ".") if wind_match else None
        humidity = int(humidity_match.group(1)) if humidity_match else None
        return {
            "ok": True,
            "city": city,
            "temp": temp,
            "feels": feels,
            "wind": wind,
            "wind_unit": "м/с",
            "humidity": humidity,
            "code": self._condition_code(condition),
            "condition": condition,
            "min": int(night_match.group(1)) if night_match else None,
            "max": int(day_match.group(1)) if day_match else None,
            "source": "Яндекс.Погода",
        }

    def _open_meteo_weather(self, city: str) -> dict[str, Any]:
        settings = self.store.settings
        lat = float(settings.get("weather_lat", 45.0355))
        lon = float(settings.get("weather_lon", 38.9753))
        if city.lower() != str(settings.get("weather_city", "")).lower():
            try:
                geo = _http_json(
                    "https://geocoding-api.open-meteo.com/v1/search?name="
                    + urllib.parse.quote(city) + "&count=1&language=ru&format=json",
                    timeout=6,
                )
                item = (geo.get("results") or [None])[0]
                if item:
                    lat, lon = item["latitude"], item["longitude"]
            except Exception:
                pass
        url = (
            "https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
            "&current=temperature_2m,apparent_temperature,weather_code,wind_speed_10m,relative_humidity_2m"
            "&daily=temperature_2m_max,temperature_2m_min&timezone=auto&forecast_days=1"
        ).format(lat=lat, lon=lon)
        data = _http_json(url, timeout=6)
        current = data.get("current") or {}
        daily = data.get("daily") or {}
        code = int(current.get("weather_code", -1))
        return {
            "ok": True,
            "city": city,
            "temp": current.get("temperature_2m"),
            "feels": current.get("apparent_temperature"),
            "wind": current.get("wind_speed_10m"),
            "wind_unit": "км/ч",
            "humidity": current.get("relative_humidity_2m"),
            "code": code,
            "condition": self.WEATHER_CODES.get(code, "неизвестно"),
            "min": (daily.get("temperature_2m_min") or [None])[0],
            "max": (daily.get("temperature_2m_max") or [None])[0],
            "source": "Open-Meteo",
        }

    def weather_info(self, city: str = "") -> dict[str, Any]:
        settings = self.store.settings
        city = (city or settings.get("weather_city") or "Краснодар").strip()
        key = city.lower()
        cached = self._weather_cache.get(key)
        if cached and time.time() - cached[0] < 600:
            return cached[1]
        errors: list[str] = []
        result: dict[str, Any] | None = None
        for loader in (self._yandex_weather, self._open_meteo_weather):
            try:
                result = loader(city)
                break
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{loader.__name__.replace('_', ' ')}: {exc}")
        if not result:
            return {"ok": False, "text": "Погода недоступна: " + "; ".join(errors[-2:])}
        parts = [f"Погода в городе {city}: {result.get('condition') or '—'}, {result.get('temp')}°C"]
        if result.get("feels") is not None:
            parts.append(f"ощущается как {result['feels']}°C")
        if result.get("wind") is not None:
            parts.append(f"ветер {result['wind']} {result.get('wind_unit', 'км/ч')}")
        if result.get("humidity") is not None:
            parts.append(f"влажность {result['humidity']}%")
        if result.get("min") is not None and result.get("max") is not None:
            parts.append(f"днём от {result['min']}°C до {result['max']}°C")
        text = ", ".join(parts) + "."
        if result.get("source"):
            text += f" Источник: {result['source']}."
        result["text"] = text
        result["ok"] = True
        self._weather_cache[key] = (time.time(), result)
        return result

    def _weather_tool(self, city: str = "") -> dict[str, Any]:
        return self.weather_info(city)

    # ------------------------------------------------------------------ registry
    def _register(self) -> None:
        def schema(**properties: Any) -> dict[str, Any]:
            required = [key for key, value in properties.items() if value.pop("_required", False)]
            return {"type": "object", "properties": properties, "required": required}

        def prop(kind: str, description: str, *, required: bool = False, **extra: Any) -> dict[str, Any]:
            value = {"type": kind, "description": description}
            value.update(extra)
            value["_required"] = required
            return value

        self.add(
            "list_dir",
            "List files and folders in a directory on the user's Windows PC. Use this to inspect the desktop, downloads, projects, etc. Relative paths are resolved against the working directory.",
            schema(path=prop("string", "Directory path. Use '.' for the working directory."),
                   limit=prop("integer", "Maximum entries to return (1-1000).", default=200)),
            self._list_dir, label="Просмотр папки", risk="read",
        )
        self.add(
            "read_file",
            "Read a text file from the PC. Returns UTF-8/cp1251 content, truncated for very large files. Do not use for binary files.",
            schema(path=prop("string", "Path to the file.", required=True),
                   max_chars=prop("integer", "Maximum characters to return.", default=20000)),
            self._read_file, label="Чтение файла", risk="read",
        )
        self.add(
            "write_file",
            "Create or overwrite a text file on the PC. Parent folders are created automatically. Requires user confirmation in smart mode.",
            schema(path=prop("string", "Path to the file.", required=True),
                   content=prop("string", "Text content to write.", required=True),
                   append=prop("boolean", "Append instead of overwriting.", default=False)),
            self._write_file, label="Запись файла", risk="high", confirm=True,
        )
        self.add(
            "search_files",
            "Search for files by name or path fragment inside a folder tree.",
            schema(query=prop("string", "Part of the file name or path.", required=True),
                   root=prop("string", "Root folder to search in.", default="."),
                   limit=prop("integer", "Maximum results (1-200).", default=50)),
            self._search_files, label="Поиск файлов", risk="read",
        )
        self.add(
            "move_path",
            "Move or rename a file/folder. Requires confirmation.",
            schema(src=prop("string", "Source path.", required=True),
                   dst=prop("string", "Destination path.", required=True)),
            self._move_path, label="Перемещение", risk="high", confirm=True,
        )
        self.add(
            "copy_path",
            "Copy a file or folder. Requires confirmation.",
            schema(src=prop("string", "Source path.", required=True),
                   dst=prop("string", "Destination path.", required=True)),
            self._copy_path, label="Копирование", risk="high", confirm=True,
        )
        self.add(
            "delete_path",
            "Move a file or folder to the Windows Recycle Bin. Never permanently deletes. Requires confirmation.",
            schema(path=prop("string", "Path to delete.", required=True)),
            self._delete_path, label="Удаление в Корзину", risk="high", confirm=True,
        )
        self.add(
            "run_command",
            "Run a cmd.exe or PowerShell command on the PC and return its output. Requires confirmation. Use for launching programs, git, package managers, system tasks.",
            schema(command=prop("string", "Command to execute.", required=True),
                   cwd=prop("string", "Working directory.", default="."),
                   shell=prop("string", "Shell: cmd or powershell.", enum=["cmd", "powershell"], default="cmd"),
                   timeout=prop("integer", "Timeout in seconds (1-300).", default=60)),
            self._run_command, label="Команда", risk="high", confirm=True,
        )
        self.add(
            "open_path",
            "Open a file, folder or URL in the default Windows application/browser. Requires confirmation.",
            schema(path=prop("string", "File, folder or http(s) URL.", required=True)),
            self._open_path, label="Открытие", risk="high", confirm=True,
        )
        self.add(
            "screenshot",
            "Take a screenshot of all monitors, save it as PNG and show it to the user. Requires confirmation.",
            schema(), self._screenshot, label="Скриншот", risk="high", confirm=True,
        )
        self.add(
            "mouse_click",
            "Move the mouse cursor to screen coordinates and click. Requires confirmation.",
            schema(x=prop("integer", "Screen X coordinate.", required=True),
                   y=prop("integer", "Screen Y coordinate.", required=True),
                   button=prop("string", "Mouse button.", enum=["left", "right", "middle"], default="left"),
                   double=prop("boolean", "Double click.", default=False)),
            self._mouse_click, label="Клик мышью", risk="high", confirm=True,
        )
        self.add(
            "type_text",
            "Type text into the currently focused window using Unicode input (supports Russian). Requires confirmation.",
            schema(text=prop("string", "Text to type.", required=True)),
            self._type_text, label="Ввод текста", risk="high", confirm=True,
        )
        self.add(
            "press_keys",
            "Press a key combination, e.g. 'ctrl+s', 'alt+tab', 'win+d', 'enter'. Requires confirmation.",
            schema(keys=prop("string", "Key combination separated by '+'.", required=True)),
            self._press_keys, label="Горячие клавиши", risk="high", confirm=True,
        )
        self.add(
            "volume",
            "Change system volume or mute it.",
            schema(action=prop("string", "Action.", enum=["up", "down", "mute"], default="up"),
                   steps=prop("integer", "Number of volume steps (1-10).", default=2)),
            self._volume, label="Громкость", risk="low",
        )
        self.add(
            "clipboard_get",
            "Read the current text from the Windows clipboard. Sensitive; requires confirmation.",
            schema(), self._clipboard_get, label="Чтение буфера", risk="high", confirm=True,
        )
        self.add(
            "clipboard_set",
            "Write text to the Windows clipboard. Requires confirmation.",
            schema(text=prop("string", "Text to copy.", required=True)),
            self._clipboard_set, label="Запись в буфер", risk="high", confirm=True,
        )
        self.add(
            "web_search",
            "Search the web via DuckDuckGo and return titles, URLs and snippets. Requires internet.",
            schema(query=prop("string", "Search query.", required=True),
                   limit=prop("integer", "Number of results (1-10).", default=5)),
            self._web_search, label="Поиск в интернете", risk="read",
        )
        self.add(
            "fetch_url",
            "Download a web page and return its readable text. Requires internet.",
            schema(url=prop("string", "HTTP(S) URL.", required=True),
                   max_chars=prop("integer", "Maximum characters to return.", default=12000)),
            self._fetch_url, label="Загрузка страницы", risk="read",
        )
        self.add(
            "memory_save",
            "Save a short user fact to local persistent memory only when the user explicitly asks to remember it.",
            schema(text=prop("string", "One concise fact to remember; do not store passwords, secrets, or sensitive details.", required=True)),
            self._memory_save, label="Запись в память", risk="low",
        )
        self.add(
            "memory_list",
            "List local user memories or search them by a short query.",
            schema(query=prop("string", "Optional search query.", default="")),
            self._memory_list, label="Память", risk="read",
        )
        self.add(
            "memory_delete",
            "Delete one exact local memory by id or unique exact text. Requires confirmation.",
            schema(identifier=prop("string", "Memory id or unique text.", required=True)),
            self._memory_delete, label="Удаление из памяти", risk="high", confirm=True,
        )
        self.add(
            "notes_create",
            "Create a note in the user's local notes. Use this when the user says 'запомни', 'запиши', 'создай заметку'.",
            schema(title=prop("string", "Note title.", required=True),
                   text=prop("string", "Note body.", default="")),
            self._notes_create, label="Создание заметки", risk="low",
        )
        self.add(
            "notes_list",
            "List or search the user's notes.",
            schema(query=prop("string", "Optional search query.", default="")),
            self._notes_list, label="Заметки", risk="read",
        )
        self.add(
            "notes_update",
            "Update an existing note by id or exact title.",
            schema(identifier=prop("string", "Note id or title.", required=True),
                   title=prop("string", "New title.", default=None),
                   text=prop("string", "New body.", default=None)),
            self._notes_update, label="Изменение заметки", risk="low",
        )
        self.add(
            "notes_delete",
            "Delete a note by id or title. Requires confirmation.",
            schema(identifier=prop("string", "Note id or title.", required=True)),
            self._notes_delete, label="Удаление заметки", risk="high", confirm=True,
        )
        self.add(
            "reminder_create",
            "Create a local reminder. Accepts human time expressions like 'через 30 минут', 'завтра в 9:00', '2026-11-12 15:00'.",
            schema(text=prop("string", "What to remind about.", required=True),
                   when=prop("string", "When to remind, in Russian or ISO format.", required=True)),
            self._reminder_create, label="Создание напоминания", risk="low",
        )
        self.add(
            "reminder_list",
            "List active reminders.",
            schema(), self._reminder_list, label="Напоминания", risk="read",
        )
        self.add(
            "reminder_delete",
            "Delete a reminder by id.",
            schema(reminder_id=prop("string", "Reminder id.", required=True)),
            self._reminder_delete, label="Удаление напоминания", risk="low",
        )
        self.add(
            "project_list",
            "Показывает сохранённые проекты и их цель, статус, решения и следующий шаг.",
            schema(query=prop("string", "Необязательная часть названия или текста для поиска.", default=""),
                   include_archived=prop("boolean", "Включить архивные проекты.", default=False)),
            self._project_list, label="Список проектов", risk="read",
        )
        self.add(
            "project_create",
            "Создаёт карточку многосоставного проекта по просьбе пользователя. Текущий чат автоматически привяжется к новому проекту.",
            schema(title=prop("string", "Короткое название проекта.", required=True),
                   goal=prop("string", "Цель проекта.", required=True),
                   next_step=prop("string", "Подтверждённый ближайший шаг.", default=""),
                   summary=prop("string", "Краткий текущий контекст.", default=""),
                   decisions=prop("string", "Подтверждённые решения, по одному на строку.", default=""),
                   files=prop("string", "Связанные пути к файлам, по одному на строку.", default="")),
            self._project_create, label="Создание проекта", risk="low",
        )
        self.add(
            "project_update",
            "Обновляет только переданные пользователем факты проекта. Не выдумывай прогресс; добавляй решения и файлы через add_decision/add_file. Архивирование обратимо.",
            schema(identifier=prop("string", "Точное название проекта или его id.", required=True),
                   title=prop("string", "Новое название.", default=None),
                   goal=prop("string", "Новая формулировка цели.", default=None),
                   status=prop("string", "Статус: active, paused, done или archived.", enum=["active", "paused", "done", "archived"], default=None),
                   next_step=prop("string", "Новый ближайший шаг; пустое значение очищает его.", default=None),
                   summary=prop("string", "Обновлённый текущий контекст.", default=None),
                   add_decision=prop("string", "Одно новое подтверждённое решение.", default=None),
                   add_file=prop("string", "Один связанный путь к файлу.", default=None)),
            self._project_update, label="Обновление проекта", risk="low",
        )
        self.add(
            "create_3d_model",
            "Generate a printable 3D model in binary STL format from a primitive: box, sphere, cylinder, cone or torus. Sizes are millimetres. Saves the file locally and returns its path.",
            schema(name=prop("string", "Model/file name.", required=True),
                   shape=prop("string", "Primitive shape.", enum=["box", "sphere", "cylinder", "cone", "torus"], required=True),
                   width=prop("number", "Box width, mm.", default=50),
                   depth=prop("number", "Box depth, mm.", default=50),
                   height=prop("number", "Height, mm.", default=50),
                   radius=prop("number", "Radius, mm.", default=25),
                   tube_radius=prop("number", "Torus tube radius, mm.", default=8),
                   segments=prop("integer", "Mesh resolution (8-96).", default=32)),
            self._create_3d_model, label="3D-модель", risk="low",
        )
        self.add(
            "generate_image",
            "Generate a picture from a text prompt using a free online image service (Pollinations/Flux). Requires internet. Returns the image and its local path.",
            schema(prompt=prop("string", "Detailed image description.", required=True),
                   width=prop("integer", "Width in pixels (256-1536).", default=1024),
                   height=prop("integer", "Height in pixels (256-1536).", default=1024)),
            self._generate_image, label="Генерация картинки", risk="low",
        )
        self.add(
            "system_info",
            "Get operating system, CPU, RAM, disk, uptime and Python information.",
            schema(), self._system_info, label="Информация о системе", risk="read",
        )
        self.add(
            "list_processes",
            "List running Windows processes, optionally filtered by name.",
            schema(filter_text=prop("string", "Case-insensitive name filter.", default=""),
                   limit=prop("integer", "Maximum rows (1-200).", default=50)),
            self._list_processes, label="Процессы", risk="read",
        )
        self.add(
            "kill_process",
            "Terminate a Windows process by PID or image name. Requires confirmation.",
            schema(pid_or_name=prop("string", "PID or process image name.", required=True),
                   force=prop("boolean", "Force termination.", default=False)),
            self._kill_process, label="Завершение процесса", risk="high", confirm=True,
        )
        self.add(
            "minecraft_status",
            "Check a Minecraft Java server status (version, players, MOTD, latency) using the Server List Ping protocol.",
            schema(host=prop("string", "Server host or host:port.", default="127.0.0.1"),
                   port=prop("integer", "Server port.", default=25565)),
            self._minecraft_status, label="Статус Minecraft", risk="read",
        )
        self.add(
            "weather",
            "Get current weather and today's forecast. Defaults to the user's city (Краснодар).",
            schema(city=prop("string", "City name. Empty means the configured city.", default="")),
            self._weather_tool, label="Погода", risk="read",
        )
