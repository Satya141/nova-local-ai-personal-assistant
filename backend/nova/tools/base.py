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
    # Web addresses this result offered but did not show the model (a page's links), which may
    # then be opened without a confirmation. See Tool.url_arg.
    vouches: str = ""


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
    # For tools whose risk depends on the arguments: clicking a plain link is harmless,
    # clicking "Place order" is not. Decided by code from the arguments, never by the model.
    confirm_when: Callable[[Any], bool] | None = None
    # Only looks; changes nothing. Read-only tools stay allowed after untrusted input.
    read_only: bool = False
    # Returns content NOVA does not control (web pages, the screen), which may try to give orders.
    reads_untrusted: bool = False
    # For tools whose risk depends on the arguments: an event with guests sends them email, which
    # makes it HIGH; the same event for the user alone is MEDIUM. Decided by code, never the model.
    risk_for: Callable[[Any], Risk] | None = None
    # The argument holding a web address. After untrusted input, an address that neither the user
    # nor a page NOVA read supplied needs confirming: it could smuggle data out in the URL.
    url_arg: str | None = None
    # Needs the user sitting at the PC (it looks at their screen). Never offered to, or run for,
    # a request from a phone: the user is away from the PC, and "your screen" is not the phone's.
    at_the_pc: bool = False
    # Arguments holding email addresses. Each must come from the user's words, NOVA's memory or
    # something NOVA read: with no WhatsApp tool, the model saved a draft to an address it made up
    # ("ananditha@example.com"), and the user approved the card without spotting it.
    address_args: tuple[str, ...] = ()
    # The argument holding text the tool puts somewhere (type_in_app's `text`). After outside content
    # was read, a call whose text is the user's own words, word for word, is not asked about again:
    # nothing outside could have chosen it. Anything else still asks.
    user_text_arg: str | None = None
    # Checked before the gate, so a call that cannot work is refused without asking the user
    # (typing into a button, clicking a number that is not on screen).
    check: Callable[[Any], str | None] | None = None

    def schema(self) -> dict[str, Any]:
        raw = self.args_model.model_json_schema()
        parameters = _plain(raw, raw.get("$defs", {}))
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": parameters,
            },
        }


def _plain(node: Any, defs: dict[str, Any]) -> Any:
    """The schema without titles and with nested models written out in place: small models
    follow an inline schema far better than "$ref" pointers."""
    if isinstance(node, list):
        return [_plain(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        return _plain(defs[node["$ref"].rsplit("/", 1)[-1]], defs)
    return {
        key: (
            {name: _plain(prop, defs) for name, prop in value.items()} if key == "properties" else _plain(value, defs)
        )
        for key, value in node.items()
        if key not in ("title", "$defs")
    }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def unregister(self, name: str) -> None:
        """Remove a tool, e.g. when the account it uses is disconnected. Unknown names are ignored."""
        self._tools.pop(name, None)

    def replace(self, tool: Tool) -> None:
        """Swap in a different implementation of a registered tool (evaluations use this)."""
        if tool.name not in self._tools:
            raise KeyError(tool.name)
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self, remote: bool = False) -> list[dict[str, Any]]:
        """The tools offered to the model; `remote` (a request from a phone) leaves out `at_the_pc` ones."""
        return [tool.schema() for tool in self._tools.values() if not (remote and tool.at_the_pc)]
