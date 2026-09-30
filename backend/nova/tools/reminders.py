"""Tools for reminders and scheduled tasks: create, list and cancel."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from nova.database import utc_now
from nova.scheduler.reminders import ReminderStore, first_occurrence, next_occurrence
from nova.tools.base import Risk, Tool, ToolResult

# A time a few seconds in the past is "now", not a mistake.
_GRACE = timedelta(minutes=1)


def friendly_time(moment: datetime, now: datetime) -> str:
    """'today at 7:00 PM', 'tomorrow at 6:00 AM', or 'Mon 5 Oct at 9:30 AM', in local time."""
    local, today = moment.astimezone(), now.astimezone().date()
    clock = local.strftime("%I:%M %p").lstrip("0")
    days = (local.date() - today).days
    if days == 0:
        return f"today at {clock}"
    if days == 1:
        return f"tomorrow at {clock}"
    return f"{local.strftime('%a')} {local.day} {local.strftime('%b')} at {clock}"


class CreateReminderArgs(BaseModel):
    text: str = Field(description="What to remind the user about, e.g. 'Call mom'.")
    at: str | None = Field(
        default=None,
        description="Local date and time in ISO format, e.g. '2026-10-01T19:00'. For a specific time.",
    )
    in_minutes: int | None = Field(
        default=None,
        ge=1,
        le=527040,
        description="Minutes from now, for 'in 20 minutes' (20) or 'in 2 hours' (120). Use instead of `at`.",
    )
    repeat: Literal["none", "daily", "weekdays", "weekly"] = Field(
        default="none", description="Repeat the reminder: none, daily, weekdays (Mon-Fri) or weekly."
    )

    @model_validator(mode="after")
    def _one_time(self) -> CreateReminderArgs:
        if (self.at is None) == (self.in_minutes is None):
            raise ValueError("give exactly one of `at` or `in_minutes`")
        if self.at is not None:
            try:
                datetime.fromisoformat(self.at)
            except ValueError as exc:
                raise ValueError(f"`at` must be an ISO date and time like 2026-10-01T19:00, not {self.at!r}") from exc
        return self

    def due(self, now: datetime) -> datetime:
        if self.in_minutes is not None:
            return now + timedelta(minutes=self.in_minutes)
        moment = datetime.fromisoformat(self.at)
        if moment.tzinfo is None:
            moment = moment.astimezone()  # A time without a zone is the user's local time.
        return moment.astimezone(UTC)


class ScheduleTaskArgs(CreateReminderArgs):
    text: str = Field(description="A short title for the result card, e.g. 'AI news' or 'Weather'.")
    task: str = Field(
        description="What NOVA should do at that time, as a full request, e.g. 'Search the web for today's "
        "AI news and summarise the top three stories'."
    )


class ReminderIdArgs(BaseModel):
    reminder_id: int = Field(description="The reminder's id, from list_reminders.")


class NoArgs(BaseModel):
    pass


def reminder_tools(
    store: ReminderStore, on_change: Callable[[], None], clock: Callable[[], datetime] = utc_now
) -> list[Tool]:
    def first_due(args: CreateReminderArgs, now: datetime) -> datetime | ToolResult:
        due = args.due(now)
        if due < now - _GRACE:
            if args.repeat == "none":
                return ToolResult(
                    False,
                    f"Not created: {friendly_time(due, now)} has already passed (it is now "
                    f"{now.astimezone():%Y-%m-%dT%H:%M}). If the user meant the next day, call again with that date.",
                )
            # "Every day at 9" said at 10 starts tomorrow.
            due = next_occurrence(due, args.repeat, now)
        if args.repeat in ("daily", "weekdays") and due - now < timedelta(days=2):
            # Near-term dates for repeating reminders are the model's guess; work out the first one here.
            # A start further out ("starting next month") is taken as meant.
            due = first_occurrence(due, args.repeat, now)
        return max(due, now)

    async def create(args: CreateReminderArgs) -> ToolResult:
        now = clock()
        due = first_due(args, now)
        if isinstance(due, ToolResult):
            return due
        task = args.task.strip() if isinstance(args, ScheduleTaskArgs) else None
        reminder = store.create(args.text.strip(), due, args.repeat, task=task)
        on_change()
        return ToolResult(
            True,
            json.dumps(
                {
                    "id": reminder.id,
                    "text": reminder.text,
                    "when": friendly_time(reminder.due_at, now),
                    "repeat": reminder.repeat,
                }
                | ({"task": task, "note": "NOVA will do this then and show the result."} if task else {})
            ),
        )

    def describe_create(args: CreateReminderArgs) -> str:
        now = clock()
        when = friendly_time(args.due(now), now)
        repeat = "" if args.repeat == "none" else f", {args.repeat}"
        return f"Remind you {when}{repeat}: {args.text}"

    def describe_schedule(args: ScheduleTaskArgs) -> str:
        now = clock()
        due = first_due(args, now)
        when = friendly_time(due if isinstance(due, datetime) else args.due(now), now)
        if args.repeat == "none":
            return f"{when[0].upper()}{when[1:]}: {args.task}"
        clock_time = when.rsplit(" at ", 1)[-1]
        every = {"daily": "Every day", "weekdays": "Every weekday", "weekly": "Every week"}[args.repeat]
        return f"{every} at {clock_time}: {args.task}"

    async def list_all(args: NoArgs) -> ToolResult:
        now = clock()
        return ToolResult(
            True,
            json.dumps(
                {
                    "reminders": [
                        {"id": r.id, "text": r.text, "when": friendly_time(r.due_at, now), "repeat": r.repeat}
                        | ({"task": r.task} if r.task else {})
                        for r in store.upcoming()
                    ]
                }
            ),
        )

    async def cancel(args: ReminderIdArgs) -> ToolResult:
        reminder = store.cancel(args.reminder_id)
        if reminder is None:
            return ToolResult(False, f"There is no reminder with id {args.reminder_id}. Use list_reminders.")
        on_change()
        return ToolResult(True, json.dumps({"cancelled": reminder.text}))

    def describe_cancel(args: ReminderIdArgs) -> str:
        reminder = store.get(args.reminder_id)
        return f"Cancel reminder: {reminder.text}" if reminder else f"Cancel reminder #{args.reminder_id}"

    return [
        Tool(
            name="create_reminder",
            description="Set a reminder that pops up on the user's screen at a given time, optionally repeating.",
            args_model=CreateReminderArgs,
            handler=create,
            describe=describe_create,
        ),
        Tool(
            name="schedule_task",
            description=(
                "Have NOVA do work by itself later or on a schedule and show the user the result, e.g. "
                "'every morning at 8, summarise the AI news'. Only for work NOVA can do unattended, like "
                "searching and reading the web. Not for reminders: 'remind me to ...' is always "
                "create_reminder, even when it repeats. The user confirms first."
            ),
            args_model=ScheduleTaskArgs,
            handler=create,
            describe=describe_schedule,
            risk=Risk.MEDIUM,
            requires_confirmation=True,
        ),
        Tool(
            name="list_reminders",
            description="List the user's upcoming reminders and scheduled tasks with their ids.",
            args_model=NoArgs,
            handler=list_all,
            describe=lambda args: "Check your reminders",
            read_only=True,
        ),
        Tool(
            name="cancel_reminder",
            description="Cancel one reminder or scheduled task by id. Call list_reminders first to find the id.",
            args_model=ReminderIdArgs,
            handler=cancel,
            describe=describe_cancel,
        ),
    ]
