"""Typed route definitions and ordered path dispatch."""

from dataclasses import dataclass
from typing import Any, Callable

from .context import RouteContext


@dataclass(frozen=True)
class RouteDefinition:
    method: str
    name: str
    matches: Callable[[str], bool]
    handler: Callable[..., Any]


def dispatch(
    routes: tuple[RouteDefinition, ...], context: RouteContext, handler: Any, path: str, *args: Any
) -> bool:
    for route in routes:
        if route.matches(path):
            route.handler(context, handler, path, *args)
            return True
    return False
