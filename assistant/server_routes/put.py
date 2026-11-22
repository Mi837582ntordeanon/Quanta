"""PUT endpoint route table and handlers."""

import traceback
from .context import RouteContext
from .table import RouteDefinition, dispatch


def _matches_put_0(path: str) -> bool:
    return path.startswith('/api/notes/')
def _handle_put_0(context: RouteContext, self, path):
    STORE = context.store
    note_id = path.split('/')[3]
    data = self._read_json()
    note = STORE.update_note(note_id, title=data.get('title'), text=data.get('text'))
    self._json({'ok': True, 'note': note})
    return
def _matches_put_1(path: str) -> bool:
    return path.startswith('/api/projects/')
def _handle_put_1(context: RouteContext, self, path):
    STORE = context.store
    project_id = path.split('/')[3]
    data = self._read_json()
    allowed = {'title', 'goal', 'status', 'next_step', 'summary', 'decisions', 'files'}
    changes = {key: data[key] for key in allowed if key in data}
    project = STORE.update_project(project_id, **changes)
    self._json({'ok': True, 'project': project})
    return
PUT_ROUTE_TABLE = (
    RouteDefinition("PUT", "path.startswith('/api/notes/')", _matches_put_0, _handle_put_0),
    RouteDefinition("PUT", "path.startswith('/api/projects/')", _matches_put_1, _handle_put_1),
)

def dispatch_put(context: RouteContext, handler, path: str) -> None:
    try:
        dispatched = dispatch(PUT_ROUTE_TABLE, context, handler, path)
        if not dispatched:
            handler._error(404, "Не найдено")
    except FileNotFoundError as exc:
        handler._error(404, str(exc))
    except ValueError as exc:
        handler._error(400, str(exc))
    except Exception:
        context.log("PUT error: " + traceback.format_exc())
        handler._error(500, "Внутренняя ошибка сервера")
