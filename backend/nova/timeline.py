"""The life timeline: what happened with NOVA, day by day.

Built only from what NOVA already keeps: what the user asked (conversations), what NOVA did
(the action log), what it came to remember, and reminders and tasks that rang. Nothing new is
collected for it. Times are stored in UTC and grouped into the user's local days here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta

from nova.database import Database, parse_timestamp, timestamp

KINDS = ("asked", "did", "remembered", "reminded")
_TEXT = 160


@dataclass(frozen=True)
class Entry:
    at: datetime
    kind: str  # one of KINDS
    text: str
    tool: str | None = None  # for "did": which tool, so the UI can show its character
    ok: bool | None = None  # for "did": whether it worked
    outcome: str | None = None  # for "did": ran, approved, declined, blocked, rejected

    def to_dict(self) -> dict:
        data = asdict(self)
        data["at"] = timestamp(self.at)
        return data


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= _TEXT else text[: _TEXT - 1] + "…"


def _local_bounds(first: date, last: date) -> tuple[str, str]:
    """UTC timestamps for the start of `first` and the end of `last`, in local time."""
    start = datetime.combine(first, time.min).astimezone()
    end = datetime.combine(last + timedelta(days=1), time.min).astimezone()
    return timestamp(start), timestamp(end)


class Timeline:
    def __init__(self, db: Database) -> None:
        self._db = db

    def between(self, first: date, last: date, limit: int = 500) -> list[Entry]:
        """Everything from the start of `first` to the end of `last` (local days), oldest first."""
        start, end = _local_bounds(first, last)
        return self._collect("BETWEEN ? AND ?", (start, end), limit)

    def search(self, words: str, first: date | None = None, last: date | None = None, limit: int = 20) -> list[Entry]:
        """Entries mentioning `words` (all of them, any order), newest first."""
        if first is not None and last is not None:
            start, end = _local_bounds(first, last)
        else:
            start, end = "0000", "9999"
        terms = [w for w in words.lower().split() if len(w) > 1]
        entries = self._collect("BETWEEN ? AND ?", (start, end), 2000)
        found = [e for e in entries if all(term in e.text.lower() for term in terms)]
        return list(reversed(found))[:limit]

    def days(self, last: date, count: int) -> list[dict]:
        """`count` days ending with `last`, newest first, each with its entries."""
        first = last - timedelta(days=count - 1)
        by_day: dict[date, list[Entry]] = {first + timedelta(days=i): [] for i in range(count)}
        for entry in self.between(first, last):
            day = entry.at.astimezone().date()
            if day in by_day:
                by_day[day].append(entry)
        return [
            {"day": day.isoformat(), "entries": [e.to_dict() for e in by_day[day]]}
            for day in sorted(by_day, reverse=True)
        ]

    def _collect(self, where: str, args: tuple, limit: int) -> list[Entry]:
        entries: list[Entry] = []
        for row in self._db.fetch(
            f"SELECT content, created_at FROM messages WHERE role = 'user' AND created_at {where} "
            "ORDER BY created_at LIMIT ?",
            (*args, limit),
        ):
            entries.append(Entry(parse_timestamp(row["created_at"]), "asked", _short(row["content"])))
        for row in self._db.fetch(
            f"SELECT tool, summary, decision, ok, created_at FROM action_log WHERE created_at {where} "
            "ORDER BY created_at LIMIT ?",
            (*args, limit),
        ):
            text = row["summary"] or row["tool"].replace("_", " ").capitalize()
            entries.append(
                Entry(
                    parse_timestamp(row["created_at"]),
                    "did",
                    _short(text),
                    tool=row["tool"],
                    ok=bool(row["ok"]) if row["ok"] is not None else None,
                    outcome=row["decision"],
                )
            )
        for row in self._db.fetch(
            f"SELECT content, created_at FROM memories WHERE created_at {where} ORDER BY created_at LIMIT ?",
            (*args, limit),
        ):
            entries.append(Entry(parse_timestamp(row["created_at"]), "remembered", _short(row["content"])))
        for row in self._db.fetch(
            f"SELECT text, task, fired_at FROM reminders WHERE fired_at IS NOT NULL AND fired_at {where} "
            "ORDER BY fired_at LIMIT ?",
            (*args, limit),
        ):
            text = f"Ran the task: {row['text']}" if row["task"] else row["text"]
            entries.append(Entry(parse_timestamp(row["fired_at"]), "reminded", _short(text)))
        entries.sort(key=lambda e: e.at)
        return entries[-limit:] if len(entries) > limit else entries


def describe(entries: list[Entry], limit: int = 40) -> str:
    """Entries as short lines for the model, local times, grouped by day. Kept small (rule 17)."""
    if not entries:
        return ""
    shown = entries[-limit:]
    lines: list[str] = []
    current: date | None = None
    for entry in shown:
        local = entry.at.astimezone()
        if local.date() != current:
            current = local.date()
            lines.append(f"{current:%A} {current.day} {current:%B %Y}:")
        mark = "" if entry.ok is not False else " (did not work)" if entry.outcome in ("ran", "approved") else ""
        if entry.outcome in ("declined", "blocked", "rejected"):
            mark = f" ({entry.outcome})"
        label = {"asked": "you asked", "did": "NOVA", "remembered": "remembered", "reminded": "reminder rang"}[entry.kind]
        lines.append(f"- {local:%H:%M} {label}: {entry.text}{mark}")
    if len(entries) > limit:
        lines.insert(0, f"(the last {limit} of {len(entries)} entries)")
    return "\n".join(lines)
