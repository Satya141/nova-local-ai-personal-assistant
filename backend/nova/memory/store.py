"""Short-term memory: conversations and their messages."""

from __future__ import annotations

import json
import uuid

from nova.database import Database, timestamp
from nova.inference.base import Message, ToolCall


class ConversationStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def create_conversation(self) -> str:
        conversation_id = uuid.uuid4().hex
        self._db.run("INSERT INTO conversations (id, created_at) VALUES (?, ?)", (conversation_id, timestamp()))
        return conversation_id

    def conversation_exists(self, conversation_id: str) -> bool:
        return self._db.fetch_one("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)) is not None

    def add_message(self, conversation_id: str, message: Message) -> None:
        tool_calls = (
            json.dumps([{"id": c.id, "name": c.name, "arguments": c.arguments} for c in message.tool_calls])
            if message.tool_calls
            else None
        )
        self._db.run(
            "INSERT INTO messages (conversation_id, role, content, tool_calls, tool_name, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, message.role, message.content, tool_calls, message.tool_name, timestamp()),
        )

    def history(self, conversation_id: str, limit: int) -> list[Message]:
        """The most recent messages, oldest first, starting at a user message."""
        rows = self._db.fetch(
            "SELECT role, content, tool_calls, tool_name FROM messages "
            "WHERE conversation_id = ? ORDER BY id DESC LIMIT ?",
            (conversation_id, limit),
        )
        messages = [
            Message(
                role=row["role"],
                content=row["content"],
                tool_calls=tuple(ToolCall(**call) for call in json.loads(row["tool_calls"])) if row["tool_calls"] else (),
                tool_name=row["tool_name"],
            )
            for row in reversed(rows)
        ]
        # The window may cut a turn in half; a tool result without its call confuses the model.
        while messages and messages[0].role != "user":
            messages.pop(0)
        return messages
