# ruff: noqa: F403,F405
"""Tool implementations grouped by filesystem domain."""

from .common import *  # noqa: F403
from .decorators import tool


class FilesystemToolsMixin:
    @tool(
        name='list_dir', label='Просмотр папки',
        risk='read', confirm=False,
        capabilities=('читает_личное',),
        network=False,
        description="List files and folders in a directory on the user's Windows PC. Use this to inspect the desktop, downloads, projects, etc. Relative paths are resolved against the working directory.",
        parameters=schema(path=prop('string', "Directory path. Use '.' for the working directory."), limit=prop('integer', 'Maximum entries to return (1-1000).', default=200)),
    )
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

    @tool(
        name='read_file', label='Чтение файла',
        risk='read', confirm=False,
        capabilities=('читает_личное',),
        network=False,
        description='Read a text file from the PC. Returns UTF-8/cp1251 content, truncated for very large files. Do not use for binary files.',
        parameters=schema(path=prop('string', 'Path to the file.', required=True), max_chars=prop('integer', 'Maximum characters to return.', default=20000)),
    )
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

    @tool(
        name='write_file', label='Запись файла',
        risk='high', confirm=True,
        capabilities=('пишет_файлы',),
        network=False,
        description='Create or overwrite a text file on the PC. Parent folders are created automatically. Requires user confirmation in smart mode.',
        parameters=schema(path=prop('string', 'Path to the file.', required=True), content=prop('string', 'Text content to write.', required=True), append=prop('boolean', 'Append instead of overwriting.', default=False)),
    )
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

    @tool(
        name='search_files', label='Поиск файлов',
        risk='read', confirm=False,
        capabilities=('читает_личное',),
        network=False,
        description='Search for files by name or path fragment inside a folder tree.',
        parameters=schema(query=prop('string', 'Part of the file name or path.', required=True), root=prop('string', 'Root folder to search in.', default='.'), limit=prop('integer', 'Maximum results (1-200).', default=50)),
    )
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

    @tool(
        name='move_path', label='Перемещение',
        risk='high', confirm=True,
        capabilities=('пишет_файлы',),
        network=False,
        description='Move or rename a file/folder. Requires confirmation.',
        parameters=schema(src=prop('string', 'Source path.', required=True), dst=prop('string', 'Destination path.', required=True)),
    )
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

    @tool(
        name='copy_path', label='Копирование',
        risk='high', confirm=True,
        capabilities=('пишет_файлы',),
        network=False,
        description='Copy a file or folder. Requires confirmation.',
        parameters=schema(src=prop('string', 'Source path.', required=True), dst=prop('string', 'Destination path.', required=True)),
    )
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
                if not ok:
                    raise ValueError("Заметка уже отсутствует")
            elif kind == "remove_reminder":
                ok = self.store.delete_reminder(str(undo.get("id") or ""))
                if not ok:
                    raise ValueError("Напоминание уже отсутствует")
            elif kind == "remove_memory":
                ok = self.store.delete_memory(str(undo.get("id") or ""))
                if not ok:
                    raise ValueError("Запись памяти уже отсутствует")
            elif kind == "remove_project":
                ok = self.store.delete_project(str(undo.get("id") or ""))
                if not ok:
                    raise ValueError("Проект уже отсутствует")
            elif kind == "remove_file":
                path = Path(str(undo.get("path") or "")).resolve()
                if not path.is_file() or _small_file_digest(path) != undo.get("sha256"):
                    raise ValueError("Файл изменился после создания; отмену не выполняю")
                if not self._recycle(path):
                    raise OSError("Не удалось отправить новый файл в Корзину")
            elif kind == "move_file":
                source = Path(str(undo.get("source") or "")).resolve()
                destination = Path(str(undo.get("destination") or "")).resolve()
                if not source.is_file() or _small_file_digest(source) != undo.get("sha256"):
                    raise ValueError("Перемещённый файл изменился; отмену не выполняю")
                if destination.exists():
                    raise ValueError("Исходный путь уже занят; файл не перезаписан")
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
            else:
                raise ValueError("Для этого действия безопасная отмена недоступна")
        except (OSError, ValueError) as exc:
            return {"ok": False, "text": str(exc)}
        self.store.mark_action_undone(action_id)
        return {"ok": True, "text": "Действие отменено"}

    @tool(
        name='delete_path', label='Удаление в Корзину',
        risk='high', confirm=True,
        capabilities=('пишет_файлы',),
        network=False,
        description='Move a file or folder to the Windows Recycle Bin. Never permanently deletes. Requires confirmation.',
        parameters=schema(path=prop('string', 'Path to delete.', required=True)),
    )
    def _delete_path(self, path: str) -> dict[str, Any]:
        p = self._resolve(path)
        if not p.exists():
            return {"ok": False, "text": f"Путь не найден: {p}"}
        if p == Path(p.anchor) or p == Path.home():
            return {"ok": False, "text": "Отказано: нельзя удалять корень диска или домашнюю папку"}
        if self._recycle(p):
            return {"ok": True, "text": f"Перемещено в Корзину: {p}"}
        return {"ok": False, "text": f"Не удалось переместить в Корзину: {p} (файл не удалён)"}

    @tool(
        name='run_command', label='Команда',
        risk='high', confirm=True,
        capabilities=('исполняет',),
        network=False,
        description='Run a cmd.exe or PowerShell command on the PC and return its output. Requires confirmation. Use for launching programs, git, package managers, system tasks.',
        parameters=schema(command=prop('string', 'Command to execute.', required=True), cwd=prop('string', 'Working directory.', default='.'), shell=prop('string', 'Shell: cmd or powershell.', enum=['cmd', 'powershell'], default='cmd'), timeout=prop('integer', 'Timeout in seconds (1-300).', default=60)),
    )
    def _run_command(
        self, command: str, cwd: str = ".", shell: str = "cmd", timeout: int = 60,
        _sandbox_root: str = "",
    ) -> dict[str, Any]:
        work = self._resolve(cwd)
        if not work.is_dir():
            return {"ok": False, "text": f"Рабочая папка не найдена: {work}"}
        try:
            timeout = max(1, min(300, int(timeout or 60)))
        except (TypeError, ValueError):
            timeout = 60
        if _sandbox_root:
            return command_sandbox.run(
                str(command), work_dir=work, sandbox_root=Path(_sandbox_root),
                shell=shell, timeout=timeout, data_dir=DATA_DIR,
            )
        if shell == "powershell":
            args = [
                "powershell", "-NoProfile", "-NonInteractive",
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

    @tool(
        name='open_path', label='Открытие',
        risk='high', confirm=True,
        capabilities=('ввод_в_ПК',),
        network=False,
        description='Open a file, folder or URL in the default Windows application/browser. Requires confirmation.',
        parameters=schema(path=prop('string', 'File, folder or http(s) URL.', required=True)),
    )
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
