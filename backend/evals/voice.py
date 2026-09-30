"""Voice pipeline evaluation with the real models and no microphone.

Speaks test phrases with the Piper voice, adds background noise, and feeds the
audio through the real voice service: the real voice activity detector, the
real wake-phrase check and the real transcription. Only the microphone is
replaced. Checks that commands come through, that ordinary speech does not wake
NOVA, and how long a command takes after the speaker stops.

    cd backend
    .venv\\Scripts\\python -m evals.voice

Synthetic speech is cleaner than a real voice in a real room, so treat a pass
here as necessary, not sufficient: try it by voice too.
"""

from __future__ import annotations

import asyncio
import re
import sys
import time

import numpy as np

from nova.config import Settings
from nova.database import Database
from nova.events import EventBus
from nova.settings import SettingsStore
from nova.voice.audio import CHUNK, SAMPLE_RATE
from nova.voice.service import WAKE_SETTING, VoiceService

# (what is said, pattern the command must match; None: NOVA must not respond)
CASES = [
    ("Hey Nova, open VS Code.", r"open (vs|visual studio) ?code"),
    ("Hey Nova, remind me in twenty minutes to check the oven.", r"remind me in (20|twenty) minutes to check the oven"),
    ("Hey Nova, what do you remember about me?", r"what do you remember about me"),
    ("Hey Nova, find my resume and open it.", r"find my resume and open it"),
    ("Hi Nova, close Notepad.", r"close note ?pad"),
    ("Hey Nova, open VS Code and show me today's reminders.", r"open (vs|visual studio) ?code and show me today'?s reminders"),
    ("I was watching a documentary about a supernova last night.", None),
    ("Open Calculator and Notepad.", None),
    ("The weather in Hyderabad is hot today.", None),
    ("My friend Nova is coming over later.", None),
]
# "Hey Nova" on its own, then the command after the chime.
TWO_PART = ("Hey Nova.", "Set a reminder for seven PM to call mom.", r"set a reminder for (7|seven) ?(pm|p\.m\.)? to call mom")


class ScriptedMic:
    """Stands in for the microphone: the eval pushes audio into it."""

    current: ScriptedMic | None = None

    def __init__(self, on_chunk):
        self.on_chunk = on_chunk
        ScriptedMic.current = self

    def start(self):
        pass

    def stop(self):
        pass

    def say(self, audio: np.ndarray) -> None:
        for start in range(0, len(audio) - CHUNK + 1, CHUNK):
            self.on_chunk(audio[start : start + CHUNK])


def spoken(voice, text: str, speed: float, noise: float, rng) -> np.ndarray:
    from piper import SynthesisConfig

    chunks = list(voice.synthesize(text, SynthesisConfig(length_scale=speed)))
    audio = np.concatenate([c.audio_float_array for c in chunks])
    rate = chunks[0].sample_rate
    positions = np.linspace(0, len(audio) - 1, int(len(audio) * SAMPLE_RATE / rate))
    audio = np.interp(positions, np.arange(len(audio)), audio).astype(np.float32)
    pad = np.zeros(int(0.6 * SAMPLE_RATE), np.float32)
    audio = np.concatenate([pad, audio, pad, pad])
    return (audio + rng.normal(0, noise, len(audio))).astype(np.float32)


async def next_command(queue, timeout: float):
    deadline = time.monotonic() + timeout
    while (left := deadline - time.monotonic()) > 0:
        try:
            event = await asyncio.wait_for(queue.get(), left)
        except TimeoutError:
            return None
        if event["type"] == "voice_command":
            return event
    return None


async def main() -> int:
    import logging

    from piper import PiperVoice

    # Show what the wake check heard whenever it says no.
    logging.basicConfig(level=logging.WARNING, format="     [%(name)s] %(message)s")
    logging.getLogger("nova.voice").setLevel(logging.DEBUG)

    settings = Settings.from_env()
    db = Database(":memory:")
    store = SettingsStore(db)
    store.set_bool(WAKE_SETTING, True)
    bus = EventBus()
    service = VoiceService(bus, store, settings.models_dir, microphone_factory=ScriptedMic, player=lambda *a: True)
    voice = PiperVoice.load(settings.models_dir / "voices" / "en_US-ljspeech-high.onnx")
    rng = np.random.default_rng(7)

    passed = total = 0
    latencies = []
    async with bus.subscribe() as queue:
        await service.start()
        # Loading the Whisper models happens on the listener thread; give it a moment.
        await asyncio.sleep(3)
        for speed, noise, label in ((1.0, 0.003, "normal"), (1.25, 0.01, "slow+noisy"), (0.85, 0.006, "fast")):
            print(f"\n-- {label} speech")
            for text, pattern in CASES:
                ScriptedMic.current.say(spoken(voice, text, speed, noise, rng))
                started = time.monotonic()
                event = await next_command(queue, timeout=6.0 if pattern else 3.0)
                total += 1
                if pattern is None:
                    ok = event is None or not event["text"]
                    outcome = "ignored" if ok else f"WOKE UP: {event['text']!r}"
                else:
                    heard = event["text"] if event else None
                    ok = bool(heard) and re.match(pattern, heard.lower().replace(",", "")) is not None
                    if event:
                        latencies.append(time.monotonic() - started)
                    outcome = f"{heard!r}" if ok else f"MISSED: {heard!r}"
                passed += ok
                print(f"  {'ok  ' if ok else 'FAIL'} {text:<58} -> {outcome}")

            wake, command, pattern = TWO_PART
            ScriptedMic.current.say(spoken(voice, wake, speed, noise, rng))
            await asyncio.sleep(1.5)
            ScriptedMic.current.say(spoken(voice, command, speed, noise, rng))
            event = await next_command(queue, timeout=6.0)
            heard = event["text"] if event else None
            ok = bool(heard) and re.match(pattern, heard.lower().replace(",", "")) is not None
            total += 1
            passed += ok
            print(f"  {'ok  ' if ok else 'FAIL'} {'Hey Nova. ... ' + command:<58} -> {heard!r}")
        await service.close()

    latencies.sort()
    print(f"\npassed {passed}/{total}")
    if latencies:
        print(f"command ready after end of audio: median {latencies[len(latencies) // 2]:.2f}s, slowest {latencies[-1]:.2f}s")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
