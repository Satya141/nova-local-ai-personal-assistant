"""Phase 8: a phone's offline changes reaching the one memory on the PC."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from conftest import FakeProvider, say
from fastapi.testclient import TestClient

from nova.api import create_app
from nova.config import Settings
from nova.database import timestamp, utc_now

TOKEN = "desktop-token"
DESKTOP = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        api_token=TOKEN, data_dir=tmp_path, embed_model="", voice=False, vision_model="", browser=False
    )
    with TestClient(create_app(settings, FakeProvider([[say("hi")]]))) as test_client:
        yield test_client


def sync(client, *ops):
    response = client.post("/api/sync", json={"ops": list(ops)}, headers=DESKTOP)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_change_sent_twice_is_applied_once(client):
    remember = {"id": "op-remember-1", "kind": "remember", "text": "My sister's name is Priya", "said_on": "2026-10-01"}
    first = sync(client, remember)
    again = sync(client, remember)  # the phone never got the first answer, so it sends it again
    assert first["results"] == again["results"] and first["results"][0]["ok"]
    contents = [m["content"] for m in again["snapshot"]["memories"]]
    assert contents == ["My sister's name is Priya"]


def test_memory_safety_still_applies_to_changes_from_a_phone(client):
    result = sync(client, {"id": "op-secret-01", "kind": "remember", "text": "my bank PIN is 4831", "said_on": "2026-10-01"})
    assert result["results"][0]["ok"] is False and "does not store" in result["results"][0]["message"]
    assert result["snapshot"]["memories"] == []


def test_next_friday_counts_from_the_day_it_was_said_not_the_day_it_synced(client):
    sync(client, {"id": "op-friday-01", "kind": "remember", "text": "My exam is next Friday", "said_on": "2026-09-28"})
    contents = [m["content"] for m in client.get("/api/memories", headers=DESKTOP).json()["memories"]]
    assert contents == ["My exam is on Friday 9 October 2026"] or "October 2026" in contents[0]
    other = sync(client, {"id": "op-friday-02", "kind": "remember", "text": "The trip is next Friday", "said_on": "2026-10-05"})
    trip = [m["content"] for m in other["snapshot"]["memories"] if "trip" in m["content"]][0]
    assert "16 October 2026" in trip


def test_reminders_made_offline_can_be_cancelled_offline_too(client):
    later = (utc_now() + timedelta(hours=3)).isoformat()
    made = {"id": "op-remind-01", "kind": "remind", "text": "Call home", "due_at": later}
    cancel = {"id": "op-cancel-01", "kind": "cancel", "made_by": "op-remind-01"}
    result = sync(client, made, cancel)
    assert [r["ok"] for r in result["results"]] == [True, True]
    assert all(r["text"] != "Call home" for r in result["snapshot"]["reminders"]), "no longer upcoming"


def test_a_reminder_the_phone_already_rang_does_not_ring_again_on_the_pc(client):
    past = (utc_now() - timedelta(minutes=10)).isoformat()
    sync(
        client,
        {"id": "op-seen-0001", "kind": "remind", "text": "Stretch", "due_at": past, "shown": True},
        {"id": "op-unseen-01", "kind": "remind", "text": "Water the plants", "due_at": past},
    )
    reminders = client.app.state.reminders
    reminders_by_text = {r.text: r for r in reminders.upcoming() + reminders.pending()}
    rung_on_pc = {r.text for r in reminders.pending()} | {r.text for r in reminders.due(utc_now())}
    assert "Stretch" not in rung_on_pc and "Stretch" not in reminders_by_text, "already rung and seen on the phone"
    assert "Water the plants" in rung_on_pc, "the phone was closed when it was due: the PC rings it, as missed"


def test_dismissing_and_snoozing_reach_the_pc(client):
    reminders = client.app.state.reminders
    rung = reminders.fire(reminders.create("Stand up", utc_now() - timedelta(minutes=1)), utc_now())
    other = reminders.create("Tea", utc_now() + timedelta(minutes=5))
    result = sync(
        client,
        {"id": "op-dismiss-1", "kind": "dismiss", "reminder_id": rung.id},
        {"id": "op-snooze-01", "kind": "snooze", "reminder_id": other.id, "minutes": 30, "at": timestamp()},
        {"id": "op-gone-0001", "kind": "dismiss", "reminder_id": 9999},
    )
    assert [r["ok"] for r in result["results"]] == [True, True, True]
    assert result["snapshot"]["pending"] == []
    assert reminders.get(other.id).due_at > utc_now() + timedelta(minutes=25)
    assert "already gone" in result["results"][2]["message"]


def test_a_reminder_rung_and_dismissed_on_the_phone_is_not_rung_again_by_the_pc(client, monkeypatch):
    reminders = client.app.state.reminders
    client.app.state.scheduler.poke = lambda: None  # the PC "was off": nothing fires it meanwhile
    once = reminders.create("Take the medicine", utc_now() - timedelta(minutes=2))
    daily = reminders.create("Stretch", utc_now() - timedelta(minutes=2), repeat="daily")
    sync(
        client,
        {"id": "op-dismiss-a", "kind": "dismiss", "reminder_id": once.id},
        {"id": "op-dismiss-b", "kind": "dismiss", "reminder_id": daily.id},
    )
    assert reminders.due(utc_now()) == [] and reminders.pending() == []
    assert reminders.get(once.id).status == "done"
    assert reminders.get(daily.id).due_at > utc_now(), "the series moves on to tomorrow"


def test_changes_from_a_phone_show_in_recent_actions(client):
    sync(client, {"id": "op-remember-9", "kind": "remember", "text": "I like green tea", "said_on": "2026-10-01"})
    action = client.get("/api/actions", headers=DESKTOP).json()["actions"][0]
    assert action["tool"] == "phone_remember" and action["summary"] == "Synced: Remembered: I like green tea"


@pytest.mark.parametrize(
    "op",
    [
        {"id": "op-naive-001", "kind": "remind", "text": "x", "due_at": "2026-10-01T10:00:00"},
        {"id": "x", "kind": "remember", "text": "too short an id", "said_on": "2026-10-01"},
        {"id": "op-nokind-01", "kind": "delete_everything"},
        {"id": "op-repeat-01", "kind": "remind", "text": "x", "due_at": "2026-10-01T10:00:00Z", "repeat": "hourly"},
    ],
)
def test_malformed_changes_are_refused(client, op):
    assert client.post("/api/sync", json={"ops": [op]}, headers=DESKTOP).status_code == 422


def test_an_empty_sync_just_returns_the_copy(client):
    snapshot = sync(client)["snapshot"]
    assert set(snapshot) == {"at", "memories", "reminders", "pending"}
    assert date.fromisoformat(snapshot["at"][:10])
