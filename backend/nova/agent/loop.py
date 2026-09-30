"""The agent loop: model -> tool selection -> permission check -> execution -> observation.

`run_turn` yields plain-dict events for the client to render:

    {"type": "token", "text": ...}                         assistant text, streamed
    {"type": "tool_call", "id", "name", "summary"}         the model picked a tool
    {"type": "confirm_request", "id", "name", "summary", "risk"}
    {"type": "tool_result", "id", "name", "ok"}
    {"type": "error", "message": ...}
    {"type": "done"}

After a turn, new long-term memories are announced on the event bus as
{"type": "memory_saved", ...}, not in this stream, because noticing them happens
after the reply is finished.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import aclosing
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from nova.dates import upcoming_days
from nova.events import EventBus
from nova.inference.base import Message, ModelError, ModelProvider, ToolCall
from nova.memory.extractor import MemoryExtractor
from nova.memory.long_term import Memory, MemoryStore
from nova.memory.store import ConversationStore
from nova.permissions.audit import ActionLog, Outcome
from nova.permissions.gate import Decision, PermissionGate
from nova.tools.base import ToolRegistry, ToolResult
from nova.vision.reader import ScreenReader

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are NOVA, a local-first AI personal assistant running on the user's Windows computer.

You act; you do not just advise. When the user asks for something a tool can do, call the tool \
instead of explaining how to do it. A request with several parts needs several tool calls: do \
every part, one after another, before you reply.

Rules:
- Only say an action happened if a tool result confirms it. If a tool fails or the user declines, \
say so plainly and do not retry the same call.
- Never invent tools or arguments. If no tool fits, answer from your own knowledge, or say you \
cannot do that yet.
- Never ask "are you sure?". NOVA itself shows the user a confirmation for any risky tool, so just \
call the tool; a successful result means the user already approved.
- If a request is ambiguous, for example several files match, ask one short question.
- To open a file the user describes, find it with search_files first, then open the match.
- When the user asks about something on their screen ("this error", "what does this chart mean", \
"why is this button disabled"), call look_at_screen with their question, then answer from what it saw.
- Reminders: for a clock time, pass `at` as a local ISO date and time, taking the date from the \
calendar below (never calculate a date yourself); for "in 20 minutes" or "in 2 hours", pass \
`in_minutes`.
- Memory: the facts listed under "What you remember about the user" are true; use them to answer \
questions about the user and to personalise what you do. NOVA saves lasting facts by itself, so \
call remember only when the user explicitly asks you to remember something. To forget a fact, \
call forget with its number from that list.
- Keep replies short: one or two sentences for actions. Use Markdown only when it helps.

Current date and time: {now}
User's home folder: {home}

Calendar:
{calendar}{memories}"""


_VOICE_NOTE = """

The user is talking to you by voice and will hear your reply read aloud. Reply in one or two \
short spoken sentences of plain text: no Markdown, no lists, no file paths."""


def _system_message(memories: list[Memory], voice: bool = False) -> Message:
    now = datetime.now().astimezone()
    remembered = ""
    if memories:
        # The number is the memory's id, so "forget that" needs no lookup first.
        lines = "\n".join(f"- #{memory.id}: {memory.content}" for memory in memories)
        remembered = f"\n\nWhat you remember about the user:\n{lines}"
    return Message(
        role="system",
        content=_SYSTEM_PROMPT.format(
            now=now.strftime("%A, %d %B %Y, %H:%M (%Z)"),
            home=Path.home(),
            calendar=upcoming_days(now),
            memories=remembered,
        )
        + (_VOICE_NOTE if voice else ""),
    )


def _validation_summary(error: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in item['loc']) or 'arguments'}: {item['msg']}"
        for item in error.errors()
    )


class Agent:
    def __init__(
        self,
        provider: ModelProvider,
        registry: ToolRegistry,
        gate: PermissionGate,
        store: ConversationStore,
        *,
        memory: MemoryStore | None = None,
        extractor: MemoryExtractor | None = None,
        actions: ActionLog | None = None,
        bus: EventBus | None = None,
        screen: ScreenReader | None = None,
        max_steps: int = 8,
        history_limit: int = 40,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._gate = gate
        self._store = store
        self._memory = memory
        self._extractor = extractor
        self._actions = actions
        self._bus = bus
        self._screen = screen
        self._max_steps = max_steps
        self._history_limit = history_limit
        self._background: set[asyncio.Task] = set()

    async def run_turn(
        self, conversation_id: str, user_text: str, voice: bool = False, screen: bool = False
    ) -> AsyncIterator[dict[str, Any]]:
        """One turn. `screen` means the user pressed the screen button: look first, no confirmation."""
        self._store.add_message(conversation_id, Message(role="user", content=user_text))
        memories = await self._memory.context_for(user_text) if self._memory else []
        system = _system_message(memories, voice)
        # Identical calls within one turn run once; a model stuck in a loop gets the earlier result back.
        results: dict[str, ToolResult] = {}
        called: set[str] = set()
        # What NOVA did this turn, in words, so memory extraction can tell requests from facts.
        handled: list[str] = []
        if screen and self._screen:
            # The screen button: the vision model's answer is the reply. Handing it to the chat
            # model as well would mean swapping models on the GPU (about 6 s) for nothing.
            answered = False
            async for event in self._look_first(conversation_id, user_text):
                answered = answered or event["type"] == "token"
                yield event
            if answered:
                yield {"type": "done"}
                self._after_turn(conversation_id, user_text, {"look_at_screen"}, ["Look at your screen"])
                return
            called.add("look_at_screen")
            handled.append("Look at your screen")
        try:
            for _ in range(self._max_steps):
                messages = [system, *self._store.history(conversation_id, self._history_limit)]
                text: list[str] = []
                calls: list[ToolCall] = []
                async for chunk in self._provider.chat(messages, self._registry.schemas()):
                    if chunk.text:
                        text.append(chunk.text)
                        yield {"type": "token", "text": chunk.text}
                    calls.extend(chunk.tool_calls)

                self._store.add_message(
                    conversation_id,
                    Message(role="assistant", content="".join(text), tool_calls=tuple(calls)),
                )
                if not calls:
                    yield {"type": "done"}
                    self._after_turn(conversation_id, user_text, called, handled)
                    return
                for call in calls:
                    called.add(call.name)
                    # aclosing: if the client disconnects mid-tool, the pending
                    # confirmation is cancelled now, not whenever the GC runs.
                    async with aclosing(self._run_tool(conversation_id, call, results)) as events:
                        async for event in events:
                            if event["type"] == "tool_call":
                                handled.append(event["summary"])
                            yield event

            yield {
                "type": "error",
                "message": f"Stopped after {self._max_steps} steps without finishing. Try a smaller request.",
            }
        except ModelError as exc:
            yield {"type": "error", "message": str(exc)}
        yield {"type": "done"}

    async def _run_tool(
        self, conversation_id: str, call: ToolCall, results: dict[str, ToolResult]
    ) -> AsyncIterator[dict[str, Any]]:
        signature = f"{call.name}:{json.dumps(call.arguments, sort_keys=True)}"
        if signature in results:
            # A repeat is answered from the first result, silently: nothing runs twice.
            earlier = results[signature]
            self._store.add_message(
                conversation_id,
                Message(
                    role="tool",
                    content=f"You already made this exact call in this turn. Its result was: {earlier.content} "
                    "Do not call it again; continue with the next step or reply.",
                    tool_name=call.name,
                ),
            )
            return

        prepared = self._prepare(call)
        if isinstance(prepared, ToolResult):
            # Rejected before it could run: unknown tool or arguments that do not fit its schema.
            yield {"type": "tool_call", "id": call.id, "name": call.name, "summary": call.name}
            result, outcome = prepared, Outcome.REJECTED
        else:
            tool, args = prepared
            summary = tool.describe(args)
            yield {"type": "tool_call", "id": call.id, "name": tool.name, "summary": summary}

            outcome = Outcome.RAN
            if self._gate.check(tool, args) is Decision.CONFIRM:
                self._gate.open_request(call.id)
                try:
                    yield {
                        "type": "confirm_request",
                        "id": call.id,
                        "name": tool.name,
                        "summary": summary,
                        "risk": tool.risk.value,
                    }
                    outcome = Outcome.APPROVED if await self._gate.wait(call.id) else Outcome.DECLINED
                finally:
                    self._gate.discard(call.id)

            if outcome is Outcome.DECLINED:
                result = ToolResult(
                    False,
                    "Not done: the user was shown a confirmation and chose Cancel. "
                    "Acknowledge in a few words. Do not retry and do not ask again.",
                )
            else:
                try:
                    result = await tool.handler(args)
                except Exception as exc:  # A tool bug must not take down the turn.
                    log.exception("Tool %s failed", tool.name)
                    result = ToolResult(False, f"The tool failed: {exc}")
            results[signature] = result

        if self._actions:
            self._actions.record(conversation_id, call.name, call.arguments, outcome, result.ok, result.content)
        self._store.add_message(
            conversation_id, Message(role="tool", content=result.content, tool_name=call.name)
        )
        yield {"type": "tool_result", "id": call.id, "name": call.name, "ok": result.ok}

    async def _look_first(self, conversation_id: str, question: str) -> AsyncIterator[dict[str, Any]]:
        """The screen button: the click is the user's permission, so the screenshot is taken straight away.

        It is recorded as an ordinary look_at_screen call so the model sees the result in context.
        """
        call = ToolCall(id=f"screen-{uuid.uuid4().hex[:12]}", name="look_at_screen", arguments={"question": question})
        yield {"type": "tool_call", "id": call.id, "name": call.name, "summary": "Look at your screen"}
        result = await self._screen.look(question)
        self._store.add_message(conversation_id, Message(role="assistant", content="", tool_calls=(call,)))
        self._store.add_message(conversation_id, Message(role="tool", content=result.content, tool_name=call.name))
        if self._actions:
            self._actions.record(conversation_id, call.name, call.arguments, Outcome.APPROVED, result.ok, result.content)
        yield {"type": "tool_result", "id": call.id, "name": call.name, "ok": result.ok}
        if result.ok:
            seen = json.loads(result.content)["seen"]
            # Stored as the reply, so follow-up questions have it in context.
            self._store.add_message(conversation_id, Message(role="assistant", content=seen))
            yield {"type": "token", "text": seen}

    def _prepare(self, call: ToolCall) -> tuple[Any, Any] | ToolResult:
        """Resolve the tool and validate its arguments. The model's word is not trusted for either."""
        tool = self._registry.get(call.name)
        if tool is None:
            return ToolResult(
                False, f"There is no tool named '{call.name}'. Available tools: {', '.join(self._registry.names())}."
            )
        try:
            return tool, tool.args_model.model_validate(call.arguments)
        except ValidationError as exc:
            return ToolResult(False, f"Invalid arguments for {tool.name}: {_validation_summary(exc)}")

    # --- after the reply -------------------------------------------------------

    def _after_turn(self, conversation_id: str, user_text: str, called: set[str], handled: list[str]) -> None:
        # If the user asked to remember something, the remember tool already saved it.
        if self._extractor is None or "remember" in called:
            return
        task = asyncio.create_task(self._extract(conversation_id, user_text, handled))
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    async def _extract(self, conversation_id: str, user_text: str, handled: list[str]) -> None:
        try:
            saved = await self._extractor.extract(user_text, conversation_id, handled)
        except Exception:  # Memory is best effort; it must never surface as a failed turn.
            log.exception("Memory extraction failed")
            return
        for item in saved:
            if self._bus:
                self._bus.publish(
                    {"type": "memory_saved", "conversation_id": conversation_id, "memory": item.memory.to_dict()}
                )

    async def drain(self) -> None:
        """Wait for background work (memory extraction) to finish. Used on shutdown and in tests."""
        while self._background:
            await asyncio.gather(*self._background, return_exceptions=True)
