"""The HTTP API the desktop and (later) mobile clients talk to.

It listens on loopback only, and every route requires the bearer token that
the desktop shell generated when it started this process. Without that, any
web page open in the user's browser could drive the agent.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from nova import __version__
from nova.agent import Agent
from nova.browser import BrowserSession
from nova.config import Settings
from nova.database import Database, utc_now
from nova.events import EventBus
from nova.inference import ModelProvider, OllamaProvider
from nova.memory import ConversationStore, MemoryStore
from nova.memory.embeddings import OllamaEmbedder
from nova.memory.extractor import MemoryExtractor
from nova.permissions import PermissionGate
from nova.permissions.audit import ActionLog
from nova.scheduler import ReminderStore, Scheduler
from nova.settings import SettingsStore
from nova.tools import build_registry
from nova.vision import ScreenReader
from nova.voice import VoiceService

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


class ConfirmationRequest(BaseModel):
    approved: bool


class SnoozeRequest(BaseModel):
    minutes: int = Field(default=10, ge=1, le=1440)


async def event_stream(
    bus: EventBus, reminders: ReminderStore, heartbeat: float = _HEARTBEAT_SECONDS
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
            yield json.dumps(event) + "\n"


def create_app(settings: Settings, provider: ModelProvider | None = None) -> FastAPI:
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
        agent = Agent(
            model,
            build_registry(memory, reminders, scheduler.poke, screen, browser),
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
        )
        scheduler.run_task = agent.run_task
        app.state.provider = model
        app.state.vision = vision
        app.state.bus = bus
        app.state.store = store
        app.state.memory = memory
        app.state.reminders = reminders
        app.state.scheduler = scheduler
        app.state.gate = gate
        app.state.actions = actions
        app.state.agent = agent
        voice = VoiceService(bus, SettingsStore(db), settings.models_dir) if settings.voice else None
        app.state.voice = voice
        scheduler.start()
        if voice:
            await voice.start()
        try:
            yield
        finally:
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
            db.close()

    def require_token(request: Request) -> None:
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(token, settings.api_token):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing or invalid token")

    app = FastAPI(
        title="NOVA",
        version=__version__,
        lifespan=lifespan,
        dependencies=[Depends(require_token)],
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

        async def events() -> AsyncIterator[str]:
            yield json.dumps({"type": "conversation", "id": conversation_id}) + "\n"
            async for event in agent.run_turn(
                conversation_id, body.message.strip(), voice=body.voice, screen=body.screen
            ):
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
            event_stream(request.app.state.bus, request.app.state.reminders),
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

    @app.get("/api/actions")
    async def recent_actions(request: Request, limit: int = 50) -> dict:
        return {"actions": request.app.state.actions.recent(min(max(limit, 1), 500))}

    return app
