"""Speech to text, and spotting the wake phrase.

There is no ready-made "Hey Nova" wake-word model, so the phrase is spotted
with speech recognition: a small Whisper model (tiny.en) reads the start of
each utterance the voice activity detector hands over. It only runs while
someone is speaking, and it lets the user say the whole thing in one breath:
"Hey Nova, open VS Code". The command itself is then transcribed by a larger
model (base.en) from the audio after the wake phrase.

Measured with synthetic speech: tiny.en given "Hey Nova" as hotwords heard it in
every test phrase and invented it in none of the others; base.en primed with the
phrase heard "hang over", so it only transcribes the command, after the cut.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from nova.voice.audio import SAMPLE_RATE

log = logging.getLogger(__name__)

_GREETINGS = {"hey", "hi", "hay", "hello", "ok", "okay", "yo", "a", "hei", "high", "pay"}
_NAME = re.compile(r"^(nova|novah|nover|novar|noah|nava)$")
# Heard as one word ("HeyNova"). The greeting is required: a bare "Nova" is handled below.
_JOINED = re.compile(r"^(hey|hi|hay|a|ok)nova$")
# The wake phrase must come first; "supernova" mid-sentence is not a call.
_WAKE_WINDOW = 3
# Whisper's habits on near-silence, which must never become commands.
_HALLUCINATIONS = {"thank you", "thank you very much", "thanks for watching", "you", "bye", "thanks"}
_FILLERS = {"or", "uh", "um", "ah", "er", "hmm"}

_WAKE_SECONDS = 3.0
# tiny.en's timestamp for the end of "Nova" is slightly early, and the command
# model turned the leftover "-va" into a word ("Never find my resume", "Remember,
# remind me..."). Cutting later fixed it: exact commands went from 18/27 to 24/27
# at +0.10 s, with no gain from waiting longer.
_CUT_AFTER_WAKE = 0.10
# How the wake check is primed, measured on 42 wake clips and 36 other sentences
# (3 speeds, with noise): no hint caught 26 wake phrases; initial_prompt "Hey Nova,"
# caught 38 (Whisper reads a prompt as words already spoken, so it sometimes
# skipped them); hotwords "Hey Nova" caught all 42. None of them woke on the others.
_WAKE_HOTWORDS = "Hey Nova"
# Without a hint base.en heard "call mom" as "Como" and "close Notepad" as "close note that".
_COMMAND_HINT = "Glossary: Notepad, Calculator, VS Code, Chrome, File Explorer, mom, dad, reminder, 7 PM."


@dataclass(frozen=True)
class Word:
    text: str
    end: float  # seconds from the start of the audio


def _norm(word: str) -> str:
    return re.sub(r"[^a-z]", "", word.lower())


def wake_end(words: list[Word]) -> float | None:
    """Where the wake phrase ends, if the utterance starts with one."""
    for index, word in enumerate(words[:_WAKE_WINDOW]):
        token = _norm(word.text)
        if _JOINED.match(token):
            return word.end
        if _NAME.match(token):
            previous = _norm(words[index - 1].text) if index else ""
            # "Hey Nova" / "Hi Nova", or "Nova," said on its own as the first word.
            if previous in _GREETINGS or (index == 0 and word.text.rstrip().endswith((",", "!", "?", "."))):
                return word.end
    return None


def clean_command(text: str) -> str:
    """Tidy a transcribed command: drop leading fillers and known hallucinations."""
    words = text.strip().split()
    while words and _norm(words[0]) in _FILLERS:
        words.pop(0)
    command = " ".join(words).strip(" ,")
    if _norm(command.replace(" ", "")) in {w.replace(" ", "") for w in _HALLUCINATIONS}:
        return ""
    return command[:1].upper() + command[1:] if command else ""


class Transcriber:
    """Loads the Whisper models on first use; they run on the CPU so the GPU stays with the chat model."""

    def __init__(self, models_dir: Path, wake_model: str = "tiny.en", command_model: str = "base.en") -> None:
        self._dir = models_dir / "whisper"
        self._names = (wake_model, command_model)
        self._wake = None
        self._command = None

    def _load(self, name: str):
        from faster_whisper import WhisperModel

        # Offline only: never reach out to Hugging Face at runtime.
        return WhisperModel(name, device="cpu", compute_type="int8", download_root=str(self._dir), local_files_only=True)

    def preload(self) -> None:
        if self._wake is None:
            self._wake = self._load(self._names[0])
        if self._command is None:
            self._command = self._load(self._names[1])

    def find_wake(self, audio: np.ndarray) -> float | None:
        """Seconds into `audio` where the command after "Hey Nova" starts, or None if there is no wake phrase."""
        self.preload()
        head = audio[: int(_WAKE_SECONDS * SAMPLE_RATE)]
        segments, _ = self._wake.transcribe(
            head, language="en", beam_size=1, hotwords=_WAKE_HOTWORDS, word_timestamps=True, vad_filter=False
        )
        words = [Word(w.word, w.end) for segment in segments for w in (segment.words or [])]
        end = wake_end(words)
        if end is None:
            log.debug("No wake phrase in %r", " ".join(w.text for w in words))
            return None
        return end + _CUT_AFTER_WAKE

    def transcribe(self, audio: np.ndarray) -> str:
        self.preload()
        segments, _ = self._command.transcribe(
            audio,
            language="en",
            beam_size=5,
            without_timestamps=True,
            vad_filter=False,
            initial_prompt=_COMMAND_HINT,
        )
        return clean_command(" ".join(segment.text.strip() for segment in segments))


def models_present(models_dir: Path, names: tuple[str, ...] = ("tiny.en", "base.en")) -> list[str]:
    """Which of the Whisper models are missing on disk."""
    missing = []
    for name in names:
        folder = models_dir / "whisper" / f"models--Systran--faster-whisper-{name}"
        if not any(folder.glob("snapshots/*/model.bin")):
            missing.append(name)
    return missing
