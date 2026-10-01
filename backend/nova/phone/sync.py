"""Bringing a phone's offline changes to NOVA, and NOVA's memory and reminders to the phone.

The PC holds the one memory. While the PC is unreachable, the phone keeps a copy to read and a
list of the changes made on it (its outbox). When it reaches NOVA again it sends the outbox
here. Each change carries an id chosen on the phone, so one sent twice (a connection dropped
before the answer arrived) is applied once. Every change goes through the same stores and
safety checks as on the PC; then the phone gets a fresh copy back.

Conflicts stay simple because changes are small and additive: forgetting something already
forgotten, or dismissing a reminder already dismissed, is a no-op.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

from nova.database import Database, timestamp, utc_now
from nova.memory.long_term import MemoryStore, SensitiveContent
from nova.permissions.audit import ActionLog, Outcome
from nova.scheduler.reminders import REPEATS, ReminderStore

log = logging.getLogger(__name__)

_OP_ID = r"^[A-Za-z0-9-]{8,64}$"


class _Op(BaseModel):
    id: str = Field(pattern=_OP_ID)


class Remember(_Op):
    kind: Literal["remember"]
    text: str = Field(min_length=1, max_length=1000)
    said_on: date  # the phone's date when it was said, for "next Friday"


class Forget(_Op):
    kind: Literal["forget"]
    memory_id: int


class Remind(_Op):
    kind: Literal["remind"]
    text: str = Field(min_length=1, max_length=500)
    due_at: datetime
    repeat: str = "none"
    # The phone already rang it (and the user saw it) while the PC was away.
    shown: bool = False

    @field_validator("due_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("due_at needs a time zone")
        return value

    @field_validator("repeat")
    @classmethod
    def _repeat(cls, value: str) -> str:
        if value not in REPEATS:
            raise ValueError(f"repeat must be one of {', '.join(REPEATS)}")
        return value


class _ReminderOp(_Op):
    # A reminder NOVA already has, or one this phone made offline (the id of its "remind" change).
    reminder_id: int | None = None
    made_by: str | None = Field(default=None, pattern=_OP_ID)


class Dismiss(_ReminderOp):
    kind: Literal["dismiss"]


class Cancel(_ReminderOp):
    kind: Literal["cancel"]


class Snooze(_ReminderOp):
    kind: Literal["snooze"]
    minutes: int = Field(ge=1, le=1440)
    at: datetime  # when the user pressed Snooze


Op = Annotated[Remember | Forget | Remind | Dismiss | Cancel | Snooze, Field(discriminator="kind")]


class SyncRequest(BaseModel):
    ops: list[Op] = Field(default_factory=list, max_length=500)


class Sync:
    def __init__(self, db: Database, memory: MemoryStore, reminders: ReminderStore, actions: ActionLog) -> None:
        self._db = db
        self._memory = memory
        self._reminders = reminders
        self._actions = actions

    async def apply(self, ops: list[Any], device: str | None) -> list[dict[str, Any]]:
        """Apply the changes in order, each id at most once. `device` is the phone's name (None: the PC)."""
        results = []
        for op in ops:
            done = self._db.fetch_one("SELECT ok, message FROM sync_ops WHERE id = ?", (op.id,))
            if done is not None:
                results.append({"id": op.id, "ok": bool(done["ok"]), "message": done["message"]})
                continue
            try:
                ok, message, ref = await self._apply(op)
            except Exception as exc:  # one bad change must not lose the rest
                log.exception("Could not apply a change from a phone")
                ok, message, ref = False, f"Could not apply this change: {exc}", None
            self._db.run(
                "INSERT INTO sync_ops (id, device_id, kind, ok, message, ref, applied_at) VALUES (?, NULL, ?, ?, ?, ?, ?)",
                (op.id, op.kind, int(ok), message, ref, timestamp()),
            )
            self._actions.record(
                None,
                f"phone_{op.kind}",
                op.model_dump(mode="json", exclude={"id", "kind"}),
                Outcome.RAN,
                ok,
                message,
                summary=f"From {device}, while the PC was away: {message}" if device else f"Synced: {message}",
            )
            results.append({"id": op.id, "ok": ok, "message": message})
        return results

    def _reminder_id(self, op: _ReminderOp) -> int | None:
        if op.reminder_id is not None:
            return op.reminder_id
        if op.made_by is None:
            return None
        row = self._db.fetch_one("SELECT ref FROM sync_ops WHERE id = ? AND kind = 'remind'", (op.made_by,))
        return row["ref"] if row else None

    async def _apply(self, op: Any) -> tuple[bool, str, int | None]:
        if isinstance(op, Remember):
            try:
                saved = await self._memory.add(op.text, "fact", "explicit", said_on=op.said_on)
            except SensitiveContent as exc:
                return False, str(exc), None
            return True, f"Remembered: {saved.memory.content}", saved.memory.id
        if isinstance(op, Forget):
            existed = self._memory.delete(op.memory_id)
            return True, "Forgot a memory" if existed else "Already forgotten", None
        if isinstance(op, Remind):
            reminder = self._reminders.create(op.text, op.due_at, op.repeat)
            if op.shown and op.due_at <= utc_now():
                # Already rung on the phone: record it as rung and seen, so the PC does not ring again.
                reminder = self._reminders.fire(reminder, utc_now())
                self._reminders.dismiss(reminder.id)
            return True, f"Reminder: {op.text}", reminder.id
        reminder_id = self._reminder_id(op)
        reminder = self._reminders.get(reminder_id) if reminder_id is not None else None
        if reminder is None:
            return True, "That reminder is already gone", None
        if isinstance(op, Dismiss | Snooze) and reminder.status == "active" and reminder.due_at <= utc_now():
            # The phone rang it while the PC was away: count that ring here, or the PC rings again.
            self._reminders.fire(reminder, utc_now())
        if isinstance(op, Dismiss):
            self._reminders.dismiss(reminder_id)
            return True, "Dismissed a reminder", reminder_id
        if isinstance(op, Cancel):
            self._reminders.cancel(reminder_id)
            return True, "Cancelled a reminder", reminder_id
        if isinstance(op, Snooze):
            snoozed = self._reminders.snooze(reminder_id, op.minutes, op.at)
            return True, f"Snoozed a reminder for {op.minutes} min", snoozed.id if snoozed else reminder_id
        return False, "Unknown change", None

    def snapshot(self) -> dict[str, Any]:
        """What the phone keeps to read while the PC is away."""
        return {
            "at": timestamp(),
            "memories": [memory.to_dict() for memory in self._memory.all()],
            "reminders": [reminder.to_dict() for reminder in self._reminders.upcoming(200)],
            "pending": [reminder.to_dict() for reminder in self._reminders.pending()],
        }
