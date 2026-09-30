from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from nova.events import EventBus
from nova.scheduler import ReminderStore, Scheduler, next_occurrence
from nova.tools.reminders import CreateReminderArgs, ReminderIdArgs, friendly_time, reminder_tools

NOW = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def reminders(db) -> ReminderStore:
    return ReminderStore(db)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def bus() -> EventBus:
    return EventBus()


def test_one_off_reminder_fires_once_and_waits_for_dismissal(reminders, bus, clock):
    reminder = reminders.create("Call mom", NOW + timedelta(minutes=5))
    scheduler = Scheduler(reminders, bus, clock)

    assert scheduler.fire_due() == []
    clock.now += timedelta(minutes=5)
    fired = scheduler.fire_due()

    assert [r.text for r in fired] == ["Call mom"]
    assert fired[0].status == "done" and fired[0].pending_ack
    assert scheduler.fire_due() == [], "a reminder fires only once"
    assert [r.id for r in reminders.pending()] == [reminder.id]
    assert reminders.dismiss(reminder.id)
    assert reminders.pending() == []


def test_reminders_missed_while_closed_fire_on_start(reminders, bus, clock):
    reminders.create("Stand-up", NOW - timedelta(hours=3))
    fired = Scheduler(reminders, bus, clock).fire_due()
    assert [r.text for r in fired] == ["Stand-up"]
    assert fired[0].fired_at == NOW - timedelta(hours=3), "the toast can say when it was really due"


def test_repeating_reminder_advances_to_the_next_future_occurrence(reminders, bus, clock):
    reminders.create("Water plants", NOW - timedelta(days=3), repeat="daily")
    [fired] = Scheduler(reminders, bus, clock).fire_due()

    assert fired.status == "active" and fired.pending_ack
    assert NOW < fired.due_at <= NOW + timedelta(days=1)


def test_next_occurrence_rules():
    friday = datetime(2026, 10, 2, 9, 0).astimezone()
    assert next_occurrence(friday, "daily", friday).date().isoformat() == "2026-10-03"
    assert next_occurrence(friday, "weekdays", friday).astimezone().weekday() == 0  # Monday
    assert (next_occurrence(friday, "weekly", friday) - friday).days == 7
    # Wall-clock time is kept.
    assert next_occurrence(friday, "daily", friday).astimezone().hour == 9


def test_snooze(reminders, clock):
    one_off = reminders.create("Tea", NOW)
    snoozed = reminders.snooze(one_off.id, 10, NOW)
    assert snoozed.status == "active" and snoozed.due_at == NOW + timedelta(minutes=10)
    assert not snoozed.pending_ack

    series = reminders.create("Stretch", NOW, repeat="daily")
    copy = reminders.snooze(series.id, 15, NOW)
    assert copy.id != series.id and copy.repeat == "none", "snoozing one occurrence leaves the series alone"
    assert reminders.get(series.id).due_at == NOW


def test_cancel(reminders):
    reminder = reminders.create("Gym", NOW + timedelta(hours=1))
    assert reminders.cancel(reminder.id).status == "cancelled"
    assert reminders.upcoming() == []
    assert reminders.cancel(999) is None


async def test_scheduler_publishes_to_subscribers_and_wakes_on_poke(reminders, bus):
    scheduler = Scheduler(reminders, bus)
    async with bus.subscribe() as queue:
        scheduler.start()
        try:
            await asyncio.sleep(0.05)
            reminders.create("Right now", datetime.now(UTC) - timedelta(seconds=1))
            scheduler.poke()
            event = await asyncio.wait_for(queue.get(), timeout=2)
        finally:
            await scheduler.stop()
    assert event["type"] == "reminder"
    assert event["reminder"]["text"] == "Right now"


# --- tools ---------------------------------------------------------------------


@pytest.fixture
def tools(reminders, clock):
    pokes: list[int] = []
    built = {tool.name: tool for tool in reminder_tools(reminders, lambda: pokes.append(1), clock)}
    built["_pokes"] = pokes
    return built


async def test_create_in_minutes(tools, reminders):
    result = await tools["create_reminder"].handler(CreateReminderArgs(text="Check the oven", in_minutes=20))
    assert result.ok
    [reminder] = reminders.upcoming()
    assert reminder.due_at == NOW + timedelta(minutes=20)
    assert tools["_pokes"] == [1], "the scheduler is told to recompute"


async def test_create_at_local_time(tools, reminders):
    local_seven_pm = NOW.astimezone().replace(hour=19, minute=0, second=0) + timedelta(days=1)
    at = local_seven_pm.replace(tzinfo=None).isoformat(timespec="minutes")
    result = await tools["create_reminder"].handler(CreateReminderArgs(text="Call mom", at=at))
    assert result.ok, result.content
    assert reminders.upcoming()[0].due_at == local_seven_pm.astimezone(UTC)
    assert json.loads(result.content)["when"].startswith("tomorrow at 7:00 PM")


async def test_a_one_off_time_in_the_past_is_refused_with_a_hint(tools, reminders):
    past = (NOW - timedelta(hours=2)).astimezone().replace(tzinfo=None).isoformat(timespec="minutes")
    result = await tools["create_reminder"].handler(CreateReminderArgs(text="Late", at=past))
    assert not result.ok
    assert "already passed" in result.content
    assert reminders.upcoming() == []


async def test_a_repeating_time_already_past_today_starts_next_time(tools, reminders):
    past = (NOW - timedelta(hours=2)).astimezone().replace(tzinfo=None).isoformat(timespec="minutes")
    result = await tools["create_reminder"].handler(CreateReminderArgs(text="Stretch", at=past, repeat="daily"))
    assert result.ok
    assert reminders.upcoming()[0].due_at > NOW


def test_create_needs_exactly_one_time():
    with pytest.raises(ValueError):
        CreateReminderArgs(text="x")
    with pytest.raises(ValueError):
        CreateReminderArgs(text="x", at="2026-10-01T19:00", in_minutes=5)
    with pytest.raises(ValueError):
        CreateReminderArgs(text="x", at="seven pm")


async def test_list_and_cancel_tools(tools, reminders):
    reminder = reminders.create("Gym", NOW + timedelta(hours=1))
    listing = json.loads((await tools["list_reminders"].handler(tools["list_reminders"].args_model())).content)
    assert listing["reminders"][0]["id"] == reminder.id
    assert tools["cancel_reminder"].describe(ReminderIdArgs(reminder_id=reminder.id)) == "Cancel reminder: Gym"
    assert (await tools["cancel_reminder"].handler(ReminderIdArgs(reminder_id=reminder.id))).ok
    assert reminders.upcoming() == []


def test_friendly_time():
    now = datetime(2026, 9, 30, 10, 0).astimezone()
    assert friendly_time(now.replace(hour=19), now) == "today at 7:00 PM"
    assert friendly_time(now.replace(hour=6) + timedelta(days=1), now) == "tomorrow at 6:00 AM"
    assert friendly_time(now + timedelta(days=5), now) == "Mon 5 Oct at 10:00 AM"
