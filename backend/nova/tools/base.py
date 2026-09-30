"""The tool interface and registry.

Every action NOVA can take is a Tool: a typed argument model, a risk level and
a handler. The model never executes anything itself. It names a tool, the agent
validates the arguments against the tool's schema, the permission gate decides,
and only then does the handler run.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class Risk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    # Sent back to the model as the tool's observation.
    content: str


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[Any], Awaitable[ToolResult]]
    # One-line, human-readable description of a specific call, shown in the UI.
    describe: Callable[[Any], str]
    risk: Risk = Risk.LOW
    requires_confirmation: bool = False

    def schema(self) -> dict[str, Any]:
        parameters = self.args_model.model_json_schema()
        parameters.pop("title", None)
        for prop in parameters.get("properties", {}).values():
            prop.pop("title", None)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def replace(self, tool: Tool) -> None:
        """Swap in a different implementation of a registered tool (evaluations use this)."""
        if tool.name not in self._tools:
            raise KeyError(tool.name)
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]
