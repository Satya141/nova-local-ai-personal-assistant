from __future__ import annotations

import json
from datetime import timedelta

import pytest
from conftest import FakeProvider, say
from fastapi.testclient import TestClient

from nova.api import create_app
from nova.api.app import event_stream
from nova.config import Settings
from nova.database import utc_now
from nova.events import EventBus
from nova.scheduler import ReminderStore

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(tmp_path):
    # No embedding model and no voice in tests: memory falls back to keywords, the microphone stays closed.
    settings = Settings(api_token=TOKEN, data_dir=tmp_path, embed_model="", voice=False, vision_model="")
    provider = FakeProvider([[say("Hello")], [say("Again")]])
    with TestClient(create_app(settings, provider)) as client:
        yield client


def events(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines()]


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}])
def test_every_route_requires_the_token(client, headers):
    assert client.get("/api/health", headers=headers).status_code == 401
    assert client.post("/api/chat", json={"message": "hi"}, headers=headers).status_code == 401
    assert client.post("/api/confirmations/x", json={"approved": True}, headers=headers).status_code == 401
    assert client.get("/api/events", headers=headers).status_code == 401
    assert client.get("/api/memories", headers=headers).status_code == 401
    assert client.delete("/api/memories/1", headers=headers).status_code == 401
    assert client.get("/api/reminders", headers=headers).status_code == 401
    assert client.post("/api/reminders/1/dismiss", headers=headers).status_code == 401
    assert client.get("/api/actions", headers=headers).status_code == 401


def test_memories_can_be_listed_and_deleted(client):
    async def seed():
        return await client.app.state.memory.add("The user likes chai.", "preference", "explicit")

    saved = client.portal.call(seed)
    listed = client.get("/api/memories", headers=AUTH).json()["memories"]
    assert [m["content"] for m in listed] == ["The user likes chai."]

    assert client.delete(f"/api/memories/{saved.memory.id}", headers=AUTH).status_code == 204
    assert client.get("/api/memories", headers=AUTH).json()["memories"] == []
    assert client.delete(f"/api/memories/{saved.memory.id}", headers=AUTH).status_code == 404


def test_reminders_can_be_listed_snoozed_and_dismissed(client):
    reminders = client.app.state.reminders
    fired = reminders.fire(reminders.create("Stretch", utc_now() - timedelta(minutes=1)), utc_now())

    body = client.get("/api/reminders", headers=AUTH).json()
    assert [r["text"] for r in body["pending"]] == ["Stretch"]

    snoozed = client.post(f"/api/reminders/{fired.id}/snooze", json={"minutes": 5}, headers=AUTH).json()["reminder"]
    assert snoozed["status"] == "active" and not snoozed["pending_ack"]
    assert client.post(f"/api/reminders/{fired.id}/snooze", json={"minutes": 0}, headers=AUTH).status_code == 422
    assert client.post(f"/api/reminders/{fired.id}/dismiss", headers=AUTH).status_code == 200
    assert client.post("/api/reminders/999/dismiss", headers=AUTH).status_code == 404


def test_voice_endpoints_when_voice_is_off(client):
    assert client.get("/api/voice", headers=AUTH).json()["available"] is False
    assert client.post("/api/voice/listen", headers=AUTH).status_code == 404
    assert client.post("/api/voice/speak", json={"text": "hi"}, headers=AUTH).status_code == 404


def test_voice_reports_missing_models_instead_of_failing(tmp_path):
    settings = Settings(
        api_token=TOKEN, data_dir=tmp_path, embed_model="", models_dir=tmp_path / "no-models", vision_model=""
    )
    with TestClient(create_app(settings, FakeProvider([]))) as voice_client:
        status = voice_client.get("/api/voice", headers=AUTH).json()
        assert status["available"] is False and "models missing" in status["detail"]
        assert status["state"] == "off"
        refused = voice_client.post("/api/voice/settings", json={"wake_word": True}, headers=AUTH)
        assert refused.status_code == 409


def test_actions_are_listed(client):
    client.post("/api/chat", json={"message": "hi"}, headers=AUTH)
    assert client.get("/api/actions?limit=5", headers=AUTH).json() == {"actions": []}


async def test_event_stream_replays_unacknowledged_reminders_then_live_events(db):
    reminders = ReminderStore(db)
    reminders.fire(reminders.create("Missed call", utc_now() - timedelta(hours=1)), utc_now())
    bus = EventBus()

    stream = event_stream(bus, reminders, heartbeat=0.05)
    first = json.loads(await anext(stream))
    assert first["type"] == "reminder" and first["reminder"]["text"] == "Missed call"

    bus.publish({"type": "memory_saved", "memory": {"id": 1}})
    assert json.loads(await anext(stream))["type"] == "memory_saved"
    assert json.loads(await anext(stream)) == {"type": "ping"}, "a quiet stream still shows signs of life"
    await stream.aclose()
    assert bus.subscriber_count == 0


def test_health_reports_model_status(client):
    body = client.get("/api/health", headers=AUTH).json()
    assert body["status"] == "ok"
    assert body["model"] == "fake" and body["model_ready"] is True
    assert body["vision_ready"] is False


def test_chat_streams_events_and_continues_a_conversation(client):
    first = events(client.post("/api/chat", json={"message": "hi"}, headers=AUTH))
    assert [e["type"] for e in first] == ["conversation", "token", "done"]

    conversation_id = first[0]["id"]
    second = events(
        client.post("/api/chat", json={"message": "more", "conversation_id": conversation_id}, headers=AUTH)
    )
    assert second[0] == {"type": "conversation", "id": conversation_id}
    assert second[1] == {"type": "token", "text": "Again"}


def test_chat_rejects_unknown_conversation_and_empty_message(client):
    unknown = client.post("/api/chat", json={"message": "hi", "conversation_id": "nope"}, headers=AUTH)
    assert unknown.status_code == 404
    assert client.post("/api/chat", json={"message": ""}, headers=AUTH).status_code == 422


def test_warmup_loads_the_model_in_the_background(client):
    assert client.post("/api/warmup", headers=AUTH).status_code == 202
    assert client.app.state.provider.warmed == 1
    assert client.post("/api/warmup").status_code == 401


def test_confirming_nothing_is_a_404(client):
    response = client.post("/api/confirmations/ghost", json={"approved": True}, headers=AUTH)
    assert response.status_code == 404


def test_cors_allows_the_desktop_origin_only(client):
    preflight = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization"}

    allowed = client.options("/api/chat", headers={"Origin": "http://tauri.localhost", **preflight})
    assert allowed.headers["access-control-allow-origin"] == "http://tauri.localhost"

    denied = client.options("/api/chat", headers={"Origin": "https://evil.example", **preflight})
    assert "access-control-allow-origin" not in denied.headers
