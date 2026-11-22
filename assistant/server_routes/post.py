"""POST endpoint route table and handlers."""

import subprocess
import threading
import traceback
from pathlib import Path
from .context import RouteContext
from .table import RouteDefinition, dispatch


def _matches_post_0(path: str) -> bool:
    return path == '/api/chat'
def _handle_post_0(context: RouteContext, self, path):
    self._handle_chat()
    return
def _matches_post_1(path: str) -> bool:
    return path == '/api/resource-profile'
def _handle_post_1(context: RouteContext, self, path):
    RESOURCE_LOCK = context.resource_lock
    RESOURCE_STATE = context.resource_state
    STORE = context.store
    request_resource_profile = context.request_resource_profile
    data = self._read_json()
    _settings, status, error = request_resource_profile(str(data.get('profile') or ''))
    if error:
        self._error(status or 400, error)
        return
    with RESOURCE_LOCK:
        transition = dict(RESOURCE_STATE)
    self._json({'ok': True, 'settings': STORE.public_settings(), 'transition': transition})
    return
def _matches_post_2(path: str) -> bool:
    return path == '/api/voice/tts'
def _handle_post_2(context: RouteContext, self, path):
    RESOURCE_LOCK = context.resource_lock
    RESOURCE_OPERATION_LOCK = context.resource_operation_lock
    RESOURCE_STATE = context.resource_state
    STORE = context.store
    voice = context.voice
    data = self._read_json()
    text = str(data.get('text') or '').strip()
    if not text:
        self._error(400, 'Пустой текст для синтеза речи')
        return
    with RESOURCE_OPERATION_LOCK:
        with RESOURCE_LOCK:
            switching = RESOURCE_STATE.get('status') == 'switching'
        if switching:
            self._error(409, 'Подожди завершения переключения профиля ресурсов')
            return
        info = voice.speak(text, STORE)
    if not info.get('ok'):
        self._error(500, str(info.get('error') or 'Синтез речи недоступен'))
        return
    self._json({'ok': True, **info})
    return
def _matches_post_3(path: str) -> bool:
    return path == '/api/voice/transcribe'
def _handle_post_3(context: RouteContext, self, path):
    RESOURCE_LOCK = context.resource_lock
    RESOURCE_OPERATION_LOCK = context.resource_operation_lock
    RESOURCE_STATE = context.resource_state
    STORE = context.store
    voice = context.voice
    length_header = self.headers.get('Content-Length')
    try:
        length = max(0, int(length_header or '0'))
    except ValueError:
        self._error(400, 'Неверный Content-Length')
        return
    audio = self.rfile.read(length) if length else b''
    if not audio:
        self._error(400, 'Пустой аудиофайл')
        return
    with RESOURCE_OPERATION_LOCK:
        with RESOURCE_LOCK:
            switching = RESOURCE_STATE.get('status') == 'switching'
        if switching:
            self._error(409, 'Подожди завершения переключения профиля ресурсов')
            return
        info = voice.transcribe(audio, 'recording.webm', STORE)
    if not info.get('ok'):
        self._error(500, str(info.get('error') or 'Распознавание речи недоступно'))
        return
    self._json({'ok': True, **info})
    return
def _matches_post_4(path: str) -> bool:
    return path == '/api/vision/pull'
def _handle_post_4(context: RouteContext, self, path):
    STORE = context.store
    VISION = context.vision
    data = self._read_json()
    model = str(data.get('model') or 'qwen3-vl:2b')
    STORE.save_settings({'vision_model': model})
    self._json(VISION.pull(model))
    return
def _matches_post_5(path: str) -> bool:
    return path == '/api/desktop/apps/open'
def _handle_post_5(context: RouteContext, self, path):
    from assistant import desktop
    data = self._read_json()
    self._json(desktop.launch_application(str(data.get('name') or '')))
    return
def _matches_post_6(path: str) -> bool:
    return path in ('/api/desktop/windows/focus', '/api/desktop/windows/close')
def _handle_post_6(context: RouteContext, self, path):
    from assistant import desktop
    data = self._read_json()
    action = desktop.focus_application if path.endswith('/focus') else desktop.close_application
    self._json(action(str(data.get('name') or '')))
    return
def _matches_post_7(path: str) -> bool:
    return path == '/api/desktop/music'
def _handle_post_7(context: RouteContext, self, path):
    from assistant import desktop
    data = self._read_json()
    self._json(desktop.music_control(str(data.get('action') or ''), str(data.get('query') or '')))
    return
def _matches_post_8(path: str) -> bool:
    return path == '/api/desktop/tree/action'
def _handle_post_8(context: RouteContext, self, path):
    from assistant import automation
    data = self._read_json()
    result = automation.perform_action(int(data.get('hwnd') or 0), data.get('path') or [], str(data.get('action') or ''), expected_name=str(data.get('expected_name') or ''), expected_type=str(data.get('expected_type') or ''), expected_automation_id=str(data.get('expected_automation_id') or ''), value=str(data.get('value') or ''))
    self._json(result)
    return
def _matches_post_9(path: str) -> bool:
    return path == '/api/desktop/vision'
def _handle_post_9(context: RouteContext, self, path):
    TOOLBOX = context.toolbox
    data = self._read_json()
    result = TOOLBOX._pc_vision(str(data.get('prompt') or 'Опиши, что видно на экране. Отвечай по-русски, коротко и по делу.'))
    self._json(result, 200 if result.get('ok') else 503)
    return
def _matches_post_10(path: str) -> bool:
    return path == '/api/minecraft/servers'
def _handle_post_10(context: RouteContext, self, path):
    MINECRAFT = context.minecraft
    data = self._read_json()
    item = MINECRAFT.create_server(name=str(data.get('name') or ''), version=str(data.get('version') or ''), eula_accepted=data.get('eula_accepted') is True, port=data.get('port', 25565), memory_mb=data.get('memory_mb', 2048), max_players=data.get('max_players', 10), lan_access=data.get('lan_access') is True)
    self._json({'ok': True, 'server': item}, 202)
    return
def _matches_post_11(path: str) -> bool:
    return path.startswith('/api/minecraft/companion/')
def _handle_post_11(context: RouteContext, self, path):
    MINECRAFT_COMPANION = context.minecraft_companion
    parts = path.split('/')
    if len(parts) < 6:
        self._error(404, 'Неизвестное действие Minecraft-компаньона')
        return
    server_id, action = (parts[4], parts[5])
    if action == 'start':
        data = self._read_json()
        self._json({'ok': True, 'bot': MINECRAFT_COMPANION.start(server_id, str(data.get('username') or 'Quanta'))}, 202)
        return
    if action == 'stop':
        self._json({'ok': True, 'bot': MINECRAFT_COMPANION.stop(server_id)})
        return
    if action == 'chat':
        data = self._read_json()
        self._json({'ok': True, 'bot': MINECRAFT_COMPANION.say(server_id, str(data.get('text') or ''))})
        return
    self._error(404, 'Неизвестное действие Minecraft-компаньона')
    return
def _matches_post_12(path: str) -> bool:
    return path.startswith('/api/minecraft/servers/')
def _handle_post_12(context: RouteContext, self, path):
    MINECRAFT = context.minecraft
    MINECRAFT_COMPANION = context.minecraft_companion
    parts = path.split('/')
    if len(parts) < 6:
        self._error(404, 'Неизвестное действие Minecraft-сервера')
        return
    server_id, action = (parts[4], parts[5])
    if action == 'start':
        self._json({'ok': True, 'server': MINECRAFT.start(server_id)})
        return
    if action == 'stop':
        MINECRAFT_COMPANION.stop_for_server(server_id)
        self._json({'ok': True, 'server': MINECRAFT.stop(server_id)})
        return
    if action == 'command':
        data = self._read_json()
        self._json(MINECRAFT.send_command(server_id, str(data.get('command') or '')))
        return
    self._error(404, 'Неизвестное действие Minecraft-сервера')
    return
def _matches_post_13(path: str) -> bool:
    return path == '/api/confirm'
def _handle_post_13(context: RouteContext, self, path):
    PENDING = context.pending
    PENDING_LOCK = context.pending_lock
    data = self._read_json()
    confirm_id = str(data.get('id') or '')
    allow = bool(data.get('allow'))
    with PENDING_LOCK:
        info = PENDING.get(confirm_id)
    if info:
        info['allow'] = allow
        if allow and isinstance(data.get('approval'), dict):
            approval = data.get('approval')
            info['approval'] = approval
        info['event'].set()
    self._json({'ok': True})
    return
def _matches_post_14(path: str) -> bool:
    return path == '/api/stop'
def _handle_post_14(context: RouteContext, self, path):
    ACTIVE = context.active
    ACTIVE_LOCK = context.active_lock
    deny_pending_for_chat = context.deny_pending_for_chat
    data = self._read_json()
    chat_id = str(data.get('chat_id') or '')
    with ACTIVE_LOCK:
        event = ACTIVE.get(chat_id)
    if event:
        event.set()
    deny_pending_for_chat(chat_id)
    self._json({'ok': True})
    return
def _matches_post_15(path: str) -> bool:
    return path == '/api/chats'
def _handle_post_15(context: RouteContext, self, path):
    STORE = context.store
    data = self._read_json()
    chat = STORE.create_chat(str(data.get('title') or 'Новый чат'))
    self._json({'ok': True, 'chat': chat})
    return
def _matches_post_16(path: str) -> bool:
    return path.startswith('/api/chats/') and path.endswith('/rename')
def _handle_post_16(context: RouteContext, self, path):
    STORE = context.store
    chat_id = path.split('/')[3]
    data = self._read_json()
    result = STORE.update_chat_title(chat_id, str(data.get('title') or 'Новый чат'))
    self._json({'ok': True, 'chat': result})
    return
def _matches_post_17(path: str) -> bool:
    return path == '/api/settings'
def _handle_post_17(context: RouteContext, self, path):
    HOTKEY = context.hotkey
    STORE = context.store
    data = self._read_json()
    if 'resource_profile' in data and data['resource_profile'] != STORE.settings.get('resource_profile', 'work'):
        self._error(400, 'Переключай профиль ресурсов через селектор Работа / Игра')
        return
    try:
        settings = STORE.save_settings(data)
        if HOTKEY:
            HOTKEY.set_enabled(bool(settings.get('global_hotkey_enabled', True)))
    except ValueError as exc:
        self._error(400, str(exc))
        return
    self._json({'ok': True, 'settings': STORE.public_settings()})
    return
def _matches_post_18(path: str) -> bool:
    return path == '/api/memory'
def _handle_post_18(context: RouteContext, self, path):
    STORE = context.store
    data = self._read_json()
    item = STORE.create_memory(str(data.get('text') or ''))
    self._json({'ok': True, 'item': item})
    return
def _matches_post_19(path: str) -> bool:
    return path == '/api/memory/semantic'
def _handle_post_19(context: RouteContext, self, path):
    STORE = context.store
    STORE.semantic_memory.start(STORE.list_memory())
    self._json({'ok': True, **STORE.semantic_memory.status()})
    return
def _matches_post_20(path: str) -> bool:
    return path == '/api/workflows'
def _handle_post_20(context: RouteContext, self, path):
    STORE = context.store
    data = self._read_json()
    item = STORE.create_workflow(str(data.get('title') or ''), str(data.get('prompt') or ''))
    self._json({'ok': True, 'item': item})
    return
def _matches_post_21(path: str) -> bool:
    return path == '/api/projects'
def _handle_post_21(context: RouteContext, self, path):
    STORE = context.store
    data = self._read_json()
    try:
        chat_id = str(data.get('chat_id') or '').strip()
        if chat_id:
            STORE.get_chat(chat_id)
        project = STORE.create_project(str(data.get('title') or ''), str(data.get('goal') or ''), next_step=str(data.get('next_step') or ''), summary=str(data.get('summary') or ''), decisions=data.get('decisions') or [], files=data.get('files') or [])
        if chat_id:
            project = STORE.link_chat_to_project(project['id'], chat_id)
    except (ValueError, FileNotFoundError) as exc:
        self._error(400 if isinstance(exc, ValueError) else 404, str(exc))
        return
    self._json({'ok': True, 'project': project})
    return
def _matches_post_22(path: str) -> bool:
    return path.startswith('/api/projects/') and path.endswith('/link')
def _handle_post_22(context: RouteContext, self, path):
    STORE = context.store
    project_id = path.split('/')[3]
    data = self._read_json()
    try:
        project = STORE.link_chat_to_project(project_id, str(data.get('chat_id') or ''))
    except FileNotFoundError as exc:
        self._error(404, str(exc))
        return
    except ValueError as exc:
        self._error(400, str(exc))
        return
    self._json({'ok': True, 'project': project})
    return
def _matches_post_23(path: str) -> bool:
    return path.startswith('/api/projects/') and path.endswith('/unlink')
def _handle_post_23(context: RouteContext, self, path):
    STORE = context.store
    project_id = path.split('/')[3]
    data = self._read_json()
    try:
        project = STORE.unlink_chat_from_project(project_id, str(data.get('chat_id') or ''))
    except FileNotFoundError as exc:
        self._error(404, str(exc))
        return
    self._json({'ok': True, 'project': project})
    return
def _matches_post_24(path: str) -> bool:
    return path.startswith('/api/projects/') and (path.endswith('/archive') or path.endswith('/restore'))
def _handle_post_24(context: RouteContext, self, path):
    STORE = context.store
    project_id = path.split('/')[3]
    try:
        project = STORE.archive_project(project_id, archived=path.endswith('/archive'))
    except FileNotFoundError as exc:
        self._error(404, str(exc))
        return
    except ValueError as exc:
        self._error(400, str(exc))
        return
    self._json({'ok': True, 'project': project})
    return
def _matches_post_25(path: str) -> bool:
    return path == '/api/actions/clear'
def _handle_post_25(context: RouteContext, self, path):
    STORE = context.store
    STORE.clear_actions()
    self._json({'ok': True})
    return
def _matches_post_26(path: str) -> bool:
    return path.startswith('/api/actions/') and path.endswith('/undo')
def _handle_post_26(context: RouteContext, self, path):
    TOOLBOX = context.toolbox
    action_id = path.split('/')[3]
    self._json(TOOLBOX.undo_action(action_id))
    return
def _matches_post_27(path: str) -> bool:
    return path == '/api/notes'
def _handle_post_27(context: RouteContext, self, path):
    STORE = context.store
    data = self._read_json()
    note = STORE.create_note(str(data.get('title') or 'Без названия'), str(data.get('text') or ''))
    self._json({'ok': True, 'note': note})
    return
def _matches_post_28(path: str) -> bool:
    return path == '/api/reminders'
def _handle_post_28(context: RouteContext, self, path):
    STORE = context.store
    TOOLBOX = context.toolbox
    data = self._read_json()
    try:
        due = TOOLBOX._parse_when(str(data.get('when') or ''))
    except ValueError as exc:
        self._error(400, str(exc))
        return
    reminder = STORE.create_reminder(str(data.get('text') or 'Напоминание'), due)
    self._json({'ok': True, 'reminder': reminder})
    return
def _matches_post_29(path: str) -> bool:
    return path.startswith('/api/reminders/') and path.endswith('/done')
def _handle_post_29(context: RouteContext, self, path):
    STORE = context.store
    reminder_id = path.split('/')[3]
    data = self._read_json()
    reminder = STORE.set_reminder_done(reminder_id, bool(data.get('done', True)))
    self._json({'ok': True, 'reminder': reminder})
    return
def _matches_post_30(path: str) -> bool:
    return path == '/api/open'
def _handle_post_30(context: RouteContext, self, path):
    _show_path_in_explorer = context.show_path_in_explorer
    data = self._read_json()
    result = _show_path_in_explorer(str(data.get('path') or ''))
    self._json(result)
    return
def _matches_post_31(path: str) -> bool:
    return path == '/api/reveal'
def _handle_post_31(context: RouteContext, self, path):
    data = self._read_json()
    target = Path(str(data.get('path') or '')).resolve()
    if target.exists():
        subprocess.Popen(['explorer', '/select,', str(target)], close_fds=True)
    self._json({'ok': target.exists()})
    return
def _matches_post_32(path: str) -> bool:
    return path == '/api/shutdown'
def _handle_post_32(context: RouteContext, self, path):
    SERVER = context.server
    STOP = context.stop
    self._json({'ok': True})
    STOP.set()
    threading.Thread(target=SERVER.shutdown, daemon=True).start()
    return
POST_ROUTE_TABLE = (
    RouteDefinition("POST", "path == '/api/chat'", _matches_post_0, _handle_post_0),
    RouteDefinition("POST", "path == '/api/resource-profile'", _matches_post_1, _handle_post_1),
    RouteDefinition("POST", "path == '/api/voice/tts'", _matches_post_2, _handle_post_2),
    RouteDefinition("POST", "path == '/api/voice/transcribe'", _matches_post_3, _handle_post_3),
    RouteDefinition("POST", "path == '/api/vision/pull'", _matches_post_4, _handle_post_4),
    RouteDefinition("POST", "path == '/api/desktop/apps/open'", _matches_post_5, _handle_post_5),
    RouteDefinition("POST", "path in ('/api/desktop/windows/focus', '/api/desktop/windows/close')", _matches_post_6, _handle_post_6),
    RouteDefinition("POST", "path == '/api/desktop/music'", _matches_post_7, _handle_post_7),
    RouteDefinition("POST", "path == '/api/desktop/tree/action'", _matches_post_8, _handle_post_8),
    RouteDefinition("POST", "path == '/api/desktop/vision'", _matches_post_9, _handle_post_9),
    RouteDefinition("POST", "path == '/api/minecraft/servers'", _matches_post_10, _handle_post_10),
    RouteDefinition("POST", "path.startswith('/api/minecraft/companion/')", _matches_post_11, _handle_post_11),
    RouteDefinition("POST", "path.startswith('/api/minecraft/servers/')", _matches_post_12, _handle_post_12),
    RouteDefinition("POST", "path == '/api/confirm'", _matches_post_13, _handle_post_13),
    RouteDefinition("POST", "path == '/api/stop'", _matches_post_14, _handle_post_14),
    RouteDefinition("POST", "path == '/api/chats'", _matches_post_15, _handle_post_15),
    RouteDefinition("POST", "path.startswith('/api/chats/') and path.endswith('/rename')", _matches_post_16, _handle_post_16),
    RouteDefinition("POST", "path == '/api/settings'", _matches_post_17, _handle_post_17),
    RouteDefinition("POST", "path == '/api/memory'", _matches_post_18, _handle_post_18),
    RouteDefinition("POST", "path == '/api/memory/semantic'", _matches_post_19, _handle_post_19),
    RouteDefinition("POST", "path == '/api/workflows'", _matches_post_20, _handle_post_20),
    RouteDefinition("POST", "path == '/api/projects'", _matches_post_21, _handle_post_21),
    RouteDefinition("POST", "path.startswith('/api/projects/') and path.endswith('/link')", _matches_post_22, _handle_post_22),
    RouteDefinition("POST", "path.startswith('/api/projects/') and path.endswith('/unlink')", _matches_post_23, _handle_post_23),
    RouteDefinition("POST", "path.startswith('/api/projects/') and (path.endswith('/archive') or path.endswith('/restore'))", _matches_post_24, _handle_post_24),
    RouteDefinition("POST", "path == '/api/actions/clear'", _matches_post_25, _handle_post_25),
    RouteDefinition("POST", "path.startswith('/api/actions/') and path.endswith('/undo')", _matches_post_26, _handle_post_26),
    RouteDefinition("POST", "path == '/api/notes'", _matches_post_27, _handle_post_27),
    RouteDefinition("POST", "path == '/api/reminders'", _matches_post_28, _handle_post_28),
    RouteDefinition("POST", "path.startswith('/api/reminders/') and path.endswith('/done')", _matches_post_29, _handle_post_29),
    RouteDefinition("POST", "path == '/api/open'", _matches_post_30, _handle_post_30),
    RouteDefinition("POST", "path == '/api/reveal'", _matches_post_31, _handle_post_31),
    RouteDefinition("POST", "path == '/api/shutdown'", _matches_post_32, _handle_post_32),
)

def dispatch_post(context: RouteContext, handler, path: str) -> None:
    try:
        dispatched = dispatch(POST_ROUTE_TABLE, context, handler, path)
        if not dispatched:
            handler._error(404, "Не найдено")
    except ValueError as exc:
        handler._error(400, str(exc))
    except FileNotFoundError as exc:
        handler._error(404, str(exc))
    except (RuntimeError, OSError) as exc:
        handler._error(503, str(exc))
    except Exception:
        context.log("POST error: " + traceback.format_exc())
        handler._error(500, "Внутренняя ошибка сервера")
