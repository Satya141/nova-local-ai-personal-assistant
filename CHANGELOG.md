# Changelog

## 0.3.0 (Phase 3: voice) - 2026-10-01

Talk to NOVA. Say "Hey Nova, open VS Code", or press the mic button, and it answers out loud.

### Voice
- "Hey Nova" wake phrase, spotted by a streaming voice activity detector plus Whisper tiny.en, so no wake-word model had to be trained. One breath ("Hey Nova, open VS Code") or two ("Hey Nova" … chime … "open VS Code").
- Commands transcribed by Whisper base.en on the CPU; about 0.7 s from the end of speech to a ready command.
- Spoken replies with Piper (`en_US-ljspeech-high`, public-domain training data), cleaned of Markdown, code and paths, and kept short.
- Interrupt by saying "Hey Nova" again, "Hey Nova, stop", Esc, typing, or the stop button.
- Mic button in the launcher for one command without the wake phrase.
- "Listen for 'Hey Nova'" in the tray menu, off by default and remembered across restarts. While the microphone is open the tray icon shows a red dot and the launcher says so.
- Nova has a new "hearing" face with sound ripples; spoken requests are marked with a mic in the transcript.
- Everything runs locally and offline; audio is never saved.

### Quality
- `evals/voice.py`: feeds synthetic speech at three speeds with background noise through the real pipeline. 97/99 commands exact, 0 false wakes on 36 other sentences (including "supernova" and "My friend Nova").
- 137 unit tests, including the voice service with fake audio and models.

### Fixed during development
- Priming Whisper with "Hey Nova," made it skip those words; `hotwords` fixed it (26 → 42 of 42 caught).
- The end of "Nova" leaked into commands ("Never find my resume"); cutting 0.10 s later fixed it.
- "call mom" was heard as "Como", and "Notepad" as "note that"; a vocabulary glossary fixed both.
- Whisper invented "Thank you very much" from silence after "Hey Nova"; the voice activity detector now confirms there is speech first.
- "Nova is my project" counted as a wake phrase.
- Voice models downloaded from inside a sandboxed host were invisible to a normally started NOVA; they now live in `backend/models`.

## 0.2.0 (Phase 2: real agent) - 2026-09-30

NOVA now remembers you across conversations, sets reminders that pop up on screen, and carries out several steps from one request.

### Memory
- Long-term memory in SQLite with semantic search through `embeddinggemma` on the CPU, falling back to keyword matching without it. Thresholds calibrated on real scores.
- Automatic memory: after each reply NOVA saves lasting facts from what you said, shown under the conversation as "Noted" with a Forget button. It knows which parts of the message were requests it carried out, and skips them.
- Tools: `remember`, `recall`, `forget` (asks first). Relevant memories, plus your profile, preferences and projects, are in every prompt with their ids.
- Guards in code: secrets and ID numbers are never stored; short-lived facts are dropped; "next Friday" is stored as a real date.

### Reminders
- Tools: `create_reminder` (one-off or daily, weekdays, weekly), `list_reminders`, `cancel_reminder`.
- A persistent scheduler: reminders survive restarts, and ones missed while NOVA was closed fire at the next start as "Missed".
- A reminder window in the bottom-right corner with Chime, the new bell character, ringing. It never takes focus from what you are doing. Snooze 10 min or Done.

### Agent
- Multi-step requests measured at 100% on the evaluation suite; the step limit is now 8. No separate planner, because measurement showed it would add latency without fixing anything.
- A 14-day calendar in the prompt, because the model cannot be trusted to work out dates.
- Identical repeated tool calls in one turn run once.
- Every attempted action is recorded in an action log (`GET /api/actions`).
- A fresh conversation starts when the launcher opens after 20 idle minutes; memory carries over.

### Platform
- One database with versioned migrations; Phase 1 databases upgrade in place.
- `GET /api/events` streams reminders and memory notices, replaying unacknowledged reminders on connect.
- New endpoints for memories (list, delete) and reminders (list, dismiss, snooze).
- `backend/evals`: agent (18 scenarios) and memory-extraction (19 cases) evaluations against the real model. 106 unit tests.

### Fixed during development
- A memory relevance threshold guessed at 0.5 would have matched nothing; measured and set to 0.3.
- "What am I working on?" ignored remembered projects, and "forget that…" could not find the memory to delete.
- The model wrote dates with the wrong weekday. Calendar lookups replaced its arithmetic.
- Reminder requests were saved as memories ("The user needs to review notes 1 minute from now").
- The extractor's three-per-message cap counted rejected lines, crowding out good ones.

## 0.1.0 (Phase 1: desktop MVP) - 2026-09-30

First working version. Press Alt+Space, ask, and NOVA acts on the computer using a model that runs locally.

### Backend
- FastAPI service on loopback, protected by a per-launch bearer token and a CORS allowlist.
- Agent loop with streaming replies, multi-step tool use (up to 6 steps) and honest failure reporting.
- `ModelProvider` interface with an Ollama provider; default model `qwen3:8b`, configurable with `NOVA_MODEL`.
- Tool registry with typed, validated arguments. Tools: `open_application`, `close_application`, `search_files`, `open_path`.
- Permission gate: risky tools wait for the user's Confirm or Cancel; no answer in 120 s is a denial.
- Conversation memory in SQLite.
- Model warm-up when the launcher opens; explicit 8192-token context.
- 55 tests.

### Desktop
- Tauri 2 shell: system tray, single instance, Alt+Space global shortcut (falls back to Ctrl+Alt+Space if taken), optional start with Windows in release builds.
- Starts and supervises the backend; the backend exits whenever the shell does.
- Launcher that sizes itself to its content, hides on Esc or when it loses focus, and opens on the monitor in use.
- Character cast (Nova, Ember, Fern, Plum) with nine animated states, used as the avatar and as tiny per-action icons.
- Live transcript: streamed Markdown replies, tool rows with status, inline confirmation card.
- Light and dark themes, reduced-motion support.

### Fixed during development
- "Open Photoshop" opened Photos and was reported as a success. Typo matching is now stricter and a fuzzy match is reported as a guess.
- Closing a Store app would have closed every Store app. Closing now targets the single window.
- A disconnected client left its confirmation pending until garbage collection. It is now cancelled immediately.
- The model asked "are you sure?" after the user had already confirmed.
