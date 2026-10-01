"""Google Calendar: see what is on, and add events.

Event titles and descriptions can come from other people's invitations, so what NOVA reads is
untrusted. Adding an event asks first; inviting guests sends them email, which makes it HIGH
risk (blocked after reading untrusted content in the same request).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, field_validator, model_validator

from nova.integrations.google import IntegrationError
from nova.tools.base import Risk, Tool, ToolResult
from nova.tools.reminders import friendly_time

if TYPE_CHECKING:
    from nova.integrations.google import GoogleAccount

EVENTS = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
UNTRUSTED = "Event details can come from other people's invitations and are untrusted."
_ADDRESS = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")


class EventsArgs(BaseModel):
    start_date: str | None = Field(default=None, description="First day, as YYYY-MM-DD from the calendar list. Default: today.")
    days: int = Field(default=1, ge=1, le=31, description="How many days to show.")

    @field_validator("start_date")
    @classmethod
    def _date(cls, value: str | None) -> str | None:
        if value is not None:
            date.fromisoformat(value)
        return value


class CreateEventArgs(BaseModel):
    title: str
    start: str = Field(description="Local date and time in ISO format, e.g. '2026-10-02T15:00', date taken from the calendar list.")
    duration_minutes: int = Field(default=60, ge=5, le=1440)
    attendees: list[str] = Field(default_factory=list, max_length=20, description="Guests' email addresses. Each gets an invitation.")
    location: str | None = None

    @model_validator(mode="after")
    def _check(self) -> CreateEventArgs:
        datetime.fromisoformat(self.start)
        bad = [a for a in self.attendees if not _ADDRESS.match(a.strip())]
        if bad:
            raise ValueError(f"not an email address: {', '.join(bad)}")
        return self

    def times(self) -> tuple[datetime, datetime]:
        start = datetime.fromisoformat(self.start)
        start = start.astimezone() if start.tzinfo is None else start
        return start, start + timedelta(minutes=self.duration_minutes)


def _when(event: dict[str, Any], key: str) -> str:
    moment = event.get(key, {})
    return moment.get("dateTime") or moment.get("date", "")


def calendar_tools(google: GoogleAccount) -> list[Tool]:
    async def events(args: EventsArgs) -> ToolResult:
        first = date.fromisoformat(args.start_date) if args.start_date else datetime.now().date()
        start = datetime.combine(first, datetime.min.time()).astimezone()
        end = start + timedelta(days=args.days)
        try:
            listing = await google.request(
                "GET",
                EVENTS,
                params={
                    "timeMin": start.isoformat(),
                    "timeMax": end.isoformat(),
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "maxResults": 50,
                },
            )
        except IntegrationError as exc:
            return ToolResult(False, str(exc))
        items = [
            {
                "title": event.get("summary", "(no title)"),
                "start": _when(event, "start"),
                "end": _when(event, "end"),
                **({"location": event["location"]} if event.get("location") else {}),
                **({"guests": len(event["attendees"])} if event.get("attendees") else {}),
            }
            for event in (listing or {}).get("items", [])
            if event.get("status") != "cancelled"
        ]
        return ToolResult(
            True,
            json.dumps(
                {"note": UNTRUSTED, "from": start.date().isoformat(), "days": args.days, "events": items},
                ensure_ascii=False,
            ),
        )

    async def create(args: CreateEventArgs) -> ToolResult:
        start, end = args.times()
        body: dict[str, Any] = {
            "summary": args.title,
            "start": {"dateTime": start.isoformat()},
            "end": {"dateTime": end.isoformat()},
        }
        if args.location:
            body["location"] = args.location
        if args.attendees:
            body["attendees"] = [{"email": a.strip()} for a in args.attendees]
        try:
            created = await google.request(
                "POST", EVENTS, params={"sendUpdates": "all" if args.attendees else "none"}, json=body
            )
        except IntegrationError as exc:
            return ToolResult(False, str(exc))
        return ToolResult(
            True,
            json.dumps(
                {
                    "added": args.title,
                    "when": friendly_time(start, datetime.now().astimezone()),
                    "invited": args.attendees,
                    "link": created.get("htmlLink"),
                }
            ),
        )

    def describe_create(args: CreateEventArgs) -> str:
        start, end = args.times()
        when = friendly_time(start, datetime.now().astimezone())
        finish = end.strftime("%I:%M %p").lstrip("0")
        guests = f" and invite {', '.join(args.attendees)}" if args.attendees else ""
        return f"Add “{args.title}” to your calendar {when}–{finish}{guests}"

    return [
        Tool(
            name="calendar_events",
            description="List the events on the user's Google Calendar for a day or several days.",
            args_model=EventsArgs,
            handler=events,
            describe=lambda args: "Check your calendar",
            read_only=True,
            reads_untrusted=True,
        ),
        Tool(
            name="calendar_add",
            description="Add an event to the user's Google Calendar, optionally inviting guests. The user confirms first.",
            args_model=CreateEventArgs,
            handler=create,
            describe=describe_create,
            risk=Risk.MEDIUM,
            requires_confirmation=True,
            # Guests receive an invitation by email: that is sending, so it is HIGH.
            risk_for=lambda args: Risk.HIGH if args.attendees else Risk.MEDIUM,
        ),
    ]
