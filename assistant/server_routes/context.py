"""Explicit dependencies used by HTTP route handlers."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RouteContext:
    active: Any
    active_lock: Any
    command_sandbox: Any
    data_dir: Any
    deny_pending_for_chat: Any
    health_info: Any
    hotkey: Any
    llm: Any
    log: Any
    media_dir: Any
    minecraft: Any
    minecraft_companion: Any
    open_app_window: Any
    pending: Any
    pending_lock: Any
    port: Any
    request_resource_profile: Any
    resource_lock: Any
    resource_operation_lock: Any
    resource_state: Any
    server: Any
    show_path_in_explorer: Any
    stop: Any
    store: Any
    system_stats: Any
    trace_store: Any
    toolbox: Any
    ui_dir: Any
    version: Any
    vision: Any
    voice: Any
