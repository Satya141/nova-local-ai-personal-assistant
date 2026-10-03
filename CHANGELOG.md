# Changelog

## 0.12.1 (reminders ring on a locked phone) - 2026-10-03

On the user's real phone, a reminder rang on the PC but not on the phone: Android pauses a web page when the screen is off, so the page could not ring. Now the PC sends it as a notification.

### Phone
- **Reminders on a locked phone** (phone app, on the chat screen until turned on, and under Memory): **Turn on** asks Chrome for permission once. Then every reminder or finished task that rings on the PC also rings on the phone as a notification (sound, vibration, stays until tapped), even when the phone is locked or NOVA is closed. A tap opens NOVA with the reminder's Done and Snooze. **Send a test** rings it once on request; **Turn off** stops it.
- While NOVA is open on screen the phone shows its reminder card instead, so nothing rings twice.
- **Reminders make a sound.** The laptop plays Windows' own reminder sound when the pop-up appears (`ring` in the shell; a never-clicked WebView2 page can't play audio). The phone app plays a two-note chime and buzzes when a reminder card appears (Web Audio, no sound file; it can sound after the first touch, which browsers require). On a locked phone, Android's notification sound plays.

### How it works
- Web Push (`nova/phone/push.py`). A locked phone's browser only wakes for its own push service (Google's, for Chrome), so each message goes through it. It is sealed on the PC for that one browser (RFC 8291, aes128gcm), so the service can't read it, and signed with the PC's own key (VAPID, RFC 8292), which is kept in the vault. Built on `cryptography`, already a dependency, so there's no new package. The encryption matches RFC 8291's worked example byte for byte.
- The PC only ever posts to browsers' push services (`PUSH_SERVICES`), so a paired phone can't point it anywhere else. Subscriptions live in `push_subscriptions` (migration 8), one per phone, and go when the phone is removed. A subscription the browser dropped (HTTP 404/410) is forgotten. With no internet, nothing is sent and the phone still shows the card when it is opened. Nothing is sent while phone access is off.
- API (phone only): `GET/POST/DELETE /api/push`, `POST /api/push/test`.
- Checked end to end in a hidden Edge through Microsoft's real push service: the test notification arrived, and so did a reminder that came due with no NOVA page open. 16 new tests, 328 in all. Then confirmed on the user's real Android phone (Chrome, through Google's service): a reminder rang on the locked phone. With earbuds connected Android plays the sound in them, and the site's own notification settings (Chrome → Sites) decide sound, pop-up and lock screen.

### Talking to Nova from the phone
- **A mic button on the phone** (next to Send, while the PC is reachable): tap, speak, and it stops by itself when you pause (or tap ■). The recording goes over the encrypted link to the PC, whose own Whisper turns it into text, and it is sent like a typed message. It never goes to Google's or anyone's speech service and is never stored (decoded in memory with PyAV; `faster_whisper.decode_audio` does not run on PyAV 19). A leading "Hey Nova" is dropped.
- `POST /api/transcribe` (phone and PC; 3 MB, 30 s at most) and `hearing_ready` in `/api/health`.
- On the real phone Whisper made lines up from noise ("I am not going to be a doctor", three times). Phone recordings are now heard as clips: silence and noise cut first (Silero VAD, bundled with faster-whisper), unsure segments dropped, no carry-over between segments, and a line repeated three or more times thrown away. Speech in noise still comes through; noise alone and silence now give nothing. The PC's own mic is unchanged.
- Checked end to end: synthetic speech in WebM/Opus heard exactly (3/3), and a phone-sized Edge with a fake microphone saying "Remind me in ten minutes to drink water" set that reminder. 5 new tests.

### Better hearing: Whisper small.en
- The command model (what follows "Hey Nova", the mic button, and the phone's recordings) is now `small.en` (464 MB, downloaded with the user's OK) instead of `base.en`, which misheard the user's phone recordings. `tiny.en` still spots the wake phrase. Configured in `config.py` (`whisper_command_model`, `NOVA_WHISPER_COMMAND_MODEL`); without `small.en` on disk NOVA falls back to `base.en`. `scripts/setup.ps1` now fetches `small.en`.
- `evals/voice.py`: 33/33 with either model; a command is ready 1.52 s after the speech ends instead of 1.08 s.

### Pocket model for more phones
- The user's phone has WebGPU but its graphics chip lacks 16-bit maths (`shader-f16`), which the 1.7B pocket model needs. The PC now also offers **Qwen3-0.6B in 32-bit maths** (342 MB, `pocket_model_small`) to such phones: the phone says whether it has 16-bit maths (`GET /api/pocket?f16=false`) and gets the model it can run. `scripts/fetch_pocket_model.py --small-only` downloads it.
- `evals/pocket.py --small`: 22/22 over two runs, the same as the 1.7B model. In pocket mode code decides reminders and what to remember, so the smaller model only answers questions from the phone's copy.

### Working in any app
- **NOVA can now use the apps on the PC**: read an open app's window (its buttons, boxes, menus and text, numbered), click, type and press keys (`read_app`, `click_in_app`, `type_in_app`, `press_keys`, in `nova/apps/control.py` through Windows UI Automation). Each action returns the window as it is afterwards, so the next step uses current numbers. Asked to message Priya on WhatsApp, the model opens WhatsApp, searches, opens her chat, types and sends (new eval `whatsapp_message`, 4/4 with a pretend WhatsApp window).
- **Safety, in code.** An app's window is untrusted like a web page, so after reading one every action that is not a plain click asks the user, with a warning. Clicks on anything labelled like an action (send, delete, pay, save, yes, OK...) and on switches always ask; typing always asks; only listed keys can be pressed, and Enter, Delete, Backspace, Ctrl+X and Ctrl+S ask. Never operated: terminals (typing there runs commands), Windows Security and admin or sign-in prompts, password managers, the user's web browsers (NOVA has its own), NOVA itself. Password boxes are never typed into and their values never shown. Text goes in through the clipboard (restored afterwards), never as key sequences. Not offered to requests from the phone (`at_the_pc`).
- From the real WhatsApp: a minimized app describes almost nothing, so `read_app` brings it to the front first; a chat row opens with a real click (selecting it did nothing), so plain items are clicked on their own rectangle; and when a click opens a dialog of the same app (Attach → Document → Windows' Open dialog), NOVA follows into it and back to the app when it closes. Checked for real on Notepad's File → Open → Cancel.
- First real run ("open my whatsapp and send hi to ravi kumar"): `read_app` listed the hidden buttons of the Edge engine WhatsApp is built on ("Search instead", "App available. Install") ahead of WhatsApp's own, and the model typed into them. Only controls with a size inside the window are listed now, Edge's own are skipped, a page's container is left out, and a control reached twice (WhatsApp exposes its page twice) is listed once: 47 real controls, the search box among the first.
- **Fewer questions, from the user's own words.** The user wanted NOVA to just type and click. After an app was read, every typing step asked with a warning (and one card offered to type into the "New chat" button). Now text taken word for word from the user's request ("Ravi Kumar", "hi") is typed without asking, since nothing outside could have chosen it (`Tool.user_text_arg`, `said_by_user`, in the gate); other text still asks with the warning, and Enter in a message box (sending) asks. A call that cannot work (typing into a button, a number not on screen) is refused before anyone is asked (`Tool.check`). "Send hi to Ravi" is now one confirmation: Send. 3 agent-loop tests; injection evals unchanged; all evals 97/100.
- New eval `whatsapp_file`: the pretend WhatsApp now has groups matching the name, an Attach menu and an Open dialog; "Send my resume to Ananditha on WhatsApp" picks her own chat, attaches the file and sends it (3/3, and `whatsapp_message` 3/3). In the real app that is three confirmations: the search, the file and Send.
- The step limit per request is now 14 (`max_steps`): six to eight steps for an app task cut a WhatsApp message short at 8.
- A 27-value enum for the keys made the model pass `extension: "anyOf"` to `list_folder` (`sort_by_type` 1/4); the key is now plain text checked in code (4/4 again). All evals 96/98, the two misses being `reminder_weekday`.
- New dependency: `uiautomation` (about 1 MB, with the user's OK). 28 tests.
- A scratch check of these tools against a real Notepad typed into and emptied the user's restored, unsaved note. Real-app checks now use only files NOVA creates.

### Faster
- **Mail search** fetches the listed emails at once instead of one after another (ten round trips to Google became one), over one kept-open connection instead of a new TLS handshake per call (`GoogleAccount._api`). Test: ten fetches of 0.2 s each finish in under 1 s.
- **The model stays loaded for 30 minutes** after the last request instead of 10 (`keep_alive`): a cold start costs 5.9 s before the first answer, a warm answer 0.7 s (qwen3:8b, 60 tokens/s on the RTX 5050). It holds about 6 GB of graphics memory meanwhile; `NOVA_KEEP_ALIVE` changes it.
- **The phone app warms the model up** when it connects, as the launcher already did when it opens.

### Fixed (found on the real phone)
- **A made-up email address.** Asked to send a resume on WhatsApp (no tool for it), the model saved a Gmail draft to "ananditha@example.com" and the user approved the card without spotting the address. Tools now name their address arguments (`Tool.address_args`: `email_draft`, `email_send`, `calendar_add` guests), and the loop refuses, before any confirmation, an address that is not in the user's words, the conversation, NOVA's memory or something NOVA read, telling the model to ask instead and not to switch to another channel. 2 agent-loop tests; email and calendar evals 20/20.
- **A request saved as a memory.** "now send resume to anandhitha on whatsapp" (no tool for it, so no action marked it as a request) became "The user sends their resume to Anandhitha on WhatsApp." (3/3 in the eval). A message that is only a command, with nothing about the user in it, is now never sent for extraction (`plain_request`, in code). 4 new extraction cases; 44/46, the 2 misses being the existing "next Friday" date case.
- **Unread email count.** Gmail is now connected for real. Asked "do I have unread mails" with 2,456 unread, the search returned the newest ten and the model said "You have 1 unread email". `email_search` now leads with Gmail's count in a sentence (`summary`, `total`: "About 2,456 emails match 'is:unread'; showing the newest 10."). New eval `email_unread_count`; `email_unread` also checks the count. 21/21 email scenarios.
- **A question from the phone could look at the PC's screen.** "Do I have any unread email?" (Gmail not connected) made the model ask to take a screenshot, and it was approved. Tools that need the user at the PC (`Tool.at_the_pc`: `look_at_screen`) are now neither offered nor run for a request from a phone, whatever the model says.
- **Unconnected accounts got a made-up answer.** With only a line in the prompt, the model asked to look at the screen for unread email (3/3) and described an empty calendar that isn't connected (3/3). Each unconnected account now has a small stand-in tool (`email_not_connected`, `calendar_not_connected`, `github_not_connected`) that says it isn't connected and how to connect it. 3 new eval scenarios (they run with no accounts connected): 0/6 before, all pass now; 92/94 overall, and the 2 failures are `reminder_weekday`, which fails the same way without this change (on a Saturday, "Friday at 5 PM" became Wednesday the 14th).

## 0.12.0 (Phase 8, part 2: Nova on the phone, with the PC off) - 2026-10-02

Chat with Nova on your phone even when the PC is off or out of reach. A small AI model runs in the phone's own browser.

### Phone
- **Put Nova on this phone** (phone app → Memory): copies NOVA's pocket model, Qwen3-1.7B (about 990 MB), from your PC to the phone over your network, never from the internet, once. Needs Chrome with WebGPU (Android 12 or newer and a recent phone); the card says plainly when a phone cannot, or has too little free space. **Remove from this phone** frees the space.
- **When the PC is away**, ask Nova questions about what it knows ("what's my sister's name?", "when is my exam?"), tell it things to remember, and set reminders ("in 10 minutes", "tomorrow at 7 AM", "on Friday", "in half an hour"). Everything you add reaches the PC by itself when the phone sees it again.
- Things only the PC can do ("open VS Code", "check my email") get an honest "that needs your PC" instead of a pretend answer.
- The phone notices within 15 seconds when the PC goes away while the app is open, and switches to pocket mode by itself.

### How it decides
- A 1.7B model is not trusted with what it is not good at, so pocket mode decides in code: what a message asks for, a reminder's time and wording (read from your words, so no date is ever worked out by the model), and what to remember (your own sentence). The model only answers questions, from the phone's copy of the memory. Secrets are refused on the phone and again by the PC when it syncs.
- The PC serves the model at `/pocket/…` (the files web-llm asks for, and nothing else) and says which one at `GET /api/pocket`. Phone pages may now run WebAssembly (`'wasm-unsafe-eval'`).

### Quality
- `evals/pocket.py`: the real model in a phone-sized Edge on WebGPU, the PC switched off, 10 messages, then the PC back on and the sync checked. 22/22 over two runs (it began at 4/8; each failure became a code fix: memories reworded and duplicated, reminders without times, a claimed "VS Code is now open").
- 8 new tests for serving the model (only the configured files, no path tricks, the PC's token still needed). 312 unit tests.
- New: `@mlc-ai/web-llm` 0.2.85 (loaded only by pocket mode), `scripts/fetch_pocket_model.py` (puts the model in `backend/models/pocket`).
## 0.11.0 (Phase 10: the installer) - 2026-10-01

NOVA now builds into a Windows installer: `NOVA_0.11.0_x64-setup.exe`, 417 MB, per-user, no admin rights. It carries Python, NOVA's packages and the voice models; the first-run screen helps with Ollama and the language models.

### First run
- When NOVA's model is not there yet, the launcher shows **Set up NOVA's thinking** instead of an error: whether Ollama is installed and running (with a link to ollama.com/download, noticed by itself once it is), and each of NOVA's models with a **Download** button and a progress bar. Nothing downloads until you press it, and only the models in NOVA's settings can be fetched (`GET /api/setup`, `POST /api/setup/pull/{role}`, PC only).
- The optional models (screen and memory search) can come later: **Start using NOVA now** appears as soon as the required one is in.

### Installing
- The shell finds an installed backend in its resources (`backend\python`, `app`, `site-packages`, `models`, `phone-ui`) and falls back to the checkout it was built from, as before.
- `scripts\package.ps1 -PythonZip <python-3.12.x-embed-amd64.zip>` stages that layout from a set-up checkout (Python's official embeddable package, NOVA's code, the .venv's packages minus test tools and pip, the voice models, the phone app), checks that the staged Python imports everything, and builds a per-user NSIS installer (no admin rights). Ollama and the language models are not bundled; the first-run screen offers them.
- Version 0.11.0 across the backend, the shell and the interface.

### Quality
- 6 new tests for the first-run check against a pretend Ollama (missing, running, tags, progress, errors, only NOVA's own models). 304 unit tests.
- Built for real (Python 3.12.2 embeddable package, Tauri's NSIS tools): the staged Python imported every package, and the installed layout, run from the build folder with test data, started its backend on the bundled Python and found the voice models. Not installed on this PC.
- The phone's pocket model (about 1 GB) is left out of the installer.
- The setup screen checked in the design gallery, with Ollama missing and with models to download.
## 0.10.0 (Phase 9: the life timeline) - 2026-10-01

See your days with NOVA, and ask about them.

### Timeline
- **Ctrl T** in the launcher (and **Timeline** in the phone app) shows your days, newest first: what you asked, what NOVA did (with each action's character, and whether it worked or you cancelled it), what it came to remember, and which reminders rang. **Earlier days** goes further back.
- **Sum up the day** has the local model write two or three sentences about a day, only when you ask.
- **Ask about your past**: "what did I do yesterday?", "when did I last open VS Code?", "what did I work on last week?". NOVA looks it up with a new read-only tool, `recall_activity`.
- Built only from what NOVA already keeps (conversations, the action log, memories, reminders). Nothing new is collected, nothing leaves the PC.

### Dates
- The time in a question ("yesterday", "last Monday", "3 days ago", "last week", "28 September") is turned into days in code (`nova.dates.past_range`), not by the model, which gets dates wrong. A weekday alone is the most recent one; "last Thursday" said on a Thursday is a week ago.
- A day's summary is told whether the day is today, yesterday or a named date, instead of working it out (it called yesterday "today").

### Quality
- 23 new tests (the timeline, local-day boundaries, search, the tool, the API, 16 date phrases). 298 unit tests.
- Evals: two new scenarios (yesterday's activity without another day's mixed in; when VS Code was last opened), 8/8; the full suite 132/132 (44 scenarios × 3) with the new tool registered.
- Checked live in the phone app, light and dark, with seeded history and the real model's summary.
## 0.9.0 (Phase 8, part 1: the phone without the PC) - 2026-10-01

Your phone now works when the PC is off or out of reach: it keeps a copy of what NOVA remembers and your reminders, lets you add to them, and brings every change to the PC when it is back. The pocket AI model that will let you chat offline comes next (part 2).

### Phone
- **Opens without the PC.** The phone app keeps itself on the phone (a service worker), so it opens from the home screen even when the PC is off.
- **A copy of the memory and reminders** is kept on the phone and refreshed whenever it reaches the PC (on connecting, on every reminder or new memory, and every two minutes).
- **Pocket mode** while the PC is away: read what NOVA remembers, add something to remember, set a reminder (quick picks or a date and time), cancel one, forget a memory. A note says how many changes are waiting.
- **Reminders ring on the phone** while the app is open, from its copy, with Done and Snooze.
- **Sync both ways.** When the PC is reachable again, the phone sends its changes and gets a fresh copy, by itself.

### Sync
- `POST /api/sync`: the phone's changes in, a fresh copy out. Each change has an id made on the phone and is applied once, even if sent twice. Changes go through the PC's own stores and checks: passwords and ID numbers are still refused, and "next Friday" counts from the day you said it on the phone, not the day it synced.
- A reminder the phone rang and you dismissed is not rung again by the PC; one that came due while the phone app was closed is rung by the PC, as missed.
- Changes from the phone appear under Recent actions ("From My phone, while the PC was away: …").
- Removing a phone on the PC also wipes its copy, the next time it tries to connect.

### Quality
- 13 new tests for sync (applied once, memory safety, dates, offline reminders, ringing once, malformed changes). 274 unit tests.
- Checked live with a test copy of NOVA and a phone-sized Edge: paired, the PC switched off, the app opened offline with the copy, a memory and a reminder added, the reminder rang on the phone and was dismissed, the PC switched back on, the phone reconnected and synced by itself, and the PC had both (the reminder not rung again, "next Friday" as 9 October).
## 0.8.0 (Phase 7: NOVA from anywhere) - 2026-10-01

Use NOVA from your phone wherever you are (college, travel, mobile data) while your PC is on at home. Through Tailscale, which you install and sign in to on the PC and the phone; NOVA does the rest. Your memory still lives only on your PC: nothing is stored in a cloud.

### Away from home
- NOVA notices Tailscale on the PC by itself (within half a minute of it connecting) and opens phone access on the PC's Tailscale address too, next to the home network. It notices Tailscale going away as well.
- The phone's setup page tries the Tailscale address first, then NOVA's `.local` name, then the address on this Wi-Fi, and uses the first the phone can reach. A phone paired through Tailscale keeps working at home and away, on any Wi-Fi or mobile data.
- Ctrl M → Phone has an **Away from home** line: how to set up Tailscale (a link to its download page), or the address phones reach NOVA at once it is on.
- If the PC leaves the Wi-Fi but keeps Tailscale (say, on a phone's hotspot), phone access carries on through Tailscale alone.

### Safety
- On the Tailscale address, NOVA accepts only senders with Tailscale addresses; on the home network, only private ones. Pairing keys, HTTPS and the desktop-only routes are unchanged.
- Tailscale encrypts the connection end to end on top of NOVA's own HTTPS. NOVA's certificate authority may now vouch for Tailscale's address range (100.64.0.0/10), which no website uses; an authority made under older rules is replaced, and the phone installs the new one once.
- Tailscale's interface is recognised by name, so other VPNs in the same address range (Cloudflare WARP's Zero Trust mode) do not count.

### Quality
- Tests: only Tailscale's own interface counts, the certificate covers the Tailscale address and still refuses one just outside it, phone access opens on both addresses and turns away non-Tailscale senders on the Tailscale one, and Tailscale appearing, the Wi-Fi going away and Tailscale going away are all followed. 261 unit tests.
- Not yet tried with Tailscale itself: it is not installed on this PC, and installing it and signing in is the user's step.
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
