"""A record of every action the agent attempted, whether or not it ran.

This is what lets the user see exactly what NOVA did on their behalf, and it
is the raw material for the activity timeline later on.
"""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Any

from nova.database import Database, timestamp

_RESULT_LIMIT = 2000


class Outcome(StrEnum):
    RAN = "ran"  # allowed without asking
    APPROVED = "approved"  # the user confirmed
    DECLINED = "declined"  # the user cancelled, or did not answer in time
    REJECTED = "rejected"  # unknown tool or invalid arguments; never reached the gate


class ActionLog:
    def __init__(self, db: Database) -> None:
        self._db = db

    def record(
        self,
        conversation_id: str | None,
        tool: str,
        arguments: dict[str, Any],
        outcome: Outcome,
        ok: bool,
        result: str,
    ) -> None:
        self._db.run(
            "INSERT INTO action_log (conversation_id, tool, arguments, decision, ok, result, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (conversation_id, tool, json.dumps(arguments), outcome.value, int(ok), result[:_RESULT_LIMIT], timestamp()),
        )

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._db.fetch("SELECT * FROM action_log ORDER BY id DESC LIMIT ?", (limit,))
        return [
            {
                "id": row["id"],
                "conversation_id": row["conversation_id"],
                "tool": row["tool"],
                "arguments": json.loads(row["arguments"]),
                "outcome": row["decision"],
                "ok": bool(row["ok"]),
                "result": row["result"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]
