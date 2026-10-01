"""Phase 9: the life timeline."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from conftest import FakeProvider, say
from fastapi.testclient import TestClient

from nova.api import create_app
from nova.config import Settings
from nova.database import timestamp
from nova.timeline import Timeline, describe
from nova.tools.timeline import RecallActivityArgs, timeline_tools

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)


def at(day: date, hour: int, minute: int = 0) -> str:
    """A stored (UTC) timestamp for a local time."""
    return timestamp(datetime.combine(day, time(hour, minute)).astimezone())


def fill(db) -> None:
    db.run("INSERT INTO conversations (id, created_at) VALUES ('c1', ?)", (at(YESTERDAY, 9),))
    db.run(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES ('c1', 'user', ?, ?)",
        ("open VS Code and find my budget sheet", at(YESTERDAY, 9, 5)),
    )
    db.run(
        "INSERT INTO messages (conversation_id, role, content, created_at) VALUES ('c1', 'assistant', ?, ?)",
        ("Opened VS Code.", at(YESTERDAY, 9, 6)),
    )
    db.run(
        "INSERT INTO action_log (tool, arguments, decision, ok, result, created_at, summary) VALUES (?, '{}', ?, ?, '', ?, ?)",
        ("open_application", "ran", 1, at(YESTERDAY, 9, 6), "Open VS Code"),
    )
    db.run(
        "INSERT INTO action_log (tool, arguments, decision, ok, result, created_at, summary) VALUES (?, '{}', ?, ?, '', ?, ?)",
        ("close_application", "declined", 0, at(YESTERDAY, 18), "Close Spotify"),
    )
    db.run(
        "INSERT INTO memories (category, content, source, created_at, updated_at) VALUES ('project', ?, 'extracted', ?, ?)",
        ("The user is building NOVA", at(YESTERDAY, 9, 7), at(YESTERDAY, 9, 7)),
    )
    db.run(
        "INSERT INTO reminders (text, due_at, repeat, status, pending_ack, fired_at, created_at) "
        "VALUES ('Stretch', ?, 'none', 'done', 0, ?, ?)",
        (at(TODAY, 0, 1), at(TODAY, 0, 1), at(YESTERDAY, 20)),
    )
    db.run(
        "INSERT INTO action_log (tool, arguments, decision, ok, result, created_at, summary) VALUES (?, '{}', ?, ?, '', ?, ?)",
        ("open_application", "ran", 1, at(TODAY, 0, 2), "Open VS Code"),
    )


def test_a_day_holds_what_was_asked_done_remembered_and_rung(db):
    fill(db)
    entries = Timeline(db).between(YESTERDAY, YESTERDAY)
    assert [(e.kind, e.text) for e in entries] == [
        ("asked", "open VS Code and find my budget sheet"),
        ("did", "Open VS Code"),
        ("remembered", "The user is building NOVA"),
        ("did", "Close Spotify"),
    ], "NOVA's own replies are not entries; just after midnight belongs to today"
    today = Timeline(db).between(TODAY, TODAY)
    assert [e.kind for e in today] == ["reminded", "did"]


def test_days_come_newest_first_with_empty_days_kept(db):
    fill(db)
    days = Timeline(db).days(TODAY, 3)
    assert [d["day"] for d in days] == [TODAY.isoformat(), YESTERDAY.isoformat(), (TODAY - timedelta(days=2)).isoformat()]
    assert len(days[1]["entries"]) == 4 and days[2]["entries"] == []


def test_search_finds_the_latest_first(db):
    fill(db)
    found = Timeline(db).search("vs code")
    assert found[0].at > found[-1].at and found[0].kind == "did"
    assert Timeline(db).search("vs code", YESTERDAY, YESTERDAY)[0].text == "Open VS Code"
    assert Timeline(db).search("photoshop") == []


def test_described_for_the_model_with_how_actions_ended(db):
    fill(db)
    text = describe(Timeline(db).between(YESTERDAY, YESTERDAY))
    assert text.splitlines()[0] == f"{YESTERDAY:%A} {YESTERDAY.day} {YESTERDAY:%B %Y}:"
    assert "you asked: open VS Code" in text and "NOVA: Close Spotify (declined)" in text


async def recall(db, **args) -> tuple[bool, str]:
    (tool,) = timeline_tools(Timeline(db))
    result = await tool.handler(RecallActivityArgs(**args))
    return result.ok, result.content


async def test_the_tool_answers_what_happened_yesterday(db):
    fill(db)
    ok, text = await recall(db, when="yesterday")
    assert ok and "Close Spotify" in text and "Stretch" not in text
    assert f"{YESTERDAY:%A} {YESTERDAY.day} {YESTERDAY:%B %Y}" in text, "the date is spelled out for the model"


async def test_the_tool_finds_when_something_last_happened(db):
    fill(db)
    ok, text = await recall(db, about="VS Code")
    assert ok and text.strip().endswith("Open VS Code") and f"{TODAY:%A}" in text


async def test_the_tool_refuses_unknown_times_and_the_future(db):
    ok, text = await recall(db, when="the other day")
    assert not ok and "yesterday" in text, "it says which phrases work"
    ok, _ = await recall(db, when=(TODAY + timedelta(days=3)).isoformat())
    assert not ok
    ok, text = await recall(db, when="last week", about="nothing like this")
    assert ok and "Nothing recorded" in text


def test_the_tool_only_reads(db):
    (tool,) = timeline_tools(Timeline(db))
    assert tool.read_only and not tool.reads_untrusted and not tool.requires_confirmation


@pytest.fixture
def client(tmp_path):
    settings = Settings(api_token="t", data_dir=tmp_path, embed_model="", voice=False, vision_model="", browser=False)
    provider = FakeProvider([[say("hi")]], json_replies=[{"summary": "You worked on NOVA and opened VS Code."}])
    with TestClient(create_app(settings, provider)) as test_client:
        yield test_client


def test_the_api_serves_days_and_sums_one_up_on_request(client):
    fill(client.app.state.timeline._db)
    headers = {"Authorization": "Bearer t"}
    days = client.get("/api/timeline", params={"days": 2}, headers=headers).json()["days"]
    assert [d["day"] for d in days] == [TODAY.isoformat(), YESTERDAY.isoformat()]
    summary = client.post("/api/timeline/summary", json={"day": YESTERDAY.isoformat()}, headers=headers).json()
    assert summary["summary"] == "You worked on NOVA and opened VS Code."
    empty = client.post("/api/timeline/summary", json={"day": "2020-01-01"}, headers=headers).json()
    assert empty["summary"] == "Nothing happened with NOVA that day."


THURSDAY = date(2026, 10, 1)


@pytest.mark.parametrize(
    ("phrase", "first", "last"),
    [
        ("today", "2026-10-01", "2026-10-01"),
        ("Yesterday", "2026-09-30", "2026-09-30"),
        ("the day before yesterday", "2026-09-29", "2026-09-29"),
        ("three days ago", "2026-09-28", "2026-09-28"),
        ("on Monday", "2026-09-28", "2026-09-28"),
        ("thursday", "2026-10-01", "2026-10-01"),  # a weekday alone: the most recent, today included
        ("last Thursday", "2026-09-24", "2026-09-24"),  # "last": the one before today
        ("this week", "2026-09-28", "2026-10-01"),
        ("last week", "2026-09-21", "2026-09-27"),
        ("last 7 days", "2026-09-25", "2026-10-01"),
        ("last month", "2026-09-01", "2026-09-30"),
        ("28 September", "2026-09-28", "2026-09-28"),
        ("December 28th", "2025-12-28", "2025-12-28"),  # not yet this year: last year's
        ("2026-09-28", "2026-09-28", "2026-09-28"),
    ],
)
def test_past_phrases_become_days_in_code_not_by_the_model(phrase, first, last):
    from nova.dates import past_range

    assert past_range(phrase, THURSDAY) == (date.fromisoformat(first), date.fromisoformat(last))


def test_any_time_and_unknown_phrases():
    from nova.dates import past_range

    assert past_range("", THURSDAY) is None and past_range("ever", THURSDAY) is None
    with pytest.raises(ValueError):
        past_range("a while back", THURSDAY)