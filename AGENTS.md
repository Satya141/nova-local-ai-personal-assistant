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
  nova/vision/           capturing the user's window and asking the vision model about it
  nova/browser/          NOVA's own Edge window (Playwright), page reading and search
  nova/integrations/     the user's Google and GitHub accounts, sign-in, the DPAPI vault, which tools are live
  nova/phone/            phone access: listeners (local network + Tailscale), certificates, .local name, pairing
  nova/settings.py       persistent user settings (e.g. wake word on/off)
  nova/api/              HTTP API (token-protected, loopback only) and the event stream
  nova/database.py       the SQLite file and its migrations
  nova/dates.py          calendar lookups for prompts and memories, and past phrases ("last Monday") as days
  nova/timeline.py       the life timeline, assembled from conversations, actions, memories and reminders
  nova/events.py         in-process pub/sub behind GET /api/events
  tests/                 pytest suite (no model needed)
  evals/                 agent, memory and voice evaluations against the real models
  models/                downloaded Whisper and Piper models (git-ignored; scripts/setup.ps1)
apps/desktop/            Tauri 2 shell + Next.js 16 UI. A thin client.
  src-tauri/src/         tray, global shortcut, launcher and reminder windows, backend supervisor
  src/app/               launcher page, /toast reminder window, /phone app, /gallery design reference
  src/components/        characters, icons, transcript, reminder card, memory & reminders panel
  src/lib/               backend client and the conversation state hook
scripts/setup.ps1        one-time dev setup
```

## Commands

| Task | Command |
|---|---|
| First-time setup | `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1` |
| Run the app (dev) | `pnpm --dir apps/desktop tauri dev` |
| Backend tests (the browser tests drive a hidden Edge against a local site) | `cd backend; .venv\Scripts\python -m pytest` |
| Agent evals (real model, ~2 min) | `cd backend; .venv\Scripts\python -m evals.run` |
| Memory extraction evals | `cd backend; .venv\Scripts\python -m evals.run --memory` |
| Voice pipeline evals (real models, synthetic speech) | `cd backend; .venv\Scripts\python -m evals.voice` |
| Screen understanding evals (synthetic screens) | `cd backend; .venv\Scripts\python -m evals.vision` |
| Real-window capture test (opens a small window) | `cd backend; $env:NOVA_GUI_TESTS=1; .venv\Scripts\python -m pytest tests/test_vision.py` |
| Frontend type-check | `pnpm --dir apps/desktop exec tsc --noEmit` |
| Character gallery | `pnpm --dir apps/desktop dev`, then open http://localhost:3000/gallery |
| Release build (no installer) | `pnpm --dir apps/desktop tauri build --no-bundle` |

Run the evals after any change to a prompt, a tool description, the memory pipeline, or anything in `nova/voice` (hints, thresholds, cut points). Unit tests cannot tell you whether the models still behave. Run each more than once: model output varies between runs. Keep checks strict (the voice eval anchors commands to the start; a loose check hid leaked words for a whole iteration). Add a case for every behaviour bug found in real use.

## Rules that must hold

1. **The model never executes anything.** It names a tool; the agent validates the arguments against the tool's schema, asks the permission gate, and only then runs the handler. Do not add a path around `PermissionGate`.
2. **Permissions come from tool metadata, not from the model.** A tool with `requires_confirmation=True` or a risk above `LOW` blocks until the user answers in the UI.
3. **No tool may run arbitrary commands.** Never interpolate model or user text into a shell or PowerShell string. `open_application` only launches entries from the Start menu index; `open_path` refuses programs and scripts.
4. **Tools report honestly.** A result says what actually happened. If a name was fuzzy-matched, say which app was really opened. The system prompt forbids claiming an action that no tool result confirms.
5. **The model does not do calendar arithmetic.** It is wrong too often (it called 8 October 2026 a Friday). Prompts carry an explicit day list (`nova.dates.upcoming_days`), weekday phrases in memories are resolved in code (`resolve_weekdays`), and tools about the past take the user's phrase and resolve it in code (`past_range`).
6. **Memory safety lives in code.** `looks_sensitive` refuses secrets and ID numbers for every memory; the extractor also drops short-lived facts. Do not move these checks into prompts.
7. **The API stays private.** It binds to loopback and every route requires the per-launch bearer token the shell generates. Keep both. Phone access is the one exception and is opt-in: HTTPS listeners on the local network and on Tailscale, decided in `authorize` by the listener a request came in on, accepting only paired phones from private addresses (Tailscale addresses on the Tailscale listener); the plain-HTTP setup listener serves only the setup page and NOVA's certificate. Keep NOVA's certificate authority name-constrained to private addresses and its key in the vault. New routes that touch credentials, the PC's hardware or phone access itself go in `_DESKTOP_ONLY`.
8. **Clients stay thin.** No agent logic in the desktop app. The one exception is the phone's pocket mode (`src/lib/pocket.ts`): a copy of memories and reminders and an outbox of changes that only `POST /api/sync` applies, with the PC's own checks. The UI renders the event streams documented at the top of `backend/nova/agent/loop.py` and in `backend/nova/api/app.py`; if you change those events, update `apps/desktop/src/lib/backend.ts` to match.
9. **No hard-coded model names** outside `backend/nova/config.py`. Everything model-specific goes behind `ModelProvider` or `Embedder`.
10. **The screen is looked at only on request.** The screen button (a click is consent) or a confirmed `look_at_screen` call; never on the model's own initiative, never continuously, and screenshots are never stored. Capture targets the user's window, never NOVA's own.
11. **The microphone is opt-in and visible.** It opens only while "Hey Nova" is switched on or for one mic-button command, and whenever it is open the tray icon and the launcher say so. Audio never touches disk or the network, and speech without the wake phrase is dropped. Voice models load with `local_files_only=True`.
12. **Outside content cannot drive actions.** Tools that return web pages, search results or the screen set `reads_untrusted`; after one runs, the gate makes every non-`read_only` tool ask with a warning and blocks `HIGH` risk ones for the rest of the request, and a `url_arg` address not found in the user's words or what NOVA read asks first. This is code, not prompt: with only a prompt rule, qwen3 obeyed a page telling it to delete files 3/3. Keep new tools' `read_only` and `reads_untrusted` flags truthful.
13. **The browser stays separate and visible.** Its own signed-out profile, never the user's; only http and https (`safe_url`); never sign in, enter credentials, or solve a "are you a person" check. Report the check to the user instead.
14. **File changes are reversible and contained.** Paths must be inside the home folder and outside AppData; standard folders cannot be moved or deleted; moves never overwrite; deletion goes to the Recycle Bin only. Do not add a permanent delete.
15. **Unattended runs never act on consent.** A scheduled task runs with `unattended=True`: anything the gate would ask about is declined, not approved. Setting a task up is what the user confirms.
16. **Keep tool results small.** Ollama silently cuts an oversized prompt from the front, question included. Return what the model needs (see `compact_page`), keep bulky data out of `content` (`ToolResult.vouches` exists for addresses), and leave `fit_context` in the loop.
17. **Schema changes are new migrations.** Append to `MIGRATIONS` in `nova/database.py`; never edit one that has shipped.
18. **No secrets in source, and credentials stay in the vault.** Account tokens live only in `nova/integrations/vault.py` (DPAPI). Never log them, return them from the API, put them in the database or action log, or show them to the model. Sign-in happens on the provider's own page; NOVA never handles passwords. Sending mail, inviting guests and posting publicly are `HIGH`.
19. **Windows stay on NOVA's pages.** The shell's navigation guard opens web links in the user's browser; do not remove it or load remote content into a NOVA window.
20. **Tests accompany behaviour changes.** New tools need tests for their matching and refusal logic; anything touching the gate needs an agent-loop test.

## Adding a tool

1. Write a Pydantic args model, an async handler returning `ToolResult`, and a `Tool(...)` in `backend/nova/tools/`. Tools that need a store are built by a factory function (see `tools/memory.py`).
2. Set `risk` and `requires_confirmation` truthfully. Deleting, sending, closing, buying: confirm. Set `read_only` if it changes nothing, `reads_untrusted` if its result contains outside content, `url_arg` if it opens an address, `confirm_when` for tools that are safe only for some arguments (see `click_element`), and `risk_for` when the arguments change the risk (see `calendar_add`). A tool for a connected account goes in `Connections` (`nova/integrations/connections.py`), not `build_registry()`.
3. Register it in `build_registry()` in `backend/nova/tools/__init__.py`.
4. Give it a character in `TOOL_OWNERS` in `apps/desktop/src/components/transcript.tsx` (or leave it to Nova).
5. Add tests, add an eval scenario with a sandboxed stand-in if it touches the computer, then try it for real through the launcher.

## The characters

Nova (the assistant, its memory and your email), Ember (apps and GitHub), Fern (files), Plum (closing and deleting things), Chime (reminders and your calendar), Iris (the screen) and Wren (the web) are NOVA's own designs.
A character keeps its colour in every state; status is shown by its face, motion and a small mark.
All visuals are in `apps/desktop/src/app/character.css`. Check changes on `/gallery` in both light and dark themes, and keep the `prefers-reduced-motion` block working.

## Testing the real app

The launcher and reminder windows can be driven through WebView2's debugging port: start `nova.exe` with `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=<port>` and talk Chrome DevTools Protocol to it. Set `NOVA_DATA_DIR` to a scratch folder so tests never write into the user's real memory, and never leave an instance running with the debug port open.

Showing the launcher takes keyboard focus. If the user is typing elsewhere, their keystrokes land in it, so avoid bringing it up while someone is at the machine, and never let a test submit what is in the input. Do not test voice live on someone's machine without asking: the mic button records whatever is being said in the room, and speaking plays audio in their ears. `evals/voice.py` covers the pipeline with the real models instead.

Processes started from inside a sandboxed (MSIX-packaged) host see a private copy of `%LOCALAPPDATA%`; that is why models live in `backend/models`, not under AppData.

## Current scope

Phases 1 to 7 are done (6 as a phone web app, chosen with the user, encrypted with NOVA's own certificate, paired by QR code, found by its .local name on any shared Wi-Fi; 7 as access from anywhere through the user's own Tailscale, with one memory on the PC rather than synced copies). Tailscale has not yet been tried for real. The Gmail, Calendar and GitHub tools have not yet been run against the real services, and the phone app not yet on the user's real phone. Phase 8 part 1 is done (the phone keeps a copy and an outbox and works while the PC is away); part 2, a small model in the phone's browser for offline chat, waits for the user's approval of the web-llm and model downloads. Phase 9 (the life timeline, assembled from existing tables, with `recall_activity` and on-request day summaries) is done. Not built yet: the installer (10). A trained wake-word model could replace the speech-recognition wake check later; `Transcriber.find_wake` is the seam. Do not start those without being asked, and do not add abstractions for them in advance.
