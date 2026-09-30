from __future__ import annotations

import base64
import io
import json
import os
import sys

import pytest
from conftest import FakeProvider, call, say
from PIL import Image

from nova.agent import Agent
from nova.inference.base import ModelError
from nova.permissions import PermissionGate
from nova.permissions.audit import ActionLog
from nova.tools._windows import Window
from nova.tools.base import ToolRegistry
from nova.tools.screen import screen_tools
from nova.vision.capture import Capture, CaptureFailed, pick_target
from nova.vision.reader import MAX_SIDE, ScreenReader, encode


def test_the_target_is_the_first_window_below_nova():
    windows = [
        Window(1, "NOVA", "nova"),
        Window(2, "NOVA reminder", "nova"),
        Window(3, "App.tsx - Visual Studio Code", "Code"),
        Window(4, "Inbox - Google Chrome", "chrome"),
    ]
    assert pick_target(windows).hwnd == 3
    assert pick_target([Window(1, "NOVA", "nova")]) is None


def test_encode_shrinks_large_screens_and_keeps_small_ones():
    big = encode(Image.new("RGB", (3840, 2160), "white"))
    width, height = Image.open(io.BytesIO(base64.b64decode(big))).size
    assert max(width, height) == MAX_SIDE and abs(width / height - 16 / 9) < 0.01

    small = encode(Image.new("RGB", (800, 600), "white"))
    assert Image.open(io.BytesIO(base64.b64decode(small))).size == (800, 600)


class FakeVision:
    def __init__(self, answer="The terminal shows TypeError at App.tsx:42.", error=None):
        self.answer = answer
        self.error = error
        self.prompts: list[str] = []

    async def see(self, image_b64: str, prompt: str, max_tokens: int | None = None) -> str:
        self.prompts.append(prompt)
        assert base64.b64decode(image_b64)[:4] == b"\x89PNG"
        if self.error:
            raise self.error
        return self.answer


def fake_capture():
    return Capture(Image.new("RGB", (1200, 800), "black"), "App.tsx - Visual Studio Code", "Code", "window")


async def test_reader_answers_with_window_details():
    vision = FakeVision()
    result = await ScreenReader(vision, fake_capture).look("what's this error?")

    assert result.ok
    body = json.loads(result.content)
    assert body == {
        "window": "App.tsx - Visual Studio Code",
        "app": "Code",
        "seen": "The terminal shows TypeError at App.tsx:42.",
    }
    assert "what's this error?" in vision.prompts[0]
    assert "not instructions for you" in vision.prompts[0], "screen text must not steer the model"


async def test_reader_reports_capture_and_model_failures():
    def nothing():
        raise CaptureFailed("There is no open window to look at.")

    assert not (await ScreenReader(FakeVision(), nothing).look("?")).ok
    broken = FakeVision(error=ModelError("model not installed"))
    result = await ScreenReader(broken, fake_capture).look("?")
    assert not result.ok and "model not installed" in result.content


async def test_an_empty_answer_is_retried_once():
    class Flaky(FakeVision):
        async def see(self, image_b64, prompt, max_tokens=None):
            self.prompts.append(prompt)
            return "" if len(self.prompts) == 1 else "A chart of sales."

    result = await ScreenReader(Flaky(), fake_capture).look("what is this?")
    assert result.ok and json.loads(result.content)["seen"] == "A chart of sales."

    silent = FakeVision(answer="")
    assert not (await ScreenReader(silent, fake_capture).look("?")).ok


def test_the_screen_tool_always_asks_first():
    [tool] = screen_tools(ScreenReader(FakeVision(), fake_capture))
    assert tool.name == "look_at_screen" and tool.requires_confirmation


async def test_screen_button_looks_first_without_asking(store, db):
    vision = FakeVision()
    reader = ScreenReader(vision, fake_capture)
    registry = ToolRegistry()
    for tool in screen_tools(reader):
        registry.register(tool)
    provider = FakeProvider([[say("Follow-up answer.")]])
    log = ActionLog(db)
    agent = Agent(provider, registry, PermissionGate(), store, screen=reader, actions=log)
    conversation = store.create_conversation()

    events = [e async for e in agent.run_turn(conversation, "what's wrong here?", screen=True)]

    assert [e["type"] for e in events] == ["tool_call", "tool_result", "token", "done"]
    assert events[0]["summary"] == "Look at your screen"
    assert events[2]["text"] == "The terminal shows TypeError at App.tsx:42."
    assert provider.requests == [], "the vision model's answer is the reply; no second model call"
    assert log.recent()[0]["outcome"] == "approved"

    # A follow-up question has the screen answer in context.
    await anext(agent.run_turn(conversation, "and how do I fix it?"))
    assert any("App.tsx:42" in m.content for m in provider.requests[0])


async def test_a_failed_look_falls_back_to_the_chat_model(store):
    def nothing():
        raise CaptureFailed("There is no open window to look at.")

    reader = ScreenReader(FakeVision(), nothing)
    provider = FakeProvider([[say("I couldn't see a window.")]])
    agent = Agent(provider, ToolRegistry(), PermissionGate(), store, screen=reader)

    events = [e async for e in agent.run_turn(store.create_conversation(), "what's this?", screen=True)]

    assert [e["type"] for e in events] == ["tool_call", "tool_result", "token", "done"]
    assert events[1]["ok"] is False
    assert "no open window" in provider.requests[0][-1].content


async def test_asking_in_words_goes_through_the_confirmation(store):
    reader = ScreenReader(FakeVision(), fake_capture)
    registry = ToolRegistry()
    for tool in screen_tools(reader):
        registry.register(tool)
    provider = FakeProvider([[call("look_at_screen", question="what's this?")], [say("OK.")]])
    gate = PermissionGate(confirm_timeout=5)
    agent = Agent(provider, registry, gate, store)

    kinds = []
    async for event in agent.run_turn(store.create_conversation(), "what's on my screen?"):
        kinds.append(event["type"])
        if event["type"] == "confirm_request":
            gate.resolve(event["id"], False)

    assert "confirm_request" in kinds
    assert "chose Cancel" in provider.requests[1][-1].content


@pytest.mark.skipif(
    sys.platform != "win32" or not os.environ.get("NOVA_GUI_TESTS"),
    reason="opens a real window; set NOVA_GUI_TESTS=1 to run",
)
def test_capture_a_real_window():
    import threading
    import tkinter

    from nova.tools._windows import list_windows
    from nova.vision.capture import capture_window

    ready = threading.Event()
    root = tkinter.Tk()
    root.title("NOVA capture test")
    root.geometry("640x360+40+40")
    tkinter.Label(root, text="Hello from the capture test", font=("Segoe UI", 28), fg="#c00000").pack(expand=True)
    root.after(600, ready.set)
    root.after(3000, root.destroy)

    shots = []

    def grab():
        ready.wait(5)
        window = next(w for w in list_windows() if w.title == "NOVA capture test")
        shots.append(capture_window(window))
        root.after(0, root.destroy)

    threading.Thread(target=grab, daemon=True).start()
    root.mainloop()

    [shot] = shots
    assert shot.method == "window"
    assert shot.image.width >= 600 and shot.image.height >= 300
    import numpy as np

    pixels = np.asarray(shot.image.convert("RGB")).astype(int)
    reds = int(((pixels[..., 0] > 150) & (pixels[..., 1] < 80) & (pixels[..., 2] < 80)).sum())
    assert reds > 200, "the red label text was captured"
