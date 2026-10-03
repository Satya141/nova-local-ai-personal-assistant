"""Notices durable facts in what the user says and saves them to long-term memory.

Runs after a turn has been answered, so it never slows a reply down. It is
deliberately conservative: most messages hold nothing worth keeping, and a
memory full of noise is worse than a sparse one.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from nova.dates import upcoming_days
from nova.inference.base import Message, ModelError, ModelProvider
from nova.memory.long_term import CATEGORIES, MemoryStore, SavedMemory, SensitiveContent

log = logging.getLogger(__name__)

_MAX_PER_MESSAGE = 3
_MAX_LENGTH = 200

# Facts that expire within hours are not long-term memory. The model sometimes
# turns a request into one ("Remind me in 1 minute to review my notes" became
# "The user needs to review notes 1 minute from now."), so this is enforced here.
# Relative day words are dropped too: a lasting fact carries an absolute date
# ("on Friday 9 October 2026"), and "tomorrow" is wrong the day after.
_SHORT_LIVED = re.compile(
    r"\b(\d+|a|an|one|few|couple of)\s+(seconds?|minutes?|mins?|hours?|hrs?)\b"
    r"|\b(from now|right now|today|tonight|tomorrow|yesterday|this (morning|afternoon|evening))\b"
    r"|\bremind",
    re.I,
)

# A message that is only a command holds no fact about the user. With no tool for the request,
# nothing marked it as one, and "now send resume to anandhitha on whatsapp" was saved as "The user
# sends their resume to Anandhitha on WhatsApp." 3/3. Words about the user themselves mean there
# may be a fact after all ("Open VS Code, I'm going to work on my portfolio website").
_LEAD = (
    r"(?:(?:now|ok|okay|so|and|then|also|just|please|pls|plz|kindly|hey nova|hi nova|nova|"
    r"can you|could you|would you|will you|can u)[,\s]+)*"
)
_COMMANDS = (
    "send", "share", "forward", "text", "message", "msg", "whatsapp", "email", "mail", "call", "ring",
    "open", "close", "launch", "start", "stop", "run", "play", "pause", "find", "search", "show", "look",
    "check", "read", "write", "draft", "reply", "post", "tell", "give", "get", "go", "take", "turn",
    "delete", "remove", "move", "copy", "rename", "sort", "organise", "organize", "create", "make", "add",
    "set", "remind", "schedule", "cancel", "book", "buy", "order", "download", "upload", "print", "save",
)
_REQUEST = re.compile(rf"^\s*{_LEAD}(?:{'|'.join(_COMMANDS)})\b", re.I)
_ABOUT_THE_USER = re.compile(r"\b(i|i'm|im|i've|i'll|i'd|my|mine|myself|we|we're|our)\b", re.I)


def plain_request(message: str) -> bool:
    """A command and nothing about the user: no memory can come from it."""
    return bool(_REQUEST.match(message)) and not _ABOUT_THE_USER.search(message)


_PROMPT = """\
You maintain the long-term memory of a personal assistant.

Read the user's message and extract facts about the user that will still be useful weeks from now: \
who they are, what they prefer, projects they are working on, people in their life, and their routines.

Rules:
- Only facts the user stated about themselves or their life. The request itself ("open calculator", \
"find my resume", "remind me to call dad", "what time is it") is not a fact, but a request can \
mention one ("..., I need to finish my thesis").
- Plans and upcoming events are worth keeping. Write their dates as absolute dates, copied from \
this calendar (never calculate a date yourself):
{calendar}
- Nothing about the assistant, and never guess: "mom" and "dad" are not names.
- Never passwords, codes, account or ID numbers, or health or money details.
- Write each memory as one short sentence that starts with "The user".
- Leave out anything already covered by the known memories.
- Most messages contain nothing worth remembering. Then return an empty list.

Categories: profile (who they are), preference, project, person (someone in their life), routine, fact.

Examples:
"Open Word, I need to finish my thesis on solar panels" -> \
{{"memories": [{{"category": "project", "content": "The user is writing a thesis on solar panels."}}]}}
"Remind me tomorrow to call dad" -> {{"memories": []}}
"My dentist appointment is on Monday, what should I ask?" -> \
{{"memories": [{{"category": "fact", "content": "The user has a dentist appointment on {next_monday}."}}]}}
"Search my documents for the lease agreement" -> {{"memories": []}}

Known memories:
{known}
{handled}
The user's message:
{message}"""

SCHEMA = {
    "type": "object",
    "properties": {
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "category": {"type": "string", "enum": list(CATEGORIES)},
                    "content": {"type": "string"},
                },
                "required": ["category", "content"],
            },
        }
    },
    "required": ["memories"],
}


class MemoryExtractor:
    def __init__(self, provider: ModelProvider, store: MemoryStore) -> None:
        self._provider = provider
        self._store = store

    async def extract(
        self, message: str, conversation_id: str | None = None, handled: list[str] | None = None
    ) -> list[SavedMemory]:
        """Save what is worth keeping from one user message. Returns the memories that are new.

        `handled` lists the actions NOVA carried out for this message ("Remind you tomorrow at
        9:00 AM: Review notes"). Those parts of the message were requests, not facts.
        """
        if plain_request(message):
            return []
        known = [memory.content for memory, _ in await self._store.search(message, limit=8)]
        today = datetime.now().astimezone()
        # The worked example resolves a weekday against the real calendar, so
        # the model sees exactly the lookup it should make.
        next_monday = (today + timedelta(days=7 - today.weekday())).date()
        prompt = _PROMPT.format(
            calendar=upcoming_days(today),
            next_monday=f"{next_monday:%A} {next_monday.day} {next_monday:%B %Y}",
            known="\n".join(f"- {line}" for line in known) or "(none)",
            handled=(
                "\nParts of the message NOVA already carried out as actions. They were requests, "
                "not facts; do not turn them into memories:\n"
                + "\n".join(f"- {action}" for action in handled)
                + "\n"
                if handled
                else ""
            ),
            message=message,
        )
        try:
            reply = await self._provider.complete_json([Message(role="user", content=prompt)], SCHEMA)
        except ModelError as exc:
            log.warning("Memory extraction skipped: %s", exc)
            return []

        saved = []
        candidates = reply.get("memories") if isinstance(reply, dict) else None
        for item in candidates or []:
            # The cap counts what was kept, so rejected lines do not crowd out good ones.
            if len(saved) == _MAX_PER_MESSAGE:
                break
            content = str(item.get("content", "")).strip() if isinstance(item, dict) else ""
            if not content or len(content) > _MAX_LENGTH:
                continue
            if not content.lower().startswith("the user"):
                # The model did not follow the format; such lines are usually not about the user.
                continue
            if _SHORT_LIVED.search(content):
                continue
            try:
                result = await self._store.add(content, str(item.get("category", "fact")), "extracted", conversation_id)
            except SensitiveContent:
                log.info("Dropped a memory that looked sensitive")
                continue
            if result.created:
                saved.append(result)
        return saved
