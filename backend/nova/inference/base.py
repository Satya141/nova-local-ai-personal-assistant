"""The model provider interface. The agent only ever talks to this."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Role = Literal["system", "user", "assistant", "tool"]


class ModelError(Exception):
    """The model could not produce a response. The message is shown to the user."""


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: Role
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    # Set on role="tool" messages: the tool whose result this is.
    tool_name: str | None = None


@dataclass(frozen=True)
class ChatChunk:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ModelStatus:
    ready: bool
    model: str
    detail: str | None = None


class ModelProvider(Protocol):
    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> AsyncIterator[ChatChunk]:
        """Stream one assistant turn. Raises ModelError on failure."""
        ...

    async def complete_json(self, messages: list[Message], schema: dict[str, Any]) -> Any:
        """One non-streamed reply constrained to `schema`, parsed. Raises ModelError on failure."""
        ...

    async def warm(self) -> None:
        """Load the model into memory ahead of the first request. Best effort."""
        ...

    async def status(self) -> ModelStatus: ...

    async def aclose(self) -> None: ...
