"""Looking back: what the user did with NOVA on a day, or when something last happened."""

from __future__ import annotations

from datetime import date, timedelta

from pydantic import BaseModel, Field

from nova.dates import PAST_PHRASES, past_range
from nova.timeline import Timeline, describe
from nova.tools.base import Tool, ToolResult


class RecallActivityArgs(BaseModel):
    when: str = Field(
        default="",
        description=(
            "The time asked about, in the user's words: 'today', 'yesterday', 'monday', 'last week', "
            "'3 days ago', '28 september'. Empty for any time."
        ),
    )
    about: str = Field(
        default="",
        description="Words to look for, e.g. 'VS Code' or 'budget'. Empty for everything in that time.",
    )


def _span(first: date, last: date) -> str:
    if first == last:
        return f"{first:%A} {first.day} {first:%B %Y}"
    return f"{first:%A} {first.day} {first:%B} to {last:%A} {last.day} {last:%B %Y}"


def timeline_tools(timeline: Timeline) -> list[Tool]:
    async def recall_activity(args: RecallActivityArgs) -> ToolResult:
        today = date.today()
        try:
            days = past_range(args.when, today)
        except ValueError:
            return ToolResult(False, f"Could not tell which day '{args.when}' means. Use one of: {PAST_PHRASES}.")
        if days is not None and days[0] > today:
            return ToolResult(False, "That is in the future; the timeline only has what already happened.")
        if args.about.strip():
            entries = timeline.search(args.about, *(days or (None, None)))
            entries.reverse()  # oldest first, like a day's timeline
            scope = f"mentioning '{args.about}'" + (f" on {_span(*days)}" if days else "")
        else:
            first, last = days or (today - timedelta(days=2), today)
            entries = timeline.between(first, last)
            scope = f"on {_span(first, last)}" if days else f"in the last three days ({_span(first, last)})"
        if not entries:
            return ToolResult(True, f"Nothing recorded {scope}. NOVA only knows what was done or said with it.")
        return ToolResult(True, f"What happened {scope} (local times):\n{describe(entries)}")

    def summary(args: RecallActivityArgs) -> str:
        when = args.when.strip() or "recently"
        return f"Look back at {when}" + (f" for '{args.about}'" if args.about.strip() else "")

    return [
        Tool(
            name="recall_activity",
            description=(
                "Look back at what the user did with NOVA: what they asked, what NOVA did, what it remembered "
                "and which reminders rang, by day. Use for 'what did I do yesterday?' or 'when did I last open "
                "VS Code?'. Pass the time in the user's own words; dates are worked out for you."
            ),
            args_model=RecallActivityArgs,
            handler=recall_activity,
            describe=summary,
            read_only=True,
        )
    ]
