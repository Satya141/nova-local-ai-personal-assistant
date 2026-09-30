from __future__ import annotations

import sqlite3

from nova.database import MIGRATIONS, Database
from nova.inference.base import Message, ToolCall
from nova.memory.store import ConversationStore


def test_messages_round_trip_including_tool_calls(store):
    conversation = store.create_conversation()
    tool_call = ToolCall(id="c1", name="open_application", arguments={"name": "Notepad"})
    store.add_message(conversation, Message("user", "open notepad"))
    store.add_message(conversation, Message("assistant", "", tool_calls=(tool_call,)))
    store.add_message(conversation, Message("tool", '{"opened": "Notepad"}', tool_name="open_application"))

    history = store.history(conversation, 10)

    assert [m.role for m in history] == ["user", "assistant", "tool"]
    assert history[1].tool_calls == (tool_call,)
    assert history[2].tool_name == "open_application"


def test_conversations_are_isolated(store):
    first, second = store.create_conversation(), store.create_conversation()
    store.add_message(first, Message("user", "one"))
    store.add_message(second, Message("user", "two"))

    assert [m.content for m in store.history(first, 10)] == ["one"]
    assert store.conversation_exists(first)
    assert not store.conversation_exists("missing")


def test_history_window_never_starts_mid_turn(store):
    conversation = store.create_conversation()
    store.add_message(conversation, Message("user", "first"))
    store.add_message(conversation, Message("assistant", "", tool_calls=(ToolCall("c", "echo", {}),)))
    store.add_message(conversation, Message("tool", "result", tool_name="echo"))
    store.add_message(conversation, Message("assistant", "done"))
    store.add_message(conversation, Message("user", "second"))
    store.add_message(conversation, Message("assistant", "ok"))

    # A window of 4 would begin at the orphaned tool result; it must slide to the next user message.
    assert [m.content for m in store.history(conversation, 4)] == ["second", "ok"]


def test_history_survives_reopening_the_database(tmp_path):
    path = tmp_path / "data" / "nova.db"
    db = Database(path)
    store = ConversationStore(db)
    conversation = store.create_conversation()
    store.add_message(conversation, Message("user", "remember me"))
    db.close()

    reopened = Database(path)
    assert [m.content for m in ConversationStore(reopened).history(conversation, 10)] == ["remember me"]
    reopened.close()


def test_new_database_gets_every_migration(db):
    assert db.version == len(MIGRATIONS)
    tables = {row["name"] for row in db.fetch("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"conversations", "messages", "memories", "reminders", "action_log"} <= tables


def test_phase_one_database_is_upgraded_in_place(tmp_path):
    """A database written by Phase 1 keeps its conversations and gains the new tables."""
    path = tmp_path / "nova.db"
    legacy = sqlite3.connect(path)
    legacy.executescript(MIGRATIONS[0])
    legacy.execute("PRAGMA user_version = 1")
    legacy.execute("INSERT INTO conversations (id, created_at) VALUES ('old', '2026-09-30T00:00:00+00:00')")
    legacy.commit()
    legacy.close()

    db = Database(path)
    assert db.version == len(MIGRATIONS)
    assert ConversationStore(db).conversation_exists("old")
    assert db.fetch("SELECT COUNT(*) AS n FROM memories")[0]["n"] == 0
    db.close()

    # Opening again applies nothing twice.
    assert Database(path).version == len(MIGRATIONS)
