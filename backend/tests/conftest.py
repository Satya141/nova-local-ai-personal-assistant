from __future__ import annotations

import hashlib
import math
import re
from collections.abc import AsyncIterator
from typing import Any

import pytest
from pydantic import BaseModel

from nova.database import Database
from nova.inference.base import ChatChunk, Message, ModelError, ModelStatus, ToolCall
from nova.memory.long_term import MemoryStore
from nova.memory.store import ConversationStore
from nova.tools.base import Risk, Tool, ToolRegistry, ToolResult


class FakeProvider:
    """A model that replays a script: one list of chunks (or an error) per call to chat()."""

    def __init__(self, script: list[list[ChatChunk] | Exception], json_replies: list[Any] | None = None) -> None:
        self._script = list(script)
        self._json_replies = list(json_replies or [])
        self.requests: list[list[Message]] = []
        self.offered: list[list[str]] = []  # the tool names offered on each call
        self.json_requests: list[list[Message]] = []
        self.warmed = 0

    async def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> AsyncIterator[ChatChunk]:
        self.requests.append(messages)
        self.offered.append([tool["function"]["name"] for tool in tools])
        step = self._script.pop(0)
        if isinstance(step, Exception):
            raise step
        for chunk in step:
            yield chunk

    async def complete_json(self, messages: list[Message], schema: dict[str, Any]) -> Any:
        self.json_requests.append(messages)
        reply = self._json_replies.pop(0) if self._json_replies else {"memories": []}
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def warm(self) -> None:
        self.warmed += 1

    async def status(self) -> ModelStatus:
        return ModelStatus(True, "fake")

    async def aclose(self) -> None:
        pass


class FakeEmbedder:
    """Bag-of-words vectors: texts that share words point the same way. Deterministic and offline."""

    name = "fake-embed"
    dims = 256

    def __init__(self) -> None:
        self.available = True
        self.calls = 0

    async def embed(self, texts: list[str], kind: str) -> list[list[float]] | None:
        self.calls += 1
        if not self.available:
            return None
        vectors = []
        for text in texts:
            vector = [0.0] * self.dims
            for word in re.findall(r"[a-z0-9]+", text.lower()):
                if word in {"the", "user", "a", "is", "my", "of", "to", "and"}:
                    continue
                vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dims] += 1.0
            norm = math.sqrt(sum(v * v for v in vector)) or 1.0
            vectors.append([v / norm for v in vector])
        return vectors

    async def aclose(self) -> None:
        pass


def call(name: str, **arguments: Any) -> ChatChunk:
    return ChatChunk(tool_calls=(ToolCall(id=f"call-{name}", name=name, arguments=arguments),))


def say(text: str) -> ChatChunk:
    return ChatChunk(text=text)


class EchoArgs(BaseModel):
    text: str


@pytest.fixture
def executed() -> list[str]:
    """Names of the tools whose handlers actually ran."""
    return []


@pytest.fixture
def registry(executed: list[str]) -> ToolRegistry:
    def handler(name: str):
        async def run(args: EchoArgs) -> ToolResult:
            executed.append(name)
            return ToolResult(True, f"{name}:{args.text}")

        return run

    async def explode(args: EchoArgs) -> ToolResult:
        raise RuntimeError("disk on fire")

    registry = ToolRegistry()
    registry.register(Tool("echo", "Echo text.", EchoArgs, handler("echo"), lambda a: f"Echo {a.text}"))
    registry.register(Tool("remember", "Remember text.", EchoArgs, handler("remember"), lambda a: f"Remember {a.text}"))
    registry.register(
        Tool(
            "danger",
            "A risky action.",
            EchoArgs,
            handler("danger"),
            lambda a: f"Danger {a.text}",
            risk=Risk.HIGH,
            requires_confirmation=True,
        )
    )
    registry.register(Tool("broken", "Always fails.", EchoArgs, explode, lambda a: "Broken"))
    return registry


@pytest.fixture
def db() -> Database:
    database = Database(":memory:")
    yield database
    database.close()


@pytest.fixture
def store(db: Database) -> ConversationStore:
    return ConversationStore(db)


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def memory(db: Database, embedder: FakeEmbedder) -> MemoryStore:
    # The fake embedder's scores are lower than a real model's; thresholds are scaled to match.
    return MemoryStore(db, embedder, duplicate_similarity=0.85, relevant_similarity=0.3)


__all__ = ["FakeEmbedder", "FakeProvider", "ModelError", "call", "say"]
