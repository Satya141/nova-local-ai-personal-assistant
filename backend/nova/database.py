"""The local SQLite database and its schema migrations.

One file holds everything NOVA remembers. Each migration runs once, in order;
`PRAGMA user_version` records how many have been applied. Never edit a
migration that has shipped: add a new one.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MIGRATIONS: tuple[str, ...] = (
    # 1: conversations (Phase 1)
    """
    CREATE TABLE IF NOT EXISTS conversations (
        id         TEXT PRIMARY KEY,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS messages (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        role            TEXT NOT NULL,
        content         TEXT NOT NULL,
        tool_calls      TEXT,
        tool_name       TEXT,
        created_at      TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, id);
    """,
    # 2: long-term memory, reminders and the action log (Phase 2)
    """
    CREATE TABLE memories (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        category        TEXT NOT NULL,
        content         TEXT NOT NULL,
        source          TEXT NOT NULL,
        conversation_id TEXT,
        embedding       BLOB,
        embedding_model TEXT,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL
    );
    CREATE TABLE reminders (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        text            TEXT NOT NULL,
        due_at          TEXT NOT NULL,
        repeat          TEXT NOT NULL,
        status          TEXT NOT NULL,
        pending_ack     INTEGER NOT NULL DEFAULT 0,
        fired_at        TEXT,
        conversation_id TEXT,
        created_at      TEXT NOT NULL
    );
    CREATE INDEX idx_reminders_due ON reminders(status, due_at);
    CREATE TABLE action_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        conversation_id TEXT,
        tool            TEXT NOT NULL,
        arguments       TEXT NOT NULL,
        decision        TEXT NOT NULL,
        ok              INTEGER,
        result          TEXT,
        created_at      TEXT NOT NULL
    );
    """,
    # 3: user settings (Phase 3)
    """
    CREATE TABLE settings (
        key   TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
    # 4: scheduled tasks, stored as reminders that carry a request (Phase 5)
    """
    ALTER TABLE reminders ADD COLUMN task TEXT;
    ALTER TABLE reminders ADD COLUMN result TEXT;
    """,
    # 5: the action log keeps the words the user saw ("Sort 7 files in Downloads into ...") (Phase 5)
    """
    ALTER TABLE action_log ADD COLUMN summary TEXT;
    """,
    # 6: phones paired with this NOVA (Phase 6). Only a hash of each phone's key is kept.
    """
    CREATE TABLE devices (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        name       TEXT NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        last_seen  TEXT
    );
    """,
)


def utc_now() -> datetime:
    return datetime.now(UTC)


def timestamp(moment: datetime | None = None) -> str:
    return (moment or utc_now()).astimezone(UTC).isoformat(timespec="seconds")


def parse_timestamp(text: str) -> datetime:
    return datetime.fromisoformat(text)


class Database:
    """A single connection shared by every store, serialised by a lock.

    Queries are sub-millisecond at NOVA's scale, so they run inline on the
    event loop rather than in threads.
    """

    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(path), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            applied = self._connection.execute("PRAGMA user_version").fetchone()[0]
            for version, script in enumerate(MIGRATIONS[applied:], start=applied + 1):
                with self._connection:
                    self._connection.executescript(script)
                    self._connection.execute(f"PRAGMA user_version = {version}")

    @property
    def version(self) -> int:
        with self._lock:
            return self._connection.execute("PRAGMA user_version").fetchone()[0]

    def fetch(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._connection.execute(sql, params).fetchall()

    def fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._connection.execute(sql, params).fetchone()

    def run(self, sql: str, params: tuple[Any, ...] = ()) -> int:
        """Execute one write and commit it. Returns the new row id, if any."""
        with self._lock, self._connection:
            return self._connection.execute(sql, params).lastrowid or 0

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._connection:
            yield self._connection

    def close(self) -> None:
        with self._lock:
            self._connection.close()
