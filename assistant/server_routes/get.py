"""GET endpoint route table and handlers."""

import os
import traceback
from datetime import datetime
from .context import RouteContext
from .table import RouteDefinition, dispatch


def _matches_get_0(path: str) -> bool:
    return path in ('/', '/index.html')
def _handle_get_0(context: RouteContext, self, path, query, parsed):
    self._serve_index()
    return
def _matches_get_1(path: str) -> bool:
    return path.startswith('/ui/')
def _handle_get_1(context: RouteContext, self, path, query, parsed):
    UI_DIR = context.ui_dir
    rel = path[len('/ui/'):]
    target = (UI_DIR / rel).resolve()
    if not str(target).startswith(str(UI_DIR.resolve()) + os.sep):
        self._error(403, 'Запрещено')
        return
    self._serve_file(target)
    return
def _matches_get_2(path: str) -> bool:
    return path.startswith('/media/')
def _handle_get_2(context: RouteContext, self, path, query, parsed):
    MEDIA_DIR = context.media_dir
    rel = path[len('/media/'):]
    target = (MEDIA_DIR / rel).resolve()
    if not str(target).startswith(str(MEDIA_DIR.resolve()) + os.sep):
        self._error(403, 'Запрещено')
        return
    self._serve_file(target)
    return
def _matches_get_3(path: str) -> bool:
    return path == '/api/bootstrap'
def _handle_get_3(context: RouteContext, self, path, query, parsed):
    DATA_DIR = context.data_dir
    STORE = context.store
    VERSION = context.version
    command_sandbox = context.command_sandbox
    health_info = context.health_info
    system_stats = context.system_stats
    settings = STORE.public_settings()
    self._json({'ok': True, 'version': VERSION, 'settings': settings, 'chats': STORE.list_chats(), 'notes': STORE.list_notes(), 'reminders': STORE.list_reminders(), 'health': health_info(), 'system': system_stats(), 'command_sandbox': command_sandbox.status(), 'server_time': datetime.now().isoformat(), 'data_dir': str(DATA_DIR)})
    return
def _matches_get_4(path: str) -> bool:
    return path == '/api/health'
def _handle_get_4(context: RouteContext, self, path, query, parsed):
    DATA_DIR = context.data_dir
    health_info = context.health_info
    system_stats = context.system_stats
    self._json({'ok': True, 'health': health_info(), 'system': system_stats(), 'data_dir': str(DATA_DIR)})
    return
def _matches_get_5(path: str) -> bool:
    return path == '/api/system'
def _handle_get_5(context: RouteContext, self, path, query, parsed):
    system_stats = context.system_stats
    self._json({'ok': True, **system_stats()})
    return
def _matches_get_6(path: str) -> bool:
    return path == '/api/weather'
def _handle_get_6(context: RouteContext, self, path, query, parsed):
    TOOLBOX = context.toolbox
    city = (query.get('city') or [''])[0]
    info = TOOLBOX.weather_info(city)
    self._json(info)
    return
def _matches_get_7(path: str) -> bool:
    return path == '/api/models'
def _handle_get_7(context: RouteContext, self, path, query, parsed):
    STORE = context.store
    llm = context.llm
    try:
        models = llm.list_models(STORE.settings)
    except llm.LLMError as exc:
        self._error(400, str(exc))
        return
    self._json({'ok': True, 'models': models, 'current': STORE.settings.get('model')})
    return
def _matches_get_8(path: str) -> bool:
    return path == '/api/voice/status'
def _handle_get_8(context: RouteContext, self, path, query, parsed):
    STORE = context.store
    voice = context.voice
    self._json({'ok': True, 'voice': voice.voice_status(STORE)})
    return
def _matches_get_9(path: str) -> bool:
    return path == '/api/vision/status'
def _handle_get_9(context: RouteContext, self, path, query, parsed):
    VISION = context.vision
    self._json({'ok': True, **VISION.status()})
    return
def _matches_get_10(path: str) -> bool:
    return path == '/api/desktop/apps'
def _handle_get_10(context: RouteContext, self, path, query, parsed):
    from assistant import desktop
    items = desktop.list_applications((query.get('query') or [''])[0], 120)
    self._json({'ok': True, 'applications': items})
    return
def _matches_get_11(path: str) -> bool:
    return path == '/api/desktop/windows'
def _handle_get_11(context: RouteContext, self, path, query, parsed):
    from assistant import desktop
    items = desktop.list_windows((query.get('query') or [''])[0], 100)
    self._json({'ok': True, 'windows': items})
    return
def _matches_get_12(path: str) -> bool:
    return path == '/api/desktop/tree'
def _handle_get_12(context: RouteContext, self, path, query, parsed):
    from assistant import automation
    hwnd = int((query.get('hwnd') or ['0'])[0] or 0)
    result = automation.control_tree(hwnd, max_depth=5, limit=180)
    self._json({'ok': True, **result})
    return
def _matches_get_13(path: str) -> bool:
    return path == '/api/minecraft/versions'
def _handle_get_13(context: RouteContext, self, path, query, parsed):
    MINECRAFT = context.minecraft
    self._json({'ok': True, **MINECRAFT.versions(limit=80)})
    return
def _matches_get_14(path: str) -> bool:
    return path == '/api/minecraft/servers'
def _handle_get_14(context: RouteContext, self, path, query, parsed):
    MINECRAFT = context.minecraft
    java = MINECRAFT.java_info()
    java.pop('path', None)
    self._json({'ok': True, 'servers': MINECRAFT.list_servers(), 'java': java})
    return
def _matches_get_15(path: str) -> bool:
    return path == '/api/minecraft/companion'
def _handle_get_15(context: RouteContext, self, path, query, parsed):
    MINECRAFT_COMPANION = context.minecraft_companion
    self._json({'ok': True, 'bots': MINECRAFT_COMPANION.list_status()})
    return
def _matches_get_16(path: str) -> bool:
    return path.startswith('/api/minecraft/servers/') and path.endswith('/logs')
def _handle_get_16(context: RouteContext, self, path, query, parsed):
    MINECRAFT = context.minecraft
    server_id = path.split('/')[4]
    self._json({'ok': True, 'logs': MINECRAFT.logs(server_id, limit=160)})
    return
def _matches_get_17(path: str) -> bool:
    return path == '/api/memory'
def _handle_get_17(context: RouteContext, self, path, query, parsed):
    STORE = context.store
    query_text = (query.get('query') or [''])[0]
    self._json({'ok': True, 'memory': STORE.list_memory(query_text)})
    return
def _matches_get_18(path: str) -> bool:
    return path == '/api/memory/semantic'
def _handle_get_18(context: RouteContext, self, path, query, parsed):
    STORE = context.store
    self._json({'ok': True, **STORE.semantic_memory.status()})
    return
def _matches_get_19(path: str) -> bool:
    return path == '/api/workflows'
def _handle_get_19(context: RouteContext, self, path, query, parsed):
    STORE = context.store
    self._json({'ok': True, 'workflows': STORE.list_workflows()})
    return
def _matches_get_20(path: str) -> bool:
    return path == '/api/projects'
def _handle_get_20(context: RouteContext, self, path, query, parsed):
    STORE = context.store
    include_archived = (query.get('all') or ['0'])[0] in ('1', 'true', 'yes')
    project_query = (query.get('query') or [''])[0]
    self._json({'ok': True, 'projects': STORE.list_projects(include_archived=include_archived, query=project_query)})
    return
def _matches_get_21(path: str) -> bool:
    return path == '/api/tasks'
def _handle_get_21(context: RouteContext, self, path, query, parsed):
    STORE = context.store
    task_status = (query.get('status') or [''])[0]
    task_chat_id = (query.get('chat_id') or [''])[0]
    self._json({'ok': True, 'tasks': STORE.list_tasks(chat_id=task_chat_id, status=task_status)})
    return
def _matches_get_22(path: str) -> bool:
    return path.startswith('/api/tasks/')
def _handle_get_22(context: RouteContext, self, path, query, parsed):
    STORE = context.store
    self._json({'ok': True, 'task': STORE.get_task(path.split('/')[3])})
    return
def _matches_get_23(path: str) -> bool:
    return path == '/api/actions'
def _handle_get_23(context: RouteContext, self, path, query, parsed):
    STORE = context.store
    self._json({'ok': True, 'actions': STORE.list_actions()})
    return
def _matches_get_24(path: str) -> bool:
    return path == '/api/chats'
def _handle_get_24(context: RouteContext, self, path, query, parsed):
    STORE = context.store
    self._json({'ok': True, 'chats': STORE.list_chats()})
    return
def _matches_get_25(path: str) -> bool:
    return path.startswith('/api/chats/')
def _handle_get_25(context: RouteContext, self, path, query, parsed):
    STORE = context.store
    chat_id = path.split('/')[3]
    try:
        chat = STORE.get_chat(chat_id)
    except (FileNotFoundError, ValueError) as exc:
        self._error(404, str(exc))
        return
    self._json({'ok': True, 'chat': chat})
    return
def _matches_get_26(path: str) -> bool:
    return path == '/api/notes'
def _handle_get_26(context: RouteContext, self, path, query, parsed):
    STORE = context.store
    q = (query.get('query') or [''])[0]
    self._json({'ok': True, 'notes': STORE.list_notes(q)})
    return
def _matches_get_27(path: str) -> bool:
    return path == '/api/reminders'
def _handle_get_27(context: RouteContext, self, path, query, parsed):
    STORE = context.store
    include_done = (query.get('all') or ['0'])[0] in ('1', 'true', 'yes')
    self._json({'ok': True, 'reminders': STORE.list_reminders(include_done)})
    return
def _matches_get_28(path: str) -> bool:
    return path == '/api/events'
def _handle_get_28(context: RouteContext, self, path, query, parsed):
    STORE = context.store
    self._json({'ok': True, 'events': STORE.drain_events()})
    return
def _matches_get_29(path: str) -> bool:
    return path == '/api/window'
def _handle_get_29(context: RouteContext, self, path, query, parsed):
    PORT = context.port
    open_app_window = context.open_app_window
    view = (query.get('view') or ['hud'])[0]
    width, height = (480, 720) if view == 'mini' else (1360, 900)
    ok = open_app_window(f'http://127.0.0.1:{PORT}/?view={view}', width, height)
    self._json({'ok': ok})
    return
GET_ROUTE_TABLE = (
    RouteDefinition("GET", "path in ('/', '/index.html')", _matches_get_0, _handle_get_0),
    RouteDefinition("GET", "path.startswith('/ui/')", _matches_get_1, _handle_get_1),
    RouteDefinition("GET", "path.startswith('/media/')", _matches_get_2, _handle_get_2),
    RouteDefinition("GET", "path == '/api/bootstrap'", _matches_get_3, _handle_get_3),
    RouteDefinition("GET", "path == '/api/health'", _matches_get_4, _handle_get_4),
    RouteDefinition("GET", "path == '/api/system'", _matches_get_5, _handle_get_5),
    RouteDefinition("GET", "path == '/api/weather'", _matches_get_6, _handle_get_6),
    RouteDefinition("GET", "path == '/api/models'", _matches_get_7, _handle_get_7),
    RouteDefinition("GET", "path == '/api/voice/status'", _matches_get_8, _handle_get_8),
    RouteDefinition("GET", "path == '/api/vision/status'", _matches_get_9, _handle_get_9),
    RouteDefinition("GET", "path == '/api/desktop/apps'", _matches_get_10, _handle_get_10),
    RouteDefinition("GET", "path == '/api/desktop/windows'", _matches_get_11, _handle_get_11),
    RouteDefinition("GET", "path == '/api/desktop/tree'", _matches_get_12, _handle_get_12),
    RouteDefinition("GET", "path == '/api/minecraft/versions'", _matches_get_13, _handle_get_13),
    RouteDefinition("GET", "path == '/api/minecraft/servers'", _matches_get_14, _handle_get_14),
    RouteDefinition("GET", "path == '/api/minecraft/companion'", _matches_get_15, _handle_get_15),
    RouteDefinition("GET", "path.startswith('/api/minecraft/servers/') and path.endswith('/logs')", _matches_get_16, _handle_get_16),
    RouteDefinition("GET", "path == '/api/memory'", _matches_get_17, _handle_get_17),
    RouteDefinition("GET", "path == '/api/memory/semantic'", _matches_get_18, _handle_get_18),
    RouteDefinition("GET", "path == '/api/workflows'", _matches_get_19, _handle_get_19),
    RouteDefinition("GET", "path == '/api/projects'", _matches_get_20, _handle_get_20),
    RouteDefinition("GET", "path == '/api/tasks'", _matches_get_21, _handle_get_21),
    RouteDefinition("GET", "path.startswith('/api/tasks/')", _matches_get_22, _handle_get_22),
    RouteDefinition("GET", "path == '/api/actions'", _matches_get_23, _handle_get_23),
    RouteDefinition("GET", "path == '/api/chats'", _matches_get_24, _handle_get_24),
    RouteDefinition("GET", "path.startswith('/api/chats/')", _matches_get_25, _handle_get_25),
    RouteDefinition("GET", "path == '/api/notes'", _matches_get_26, _handle_get_26),
    RouteDefinition("GET", "path == '/api/reminders'", _matches_get_27, _handle_get_27),
    RouteDefinition("GET", "path == '/api/events'", _matches_get_28, _handle_get_28),
    RouteDefinition("GET", "path == '/api/window'", _matches_get_29, _handle_get_29),
)

def dispatch_get(context: RouteContext, handler, path: str, query, parsed) -> None:
    try:
        dispatched = dispatch(GET_ROUTE_TABLE, context, handler, path, query, parsed)
        if not dispatched:
            handler._error(404, "Не найдено")
    except FileNotFoundError as exc:
        handler._error(404, str(exc))
    except ValueError as exc:
        handler._error(400, str(exc))
    except RuntimeError as exc:
        handler._error(503, str(exc))
    except Exception:
        context.log("GET error: " + traceback.format_exc())
        handler._error(500, "Внутренняя ошибка сервера")
