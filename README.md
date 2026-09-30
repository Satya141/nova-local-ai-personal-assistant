# NOVA

**One AI. One Memory. Any Device.**

NOVA is an open-source, local-first AI personal assistant. It runs a language model on your own computer, and it acts: ask it to open an app, find a file or remind you of something and it does that, rather than telling you how.

This repository is partway through **Phase 5**: a Windows desktop agent with long-term memory, reminders, voice, screen understanding, web browsing and file organising. Account integrations (Gmail, Calendar, GitHub), the phone app, shared memory and distributed inference are planned and not built yet.

![NOVA reading an error from the window behind it](docs/screenshots/screen.png)

![NOVA setting a reminder and remembering an interview date](docs/screenshots/memory.png)

NOVA's characters show what it is doing. Nova is the assistant and keeps its memory; Ember opens apps, Fern handles files, Plum closes and deletes things and asks first, Chime rings when a reminder is due, Iris looks at your screen, and Wren browses the web.

![NOVA's first four characters in each of their states](docs/screenshots/characters.jpg)

![A reminder popping up in the corner of the screen](docs/screenshots/reminder.png)

## What works today

- Press **Alt+Space** anywhere to open the launcher. NOVA lives in the system tray.
- Runs **qwen3:8b locally through Ollama**. Nothing you type leaves your machine.
- **Acts, including several steps in one request** ("find the budget spreadsheet, open it, and remind me tomorrow at 10 to review it"):
  - open or close applications ("open VS Code", "close Notepad" after you confirm)
  - find files and folders by name, and open them
  - organise folders: "sort my Downloads into folders by type", rename, move, and delete to the Recycle Bin (each asks first)
  - search the web, open sites, read pages and follow links ("what's the latest Python version?", "open python.org and find the release notes")
  - do work later by itself and show you the result ("every morning at 8, search the web for AI news and give me a short summary"); nothing that needs your OK happens while you are away
  - set, list and cancel reminders, one-off or repeating ("every weekday at 9 remind me to stretch")
- **Reminders** pop up in the corner of the screen without stealing focus, survive restarts, and show up as missed if NOVA was closed when they were due. Snooze or dismiss them there.
- **See and change what NOVA holds.** Press **Ctrl M** in the launcher for every upcoming reminder and scheduled task (Cancel), everything NOVA remembers (Forget), and what it recently did and how each action ended.
- **Long-term memory.** NOVA notices lasting facts in what you say ("I'm preparing for my Cognizant interview next Friday") and remembers them across conversations, with the date written out. Each one appears under the conversation with a **Forget** button. You can also say "remember that…", "what do you remember about me?" or "forget that…".
- **Screen understanding.** Press the screen button in the launcher (or the "Explain what's on my screen" chip) and ask about what you were looking at: "what's wrong with this code?", "what does this chart show?", "why is this button disabled?". NOVA reads the window you were working in, not itself, with the local vision model `qwen3-vl:8b`. Asking in words or by voice ("Hey Nova, explain the error on my screen") works too, after you confirm. NOVA never looks at your screen on its own.
- **Voice.** Turn on **Listen for "Hey Nova"** in the tray menu, then say "Hey Nova, open VS Code" in one breath, or "Hey Nova", wait for the chime, and speak. The microphone button in the launcher takes one command without the wake phrase. Spoken requests get a short spoken reply; press **Esc** or say "Hey Nova, stop" to interrupt. While the microphone is on, the tray icon shows a red dot and the launcher says so.
- Asks before anything risky, keeps a log of every action it attempts, and tells you plainly when something failed.

## Requirements

Windows 11, [Python 3.12+](https://www.python.org/), [Node.js](https://nodejs.org/) with pnpm, the [Rust toolchain](https://rustup.rs/) with the MSVC build tools, and [Ollama](https://ollama.com/). A GPU with 8 GB of VRAM runs the default model comfortably; the memory model runs on the CPU.

## Run it

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
pnpm --dir apps/desktop tauri dev
```

The first command creates the Python environment, installs dependencies and downloads the models (about 12 GB: the chat and vision models through Ollama, plus 330 MB of voice models in `backend\models`). The second starts NOVA; press Alt+Space.

If another app already uses Alt+Space, NOVA takes Ctrl+Alt+Space instead and shows that in the launcher's footer and the tray tooltip.

## Configuration

Set these environment variables before starting NOVA.

| Variable | Default | Meaning |
|---|---|---|
| `NOVA_MODEL` | `qwen3:8b` | Any Ollama model that supports tool calling |
| `NOVA_EMBED_MODEL` | `embeddinggemma` | Embedding model for finding memories by meaning. Empty: keyword matching |
| `NOVA_AUTO_MEMORY` | `true` | Notice and save lasting facts without being asked |
| `NOVA_OLLAMA_URL` | `http://127.0.0.1:11434` | Where Ollama is listening |
| `NOVA_THINK` | `false` | Let the model reason before answering (slower) |
| `NOVA_NUM_CTX` | `8192` | Context window in tokens |
| `NOVA_KEEP_ALIVE` | `10m` | How long the models stay loaded when idle |
| `NOVA_DATA_DIR` | `%LOCALAPPDATA%\NOVA` | Where NOVA's database lives |
| `NOVA_VISION_MODEL` | `qwen3-vl:8b` | Ollama vision model for reading the screen. Empty turns screen understanding off |
| `NOVA_VOICE` | `true` | Offer voice at all. The microphone still only opens when you turn it on |
| `NOVA_MODELS_DIR` | `backend\models` | Where the Whisper and Piper models are |
| `NOVA_BROWSER` | `true` | Let NOVA use its own Edge window for the web tools |

## Privacy and safety

- Inference, memory, tools and voice all run locally. The interface makes no network requests of its own, and speech models load strictly offline. The only traffic to the internet is the web pages you ask NOVA to search or open.
- NOVA browses in its own visible Edge window with a separate profile that is signed out of everything, so it cannot act on your accounts. Buttons, forms and typing ask first; plain links do not.
- Text on web pages and the screen is treated as untrusted. If a request read any, every action that changes something in that request asks first with a warning, and deleting is blocked. This is enforced in code, because the model alone obeyed a page telling it to delete files.
- Deleting files only ever moves them to the Recycle Bin, and NOVA never touches AppData or moves your standard folders.
- NOVA looks at your screen only when you press the screen button, or confirm when it asks. It captures one window, answers, and keeps nothing: screenshots are never saved.
- The microphone is off until you turn on "Hey Nova" or press the mic button. Audio is never recorded to disk or sent anywhere; NOVA keeps at most the utterance it is currently listening to, in memory. Speech that does not start with "Hey Nova" is discarded.
- The model cannot run commands. It can only request one of the registered tools, with arguments that are validated first.
- Risky actions (closing apps, deleting a memory) wait for your confirmation in the launcher.
- Memory never stores passwords, codes, keys, card numbers or ID numbers, even when asked: that is enforced in code, not left to the model. Automatic memory also skips health and money details.
- Every action NOVA attempts is recorded in a local log (`GET /api/actions`).
- The backend listens only on your own machine and requires a token that is generated fresh at every launch.

## Quality

The agent is measured against the real local models, not only unit-tested. `backend/evals` runs realistic requests with the computer-facing tools sandboxed. Current results on qwen3:8b: 99/99 agent task runs (including a web page that tries to make NOVA delete files) and 38/38 memory-extraction cases. Screen understanding with qwen3-vl:8b answered 12 of 12 questions about realistic screens (an editor error, a dialog, a chart, a form, a traceback, an invoice), about 3 to 8 s each once loaded. The voice pipeline, fed synthetic speech at three speeds with background noise, heard 97 of 99 commands exactly and never woke on the 36 sentences that were not meant for it; a command is ready about 0.7 s after you stop speaking. See [AGENTS.md](AGENTS.md) for how to run them.

## Licences of what NOVA uses

The voice uses [Piper](https://github.com/OHF-Voice/piper1-gpl), whose engine is GPL-3.0; the `en_US-ljspeech-high` voice is trained on the public-domain LJ Speech dataset. Whisper models come from [Systran](https://huggingface.co/Systran) (MIT). This matters when NOVA chooses its own licence.

## Project layout

See [AGENTS.md](AGENTS.md) for the layout, commands and rules, and [docs/architecture.md](docs/architecture.md) for how it fits together and why.

## License

Not chosen yet. Until a license file is added, the code is not licensed for reuse.
