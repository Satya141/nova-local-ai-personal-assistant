"""Microphone input and speaker output.

Audio never leaves this process and is never written to disk: the microphone
feeds 32 ms chunks to the listener, which keeps at most one utterance in memory.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
# 512 samples at 16 kHz: the chunk size the voice activity detector expects.
CHUNK = 512


class AudioUnavailable(RuntimeError):
    """No usable microphone or speaker (none connected, or blocked in Windows privacy settings)."""


def _resample(audio: np.ndarray, source_rate: float, target_rate: float, length: int | None = None) -> np.ndarray:
    if source_rate == target_rate:
        return audio.astype(np.float32, copy=False)
    count = length or int(round(len(audio) * target_rate / source_rate))
    positions = np.linspace(0, len(audio) - 1, count)
    return np.interp(positions, np.arange(len(audio)), audio).astype(np.float32)


class Microphone:
    """Calls `on_chunk` with 512-sample, 16 kHz mono float32 chunks from the default input."""

    def __init__(self, on_chunk: Callable[[np.ndarray], None]) -> None:
        self._on_chunk = on_chunk
        self._stream = None

    def start(self) -> None:
        import sounddevice as sd

        try:
            # Most Windows drivers resample to 16 kHz for us.
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE, channels=1, dtype="float32", blocksize=CHUNK, callback=self._direct
            )
        except Exception:
            try:
                native = float(sd.query_devices(kind="input")["default_samplerate"])
                self._native = native
                self._stream = sd.InputStream(
                    samplerate=native,
                    channels=1,
                    dtype="float32",
                    blocksize=int(round(CHUNK * native / SAMPLE_RATE)),
                    callback=self._resampled,
                )
            except Exception as exc:
                raise AudioUnavailable(f"No microphone NOVA can use: {exc}") from exc
        try:
            self._stream.start()
        except Exception as exc:
            raise AudioUnavailable(f"The microphone could not be opened: {exc}") from exc

    def _direct(self, indata, frames, time_info, status) -> None:
        self._on_chunk(indata[:, 0].copy())

    def _resampled(self, indata, frames, time_info, status) -> None:
        self._on_chunk(_resample(indata[:, 0], self._native, SAMPLE_RATE, CHUNK))

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            finally:
                self._stream = None

    @property
    def running(self) -> bool:
        return self._stream is not None


def play(audio: np.ndarray, rate: int, stop: threading.Event) -> bool:
    """Play mono float32 audio on the default output. Returns False if `stop` cut it short."""
    import sounddevice as sd

    slice_length = max(1, rate // 10)  # check for interruption every 100 ms
    try:
        with sd.OutputStream(samplerate=rate, channels=1, dtype="float32") as stream:
            for start in range(0, len(audio), slice_length):
                if stop.is_set():
                    return False
                stream.write(audio[start : start + slice_length].reshape(-1, 1))
    except Exception as exc:
        raise AudioUnavailable(f"No speaker NOVA can use: {exc}") from exc
    return not stop.is_set()


def chime(rate: int = 22050) -> np.ndarray:
    """A soft two-note chime: NOVA heard its name and is listening."""
    notes = []
    for frequency, seconds in ((880.0, 0.09), (1318.5, 0.14)):
        t = np.arange(int(rate * seconds)) / rate
        envelope = np.minimum(1.0, t / 0.01) * np.exp(-t * 18)
        notes.append(0.18 * envelope * np.sin(2 * np.pi * frequency * t))
    return np.concatenate(notes).astype(np.float32)
