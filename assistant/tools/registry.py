"""Stable assembly order for decorated tool methods."""

from typing import Any


TOOL_ORDER = ('list_dir', 'read_file', 'write_file', 'search_files', 'move_path', 'copy_path', 'delete_path', 'run_command', 'open_path', 'screenshot', 'mouse_click', 'type_text', 'press_keys', 'volume', 'clipboard_get', 'clipboard_set', 'web_search', 'fetch_url', 'memory_save', 'memory_list', 'memory_delete', 'notes_create', 'notes_list', 'notes_update', 'notes_delete', 'reminder_create', 'reminder_list', 'reminder_delete', 'project_list', 'project_create', 'project_update', 'create_3d_model', 'generate_image', 'system_info', 'list_processes', 'kill_process', 'minecraft_status', 'weather', 'pc_apps', 'pc_app_open', 'pc_windows', 'pc_window_focus', 'pc_window_close', 'pc_uia_tree', 'pc_uia_action', 'pc_vision', 'yandex_music', 'minecraft_servers', 'minecraft_server_start', 'minecraft_server_stop', 'minecraft_server_command')


def register_tool_methods(toolbox: Any) -> None:
    definitions = {}
    for owner in reversed(type(toolbox).__mro__):
        for method_name, member in owner.__dict__.items():
            function = getattr(member, "__func__", member)
            spec = getattr(function, "__tool_spec__", None)
            if spec is not None:
                definitions[spec.name] = (method_name, spec)
    missing = [name for name in TOOL_ORDER if name not in definitions]
    if missing:
        raise RuntimeError("Decorated tools missing from registry: " + ", ".join(missing))
    for name in TOOL_ORDER:
        method_name, spec = definitions[name]
        toolbox.add(name, spec.description, spec.parameters, getattr(toolbox, method_name),
                    label=spec.label, risk=spec.risk, confirm=spec.confirm,
                    capabilities=spec.capabilities, network=spec.network)
