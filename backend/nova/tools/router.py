"""Which tools the model is shown for a request.

Every tool definition travels with every model call. With every account connected they come to
about 5,000 tokens of the 8,192-token window, so most of the room was spent before the user's
words were read, and memories ended up far from the question (see `_with_memories`).

Code picks the groups a request needs, from the user's own words only: never from web pages or tool
results, so outside content cannot change what the model is offered. Matching is generous on
purpose. A tool shown without need costs a few tokens; a tool hidden when needed fails the request.
When nothing matches (small talk, an unusual request) every tool is offered, as before. Only
offering is limited here: the gate and the agent loop still check every call against the full registry.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Group:
    name: str
    tools: frozenset[str]
    trigger: re.Pattern[str]


def _group(name: str, tools: str, *patterns: str) -> Group:
    return Group(name, frozenset(tools.split()), re.compile("|".join(patterns), re.IGNORECASE))


_DAYS = r"(?:mon|tues?|wed(?:nes)?|thu(?:rs)?|fri|sat(?:ur)?|sun)(?:day)?"

GROUPS: tuple[Group, ...] = (
    _group(
        "apps",
        "open_application close_application read_app click_in_app type_in_app press_keys",
        r"\b(?:open|launch|start|run|close|quit|exit|switch|apps?|applications?|windows?|click|type|typing|press|keys?"
        r"|keyboard|whatsapp|telegram|spotify|notepad|calculator|outlook|teams|slack|discord|zoom|chrome|edge"
        r"|firefox|vs ?code|visual studio|terminal|settings|paint|explorer|play|message|text|chat|send)\b",
        r"\b(?:microsoft|ms) word\b",
    ),
    _group(
        "files",
        "search_files open_path list_folder create_folder sort_files move_paths rename_path delete_paths",
        r"\b(?:files?|folders?|director(?:y|ies)|documents?|docs?|downloads?|desktop|pictures|photos?|images?"
        r"|videos?|music|resume|cv|pdf|docx?|xlsx?|pptx?|csv|txt|zip|spreadsheets?|presentations?|invoices?"
        r"|sort|organi[sz]e|rename|move|copy|delete|remove|trash|recycle|find|search|locate|open|path|saved?"
        r"|where is)\b",
        r"[a-z]:\\",
    ),
    _group(
        "web",
        "web_search open_web_page read_web_page click_element type_into",
        r"\b(?:web|websites?|sites?|online|internet|google|bing|search|browse|browser|look ?up|news|latest"
        r"|current|weather|price|prices|stock|score|wikipedia|youtube|reddit|links?|url|pages?|open)\b",
        r"https?://|www\.|\.(?:com|org|net|io|in|dev|ai|edu|gov)\b",
    ),
    _group(
        "reminders",
        "create_reminder schedule_task list_reminders cancel_reminder",
        r"\b(?:remind\w*|alarms?|timers?|snooze|schedul\w+|daily|weekly|recurring|tasks?|later|tomorrow|tonight"
        r"|cancel|every|each (?:day|morning|evening|night|week))\b",
        rf"\bon {_DAYS}\b",
        r"\bat \d",
        r"\bin \d+ ?(?:min\w*|hours?|h)\b",
    ),
    _group(
        "memory",
        "remember recall forget",
        r"\b(?:remember|forget|memor(?:y|ies)|recall|know about me|about me|my name|note that|keep in mind"
        r"|don'?t forget)\b",
        r"\bwhat do you know\b",
    ),
    _group(
        "email",
        "email_search email_read email_draft email_send email_not_connected",
        r"\b(?:e-?mails?|mail|inbox|gmail|unread|drafts?|repl(?:y|ied)|send|sent|message|forward|attachments?"
        r"|subject|google)\b",
    ),
    _group(
        "calendar",
        "calendar_events calendar_add calendar_not_connected",
        r"\b(?:calendar|meetings?|appointments?|events?|agenda|invites?|invited|schedul\w+|busy|free|available"
        r"|today|tomorrow|tonight|this week|next week|google)\b",
        rf"\bon {_DAYS}\b",
        r"\bwhat'?s on\b",
    ),
    _group(
        "github",
        "github_activity github_search github_read github_comment github_new_issue github_not_connected",
        r"\b(?:github|git hub|pull requests?|prs?|issues?|repos?|repositor(?:y|ies)|commits?|branch|notifications?"
        r"|review|merge)\b",
    ),
    _group(
        "screen",
        "look_at_screen",
        r"\b(?:screen|display|monitor|windows?|looking at|see this|what'?s wrong|explain this|read this"
        r"|this (?:error|code|chart|page|dialog|button|window))\b",
    ),
    _group(
        "timeline",
        "recall_activity",
        r"\b(?:yesterday|last (?:week|month|night|time)|when did i|what did i|did i|activity|history|timeline"
        r"|sum up|summary of my day|earlier|how many times|this morning|recently)\b",
        rf"\blast {_DAYS}\b",
    ),
)

_GROUPED = frozenset(name for group in GROUPS for name in group.tools)

# A reply this short ("yes", "the second one", "send it") means nothing without the request before it.
_SHORT_WORDS = 6


def tools_for(request: str, earlier: Sequence[str], available: Iterable[str]) -> set[str] | None:
    """The tool names to offer for a request, or None to offer every tool.

    `earlier` holds the user's earlier messages in this conversation, oldest first; a short follow-up
    is read together with the one before it. Tools that belong to no group are always offered, so a
    new tool is never hidden by forgetting to list it here.
    """
    text = request
    if len(request.split()) < _SHORT_WORDS and earlier:
        text = f"{earlier[-1]}\n{request}"
    matched = [group for group in GROUPS if group.trigger.search(text)]
    if not matched:
        return None
    offered = {name for group in matched for name in group.tools}
    offered |= {name for name in available if name not in _GROUPED}
    return offered
