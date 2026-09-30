"""Voice activity detection: which 32 ms chunks contain speech, and where utterances begin and end."""

from __future__ import annotations

import os
from collections import deque
from collections.abc import Callable

import numpy as np

from nova.voice.audio import CHUNK, SAMPLE_RATE

_CONTEXT = 64  # samples of the previous chunk the model sees with each new one
_CHUNK_MS = CHUNK * 1000 // SAMPLE_RATE


class StreamingVad:
    """Silero VAD, one chunk at a time, keeping its recurrent state between calls.

    Uses the ONNX model that ships inside faster-whisper, so it adds no download.
    Its own wrapper there only handles whole recordings.
    """

    def __init__(self) -> None:
        import faster_whisper
        import onnxruntime

        path = os.path.join(os.path.dirname(faster_whisper.__file__), "assets", "silero_vad_v6.onnx")
        options = onnxruntime.SessionOptions()
        options.inter_op_num_threads = 1
        options.intra_op_num_threads = 1
        options.log_severity_level = 4
        self._session = onnxruntime.InferenceSession(path, providers=["CPUExecutionProvider"], sess_options=options)
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(_CONTEXT, dtype=np.float32)

    def __call__(self, chunk: np.ndarray) -> float:
        """Probability that this 512-sample chunk is speech."""
        window = np.concatenate([self._context, chunk])[None, :]
        probs, self._h, self._c = self._session.run(None, {"input": window, "h": self._h, "c": self._c})
        self._context = chunk[-_CONTEXT:]
        return float(probs.reshape(-1)[0])

    def speech_ms(self, audio: np.ndarray, threshold: float = 0.5) -> int:
        """How much of a recording is speech. Resets the stream state."""
        self.reset()
        count = sum(
            self(audio[start : start + CHUNK]) >= threshold
            for start in range(0, len(audio) - CHUNK + 1, CHUNK)
        )
        self.reset()
        return count * _CHUNK_MS


class Segmenter:
    """Cuts a stream of chunks into utterances.

    An utterance starts at the first speech chunk (with a little audio before
    it, so the first syllable is not clipped) and ends after a pause.
    """

    def __init__(
        self,
        vad: Callable[[np.ndarray], float],
        *,
        start_threshold: float = 0.5,
        end_threshold: float = 0.35,
        min_speech_ms: int = 250,
        end_silence_ms: int = 700,
        max_ms: int = 15000,
        preroll_ms: int = 300,
    ) -> None:
        self._vad = vad
        self._start = start_threshold
        self._end = end_threshold
        self._min_speech = min_speech_ms
        self._end_silence = end_silence_ms
        self._max = max_ms
        self._preroll: deque[np.ndarray] = deque(maxlen=max(1, preroll_ms // _CHUNK_MS))
        self._recording: list[np.ndarray] | None = None
        self._speech_ms = 0
        self._silence_ms = 0

    @property
    def in_speech(self) -> bool:
        return self._recording is not None

    def reset(self) -> None:
        self._preroll.clear()
        self._recording = None
        self._speech_ms = self._silence_ms = 0

    def feed(self, chunk: np.ndarray) -> np.ndarray | None:
        """Add one chunk. Returns a finished utterance, or None."""
        probability = self._vad(chunk)
        if self._recording is None:
            if probability >= self._start:
                self._recording = [*self._preroll, chunk]
                self._speech_ms, self._silence_ms = _CHUNK_MS, 0
                self._preroll.clear()
            else:
                self._preroll.append(chunk)
            return None

        self._recording.append(chunk)
        if probability >= self._end:
            self._speech_ms += _CHUNK_MS
            self._silence_ms = 0
        else:
            self._silence_ms += _CHUNK_MS

        length_ms = len(self._recording) * _CHUNK_MS
        if self._silence_ms < self._end_silence and length_ms < self._max:
            return None

        recording, speech = self._recording, self._speech_ms
        self._recording = None
        self._speech_ms = self._silence_ms = 0
        if speech < self._min_speech:
            return None  # a cough or a click
        return np.concatenate(recording)
