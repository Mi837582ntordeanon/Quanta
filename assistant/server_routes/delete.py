"""DELETE endpoint route table and handlers."""

import traceback
from .context import RouteContext
from .table import RouteDefinition, dispatch


def _matches_delete_0(path: str) -> bool:
    return path.startswith('/api/chats/')
def _handle_delete_0(context: RouteContext, self, path):
    STORE = context.store
    chat_id = path.split('/')[3]
    STORE.delete_chat(chat_id)
    self._json({'ok': True})
    return
def _matches_delete_1(path: str) -> bool:
    return path.startswith('/api/notes/')
def _handle_delete_1(context: RouteContext, self, path):
    STORE = context.store
    note_id = path.split('/')[3]
    self._json({'ok': STORE.delete_note(note_id)})
    return
def _matches_delete_2(path: str) -> bool:
    return path.startswith('/api/reminders/')
def _handle_delete_2(context: RouteContext, self, path):
    STORE = context.store
    reminder_id = path.split('/')[3]
    self._json({'ok': STORE.delete_reminder(reminder_id)})
    return
def _matches_delete_3(path: str) -> bool:
    return path.startswith('/api/projects/')
def _handle_delete_3(context: RouteContext, self, path):
    STORE = context.store
    project_id = path.split('/')[3]
    self._json({'ok': STORE.delete_project(project_id)})
    return
def _matches_delete_4(path: str) -> bool:
    return path.startswith('/api/memory/')
def _handle_delete_4(context: RouteContext, self, path):
    STORE = context.store
    memory_id = path.split('/')[3]
    self._json({'ok': STORE.delete_memory(memory_id)})
    return
def _matches_delete_5(path: str) -> bool:
    return path.startswith('/api/workflows/')
def _handle_delete_5(context: RouteContext, self, path):
    STORE = context.store
    workflow_id = path.split('/')[3]
    self._json({'ok': STORE.delete_workflow(workflow_id)})
    return
def _matches_delete_6(path: str) -> bool:
    return path == '/api/actions'
def _handle_delete_6(context: RouteContext, self, path):
    STORE = context.store
    STORE.clear_actions()
    self._json({'ok': True})
    return
DELETE_ROUTE_TABLE = (
    RouteDefinition("DELETE", "path.startswith('/api/chats/')", _matches_delete_0, _handle_delete_0),
    RouteDefinition("DELETE", "path.startswith('/api/notes/')", _matches_delete_1, _handle_delete_1),
    RouteDefinition("DELETE", "path.startswith('/api/reminders/')", _matches_delete_2, _handle_delete_2),
    RouteDefinition("DELETE", "path.startswith('/api/projects/')", _matches_delete_3, _handle_delete_3),
    RouteDefinition("DELETE", "path.startswith('/api/memory/')", _matches_delete_4, _handle_delete_4),
    RouteDefinition("DELETE", "path.startswith('/api/workflows/')", _matches_delete_5, _handle_delete_5),
    RouteDefinition("DELETE", "path == '/api/actions'", _matches_delete_6, _handle_delete_6),
)

def dispatch_delete(context: RouteContext, handler, path: str) -> None:
    try:
        dispatched = dispatch(DELETE_ROUTE_TABLE, context, handler, path)
        if not dispatched:
            handler._error(404, "Не найдено")
    except Exception:
        context.log("DELETE error: " + traceback.format_exc())
        handler._error(500, "Внутренняя ошибка сервера")
