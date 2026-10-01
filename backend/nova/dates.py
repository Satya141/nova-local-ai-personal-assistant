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


_NUMBERS = {w: n for n, w in enumerate("zero one two three four five six seven eight nine ten".split())}
_MONTHS = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december")
_MONTH = r"(?P<month>" + "|".join(m[:3] + m[3:].join(("(?:", ")?")) for m in _MONTHS) + r")"
_DAY_MONTH = re.compile(r"^(?:(?P<d1>\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH + r"|" + _MONTH.replace("month", "month2")
                        + r"\s+(?P<d2>\d{1,2})(?:st|nd|rd|th)?)(?:,?\s+(?P<year>\d{4}))?$")

PAST_PHRASES = (
    "today, yesterday, day before yesterday, 3 days ago, monday, last monday, this week, last week, "
    "last 7 days, this month, last month, 28 september, 2026-09-28"
)


def past_range(phrase: str, today: date) -> tuple[date, date] | None:
    """The days (first, last, inclusive) a phrase about the past means; None for "any time".

    Worked out here because the model gets dates wrong (see the top of this module). A weekday
    on its own is the most recent one, today included; "last Monday" is the one before today.
    Raises ValueError for a phrase it does not know.
    """
    text = re.sub(r"[^\w\s-]", " ", phrase.lower()).strip()
    text = re.sub(r"\b(on|the|in|during|of)\b", " ", text)
    text = " ".join(text.split())
    if text in ("", "any", "anytime", "any time", "ever", "all", "always", "all time"):
        return None
    if text in ("today", "now", "so far today"):
        return today, today
    if text == "yesterday":
        return today - timedelta(days=1), today - timedelta(days=1)
    if text in ("day before yesterday", "day before"):
        return today - timedelta(days=2), today - timedelta(days=2)
    if match := re.fullmatch(r"(\w+) days? ago", text):
        count = _NUMBERS.get(match[1]) if not match[1].isdigit() else int(match[1])
        if count is not None:
            return today - timedelta(days=count), today - timedelta(days=count)
    if match := re.fullmatch(r"(?:last|past) (\w+) days?", text):
        count = _NUMBERS.get(match[1]) if not match[1].isdigit() else int(match[1])
        if count:
            return today - timedelta(days=count - 1), today
    monday = today - timedelta(days=today.weekday())
    if text == "this week":
        return monday, today
    if text == "last week":
        return monday - timedelta(days=7), monday - timedelta(days=1)
    if text == "this month":
        return today.replace(day=1), today
    if text == "last month":
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    if match := re.fullmatch(r"(last |this |past )?(" + "|".join(_WEEKDAYS) + ")", text):
        back = (today.weekday() - _WEEKDAYS.index(match[2])) % 7
        if match[1] == "last " and back == 0:
            back = 7
        day = today - timedelta(days=back)
        return day, day
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        day = date.fromisoformat(text)
        return day, day
    if match := _DAY_MONTH.fullmatch(text):
        name = match["month"] or match["month2"]
        month = next(i for i, m in enumerate(_MONTHS, start=1) if m.startswith(name[:3]))
        number = int(match["d1"] or match["d2"])
        year = int(match["year"]) if match["year"] else today.year
        day = date(year, month, number)
        if not match["year"] and day > today:
            day = date(year - 1, month, number)  # "28 December" said in October means last December
        return day, day
    raise ValueError(f"Not a time I can look up: {phrase!r}. Try: {PAST_PHRASES}.")


def upcoming_days(now: datetime | None = None, days: int = 14) -> str:
    """'- Wednesday 30 September 2026 (today) = 2026-09-30' and the following days, one per line."""
    today = (now or datetime.now().astimezone()).date()
    labels = {0: " (today)", 1: " (tomorrow)"}
    lines = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        lines.append(f"- {day:%A} {day.day} {day:%B %Y}{labels.get(offset, '')} = {day.isoformat()}")
    return "\n".join(lines)
