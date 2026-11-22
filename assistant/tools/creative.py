# ruff: noqa: F403,F405
"""Tool implementations grouped by creative domain."""

from .common import *  # noqa: F403
from .decorators import tool


class CreativeToolsMixin:
    @tool(
        name='minecraft_servers', label='Список Minecraft-серверов',
        risk='read', confirm=False,
        capabilities=('сеть_наружу',),
        description='List local Minecraft servers managed by Quanta.',
        parameters=schema(),
    )
    def _minecraft_servers(self) -> dict[str, Any]:
        if self.minecraft is None:
            return {"ok": False, "text": "Менеджер Minecraft не подключён."}
        servers = self.minecraft.list_servers()
        lines = [f"{item.get('name')} id={item.get('id')} version={item.get('version')} status={item.get('status')} address={item.get('address')}"
                 for item in servers]
        return {"ok": True, "text": f"Серверов Кванты: {len(servers)}.\n" + "\n".join(lines), "servers": servers}

    @tool(
        name='minecraft_server_start', label='Запуск Minecraft-сервера',
        risk='high', confirm=True,
        capabilities=('сеть_наружу',),
        description='Start a prepared local Minecraft Java server.',
        parameters=schema(server_id=prop('string', 'Server id.', required=True)),
    )
    def _minecraft_server_start(self, server_id: str) -> dict[str, Any]:
        if self.minecraft is None:
            raise RuntimeError("Менеджер Minecraft не подключён")
        return self.minecraft.start(server_id)

    @tool(
        name='minecraft_server_stop', label='Остановка Minecraft-сервера',
        risk='high', confirm=True,
        capabilities=('сеть_наружу',),
        description='Stop a Minecraft server managed by Quanta gracefully.',
        parameters=schema(server_id=prop('string', 'Server id.', required=True)),
    )
    def _minecraft_server_stop(self, server_id: str) -> dict[str, Any]:
        if self.minecraft is None:
            raise RuntimeError("Менеджер Minecraft не подключён")
        return self.minecraft.stop(server_id)

    @tool(
        name='minecraft_server_command', label='Команда Minecraft-серверу',
        risk='high', confirm=True,
        capabilities=('сеть_наружу',),
        description='Send a one-line Minecraft server console command.',
        parameters=schema(server_id=prop('string', 'Server id.', required=True), command=prop('string', 'Console command without a leading slash.', required=True)),
    )
    def _minecraft_server_command(self, server_id: str, command: str) -> dict[str, Any]:
        if self.minecraft is None:
            raise RuntimeError("Менеджер Minecraft не подключён")
        return self.minecraft.send_command(server_id, command)

    @tool(
        name='create_3d_model', label='3D-модель',
        risk='low', confirm=False,
        capabilities=('пишет_файлы',),
        network=False,
        description='Generate a printable 3D model in binary STL format from a primitive: box, sphere, cylinder, cone or torus. Sizes are millimetres. Saves the file locally and returns its path.',
        parameters=schema(name=prop('string', 'Model/file name.', required=True), shape=prop('string', 'Primitive shape.', enum=['box', 'sphere', 'cylinder', 'cone', 'torus'], required=True), width=prop('number', 'Box width, mm.', default=50), depth=prop('number', 'Box depth, mm.', default=50), height=prop('number', 'Height, mm.', default=50), radius=prop('number', 'Radius, mm.', default=25), tube_radius=prop('number', 'Torus tube radius, mm.', default=8), segments=prop('integer', 'Mesh resolution (8-96).', default=32)),
    )
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

    @tool(
        name='generate_image', label='Генерация картинки',
        risk='low', confirm=False,
        capabilities=('сеть_наружу',),
        description='Generate a picture from a text prompt using a free online image service (Pollinations/Flux). Requires internet. Returns the image and its local path.',
        parameters=schema(prompt=prop('string', 'Detailed image description.', required=True), width=prop('integer', 'Width in pixels (256-1536).', default=1024), height=prop('integer', 'Height in pixels (256-1536).', default=1024)),
    )
    def _generate_image(
        self, prompt: str, width: int = 1024, height: int = 1024,
        _quanta_seed: int | None = None,
    ) -> dict[str, Any]:
        prompt = (prompt or "").strip()
        if not prompt:
            return {"ok": False, "text": "Пустой запрос на изображение"}
        width = max(256, min(1536, int(width or 1024)))
        height = max(256, min(1536, int(height or 1024)))
        encoded = urllib.parse.quote(prompt, safe="")
        seed = int(_quanta_seed) % 1_000_000 if _quanta_seed is not None else int(time.time()) % 1_000_000
        url = (f"https://image.pollinations.ai/prompt/{encoded}"
               f"?width={width}&height={height}&nologo=true&model=flux&seed={seed}")
        try:
            data, content_type, _final_url = _safe_get(
                url, timeout=180, headers={"Accept": "image/*"}, max_bytes=25_000_000
            )
            content_type = content_type or "image/jpeg"
        except (urllib.error.URLError, TimeoutError, OSError, ValueError, RuntimeError) as exc:
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

    @tool(
        name='system_info', label='Информация о системе',
        risk='read', confirm=False,
        capabilities=('читает_личное',),
        network=False,
        description='Get operating system, CPU, RAM, disk, uptime and Python information.',
        parameters=schema(),
    )
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

    @tool(
        name='list_processes', label='Процессы',
        risk='read', confirm=False,
        capabilities=('читает_личное',),
        network=False,
        description='List running Windows processes, optionally filtered by name.',
        parameters=schema(filter_text=prop('string', 'Case-insensitive name filter.', default=''), limit=prop('integer', 'Maximum rows (1-200).', default=50)),
    )
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

    @tool(
        name='kill_process', label='Завершение процесса',
        risk='high', confirm=True,
        capabilities=('исполняет',),
        network=False,
        description='Terminate a Windows process by PID or image name. Requires confirmation.',
        parameters=schema(pid_or_name=prop('string', 'PID or process image name.', required=True), force=prop('boolean', 'Force termination.', default=False)),
    )
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

    @tool(
        name='minecraft_status', label='Статус Minecraft',
        risk='read', confirm=False,
        capabilities=('сеть_наружу',),
        description='Check a Minecraft Java server status (version, players, MOTD, latency) using the Server List Ping protocol.',
        parameters=schema(host=prop('string', 'Server host or host:port.', default='127.0.0.1'), port=prop('integer', 'Server port.', default=25565)),
    )
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
