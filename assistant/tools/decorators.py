"""Metadata for built-in tools and validation of their declared capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence


@dataclass(frozen=True)
class ToolSpec:
    name: str
    label: str
    risk: str
    confirm: bool
    capabilities: tuple[str, ...]
    network: bool
    description: str
    parameters: dict[str, Any]


def tool(
    name: str,
    label: str,
    risk: str = "read",
    confirm: bool = False,
    *,
    capabilities: Sequence[str],
    network: bool = True,
    description: str,
    parameters: dict[str, Any],
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare a built-in tool; capabilities are explicit and never empty."""
    if isinstance(capabilities, (str, bytes)):
        raise TypeError("capabilities must be a non-empty sequence of names")
    normalized = tuple(str(item).strip() for item in capabilities if str(item).strip())
    if not normalized:
        raise ValueError("capabilities must contain at least one non-empty name")
    if not name.strip():
        raise ValueError("tool name cannot be empty")

    spec = ToolSpec(
        name=name,
        label=label,
        risk=risk,
        confirm=bool(confirm),
        capabilities=normalized,
        network=bool(network),
        description=description,
        parameters=parameters,
    )

    def decorate(func: Callable[..., Any]) -> Callable[..., Any]:
        setattr(func, "__tool_spec__", spec)
        return func

    return decorate
