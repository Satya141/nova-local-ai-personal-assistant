"""Reminders and scheduled tasks that survive restarts, and the loop that fires them.

The `reminders` table is the source of truth; the scheduler only ever reads
it. A reminder that comes due while NOVA is closed fires as soon as NOVA
starts again, and stays on screen (`pending_ack`) until the user dismisses it.

A scheduled task is a reminder with a `task`: a request NOVA carries out by
itself when it comes due. Its card appears once the result is ready.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from nova.database import Database, parse_timestamp, timestamp, utc_now
from nova.events import EventBus

log = logging.getLogger(__name__)

REPEATS = ("none", "daily", "weekdays", "weekly")

# Even with nothing due, look again this often: the clock can jump (sleep, DST, manual change).
_MAX_SLEEP = 30.0


@dataclass(frozen=True)
class Reminder:
    id: int
    text: str
    due_at: datetime
    repeat: str
    status: str  # active | done | cancelled
    pending_ack: bool  # fired and still waiting for the user to dismiss it
    fired_at: datetime | None
    task: str | None = None  # a request NOVA carries out when this comes due
    result: str | None = None  # what NOVA replied the last time it ran the task

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "text": self.text,
            "due_at": timestamp(self.due_at),
            "repeat": self.repeat,
            "status": self.status,
            "pending_ack": self.pending_ack,
            "fired_at": timestamp(self.fired_at) if self.fired_at else None,
            "task": self.task,
            "result": self.result,
        }


def next_occurrence(due: datetime, repeat: str, after: datetime) -> datetime:
    """The first repeat of `due` later than `after`.

    Stepped in local wall-clock time, so "every day at 9:00" stays at 9:00
    across daylight-saving changes.
    """
    local = due.astimezone().replace(tzinfo=None)
    while True:
        local += timedelta(days=7 if repeat == "weekly" else 1)
        if repeat == "weekdays":
            while local.weekday() >= 5:
                local += timedelta(days=1)
        candidate = local.astimezone(UTC)
        if candidate > after:
            return candidate


def first_occurrence(due: datetime, repeat: str, now: datetime) -> datetime:
    """The first time a daily or weekday reminder at `due`'s clock time should ring after `now`.

    The model picks a date for "every weekday at 9" and, at 1 AM, picked tomorrow
    instead of later today. The clock time is what the user said; the date is worked
    out here.
    """
    clock = due.astimezone()
    local_now = now.astimezone()
    candidate = local_now.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)
    if candidate <= local_now:
        candidate += timedelta(days=1)
    if repeat == "weekdays":
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
    return candidate.astimezone(UTC)


class ReminderStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def _reminder(row) -> Reminder:
        return Reminder(
            id=row["id"],
            text=row["text"],
            due_at=parse_timestamp(row["due_at"]),
            repeat=row["repeat"],
            status=row["status"],
            pending_ack=bool(row["pending_ack"]),
            fired_at=parse_timestamp(row["fired_at"]) if row["fired_at"] else None,
            task=row["task"],
            result=row["result"],
        )

    def get(self, reminder_id: int) -> Reminder | None:
        row = self._db.fetch_one("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
        return self._reminder(row) if row else None

    def create(
        self,
        text: str,
        due_at: datetime,
        repeat: str = "none",
        conversation_id: str | None = None,
        task: str | None = None,
    ) -> Reminder:
        if repeat not in REPEATS:
            raise ValueError(f"repeat must be one of {', '.join(REPEATS)}")
        reminder_id = self._db.run(
            "INSERT INTO reminders (text, due_at, repeat, status, pending_ack, conversation_id, created_at, task) "
            "VALUES (?, ?, ?, 'active', 0, ?, ?, ?)",
            (text, timestamp(due_at), repeat, conversation_id, timestamp(), task),
        )
        return self.get(reminder_id)

    def upcoming(self, limit: int = 50) -> list[Reminder]:
        rows = self._db.fetch("SELECT * FROM reminders WHERE status = 'active' ORDER BY due_at LIMIT ?", (limit,))
        return [self._reminder(row) for row in rows]

    def pending(self) -> list[Reminder]:
        """Fired reminders the user has not dismissed yet."""
        rows = self._db.fetch("SELECT * FROM reminders WHERE pending_ack = 1 ORDER BY fired_at")
        return [self._reminder(row) for row in rows]

    def due(self, now: datetime) -> list[Reminder]:
        rows = self._db.fetch(
            "SELECT * FROM reminders WHERE status = 'active' AND due_at <= ? ORDER BY due_at", (timestamp(now),)
        )
        return [self._reminder(row) for row in rows]

    def next_due_at(self) -> datetime | None:
        row = self._db.fetch_one("SELECT MIN(due_at) AS due FROM reminders WHERE status = 'active'")
        return parse_timestamp(row["due"]) if row and row["due"] else None

    def fire(self, reminder: Reminder, now: datetime) -> Reminder:
        # A task's card waits for its result (see finish_task); a plain reminder shows at once.
        ack = 0 if reminder.task else 1
        if reminder.repeat == "none":
            self._db.run(
                "UPDATE reminders SET status = 'done', pending_ack = ?, fired_at = ? WHERE id = ?",
                (ack, timestamp(reminder.due_at), reminder.id),
            )
        else:
            upcoming = next_occurrence(reminder.due_at, reminder.repeat, now)
            self._db.run(
                "UPDATE reminders SET due_at = ?, pending_ack = ?, fired_at = ? WHERE id = ?",
                (timestamp(upcoming), ack, timestamp(reminder.due_at), reminder.id),
            )
        return self.get(reminder.id)

    def finish_task(self, reminder_id: int, result: str) -> Reminder | None:
        """Store what the task found and put its card on screen."""
        self._db.run("UPDATE reminders SET result = ?, pending_ack = 1 WHERE id = ?", (result, reminder_id))
        return self.get(reminder_id)

    def dismiss(self, reminder_id: int) -> bool:
        if self.get(reminder_id) is None:
            return False
        self._db.run("UPDATE reminders SET pending_ack = 0 WHERE id = ?", (reminder_id,))
        return True

    def snooze(self, reminder_id: int, minutes: int, now: datetime) -> Reminder | None:
        reminder = self.get(reminder_id)
        if reminder is None:
            return None
        later = now + timedelta(minutes=minutes)
        if reminder.repeat == "none":
            self._db.run(
                "UPDATE reminders SET status = 'active', due_at = ?, pending_ack = 0 WHERE id = ?",
                (timestamp(later), reminder_id),
            )
            return self.get(reminder_id)
        # Snoozing one occurrence of a repeating reminder must not move the whole series.
        self.dismiss(reminder_id)
        return self.create(reminder.text, later)

    def cancel(self, reminder_id: int) -> Reminder | None:
        if self.get(reminder_id) is None:
            return None
        self._db.run("UPDATE reminders SET status = 'cancelled', pending_ack = 0 WHERE id = ?", (reminder_id,))
        return self.get(reminder_id)


TaskRunner = Callable[[str], Awaitable[str]]


class Scheduler:
    """Fires reminders when they come due, runs scheduled tasks, and tells connected clients."""

    def __init__(
        self,
        store: ReminderStore,
        bus: EventBus,
        clock: Callable[[], datetime] = utc_now,
        run_task: TaskRunner | None = None,
    ) -> None:
        self._store = store
        self._bus = bus
        self._clock = clock
        # Set after construction too: the agent that runs tasks needs the scheduler's tools first.
        self.run_task = run_task
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._running: dict[int, asyncio.Task] = {}

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="reminder-scheduler")

    async def stop(self) -> None:
        for task in [self._task, *self._running.values()]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    def poke(self) -> None:
        """Reminders changed: recompute when to wake up."""
        self._wake.set()

    def fire_due(self) -> list[Reminder]:
        now = self._clock()
        fired = []
        for reminder in self._store.due(now):
            updated = self._store.fire(reminder, now)
            fired.append(updated)
            if updated.task:
                self._start_task(updated)
                continue
            self._bus.publish({"type": "reminder", "reminder": updated.to_dict()})
            log.info("Reminder %s fired: %s", updated.id, updated.text)
        return fired

    def _start_task(self, reminder: Reminder) -> None:
        if reminder.id in self._running:
            # Still busy with the previous run (a slow model, a repeat every minute): skip this one.
            log.warning("Task %s is still running; skipped a run", reminder.id)
            return
        job = asyncio.create_task(self._carry_out(reminder), name=f"task-{reminder.id}")
        self._running[reminder.id] = job
        job.add_done_callback(lambda _: self._running.pop(reminder.id, None))

    async def _carry_out(self, reminder: Reminder) -> None:
        log.info("Task %s started: %s", reminder.id, reminder.task)
        if self.run_task is None:
            result = "NOVA could not run this task: scheduled tasks are not available right now."
        else:
            try:
                result = await self.run_task(reminder.task)
            except Exception as exc:  # A failed task still tells the user, and never stops the scheduler.
                log.exception("Task %s failed", reminder.id)
                result = f"NOVA could not finish this task: {exc}"
        finished = self._store.finish_task(reminder.id, result.strip() or "NOVA finished the task but had nothing to report.")
        if finished:
            self._bus.publish({"type": "reminder", "reminder": finished.to_dict()})

    async def wait_for_tasks(self) -> None:
        """Let running tasks finish. Used in tests."""
        while self._running:
            await asyncio.gather(*self._running.values(), return_exceptions=True)

    async def _run(self) -> None:
        while True:
            # Cleared before looking, so a poke that arrives while we look is not lost.
            self._wake.clear()
            try:
                self.fire_due()
            except Exception:  # A bad row must not stop every future reminder.
                log.exception("Firing reminders failed")
            next_due = self._store.next_due_at()
            delay = _MAX_SLEEP
            if next_due is not None:
                delay = min(delay, max(0.0, (next_due - self._clock()).total_seconds()))
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except TimeoutError:
                pass
