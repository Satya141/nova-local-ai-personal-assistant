"""The HTTP API the desktop and (later) mobile clients talk to.

It listens on loopback only, and every route requires the bearer token that
the desktop shell generated when it started this process. Without that, any
web page open in the user's browser could drive the agent.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import secrets
import webbrowser
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from nova import __version__
from nova.agent import Agent
from nova.browser import BrowserSession
from nova.config import Settings
from nova.database import Database, utc_now
from nova.events import EventBus
from nova.inference import Message, ModelError, ModelProvider, OllamaProvider
from nova.integrations.connections import Connections, IntegrationError
from nova.memory import ConversationStore, MemoryStore
from nova.memory.embeddings import OllamaEmbedder
from nova.memory.extractor import MemoryExtractor
from nova.permissions import PermissionGate
from nova.permissions.audit import ActionLog
from nova.phone.devices import DeviceStore
from nova.phone.certs import Certificates
from nova.phone.service import PhoneAccess, qr_svg
from nova.phone.sync import Sync, SyncRequest
from nova.setup_check import SetupCheck
from nova.timeline import Timeline
from nova.timeline import describe as describe_timeline
from nova.scheduler import ReminderStore, Scheduler
from nova.settings import SettingsStore
from nova.tools import build_registry
from nova.vision import ScreenReader
from nova.voice import VoiceService

# Windows does not know this type; Android Chrome wants it for the home-screen app.
mimetypes.add_type("application/manifest+json", ".webmanifest")

_HEARTBEAT_SECONDS = 15.0
_NDJSON_HEADERS = {"Cache-Control": "no-store"}


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None
    # Spoken by the user; the reply will be read aloud, so it should be short plain speech.
    voice: bool = False
    # The user pressed the screen button: look at their window before answering.
    screen: bool = False


class VoiceSettingsRequest(BaseModel):
    wake_word: bool


class SpeakRequest(BaseModel):
    text: str = Field(min_length=1, max_length=8000)


class GoogleClientRequest(BaseModel):
    # The contents of the client file the user downloaded from Google Cloud.
    client: str = Field(min_length=2, max_length=20000)


class GitHubConnectRequest(BaseModel):
    token: str | None = Field(default=None, max_length=500)
    # Use the token of the GitHub CLI the user is signed in to.
    from_cli: bool = False


class DaySummaryRequest(BaseModel):
    day: date


class PhoneAccessRequest(BaseModel):
    enabled: bool


class PairRequest(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    name: str = Field(default="Phone", max_length=60)


# Routes a phone may never use: account credentials, the PC's microphone and speakers, and phone
# access itself (a phone must not be able to pair more phones or remove the PC's control).
_DESKTOP_ONLY = ("/api/connections", "/api/voice", "/api/phone", "/api/setup")
# Pages served to phones: the app's own scripts and styles, and nowhere else to talk to.
_PHONE_CSP = (
    "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"
)


class ConfirmationRequest(BaseModel):
    approved: bool


class SnoozeRequest(BaseModel):
    minutes: int = Field(default=10, ge=1, le=1440)


# Events about phone access itself (paired phones, the network) are for the PC's panel only.
_DESKTOP_EVENTS = frozenset({"phones"})


async def event_stream(
    bus: EventBus, reminders: ReminderStore, heartbeat: float = _HEARTBEAT_SECONDS, phone: bool = False
) -> AsyncIterator[str]:
    """Everything a client should be told outside a chat turn, as NDJSON lines.

    Subscribes before listing unacknowledged reminders, so one that fires in
    between is not lost; the client may see it twice and dedupes by id.
    """
    async with bus.subscribe() as queue:
        for reminder in reminders.pending():
            yield json.dumps({"type": "reminder", "reminder": reminder.to_dict()}) + "\n"
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=heartbeat)
            except TimeoutError:
                # Lets both ends notice a dead connection.
                event = {"type": "ping"}
            if phone and event.get("type") in _DESKTOP_EVENTS:
                continue
            yield json.dumps(event) + "\n"


def create_app(
    settings: Settings, provider: ModelProvider | None = None, connections: Connections | None = None
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings.database_path)
        model = provider or OllamaProvider(
            settings.ollama_url,
            settings.model,
            think=settings.think,
            keep_alive=settings.keep_alive,
            num_ctx=settings.num_ctx,
        )
        embedder = (
            OllamaEmbedder(settings.ollama_url, settings.embed_model, keep_alive=settings.keep_alive)
            if settings.embed_model
            else None
        )
        # One model for everything when the chat model can also see; otherwise a second
        # provider with a smaller context, loaded only for screen questions.
        vision = None
        if settings.vision_model:
            vision = (
                model
                if settings.vision_model == settings.model and hasattr(model, "see")
                else OllamaProvider(
                    settings.ollama_url,
                    settings.vision_model,
                    keep_alive=settings.keep_alive,
                    num_ctx=settings.vision_num_ctx,
                )
            )
        screen = ScreenReader(vision) if vision else None
        browser = BrowserSession(settings.data_dir / "browser-profile") if settings.browser else None
        bus = EventBus()
        store = ConversationStore(db)
        memory = MemoryStore(db, embedder)
        reminders = ReminderStore(db)
        scheduler = Scheduler(reminders, bus)
        gate = PermissionGate(settings.confirm_timeout)
        actions = ActionLog(db)
        timeline = Timeline(db)
        registry = build_registry(memory, reminders, scheduler.poke, screen, browser, timeline=timeline)
        accounts = connections or Connections(settings.data_dir)
        accounts.sync(registry)
        agent = Agent(
            model,
            registry,
            gate,
            store,
            memory=memory,
            extractor=MemoryExtractor(model, memory) if settings.auto_memory else None,
            actions=actions,
            bus=bus,
            screen=screen,
            max_steps=settings.max_steps,
            history_limit=settings.history_limit,
            num_ctx=settings.num_ctx,
            prompt_note=accounts.prompt_note,
        )
        scheduler.run_task = agent.run_task
        app.state.connections = accounts
        app.state.registry = registry
        app.state.provider = model
        setup = SetupCheck(
            settings.ollama_url,
            {"chat": settings.model, "vision": settings.vision_model, "embed": settings.embed_model},
        )
        app.state.setup = setup
        app.state.vision = vision
        app.state.bus = bus
        app.state.store = store
        app.state.memory = memory
        app.state.reminders = reminders
        app.state.scheduler = scheduler
        app.state.gate = gate
        app.state.actions = actions
        app.state.agent = agent
        app.state.timeline = timeline
        app.state.sync = Sync(db, memory, reminders, actions)
        user_settings = SettingsStore(db)
        voice = VoiceService(bus, user_settings, settings.models_dir) if settings.voice else None
        app.state.voice = voice
        phone = PhoneAccess(
            app,
            user_settings,
            DeviceStore(db),
            Certificates(settings.data_dir / "phone"),
            settings.phone_port,
            settings.phone_secure_port,
            settings.phone_host,
        )
        phone.on_change = lambda: bus.publish({"type": "phones", "status": phone.status()})
        app.state.phone = phone
        scheduler.start()
        if voice:
            await voice.start()
        if phone.enabled:
            await phone.start()
        try:
            yield
        finally:
            await phone.stop()
            if voice:
                await voice.close()
            if browser:
                await browser.close()
            await scheduler.stop()
            await agent.drain()
            if embedder:
                await embedder.aclose()
            if vision is not None and vision is not model:
                await vision.aclose()
            await model.aclose()
            await setup.aclose()
            db.close()

    def authorize(request: Request) -> None:
        """Who is asking, decided by the listener the request arrived on.

        The desktop's loopback listener needs the launch token. The phone listener needs a
        private-network sender and a paired phone's key, and never reaches desktop-only routes.
        `request.state.device` is the phone, or None for the desktop.
        """
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        bearer = token if scheme.lower() == "bearer" else ""
        path = request.url.path
        phone: PhoneAccess | None = getattr(request.app.state, "phone", None)
        request.state.device = None
        if phone is not None and phone.serves(request.scope.get("server")):
            if not phone.client_allowed(request.scope.get("server"), request.scope.get("client")):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "Phone access is for your own network and devices only")
            if not path.startswith("/api/") or path == "/api/pair":
                return  # the phone app's own files, and pairing, which checks its code
            if path.startswith(_DESKTOP_ONLY):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "This can only be done on the PC")
            device = phone.devices.authenticate(bearer)
            if device is None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This phone is not paired, or was removed")
            request.state.device = device
            return
        if not secrets.compare_digest(bearer, settings.api_token):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid token")

    app = FastAPI(
        title="NOVA",
        version=__version__,
        lifespan=lifespan,
        dependencies=[Depends(authorize)],
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/api/health")
    async def health(request: Request) -> dict:
        model = await request.app.state.provider.status()
        vision = request.app.state.vision
        vision_status = await vision.status() if vision is not None else None
        return {
            "status": "ok",
            "version": __version__,
            "model": model.model,
            "model_ready": model.ready,
            "detail": model.detail,
            # Whether NOVA can look at the screen; the UI shows the screen button only then.
            "vision_ready": bool(vision_status and vision_status.ready),
        }

    @app.get("/api/setup")
    async def setup_status(request: Request) -> dict:
        """What a first run still needs: Ollama, and which of NOVA's models it has."""
        return await request.app.state.setup.status()

    @app.post("/api/setup/pull/{role}")
    async def setup_pull(role: str, request: Request) -> StreamingResponse:
        """Download one of NOVA's models through Ollama (the user pressed its button), as NDJSON progress."""

        async def lines() -> AsyncIterator[str]:
            async for event in request.app.state.setup.pull(role):
                yield json.dumps(event) + "\n"

        return StreamingResponse(lines(), media_type="application/x-ndjson", headers=_NDJSON_HEADERS)

    @app.post("/api/warmup", status_code=status.HTTP_202_ACCEPTED)
    async def warmup(request: Request, background: BackgroundTasks, vision: bool = False) -> dict:
        """Called when the launcher opens (or the screen button is armed, with ?vision=true),
        so the right model is loading while the user types."""
        target = request.app.state.vision if vision and request.app.state.vision else request.app.state.provider
        background.add_task(target.warm)
        return {"status": "warming"}

    @app.post("/api/chat")
    async def chat(body: ChatRequest, request: Request) -> StreamingResponse:
        store: ConversationStore = request.app.state.store
        agent: Agent = request.app.state.agent
        conversation_id = body.conversation_id
        if conversation_id is None:
            conversation_id = store.create_conversation()
        elif not store.conversation_exists(conversation_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown conversation")

        # From a phone: no spoken reply on the PC's speakers, and no screen button.
        from_phone = request.state.device is not None
        voice, screen = body.voice and not from_phone, body.screen and not from_phone

        async def events() -> AsyncIterator[str]:
            yield json.dumps({"type": "conversation", "id": conversation_id}) + "\n"
            async for event in agent.run_turn(conversation_id, body.message.strip(), voice=voice, screen=screen):
                yield json.dumps(event) + "\n"

        return StreamingResponse(events(), media_type="application/x-ndjson", headers=_NDJSON_HEADERS)

    @app.post("/api/confirmations/{call_id}")
    async def confirm(call_id: str, body: ConfirmationRequest, request: Request) -> dict:
        if not request.app.state.gate.resolve(call_id, body.approved):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No action is waiting for this confirmation")
        return {"status": "ok"}

    @app.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        return StreamingResponse(
            event_stream(request.app.state.bus, request.app.state.reminders, phone=request.state.device is not None),
            media_type="application/x-ndjson",
            headers=_NDJSON_HEADERS,
        )

    # --- memory --------------------------------------------------------------

    @app.get("/api/memories")
    async def list_memories(request: Request) -> dict:
        return {"memories": [memory.to_dict() for memory in request.app.state.memory.all()]}

    @app.delete("/api/memories/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_memory(memory_id: int, request: Request) -> Response:
        # The user clicked Forget on this exact memory; that click is the confirmation.
        if not request.app.state.memory.delete(memory_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such memory")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # --- reminders -----------------------------------------------------------

    @app.get("/api/reminders")
    async def list_reminders(request: Request) -> dict:
        reminders: ReminderStore = request.app.state.reminders
        return {
            "upcoming": [r.to_dict() for r in reminders.upcoming()],
            "pending": [r.to_dict() for r in reminders.pending()],
        }

    @app.post("/api/reminders/{reminder_id}/dismiss")
    async def dismiss_reminder(reminder_id: int, request: Request) -> dict:
        if not request.app.state.reminders.dismiss(reminder_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such reminder")
        return {"status": "ok"}

    @app.post("/api/reminders/{reminder_id}/cancel")
    async def cancel_reminder(reminder_id: int, request: Request) -> dict:
        # The user clicked Cancel on this exact reminder or task; that click is the confirmation.
        reminder = request.app.state.reminders.cancel(reminder_id)
        if reminder is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such reminder")
        request.app.state.scheduler.poke()
        return {"reminder": reminder.to_dict()}

    @app.post("/api/reminders/{reminder_id}/snooze")
    async def snooze_reminder(reminder_id: int, body: SnoozeRequest, request: Request) -> dict:
        reminder = request.app.state.reminders.snooze(reminder_id, body.minutes, utc_now())
        if reminder is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such reminder")
        request.app.state.scheduler.poke()
        return {"reminder": reminder.to_dict()}

    # --- voice ---------------------------------------------------------------

    def voice_service(request: Request) -> VoiceService:
        voice: VoiceService | None = request.app.state.voice
        if voice is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Voice is turned off (NOVA_VOICE=false)")
        return voice

    @app.get("/api/voice")
    async def voice_status(request: Request) -> dict:
        voice: VoiceService | None = request.app.state.voice
        if voice is None:
            return {"available": False, "detail": "Voice is turned off.", "wake_word": False, "state": "off", "can_speak": False}
        return voice.status()

    @app.post("/api/voice/settings")
    async def voice_settings(body: VoiceSettingsRequest, request: Request) -> dict:
        voice = voice_service(request)
        if body.wake_word and not voice.availability()[0]:
            raise HTTPException(status.HTTP_409_CONFLICT, voice.availability()[1])
        voice.set_wake_word(body.wake_word)
        return voice.status()

    @app.post("/api/voice/listen")
    async def voice_listen(request: Request) -> dict:
        voice = voice_service(request)
        if not voice.availability()[0]:
            raise HTTPException(status.HTTP_409_CONFLICT, voice.availability()[1])
        voice.listen_once()
        return voice.status()

    @app.post("/api/voice/speak", status_code=status.HTTP_202_ACCEPTED)
    async def voice_speak(body: SpeakRequest, request: Request) -> dict:
        voice_service(request).speak(body.text)
        return {"status": "speaking"}

    @app.post("/api/voice/stop")
    async def voice_stop(request: Request) -> dict:
        voice = voice_service(request)
        voice.stop()
        return voice.status()

    # --- transparency --------------------------------------------------------

    # --- connected accounts ---------------------------------------------------------
    # Credentials only ever travel from the user's own launcher to here, and go straight into the
    # vault. They are never logged, never returned, and never shown to the model.

    def connections_changed(request: Request) -> None:
        request.app.state.connections.sync(request.app.state.registry)
        request.app.state.bus.publish({"type": "connections", "status": request.app.state.connections.status()})

    @app.get("/api/connections")
    async def connection_status(request: Request) -> dict:
        return request.app.state.connections.status()

    @app.post("/api/connections/google/client")
    async def google_client(body: GoogleClientRequest, request: Request) -> dict:
        try:
            request.app.state.connections.google.set_client(body.client)
        except IntegrationError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        return request.app.state.connections.status()

    @app.post("/api/connections/google/connect")
    async def google_connect(request: Request) -> dict:
        """Open Google's consent page in the user's browser; the result arrives as a `connections` event."""
        try:
            url = await request.app.state.connections.google.start_sign_in(on_done=lambda: connections_changed(request))
        except IntegrationError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        await asyncio.to_thread(webbrowser.open, url)
        return {"status": "signing_in"}

    @app.post("/api/connections/github")
    async def github_connect(body: GitHubConnectRequest, request: Request) -> dict:
        try:
            await request.app.state.connections.github.connect(body.token, from_cli=body.from_cli)
        except IntegrationError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        connections_changed(request)
        return request.app.state.connections.status()

    @app.delete("/api/connections/{service}")
    async def disconnect(service: str, request: Request) -> dict:
        accounts: Connections = request.app.state.connections
        if service == "google":
            await accounts.google.disconnect()
        elif service == "github":
            accounts.github.disconnect()
        else:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such service")
        connections_changed(request)
        return accounts.status()

    @app.get("/api/actions")
    async def recent_actions(request: Request, limit: int = 50) -> dict:
        return {"actions": request.app.state.actions.recent(min(max(limit, 1), 500))}

    # --- phones ------------------------------------------------------------------------

    def phones_changed(request: Request) -> None:
        request.app.state.bus.publish({"type": "phones", "status": request.app.state.phone.status()})

    @app.get("/api/phone")
    async def phone_status(request: Request) -> dict:
        return request.app.state.phone.status()

    @app.post("/api/phone")
    async def phone_toggle(body: PhoneAccessRequest, request: Request) -> dict:
        await request.app.state.phone.set_enabled(body.enabled)
        phones_changed(request)
        return request.app.state.phone.status()

    @app.post("/api/phone/code")
    async def phone_code(request: Request) -> dict:
        """A new six-digit code for pairing a phone, shown on the PC."""
        phone: PhoneAccess = request.app.state.phone
        if not phone.running:
            raise HTTPException(status.HTTP_409_CONFLICT, "Turn on phone access first")
        code, seconds = phone.pairing.new()
        link = phone.pairing_link(code)
        return {"code": code, "expires_in": seconds, "url": phone.url, "qr": qr_svg(link) if link else None}

    @app.delete("/api/phone/devices/{device_id}")
    async def phone_remove(device_id: int, request: Request) -> dict:
        if not request.app.state.phone.devices.remove(device_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such phone")
        phones_changed(request)
        return request.app.state.phone.status()

    @app.post("/api/pair")
    async def pair(body: PairRequest, request: Request) -> dict:
        """The phone side of pairing: the code shown on the PC buys this phone its own key."""
        phone: PhoneAccess = request.app.state.phone
        if not phone.serves(request.scope.get("server")):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Pair from the phone app")
        if not phone.pairing.redeem(body.code):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "That code is wrong or has expired. Make a new one on the PC.")
        device, token = phone.devices.add(body.name)
        phones_changed(request)
        return {"token": token, "device": device.to_dict()}

    @app.get("/api/timeline")
    async def timeline_days(request: Request, last: date | None = None, days: int = 7) -> dict:
        """Days of the life timeline, newest first, ending with last (default: today)."""
        return {"days": request.app.state.timeline.days(last or date.today(), min(max(days, 1), 31))}

    @app.post("/api/timeline/summary")
    async def timeline_summary(body: DaySummaryRequest, request: Request) -> dict:
        """A few sentences about one day, written by the local model when asked."""
        entries = request.app.state.timeline.between(body.day, body.day)
        if not entries:
            return {"summary": "Nothing happened with NOVA that day."}
        # The model is told how to name the day, rather than left to work out "today" from a date.
        ago = (date.today() - body.day).days
        name = "today" if ago == 0 else "yesterday" if ago == 1 else f"on {body.day:%A} {body.day.day} {body.day:%B}"
        prompt = (
            f"Here is what the user did with their assistant NOVA {name}:\n"
            f"{describe_timeline(entries, limit=60)}\n\n"
            "Write two or three friendly sentences to the user (\"you\") about that day: what they worked on "
            f"and what NOVA helped with. Refer to the day as \"{name}\". Only use what is listed. No lists, no times."
        )
        try:
            reply = await request.app.state.provider.complete_json(
                [Message(role="user", content=prompt)],
                {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
            )
        except ModelError as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        text = reply.get("summary") if isinstance(reply, dict) else None
        return {"summary": str(text or "").strip() or "NOVA could not sum up that day."}

    @app.post("/api/sync")
    async def sync(body: SyncRequest, request: Request) -> dict:
        """A phone's offline changes in, a fresh copy of memories and reminders out."""
        device = request.state.device
        results = await request.app.state.sync.apply(body.ops, device.name if device else None)
        if body.ops:
            request.app.state.scheduler.poke()
        return {"results": results, "snapshot": request.app.state.sync.snapshot()}

    # --- the phone app's files ---------------------------------------------------------

    ui = settings.phone_ui_dir

    @app.get("/", include_in_schema=False)
    async def phone_home() -> RedirectResponse:
        return RedirectResponse("/phone")

    @app.get("/phone", include_in_schema=False)
    async def phone_page() -> Response:
        page = ui / "phone.html"
        if not page.is_file():
            return Response("The phone app has not been built. Run: pnpm --dir apps/desktop build", status_code=503)
        return FileResponse(page, headers={"Cache-Control": "no-cache"})

    @app.middleware("http")
    async def phone_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", _PHONE_CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.path == "/phone-sw.js":
            response.headers["Cache-Control"] = "no-cache"  # a new app version reaches phones at once
        return response

    if ui.is_dir():
        # Scripts, styles and icons. Added last, so every route above wins.
        app.mount("/", StaticFiles(directory=ui), name="phone-ui")

    return app
