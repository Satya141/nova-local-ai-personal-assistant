"""The voice service: hear "Hey Nova", take a command, speak the reply.

States, announced to clients as {"type": "voice_state", "state": ..., "wake_word": bool}:

    off           the microphone is closed
    waiting       listening for "Hey Nova"
    listening     taking a command (after "Hey Nova" on its own, or the mic button)
    transcribing  turning the command into text
    speaking      reading a reply aloud

A recognised command goes out as {"type": "voice_command", "text": ..., "source": "wake" | "button"}.
An empty `text` means NOVA was listening but did not catch anything. The desktop
app runs the command as an ordinary chat turn, so confirmations and tool rows
work exactly as for typed requests, and hands the reply back to be spoken.

Threads: the microphone driver delivers chunks on its own thread; one worker
thread runs detection and transcription; replies are spoken on a short-lived
thread each. Events reach the asyncio side through `call_soon_threadsafe`.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from collections.abc import Callable
from enum import StrEnum
from pathlib import Path
from typing import Any

import numpy as np

from nova.events import EventBus
from nova.settings import SettingsStore
from nova.voice.audio import SAMPLE_RATE, AudioUnavailable, Microphone, chime, play
from nova.voice.stt import Transcriber, models_present
from nova.voice.tts import Synthesizer, to_speech
from nova.voice.vad import Segmenter, StreamingVad

log = logging.getLogger(__name__)

WAKE_SETTING = "voice.wake_word"
# After "Hey Nova" on its own, or the mic button, the next utterance within this many seconds is the command.
COMMAND_WINDOW = 8.0
# Speech after the wake phrase shorter than this is a trailing sound, not a command.
_MIN_COMMAND_MS = 300
_STOP_WORDS = {"stop", "cancel", "never mind", "nevermind", "be quiet", "quiet", "shut up", "enough", "that's enough"}
_RESET = object()  # queued when the microphone closes, so a half-heard utterance is dropped


class VoiceState(StrEnum):
    OFF = "off"
    WAITING = "waiting"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    SPEAKING = "speaking"


def _is_stop(text: str) -> bool:
    return text.lower().strip(" .!?,") in _STOP_WORDS


class VoiceService:
    def __init__(
        self,
        bus: EventBus,
        settings: SettingsStore,
        models_dir: Path,
        *,
        transcriber: Any = None,
        synthesizer: Any = None,
        vad_factory: Callable[[], Any] = StreamingVad,
        microphone_factory: Callable[[Callable[[np.ndarray], None]], Any] = Microphone,
        player: Callable[[np.ndarray, int, threading.Event], bool] = play,
        clock: Callable[[], float] = time.monotonic,
        command_window: float = COMMAND_WINDOW,
    ) -> None:
        self._bus = bus
        self._settings = settings
        self._models_dir = models_dir
        self._transcriber = transcriber or Transcriber(models_dir)
        self._synthesizer = synthesizer or Synthesizer(models_dir)
        self._vad_factory = vad_factory
        self._microphone_factory = microphone_factory
        self._player = player
        self._clock = clock
        self._command_window = command_window

        self._lock = threading.RLock()
        self._chunks: queue.Queue = queue.Queue(maxsize=1000)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker: threading.Thread | None = None
        self._running = False
        self._mic = None
        self._wake_word = settings.get_bool(WAKE_SETTING, False)
        self._awaiting_until: float | None = None
        self._source = "button"
        self._state = VoiceState.OFF
        self._speech_stop = threading.Event()
        self._speaking = False
        self._problem: str | None = None

    # --- lifecycle ---------------------------------------------------------------

    def availability(self) -> tuple[bool, str | None]:
        try:
            import faster_whisper  # noqa: F401
            import sounddevice  # noqa: F401
        except Exception as exc:  # ImportError, or PortAudio missing
            return False, f"Voice needs extra packages: {exc}"
        missing = models_present(self._models_dir) if isinstance(self._transcriber, Transcriber) else []
        if missing:
            return False, f"Voice models missing ({', '.join(missing)}). Run scripts\\setup.ps1."
        return True, self._problem

    @property
    def can_speak(self) -> bool:
        return bool(getattr(self._synthesizer, "present", True))

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        if self._wake_word and self.availability()[0]:
            self._open_mic()
        self._publish_state()

    async def close(self) -> None:
        self._speech_stop.set()
        with self._lock:
            self._close_mic()
            self._running = False
        if self._worker:
            await asyncio.to_thread(self._worker.join, 2)

    def status(self) -> dict[str, Any]:
        available, detail = self.availability()
        return {
            "available": available,
            "detail": detail,
            "wake_word": self._wake_word,
            "state": self._state.value,
            "can_speak": self.can_speak,
        }

    # --- control, called from API handlers ------------------------------------------------

    def set_wake_word(self, enabled: bool) -> None:
        self._settings.set_bool(WAKE_SETTING, enabled)
        with self._lock:
            self._wake_word = enabled
            if enabled:
                self._open_mic()
            elif self._awaiting_until is None:
                self._close_mic()
        self._publish_state()

    def listen_once(self) -> None:
        """The mic button: take one command without the wake phrase."""
        self.stop_speaking()
        with self._lock:
            self._open_mic()
            self._awaiting(source="button")

    def speak(self, text: str) -> None:
        spoken = to_speech(text)
        if not spoken or not self.can_speak:
            return
        self.stop_speaking()
        stop = threading.Event()
        self._speech_stop = stop
        threading.Thread(target=self._speak, args=(spoken, stop), name="nova-speak", daemon=True).start()

    def stop_speaking(self) -> None:
        self._speech_stop.set()

    def stop(self) -> None:
        """Stop talking and stop waiting for a command."""
        self.stop_speaking()
        with self._lock:
            self._awaiting_until = None
            if not self._wake_word:
                self._close_mic()
        self._publish_state()

    # --- microphone ----------------------------------------------------------------

    def _open_mic(self) -> None:
        if self._mic is not None:
            return
        self._ensure_worker()
        mic = self._microphone_factory(self._enqueue)
        try:
            mic.start()
        except AudioUnavailable as exc:
            self._problem = str(exc)
            self._publish({"type": "voice_error", "message": str(exc)})
            return
        self._problem = None
        self._mic = mic

    def _close_mic(self) -> None:
        if self._mic is None:
            return
        self._mic.stop()
        self._mic = None
        self._chunks.put(_RESET)

    def _enqueue(self, chunk: np.ndarray) -> None:
        try:
            self._chunks.put_nowait(chunk)
        except queue.Full:
            pass  # the worker is behind (a long transcription); dropping audio beats growing without bound

    def _ensure_worker(self) -> None:
        if self._worker and self._worker.is_alive():
            return
        self._running = True
        self._worker = threading.Thread(target=self._listen, name="nova-listen", daemon=True)
        self._worker.start()

    # --- the worker thread ------------------------------------------------------------

    def _listen(self) -> None:
        vad = self._vad_factory()
        checker = self._vad_factory()
        segmenter = Segmenter(vad)
        try:
            self._transcriber.preload()
        except Exception as exc:
            log.exception("Loading speech models failed")
            self._publish({"type": "voice_error", "message": f"Speech models could not load: {exc}"})
        while self._running:
            try:
                chunk = self._chunks.get(timeout=0.25)
            except queue.Empty:
                chunk = None
            if chunk is _RESET:
                segmenter.reset()
                vad.reset()
                continue
            if self._awaiting_until is not None and not segmenter.in_speech and self._clock() > self._awaiting_until:
                # Nothing was said in time.
                with self._lock:
                    self._awaiting_until = None
                    if not self._wake_word:
                        self._close_mic()
                self._publish({"type": "voice_command", "text": "", "source": self._source})
                self._publish_state()
            if chunk is None:
                continue
            utterance = segmenter.feed(chunk)
            if utterance is not None:
                try:
                    self._handle(utterance, checker)
                except Exception:
                    log.exception("Handling an utterance failed")
                    self._publish_state()

    def _handle(self, utterance: np.ndarray, checker) -> None:
        if self._awaiting_until is not None:
            self._set_state(VoiceState.TRANSCRIBING)
            # "Hey Nova, ..." after the mic button, or said again, is fine: drop the phrase.
            end = self._transcriber.find_wake(utterance)
            text = self._transcriber.transcribe(utterance[int(end * SAMPLE_RATE) :] if end else utterance)
            self._finish_command(text)
            return

        if not self._wake_word:
            return
        end = self._transcriber.find_wake(utterance)
        if end is None:
            return
        self.stop_speaking()  # interrupting NOVA mid-sentence is the point of saying its name
        rest = utterance[int(end * SAMPLE_RATE) :]
        speech_ms = checker.speech_ms(rest)
        log.debug(
            "Wake phrase ends at %.2fs of %.2fs; %d ms of speech after it",
            end, len(utterance) / SAMPLE_RATE, speech_ms,
        )
        if speech_ms >= _MIN_COMMAND_MS:
            self._source = "wake"
            self._set_state(VoiceState.TRANSCRIBING)
            self._finish_command(self._transcriber.transcribe(rest))
        else:
            with self._lock:
                self._awaiting(source="wake")

    def _awaiting(self, source: str) -> None:
        self._source = source
        self._awaiting_until = self._clock() + self._command_window
        self._set_state(VoiceState.LISTENING)
        self._chime()

    def _finish_command(self, text: str) -> None:
        with self._lock:
            self._awaiting_until = None
            if not self._wake_word:
                self._close_mic()
        if _is_stop(text):
            self.stop_speaking()
            self._publish_state()
            return
        self._publish({"type": "voice_command", "text": text, "source": self._source})
        self._publish_state()

    # --- speaking ----------------------------------------------------------------

    def _speak(self, text: str, stop: threading.Event) -> None:
        self._speaking = True
        self._set_state(VoiceState.SPEAKING)
        try:
            for audio, rate in self._synthesizer.sentences(text):
                if stop.is_set() or not self._player(audio, rate, stop):
                    break
        except AudioUnavailable as exc:
            self._publish({"type": "voice_error", "message": str(exc)})
        except Exception:
            log.exception("Speaking failed")
        finally:
            if self._speech_stop is stop:
                self._speaking = False
                self._publish_state()

    def _chime(self) -> None:
        def ring() -> None:
            try:
                self._player(chime(), 22050, threading.Event())
            except Exception:
                pass  # a missing speaker must not stop listening

        threading.Thread(target=ring, name="nova-chime", daemon=True).start()

    # --- state and events ---------------------------------------------------------

    def _resting_state(self) -> VoiceState:
        if self._speaking:
            return VoiceState.SPEAKING
        if self._awaiting_until is not None:
            return VoiceState.LISTENING
        if self._mic is not None and self._wake_word:
            return VoiceState.WAITING
        return VoiceState.OFF

    def _set_state(self, state: VoiceState) -> None:
        self._state = state
        self._publish({"type": "voice_state", "state": state.value, "wake_word": self._wake_word})

    def _publish_state(self) -> None:
        self._set_state(self._resting_state())

    def _publish(self, event: dict[str, Any]) -> None:
        if self._loop is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._bus.publish, event)
        except RuntimeError:
            pass  # the loop is shutting down
