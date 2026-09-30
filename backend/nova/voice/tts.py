"""Text to speech with Piper, and turning a Markdown reply into something worth hearing."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import numpy as np

DEFAULT_VOICE = "en_US-ljspeech-high"
_SPOKEN_LIMIT = 400


def to_speech(markdown: str, limit: int = _SPOKEN_LIMIT) -> str:
    """Plain sentences to read aloud: no code, no Markdown symbols, no long paths or links."""
    text = re.sub(r"```.*?```", " I've put the code on screen. ", markdown, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"https?://\S+", "a link", text)
    # C:\Users\you\Documents\Resume.pdf -> Resume.pdf
    text = re.sub(r"[A-Za-z]:\\(?:[^\\\s]+\\)*([^\\\s]+)", r"\1", text)
    text = re.sub(r"^\s*#+\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", text, flags=re.M)
    text = re.sub(r"[*_~#>|]+", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    # Read the opening sentences; the rest stays on screen.
    sentences = re.split(r"(?<=[.!?])\s+", text)
    spoken = ""
    for sentence in sentences:
        if len(spoken) + len(sentence) > limit:
            break
        spoken = f"{spoken} {sentence}".strip()
    return f"{spoken or text[:limit]} The rest is on screen."


class Synthesizer:
    def __init__(self, models_dir: Path, voice: str = DEFAULT_VOICE) -> None:
        self._path = models_dir / "voices" / f"{voice}.onnx"
        self._voice = None

    @property
    def present(self) -> bool:
        return self._path.exists() and Path(f"{self._path}.json").exists()

    def preload(self) -> None:
        if self._voice is None:
            from piper import PiperVoice

            self._voice = PiperVoice.load(self._path)

    def sentences(self, text: str) -> Iterator[tuple[np.ndarray, int]]:
        """Audio one sentence at a time, so playback starts before the whole reply is ready."""
        self.preload()
        for chunk in self._voice.synthesize(text):
            yield chunk.audio_float_array.astype(np.float32), chunk.sample_rate
