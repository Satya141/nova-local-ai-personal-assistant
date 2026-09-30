from __future__ import annotations

import asyncio

from conftest import FakeProvider, call, say

from nova.agent import Agent
from nova.events import EventBus
from nova.inference.base import ModelError
from nova.memory.extractor import MemoryExtractor
from nova.permissions import PermissionGate
from nova.permissions.audit import ActionLog


def make_agent(provider, registry, store, gate=None, **kwargs) -> tuple[Agent, PermissionGate]:
    gate = gate or PermissionGate(confirm_timeout=5)
    return Agent(provider, registry, gate, store, **kwargs), gate


async def collect(agent: Agent, conversation_id: str, text: str) -> list[dict]:
    return [event async for event in agent.run_turn(conversation_id, text)]


def types(events: list[dict]) -> list[str]:
    return [event["type"] for event in events]


async def test_plain_answer_streams_tokens_and_is_stored(registry, store):
    provider = FakeProvider([[say("Hello"), say(" there")]])
    agent, _ = make_agent(provider, registry, store)
    conversation = store.create_conversation()

    events = await collect(agent, conversation, "hi")

    assert types(events) == ["token", "token", "done"]
    history = store.history(conversation, 10)
    assert [(m.role, m.content) for m in history] == [("user", "hi"), ("assistant", "Hello there")]


async def test_tool_call_runs_and_its_result_reaches_the_model(registry, store, executed):
    provider = FakeProvider([[call("echo", text="ping")], [say("Done.")]])
    agent, _ = make_agent(provider, registry, store)
    conversation = store.create_conversation()

    events = await collect(agent, conversation, "echo ping")

    assert types(events) == ["tool_call", "tool_result", "token", "done"]
    assert events[0]["summary"] == "Echo ping"
    assert events[1]["ok"] is True
    assert executed == ["echo"]
    # The second model request must contain the tool's observation.
    second_request = provider.requests[1]
    assert second_request[0].role == "system"
    assert (second_request[-1].role, second_request[-1].content, second_request[-1].tool_name) == (
        "tool",
        "echo:ping",
        "echo",
    )


async def test_risky_tool_waits_for_approval_then_runs(registry, store, executed):
    provider = FakeProvider([[call("danger", text="x")], [say("Done.")]])
    agent, gate = make_agent(provider, registry, store)
    conversation = store.create_conversation()

    events = []
    async for event in agent.run_turn(conversation, "do it"):
        events.append(event)
        if event["type"] == "confirm_request":
            assert executed == [], "handler ran before the user confirmed"
            assert event["risk"] == "high"
            assert gate.resolve(event["id"], True)

    assert types(events) == ["tool_call", "confirm_request", "tool_result", "token", "done"]
    assert executed == ["danger"]


async def test_declined_tool_never_runs_and_model_is_told(registry, store, executed):
    provider = FakeProvider([[call("danger", text="x")], [say("Okay, cancelled.")]])
    agent, gate = make_agent(provider, registry, store)
    conversation = store.create_conversation()

    events = []
    async for event in agent.run_turn(conversation, "do it"):
        events.append(event)
        if event["type"] == "confirm_request":
            gate.resolve(event["id"], False)

    assert executed == []
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["ok"] is False
    assert "chose Cancel" in provider.requests[1][-1].content


async def test_unanswered_confirmation_times_out_as_denial(registry, store, executed):
    provider = FakeProvider([[call("danger", text="x")], [say("Timed out.")]])
    agent, _ = make_agent(provider, registry, store, gate=PermissionGate(confirm_timeout=0.05))

    events = await collect(agent, store.create_conversation(), "do it")

    assert executed == []
    assert next(e for e in events if e["type"] == "tool_result")["ok"] is False


async def test_abandoned_turn_leaves_no_pending_confirmation(registry, store, executed):
    provider = FakeProvider([[call("danger", text="x")]])
    agent, gate = make_agent(provider, registry, store)

    turn = agent.run_turn(store.create_conversation(), "do it")
    async for event in turn:
        if event["type"] == "confirm_request":
            call_id = event["id"]
            break
    await turn.aclose()  # The client disconnected.

    assert gate.resolve(call_id, True) is False
    assert executed == []


async def test_unknown_tool_is_reported_not_executed(registry, store, executed):
    provider = FakeProvider([[call("format_disk", text="c")], [say("I can't do that.")]])
    agent, _ = make_agent(provider, registry, store)

    events = await collect(agent, store.create_conversation(), "wipe it")

    assert executed == []
    assert next(e for e in events if e["type"] == "tool_result")["ok"] is False
    assert "no tool named 'format_disk'" in provider.requests[1][-1].content


async def test_invalid_arguments_are_rejected_before_the_handler(registry, store, executed):
    provider = FakeProvider([[call("echo", wrong="field")], [say("Sorry.")]])
    agent, _ = make_agent(provider, registry, store)

    await collect(agent, store.create_conversation(), "echo")

    assert executed == []
    assert "Invalid arguments for echo" in provider.requests[1][-1].content


async def test_tool_exception_becomes_a_failed_result(registry, store):
    provider = FakeProvider([[call("broken", text="x")], [say("It failed.")]])
    agent, _ = make_agent(provider, registry, store)

    events = await collect(agent, store.create_conversation(), "go")

    assert next(e for e in events if e["type"] == "tool_result")["ok"] is False
    assert "disk on fire" in provider.requests[1][-1].content
    assert types(events)[-1] == "done"


async def test_step_limit_stops_a_model_that_never_finishes(registry, store, executed):
    provider = FakeProvider([[call("echo", text=str(n))] for n in range(3)])
    agent, _ = make_agent(provider, registry, store, max_steps=3)

    events = await collect(agent, store.create_conversation(), "loop")

    assert executed == ["echo"] * 3
    assert types(events)[-2:] == ["error", "done"]


async def test_an_identical_repeated_call_runs_only_once(registry, store, executed):
    provider = FakeProvider([[call("echo", text="same")], [call("echo", text="same")], [say("Done.")]])
    agent, _ = make_agent(provider, registry, store)

    events = await collect(agent, store.create_conversation(), "echo twice")

    assert executed == ["echo"]
    assert types(events) == ["tool_call", "tool_result", "token", "done"]
    assert "already made this exact call" in provider.requests[2][-1].content


async def test_model_failure_is_surfaced_as_an_error_event(registry, store):
    provider = FakeProvider([ModelError("Cannot reach Ollama")])
    agent, _ = make_agent(provider, registry, store)

    events = await collect(agent, store.create_conversation(), "hi")

    assert events == [{"type": "error", "message": "Cannot reach Ollama"}, {"type": "done"}]


async def test_remembered_facts_reach_the_system_prompt(registry, store, memory):
    await memory.add("The user's name is Satya.", "profile", "explicit")
    provider = FakeProvider([[say("Hi Satya.")]])
    agent, _ = make_agent(provider, registry, store, memory=memory)

    await collect(agent, store.create_conversation(), "hello")

    system = provider.requests[0][0].content
    assert "What you remember about the user:\n- #1: The user's name is Satya." in system
    assert "(tomorrow) = " in system, "the calendar is in the prompt"


async def test_spoken_turns_ask_for_short_plain_replies(registry, store):
    provider = FakeProvider([[say("Done.")], [say("Done.")]])
    agent, _ = make_agent(provider, registry, store)
    conversation = store.create_conversation()

    await collect(agent, conversation, "typed")
    async for _ in agent.run_turn(conversation, "spoken", voice=True):
        pass

    assert "read aloud" not in provider.requests[0][0].content
    assert "read aloud" in provider.requests[1][0].content


async def test_every_action_is_logged_with_its_outcome(registry, store, db):
    log = ActionLog(db)
    provider = FakeProvider(
        [
            [call("echo", text="a")],
            [call("danger", text="b")],
            [call("nope", text="c")],
            [say("Done.")],
        ]
    )
    agent, gate = make_agent(provider, registry, store, actions=log)

    async for event in agent.run_turn(store.create_conversation(), "go"):
        if event["type"] == "confirm_request":
            gate.resolve(event["id"], False)

    entries = list(reversed(log.recent()))
    assert [(e["tool"], e["outcome"], e["ok"]) for e in entries] == [
        ("echo", "ran", True),
        ("danger", "declined", False),
        ("nope", "rejected", False),
    ]
    assert entries[0]["arguments"] == {"text": "a"}


async def test_lasting_facts_are_extracted_after_the_reply(registry, store, memory):
    provider = FakeProvider(
        [[say("Good luck!")]],
        json_replies=[{"memories": [{"category": "project", "content": "The user has an interview at Cognizant."}]}],
    )
    bus = EventBus()
    agent, _ = make_agent(
        provider, registry, store, memory=memory, extractor=MemoryExtractor(provider, memory), bus=bus
    )

    async with bus.subscribe() as queue:
        events = await collect(agent, store.create_conversation(), "I have an interview at Cognizant")
        assert types(events) == ["token", "done"], "extraction never delays the reply"
        await agent.drain()
        announced = queue.get_nowait()

    assert announced["type"] == "memory_saved"
    assert announced["memory"]["content"] == "The user has an interview at Cognizant."
    assert [m.content for m in memory.all()] == ["The user has an interview at Cognizant."]


async def test_extraction_is_told_what_was_already_done(registry, store, memory):
    provider = FakeProvider([[call("echo", text="notes")], [say("Set.")]])
    agent, _ = make_agent(provider, registry, store, memory=memory, extractor=MemoryExtractor(provider, memory))

    await collect(agent, store.create_conversation(), "I have an exam soon, echo my notes")
    await agent.drain()

    prompt = provider.json_requests[0][0].content
    assert "already carried out as actions" in prompt
    assert "- Echo notes" in prompt


async def test_no_extraction_when_the_user_asked_to_remember(registry, store, memory):
    provider = FakeProvider([[call("remember", text="tea")], [say("Noted.")]])
    agent, _ = make_agent(provider, registry, store, memory=memory, extractor=MemoryExtractor(provider, memory))

    await collect(agent, store.create_conversation(), "remember that I like tea")
    await agent.drain()

    assert provider.json_requests == []


async def test_confirmation_can_arrive_from_another_task(registry, store, executed):
    """The real flow: the stream is suspended in wait() while a second request resolves it."""
    provider = FakeProvider([[call("danger", text="x")], [say("Done.")]])
    agent, gate = make_agent(provider, registry, store)
    turn = agent.run_turn(store.create_conversation(), "do it")

    async def approve_when_asked(event: dict) -> None:
        await asyncio.sleep(0.01)
        gate.resolve(event["id"], True)

    async for event in turn:
        if event["type"] == "confirm_request":
            asyncio.create_task(approve_when_asked(event))

    assert executed == ["danger"]
