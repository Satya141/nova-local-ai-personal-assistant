"""A plain list of upcoming dates for prompts.

Small local models cannot reliably work out which date "next Friday" is: in
testing, qwen3:8b produced "Friday, 8 October 2026" (a Thursday) and
"Friday, 3 October 2026" (a Saturday). Looking a date up in a list is
something they do reliably, so prompts get the list.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_WEEKDAY_PHRASE = re.compile(
    r"\b(?:on\s+)?(?P<which>next|this|coming|on)\s+(?P<day>" + "|".join(_WEEKDAYS) + r")\b", re.I
)


def resolve_weekdays(text: str, today: date) -> str:
    """Replace "next Friday", "this Friday", "on Friday" with the actual date.

    "this/coming/on Friday" is the next Friday after today; "next Friday" is
    the Friday of next week (Monday-start), which is how the phrase is usually
    meant when said early in a week. The result reads "on Friday 9 October 2026".
    """

    def replace(match: re.Match) -> str:
        weekday = _WEEKDAYS.index(match["day"].lower())
        ahead = (weekday - today.weekday()) % 7 or 7
        target = today + timedelta(days=ahead)
        if match["which"].lower() == "next":
            start_of_next_week = today + timedelta(days=7 - today.weekday())
            target = start_of_next_week + timedelta(days=weekday)
        return f"on {target:%A} {target.day} {target:%B %Y}"

    return _WEEKDAY_PHRASE.sub(replace, text)


def upcoming_days(now: datetime | None = None, days: int = 14) -> str:
    """'- Wednesday 30 September 2026 (today) = 2026-09-30' and the following days, one per line."""
    today = (now or datetime.now().astimezone()).date()
    labels = {0: " (today)", 1: " (tomorrow)"}
    lines = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        lines.append(f"- {day:%A} {day.day} {day:%B %Y}{labels.get(offset, '')} = {day.isoformat()}")
    return "\n".join(lines)
