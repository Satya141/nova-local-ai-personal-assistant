"""Server-to-client notifications that are not part of a chat turn.

Reminders firing and memories being saved happen on NOVA's schedule, not in
answer to a request, so clients hold open `GET /api/events` and receive them
here. Every connected client gets every event.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

Event = dict[str, Any]

_QUEUE_LIMIT = 100


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[Event]] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def publish(self, event: Event) -> None:
        for queue in self._subscribers:
            # A client that stopped reading must not block the others.
            if queue.qsize() < _QUEUE_LIMIT:
                queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[Event]]:
        queue: asyncio.Queue[Event] = asyncio.Queue()
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)
