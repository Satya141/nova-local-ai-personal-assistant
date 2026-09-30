from __future__ import annotations

import asyncio
import threading

import numpy as np
import pytest

from nova.events import EventBus
from nova.settings import SettingsStore
from nova.voice.audio import CHUNK
from nova.voice.service import WAKE_SETTING, VoiceService
from nova.voice.stt import Word, clean_command, wake_end
from nova.voice.tts import to_speech
from nova.voice.vad import Segmenter

# --- pure pieces ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("heard", "expected"),
    [
        (["Hey", "Nova,", "open", "VS", "Code."], 0.8),
        (["Hi", "Nova,", "close", "Notepad."], 0.8),
        (["Hey", "Nova"], 0.8),
        (["A", "Nova", "find", "my", "resume"], 0.8),
        (["Nova,", "what", "time", "is", "it?"], 0.4),
        (["HeyNova", "open", "paint"], 0.4),
        (["I", "watched", "a", "documentary", "about", "Supernova"], None),
        (["Nova", "is", "my", "project"], None),  # the name mid-sentence, no greeting, no pause
        (["Open", "Calculator", "and", "NoPad."], None),
        ([], None),
    ],
)
def test_wake_end(heard, expected):
    words = [Word(text, 0.4 * (index + 1)) for index, text in enumerate(heard)]
    assert wake_end(words) == expected


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("or close notepad.", "Close notepad."),
        ("uh, open calculator", "Open calculator"),
        (" Open VS Code. ", "Open VS Code."),
        ("Thank you very much.", ""),
        ("Thanks for watching!", ""),
        ("", ""),
    ],
)
def test_clean_command(raw, clean):
    assert clean_command(raw) == clean


def test_to_speech_reads_like_a_person():
    reply = (
        "I found **2 files**:\n\n1. `Resume 2026.pdf` in C:\\Users\\you\\Documents\\Resume 2026.pdf\n"
        "2. See [the guide](https://example.com/guide)\n\n```python\nprint('hi')\n```"
    )
    spoken = to_speech(reply)
    assert "**" not in spoken and "`" not in spoken and "C:\\" not in spoken
    assert "the guide" in spoken and "example.com" not in spoken
    assert "I've put the code on screen." in spoken


def test_to_speech_shortens_long_replies():
    reply = " ".join(f"Sentence number {n} is here." for n in range(60))
    spoken = to_speech(reply)
    assert len(spoken) < 460
    assert spoken.endswith("The rest is on screen.")


def speech(n: int) -> list[np.ndarray]:
    return [np.ones(CHUNK, np.float32) for _ in range(n)]


def silence(n: int) -> list[np.ndarray]:
    return [np.zeros(CHUNK, np.float32) for _ in range(n)]


class FakeVad:
    """Speech is a chunk full of ones."""

    def __call__(self, chunk: np.ndarray) -> float:
        return 1.0 if chunk.mean() > 0.5 else 0.0

    def reset(self) -> None:
        pass

    def speech_ms(self, audio: np.ndarray, threshold: float = 0.5) -> int:
        chunks = [audio[i : i + CHUNK] for i in range(0, len(audio) - CHUNK + 1, CHUNK)]
        return sum(c.mean() > 0.5 for c in chunks) * 32


def test_segmenter_returns_an_utterance_after_a_pause():
    segmenter = Segmenter(FakeVad())
    outputs = [segmenter.feed(c) for c in silence(5) + speech(20) + silence(30)]
    finished = [o for o in outputs if o is not None]
    assert len(finished) == 1
    # Pre-roll: the chunks just before speech are included, so the first syllable survives.
    assert finished[0][:CHUNK].mean() == 0.0 and finished[0].mean() > 0.3


def test_segmenter_ignores_clicks_and_caps_long_speech():
    segmenter = Segmenter(FakeVad(), max_ms=1000)
    assert all(segmenter.feed(c) is None for c in speech(3) + silence(30))  # 96 ms: a click
    finished = [o for o in (segmenter.feed(c) for c in speech(40)) if o is not None]
    assert len(finished) == 1, "a monologue is cut at max_ms"


# --- the service, with fake audio and models ---------------------------------------------


class FakeMic:
    instances: list[FakeMic] = []

    def __init__(self, on_chunk):
        self.on_chunk = on_chunk
        self.running = False
        FakeMic.instances.append(self)

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def say(self, chunks):
        for chunk in chunks:
            self.on_chunk(chunk)


class FakeTranscriber:
    """Answers from a script: what find_wake and transcribe return, in order."""

    def __init__(self, wakes: list[float | None], texts: list[str]):
        self.wakes = list(wakes)
        self.texts = list(texts)

    def preload(self):
        pass

    def find_wake(self, audio):
        return self.wakes.pop(0) if self.wakes else None

    def transcribe(self, audio):
        return self.texts.pop(0) if self.texts else ""


class FakeSynth:
    present = True

    def sentences(self, text):
        yield np.zeros(100, np.float32), 22050
        yield np.zeros(100, np.float32), 22050


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def played():
    return []


def make_voice(db, bus, transcriber, played, *, wake_word=True, player=None, window=5.0):
    FakeMic.instances.clear()
    settings = SettingsStore(db)
    settings.set_bool(WAKE_SETTING, wake_word)

    def record(audio, rate, stop):
        played.append(len(audio))
        return True

    return VoiceService(
        bus,
        settings,
        models_dir=None,
        transcriber=transcriber,
        synthesizer=FakeSynth(),
        vad_factory=FakeVad,
        microphone_factory=FakeMic,
        player=player or record,
        command_window=window,
    )


async def next_event(queue, kind, timeout=3.0):
    while True:
        event = await asyncio.wait_for(queue.get(), timeout)
        if event["type"] == kind:
            return event


async def test_wake_phrase_with_command_in_one_breath(db, bus, played):
    voice = make_voice(db, bus, FakeTranscriber([0.5], ["Open VS Code."]), played)
    async with bus.subscribe() as queue:
        await voice.start()
        assert FakeMic.instances[0].running, "the wake word setting opens the microphone"
        FakeMic.instances[0].say(silence(10) + speech(25) + silence(30))
        command = await next_event(queue, "voice_command")
    await voice.close()
    assert command == {"type": "voice_command", "text": "Open VS Code.", "source": "wake"}


async def test_wake_phrase_alone_then_the_command(db, bus, played):
    voice = make_voice(db, bus, FakeTranscriber([1.2, None], ["Set a reminder"]), played)
    async with bus.subscribe() as queue:
        await voice.start()
        mic = FakeMic.instances[0]
        mic.say(speech(12) + silence(30))  # "Hey Nova" and a pause
        state = await next_event(queue, "voice_state")
        while state["state"] != "listening":
            state = await next_event(queue, "voice_state")
        mic.say(speech(20) + silence(30))
        command = await next_event(queue, "voice_command")
    await voice.close()
    assert command["text"] == "Set a reminder"
    # The chime plays on its own thread; under load it can lag the command.
    for _ in range(50):
        if played:
            break
        await asyncio.sleep(0.02)
    assert played, "a chime says NOVA is listening"


async def test_other_speech_is_ignored(db, bus, played):
    voice = make_voice(db, bus, FakeTranscriber([None], []), played)
    async with bus.subscribe() as queue:
        await voice.start()
        FakeMic.instances[0].say(speech(25) + silence(30))
        with pytest.raises(TimeoutError):
            await next_event(queue, "voice_command", timeout=1.0)
    await voice.close()


async def test_mic_button_takes_one_command_then_closes_the_mic(db, bus, played):
    voice = make_voice(db, bus, FakeTranscriber([None], ["What time is it?"]), played, wake_word=False)
    async with bus.subscribe() as queue:
        await voice.start()
        assert FakeMic.instances == [], "no wake word: the microphone stays closed"
        voice.listen_once()
        mic = FakeMic.instances[0]
        assert mic.running
        mic.say(speech(20) + silence(30))
        command = await next_event(queue, "voice_command")
        await asyncio.sleep(0.1)
    await voice.close()
    assert command == {"type": "voice_command", "text": "What time is it?", "source": "button"}
    assert not mic.running, "the microphone closes after the command"


async def test_nothing_said_after_the_button_times_out(db, bus, played):
    voice = make_voice(db, bus, FakeTranscriber([], []), played, wake_word=False, window=0.3)
    async with bus.subscribe() as queue:
        await voice.start()
        voice.listen_once()
        command = await next_event(queue, "voice_command")
    await voice.close()
    assert command["text"] == ""
    assert not FakeMic.instances[0].running


async def test_stop_words_are_not_sent_as_commands(db, bus, played):
    voice = make_voice(db, bus, FakeTranscriber([0.5], ["Stop."]), played)
    async with bus.subscribe() as queue:
        await voice.start()
        FakeMic.instances[0].say(silence(10) + speech(25) + silence(30))
        with pytest.raises(TimeoutError):
            await next_event(queue, "voice_command", timeout=1.0)
    await voice.close()


async def test_wake_word_setting_persists_and_controls_the_mic(db, bus, played):
    voice = make_voice(db, bus, FakeTranscriber([], []), played, wake_word=False)
    await voice.start()
    voice.set_wake_word(True)
    assert SettingsStore(db).get_bool(WAKE_SETTING) is True
    assert FakeMic.instances[-1].running
    voice.set_wake_word(False)
    assert not FakeMic.instances[-1].running
    assert voice.status()["state"] == "off"
    await voice.close()


async def test_speaking_can_be_interrupted(db, bus, played):
    started = threading.Event()

    def slow_player(audio, rate, stop):
        started.set()
        return not stop.wait(2.0)  # returns False once interrupted

    voice = make_voice(db, bus, FakeTranscriber([], []), played, wake_word=False, player=slow_player)
    async with bus.subscribe() as queue:
        await voice.start()
        voice.speak("Here is a long answer. It has two sentences.")
        assert await asyncio.to_thread(started.wait, 2.0)
        assert (await next_event(queue, "voice_state"))["state"] in {"off", "speaking"}
        voice.stop()
        state = await next_event(queue, "voice_state")
        while state["state"] == "speaking":
            state = await next_event(queue, "voice_state")
    await voice.close()
    assert state["state"] == "off"
