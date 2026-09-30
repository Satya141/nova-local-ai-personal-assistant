"""The permission gate: the only path from a model's tool call to execution.

The decision is made from the tool's declared metadata, never from anything
the model says. A call that needs confirmation blocks until the user answers
through the API, and is denied if they do not answer in time.
"""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import Any

from nova.tools.base import Risk, Tool


class Decision(StrEnum):
    ALLOW = "allow"
    CONFIRM = "confirm"


class PermissionGate:
    def __init__(self, confirm_timeout: float = 120.0) -> None:
        self._confirm_timeout = confirm_timeout
        self._pending: dict[str, asyncio.Future[bool]] = {}

    def check(self, tool: Tool, args: Any) -> Decision:
        if tool.requires_confirmation or tool.risk is not Risk.LOW:
            return Decision.CONFIRM
        return Decision.ALLOW

    def open_request(self, call_id: str) -> None:
        """Register a confirmation before the UI is told about it, so an answer cannot arrive early."""
        self._pending[call_id] = asyncio.get_running_loop().create_future()

    async def wait(self, call_id: str) -> bool:
        """Block until the user answers. No answer within the timeout is a denial."""
        try:
            return await asyncio.wait_for(self._pending[call_id], self._confirm_timeout)
        except TimeoutError:
            return False
        finally:
            self._pending.pop(call_id, None)

    def discard(self, call_id: str) -> None:
        future = self._pending.pop(call_id, None)
        if future and not future.done():
            future.cancel()

    def resolve(self, call_id: str, approved: bool) -> bool:
        """Record the user's answer. Returns False if nothing is waiting on this id."""
        future = self._pending.get(call_id)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True
