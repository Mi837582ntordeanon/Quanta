"""Typed descriptions of the event payloads streamed to the UI."""

from __future__ import annotations

from typing import Any, Literal, NotRequired, TypedDict


class UserSavedEvent(TypedDict):
    type: Literal["user_saved"]
    message: dict[str, Any]
    chat_id: str
    title: str


class StepStartEvent(TypedDict):
    type: Literal["step_start"]
    step: int


class DeltaEvent(TypedDict):
    type: Literal["delta"]
    text: str


class StepEndEvent(TypedDict):
    type: Literal["step_end"]
    message: dict[str, Any]
    stopped: NotRequired[bool]


class ToolCallEvent(TypedDict):
    type: Literal["tool_call"]
    id: str
    name: str
    label: str
    args: dict[str, Any]
    allowed: bool
    confirm: bool


class ToolResultEvent(TypedDict):
    type: Literal["tool_result"]
    id: str
    name: str
    ok: bool
    text: str
    media: list[dict[str, Any]]


class DoneEvent(TypedDict):
    type: Literal["done"]
    chat_id: str
    title: str
    steps: int
    message: dict[str, Any] | None
    limit: NotRequired[bool]


class ErrorEvent(TypedDict):
    type: Literal["error"]
    message: str


class StoppedEvent(TypedDict):
    type: Literal["stopped"]


class PlannerStatusEvent(TypedDict, total=False):
    type: Literal["planner_status"]
    status: str
    model: str | None
    provider: str | None
    message: str


class ProviderFallbackEvent(TypedDict, total=False):
    type: Literal["provider_fallback"]
    provider: str
    model: str
    reason: str


AgentEvent = (
    UserSavedEvent | StepStartEvent | DeltaEvent | StepEndEvent | ToolCallEvent
    | ToolResultEvent | DoneEvent | ErrorEvent | StoppedEvent | PlannerStatusEvent
    | ProviderFallbackEvent
)
