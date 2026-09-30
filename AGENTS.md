# NOVA: guide for coding agents

NOVA is an open-source, local-first AI personal assistant. One AI, one memory, any device.
It is an agent that performs actions, not a chatbot, and it is not a clone of or affiliated with any other product.

Read `docs/architecture.md` before changing how the pieces fit together, and add to `CHANGELOG.md` when you finish a change.

## Layout

```
backend/                 Python 3.12, FastAPI. All agent logic lives here.
  nova/agent/            the agent loop (model -> tool -> permission -> result), system prompt
  nova/inference/        ModelProvider interface + the Ollama provider
  nova/tools/            Tool interface, registry, and each tool
  nova/permissions/      the permission gate, confirmation flow and action log
  nova/memory/           conversations, long-term memory, embeddings, automatic extraction
  nova/scheduler/        reminders and the loop that fires them
  nova/voice/            microphone, voice activity, wake phrase + Whisper, Piper, the voice service
  nova/settings.py       persistent user settings (e.g. wake word on/off)
  nova/api/              HTTP API (token-protected, loopback only) and the event stream
  nova/database.py       the SQLite file and its migrations
  nova/dates.py          calendar lookups for prompts and memories
  nova/events.py         in-process pub/sub behind GET /api/events
  tests/                 pytest suite (no model needed)
  evals/                 agent, memory and voice evaluations against the real models
  models/                downloaded Whisper and Piper models (git-ignored; scripts/setup.ps1)
apps/desktop/            Tauri 2 shell + Next.js 16 UI. A thin client.
  src-tauri/src/         tray, global shortcut, launcher and reminder windows, backend supervisor
  src/app/               launcher page, /toast reminder window, /gallery design reference
  src/components/        characters, icons, transcript, reminder card
  src/lib/               backend client and the conversation state hook
scripts/setup.ps1        one-time dev setup
```

## Commands

| Task | Command |
|---|---|
| First-time setup | `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1` |
| Run the app (dev) | `pnpm --dir apps/desktop tauri dev` |
| Backend tests | `cd backend; .venv\Scripts\python -m pytest` |
| Agent evals (real model, ~2 min) | `cd backend; .venv\Scripts\python -m evals.run` |
| Memory extraction evals | `cd backend; .venv\Scripts\python -m evals.run --memory` |
| Voice pipeline evals (real models, synthetic speech) | `cd backend; .venv\Scripts\python -m evals.voice` |
| Frontend type-check | `pnpm --dir apps/desktop exec tsc --noEmit` |
| Character gallery | `pnpm --dir apps/desktop dev`, then open http://localhost:3000/gallery |
| Release build (no installer) | `pnpm --dir apps/desktop tauri build --no-bundle` |

Run the evals after any change to a prompt, a tool description, the memory pipeline, or anything in `nova/voice` (hints, thresholds, cut points). Unit tests cannot tell you whether the models still behave. Run each more than once: model output varies between runs. Keep checks strict (the voice eval anchors commands to the start; a loose check hid leaked words for a whole iteration). Add a case for every behaviour bug found in real use.

## Rules that must hold

1. **The model never executes anything.** It names a tool; the agent validates the arguments against the tool's schema, asks the permission gate, and only then runs the handler. Do not add a path around `PermissionGate`.
2. **Permissions come from tool metadata, not from the model.** A tool with `requires_confirmation=True` or a risk above `LOW` blocks until the user answers in the UI.
3. **No tool may run arbitrary commands.** Never interpolate model or user text into a shell or PowerShell string. `open_application` only launches entries from the Start menu index; `open_path` refuses programs and scripts.
4. **Tools report honestly.** A result says what actually happened. If a name was fuzzy-matched, say which app was really opened. The system prompt forbids claiming an action that no tool result confirms.
5. **The model does not do calendar arithmetic.** It is wrong too often (it called 8 October 2026 a Friday). Prompts carry an explicit day list (`nova.dates.upcoming_days`), and weekday phrases in memories are resolved in code (`resolve_weekdays`).
6. **Memory safety lives in code.** `looks_sensitive` refuses secrets and ID numbers for every memory; the extractor also drops short-lived facts. Do not move these checks into prompts.
7. **The API stays private.** It binds to loopback and every route requires the per-launch bearer token the shell generates. Keep both.
8. **Clients stay thin.** No agent logic in the desktop app. The UI renders the event streams documented at the top of `backend/nova/agent/loop.py` and in `backend/nova/api/app.py`; if you change those events, update `apps/desktop/src/lib/backend.ts` to match.
9. **No hard-coded model names** outside `backend/nova/config.py`. Everything model-specific goes behind `ModelProvider` or `Embedder`.
10. **The microphone is opt-in and visible.** It opens only while "Hey Nova" is switched on or for one mic-button command, and whenever it is open the tray icon and the launcher say so. Audio never touches disk or the network, and speech without the wake phrase is dropped. Voice models load with `local_files_only=True`.
11. **Schema changes are new migrations.** Append to `MIGRATIONS` in `nova/database.py`; never edit one that has shipped.
12. **No secrets in source.** Credentials come from environment variables.
13. **Tests accompany behaviour changes.** New tools need tests for their matching and refusal logic; anything touching the gate needs an agent-loop test.

## Adding a tool

1. Write a Pydantic args model, an async handler returning `ToolResult`, and a `Tool(...)` in `backend/nova/tools/`. Tools that need a store are built by a factory function (see `tools/memory.py`).
2. Set `risk` and `requires_confirmation` truthfully. Deleting, sending, closing, buying: confirm.
3. Register it in `build_registry()` in `backend/nova/tools/__init__.py`.
4. Give it a character in `TOOL_OWNERS` in `apps/desktop/src/components/transcript.tsx` (or leave it to Nova).
5. Add tests, add an eval scenario with a sandboxed stand-in if it touches the computer, then try it for real through the launcher.

## The characters

Nova (the assistant and its memory), Ember (apps), Fern (files), Plum (closing things) and Chime (reminders) are NOVA's own designs.
A character keeps its colour in every state; status is shown by its face, motion and a small mark.
All visuals are in `apps/desktop/src/app/character.css`. Check changes on `/gallery` in both light and dark themes, and keep the `prefers-reduced-motion` block working.

## Testing the real app

The launcher and reminder windows can be driven through WebView2's debugging port: start `nova.exe` with `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=<port>` and talk Chrome DevTools Protocol to it. Set `NOVA_DATA_DIR` to a scratch folder so tests never write into the user's real memory, and never leave an instance running with the debug port open.

Showing the launcher takes keyboard focus. If the user is typing elsewhere, their keystrokes land in it, so avoid bringing it up while someone is at the machine, and never let a test submit what is in the input. Do not test voice live on someone's machine without asking: the mic button records whatever is being said in the room, and speaking plays audio in their ears. `evals/voice.py` covers the pipeline with the real models instead.

Processes started from inside a sandboxed (MSIX-packaged) host see a private copy of `%LOCALAPPDATA%`; that is why models live in `backend/models`, not under AppData.

## Current scope

Phases 1, 2 and 3 are done. Not built yet: screen understanding (Phase 4), integrations such as Gmail, Calendar, GitHub and browser automation (5), mobile (6), sync (7), distributed inference (8), the life timeline (9). Scheduled agent tasks ("every morning, summarise my email") wait for Phase 5, when there is something for them to do. A trained wake-word model could replace the speech-recognition wake check later; `Transcriber.find_wake` is the seam. Do not start those without being asked, and do not add abstractions for them in advance.
