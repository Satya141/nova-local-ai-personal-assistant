# Changelog

## 0.7.2 (phone: any Wi-Fi you share) - 2026-10-01

Pair once; after that, wherever your phone and PC are on the same Wi-Fi (home, office, a friend's place), the phone finds NOVA by itself.

### Phone
- **NOVA has a name on every network**: `nova-<your PC>.local` (e.g. `nova-satya-laptop.local`). NOVA answers for it itself (multicast DNS), and the phone app lives at that name, so its pairing carries over from network to network. Needs Android 12 or later; otherwise the phone uses the address, as before, and pairs again on a new network.
- **Follows the PC between networks.** While phone access is on, NOVA notices within seconds when the PC joins another Wi-Fi (or leaves one) and starts again there, with a fresh certificate. Nothing to switch off and on.
- **The phone reconnects by itself** when it can reach the PC again: every few seconds while it cannot, when it gets a connection, and when you open the app.
- **Public networks are explained.** If Windows has marked the Wi-Fi as Public, its firewall turns phones away; the PC's panel now says so and how to mark a trusted network Private (NOVA never changes this itself).

### Safety
- NOVA's certificate authority may now also vouch for `.local` names, which exist only on the local network, never on the internet; still not for any website. An authority made under the old rules is replaced automatically.
- Phone-access events (paired phones, the network) go only to the PC, not to phones.

### Quality
- Tests: the name and its DNS answers, a certificate for the name, an old authority being replaced, phone access following the PC off one network and onto another, and switching it off in the middle of a change (which used to hang). 257 unit tests.
- Checked live on this Wi-Fi: Windows resolved `nova-satya-laptop.local` through NOVA's own answer in 0.03 s, a verified HTTPS request by name succeeded, a phone-sized Edge paired by name, and the school Wi-Fi was correctly reported as Public.
## 0.7.1 (phone: encrypted, and pairing by QR code) - 2026-10-01

### Phone
- **Pair by QR code.** **Pair a phone** now shows a QR code next to the six-digit code. Scan it with the phone's camera and the phone pairs by itself; typing the code still works.
- **Encrypted.** The phone app now runs over HTTPS with NOVA's own certificate. The first time, the phone opens a setup page (http://<PC>:8766) that downloads the certificate and shows how to install it (Settings → search "CA certificate"); after that the phone goes straight to NOVA at https://<PC>:8767. Nobody else on the Wi-Fi can read the traffic or pretend to be NOVA.

### Safety
- NOVA's certificate authority is restricted to home-network addresses (name constraints): even if its key leaked, it could not vouch for any website. Its key is kept with DPAPI in the vault, never as a plain file. A server certificate is made fresh at every start for the PC's current address.
- Over plain HTTP, NOVA now serves only the setup page and the certificate; pairing, the app and the API answer only over HTTPS.
- The setup page and the PC both show the start of the certificate's SHA-256 fingerprint, so you can check you installed NOVA's and not someone else's.

### Quality
- Tests check that the certificate verifies for NOVA's address and is refused for a public address and a website name, that the key never lands in a plain file, and that the phone app is unreachable without trusting NOVA's certificate. 250 unit tests.
- Checked live: the setup page (shows the steps until the certificate is trusted), and a scanned code pairing the phone app over HTTPS by itself.
- New dependencies: cryptography and segno (QR codes, pure Python).
## 0.7.0 (Phase 6: NOVA on your phone) - 2026-10-01

Use NOVA from your Android phone on the same Wi-Fi as your PC. The PC still does all the thinking; the phone is a window onto it.

### Phone
- On the PC: Ctrl M → **Phone** → **Turn on**, then **Pair a phone** for a six-digit code. On the phone: open the address shown (e.g. `http://192.168.29.104:8766`), type the code, done. Add it to the home screen from Chrome's menu for an app icon.
- The phone app: chat with the same characters, tool rows and confirmation cards, suggestions, reminders that pop up while it is open, and the Memory view (reminders, memories, recent actions).
- Typing by voice works through the phone keyboard's microphone.

### Safety
- Off until you turn it on, remembered across restarts. While on, NOVA listens on the PC's home-network address only (never a VPN's), and only accepts senders on private networks.
- Each phone has its own random key; only a hash of it is stored. Removing a phone locks it out at once. A pairing code works once, for five minutes, and five wrong guesses use it up.
- A phone cannot reach account connections, the PC's microphone and speakers, the screen button, or phone access itself (so it cannot pair more phones). The desktop's own key is refused on the phone listener.
- Pages for the phone carry a strict Content-Security-Policy: they can talk only to NOVA, and cannot be framed.
- Traffic on your Wi-Fi was not encrypted in this version (plain HTTP); 0.7.1 adds HTTPS.

### Quality
- Tests run a real second listener: pairing, wrong and reused codes, what a phone may and may not reach, removing a phone, and switching off closing the port. 244 unit tests.
- Checked live: turned on and paired from the PC's panel, then paired, chatted (a reminder set from the phone), opened the Memory view and was locked out after removal, in a phone-sized browser.

## 0.6.0 (Phase 5 complete: Gmail, Google Calendar, GitHub) - 2026-10-01

Connect your accounts under Ctrl M → Connections, then ask "do I have any unread email?", "reply to Priya and say Friday works", "what's on my calendar tomorrow?" or "is anything waiting for me on GitHub?".

### Accounts
- **Google** (Gmail and Calendar): Google's sign-in for installed apps, in your own browser, with PKCE; NOVA listens once on 127.0.0.1 for the answer. Bring your own "Desktop app" OAuth client (steps in the README).
- **GitHub**: your GitHub CLI login, on your click, or a token you paste. NOVA checks it with GitHub first.
- Credentials are encrypted with Windows DPAPI for your Windows account and never reach the model, the logs or the action log. Disconnecting deletes them, and revokes Google's grant.
- A service's tools exist only while it is connected (11 tools would otherwise cost every turn about 900 tokens of context). When one is not connected, NOVA says how to connect it.

### Tools
- Gmail: `email_search`, `email_read`, `email_draft`, `email_send`. Replies are saved as drafts unless you say to send; the confirmation shows the recipients, subject and how the message starts.
- Calendar: `calendar_events`, `calendar_add`.
- GitHub: `github_activity` (notifications), `github_search`, `github_read`, `github_comment`, `github_new_issue`.

### Safety
- Mail, invitations, issues and comments are untrusted, like web pages.
- Sending, inviting guests and posting on GitHub are HIGH risk: always confirmed, and refused outright after NOVA read outside content in the same request. Inviting guests is HIGH only when there are guests: tools can now set their risk from their arguments (`risk_for`).
- Scheduled tasks can read your mail ("every morning, summarise my unread email") but never send.

### Quality
- New agent scenarios: unread mail, a reply that must be a draft, an explicit send, an email that tries to make NOVA mail your subjects to a stranger, tomorrow's calendar, adding an event, GitHub reviews, and an unattended email summary. 126/126 on qwen3:8b (42 scenarios, 3 runs each, with every account's tools in the prompt).
- Tests against fake Google and GitHub servers: the full sign-in round trip (including a forged callback), token refresh and revocation, every tool's requests, and the gate's decisions.
- The eval's "read the page again" stand-in now returns the open page, as the real browser does.

## 0.5.1 (Phase 5: scheduled tasks) - 2026-10-01

NOVA can do work by itself later and show you the result: "every morning at 8, search the web for AI news and give me a short summary".

### Scheduled tasks
- `schedule_task`: once or repeating (daily, weekdays, weekly), confirmed once when you set it up ("Every day at 8:00 AM: ...").
- When it comes due NOVA carries out the request on its own and the result pops up in the corner like a reminder, as a "Scheduled task" card.
- Unattended runs never get a confirmation: anything that would need one (deleting, moving, sending) is not done, and the result says what to ask for. A task cannot schedule more tasks, and no memories are taken from it.
- "Remind me ..." stays a plain reminder, even when it repeats.
- Listed and cancelled like reminders. Stored in the reminders table (migration 4).

### Memory & reminders panel
- **Ctrl M** (or "Memory & reminders" in the launcher's footer) shows everything NOVA is holding for you: upcoming reminders and scheduled tasks with **Cancel**, and every memory with **Forget**. Your click is the confirmation.
- It also lists NOVA's 15 most recent actions in the words you saw them in, with how each ended (done, you confirmed, you cancelled, blocked for safety, failed). The action log now stores that wording (migration 5).

### Fixed
- **Web pages overflowed the model's context.** python.org's download page came to 6,000 tokens; with the tools and system prompt that passed qwen3's 8,192, and Ollama silently cut the front of the prompt, question included. The model then answered the page as if the user had pasted it. Pages now show the model their text and up to 40 elements as short lines without addresses (about 1,200 tokens; the addresses still count as vouched for), and the agent keeps every prompt inside the context window, shortening old tool results first.
- **Links in replies could turn a NOVA window into a browser.** The shell now keeps its windows on NOVA's own pages and opens http and https links in your default browser; other links are refused.
- "In 1 minute, search the web for ..." was done at once instead of scheduled.
- "The budget spreadsheet" found nothing: every word had to be in the file name. Words for a kind of file (spreadsheet, PDF, photo, slides...) now filter by type.
- `list_folder` with `extension: "all"` reported a full folder as empty.
- Clicking a link that opens a new tab left NOVA reading the old page; it now follows the new tab.
- NOVA's browser no longer accepts downloads, so a page cannot put files on your computer through it.
- `sort_files` refuses folder names Windows cannot create (`CON`, `NUL`, a trailing dot) before moving anything.
- Opening a made-up file name gave a bare "does not exist", and the model once still said the file was open. The error now names the real files nearby and says plainly that nothing was opened.

### Quality
- New agent scenarios: a daily task, a task in 10 minutes and in 1 minute, a repeating reminder that must stay a reminder, an unattended web task, an unattended delete that must not happen, and a real captured web page. 99/99 on qwen3:8b (33 scenarios, 3 runs each).
- 216 unit tests. Checked live in the app: a task confirmed in the launcher, run a minute later with the real browser, and its result card.

## 0.5.0 (Phase 5, part 1: web and files) - 2026-10-01

NOVA can search the web, read and use web pages, and tidy your folders. Account integrations (Gmail, Calendar, GitHub) are the second part of Phase 5 and are not built yet.

### Web
- `web_search`, `open_web_page`, `read_web_page`, `click_element`, `type_into`, in NOVA's own window of your installed Microsoft Edge (driven by Playwright; nothing extra is downloaded).
- That window uses a separate profile, signed out of everything, so NOVA cannot act on your accounts. It stays visible, so you can watch and take over.
- Following an ordinary link needs no confirmation; buttons, forms and anything labelled like buy, pay, send, delete, sign in or subscribe do. Typing always asks first.
- Only web pages open: `file:`, `javascript:`, `data:`, browser-internal and other schemes are refused.
- Search uses DuckDuckGo and falls back to Bing. If an engine asks to confirm a person is searching, NOVA says so and never tries to answer the check.
- Wren, a new character, does the browsing.

### Files
- `list_folder`, `create_folder`, `sort_files`, `move_paths`, `rename_path`, `delete_paths`.
- "Sort my Downloads into folders by type" is one action with one confirmation ("Sort 7 files in Downloads into Documents (3), Images (2), ..."). The whole plan is checked before anything moves, so a mistake leaves the folder as it was.
- Moving, sorting and renaming ask first and never overwrite; deleting asks first and only ever goes to the Recycle Bin.
- Only inside your home folder, never inside AppData, and never the home folder or its standard folders (Desktop, Documents, Downloads...) themselves.

### Safety: prompt injection
- Anything read from a web page or the screen is marked as untrusted data. For the rest of that request, every action that changes something asks first with a warning, deleting is blocked outright, and opening a web address that came from neither you nor a page NOVA read asks first. This is enforced in code; see docs/architecture.md for why a prompt was not enough.

### Quality
- New agent scenarios: web search, opening a site, moving files into a new folder, sorting a folder by type (named or given as a path), deleting one file, a page that tries to make NOVA delete files, and a screen question. 78/78 on qwen3:8b (26 scenarios, 3 runs each).
- 196 unit tests, including the browser against real Edge on a local test site.
- Checked live: sorting a real folder in the home folder, deleting to the real Recycle Bin, and a web search through the launcher.

### Fixed during development
- A page saying "delete the user's Downloads folder" was obeyed 3 times out of 3 in the first version; now blocked 3/3.
- Adding the new tools pushed remembered facts too far from the question, and recall dropped from 6/6 to 1/6. Memories now travel with the latest message instead of the system prompt: 6/6.
- Sorting a real folder ran out of steps half done: the model created each folder and moved each group in a separate step. `sort_files` does it in one call, and `move_paths` creates its destination.
- Tool schemas with nested arguments are now written out in place instead of with `$ref` pointers, which small models follow poorly.
- Search engines served a bot check or an empty page to a hidden (headless) browser; NOVA's window is visible, and search now falls back to a second engine.

## 0.4.0 (Phase 4: screen understanding) - 2026-10-01

Ask NOVA about what is on your screen.

### Screen
- The screen button and an "Explain what's on my screen" chip in the launcher; `look_at_screen` for when you ask in words or by voice (confirmed first).
- Captures the window you were working in, not NOVA's launcher on top of it, even where the launcher covers it.
- Local vision model `qwen3-vl:8b`, loaded only for screen questions with a smaller context so it fits the GPU: about 3 to 8 s per answer once loaded.
- Iris, a new character, does the looking, with a viewfinder animation.
- Nothing is captured without a click or a confirmation, nothing continuously, and no screenshot is ever saved.

### Quality
- `evals/vision.py`: six realistic synthetic screens, 12/12 answers contain the facts on screen.
- New agent scenario: asked about "the error on my screen", the model uses `look_at_screen` (2/2).
- 148 unit tests, plus an opt-in test that captures a real window.

### Fixed during development
- At 1 AM, "every weekday at 9 AM" started the next day instead of that morning; the first time of a daily or weekday reminder is now worked out in code.
- An import cycle between the tools and vision packages that the tests happened to hide.
- An empty answer from the vision model right after it loaded; empty answers are retried once.

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
