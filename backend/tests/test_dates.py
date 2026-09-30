from __future__ import annotations

from datetime import date, datetime

import pytest

from nova.dates import resolve_weekdays, upcoming_days

WEDNESDAY = date(2026, 9, 30)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("an interview next Friday", "an interview on Friday 9 October 2026"),
        ("an interview on next Friday", "an interview on Friday 9 October 2026"),
        ("an interview this Friday", "an interview on Friday 2 October 2026"),
        ("an interview on Friday", "an interview on Friday 2 October 2026"),
        ("an interview coming Friday", "an interview on Friday 2 October 2026"),
        ("an exam next Monday", "an exam on Monday 5 October 2026"),
        # "this Wednesday" said on a Wednesday means next week's.
        ("a call this Wednesday", "a call on Wednesday 7 October 2026"),
        ("plays cricket every Sunday", "plays cricket every Sunday"),
        ("lives in Hyderabad", "lives in Hyderabad"),
    ],
)
def test_resolve_weekdays(text, expected):
    assert resolve_weekdays(text, WEDNESDAY) == expected


def test_resolved_weekday_matches_its_date():
    text = resolve_weekdays("next Sunday", WEDNESDAY)
    day = datetime.strptime(text.removeprefix("on "), "%A %d %B %Y")
    assert day.strftime("%A") == "Sunday"


def test_upcoming_days_lists_real_weekdays():
    lines = upcoming_days(datetime(2026, 9, 30, 12, 0), days=3).splitlines()
    assert lines == [
        "- Wednesday 30 September 2026 (today) = 2026-09-30",
        "- Thursday 1 October 2026 (tomorrow) = 2026-10-01",
        "- Friday 2 October 2026 = 2026-10-02",
    ]
