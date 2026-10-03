"""Runtime settings, read once from the environment."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

# Origins the desktop webview is served from: the Tauri production scheme on
# Windows and the Next.js dev server.
DEFAULT_ORIGINS = (
    "http://tauri.localhost",
    "https://tauri.localhost",
    "tauri://localhost",
    "http://localhost:3000",
)


def _default_models_dir() -> Path:
    # backend/models. While NOVA runs from its source checkout, voice models live
    # beside the code, outside AppData, so every way of starting NOVA finds them.
    return Path(__file__).resolve().parents[1] / "models"


def _default_phone_ui_dir() -> Path:
    # The desktop app's static export, which also contains the phone page (/phone).
    return Path(__file__).resolve().parents[2] / "apps" / "desktop" / "out"


def _default_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "NOVA"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    api_token: str
    data_dir: Path
    host: str = "127.0.0.1"
    port: int = 8765
    ollama_url: str = "http://127.0.0.1:11434"
    model: str = "qwen3:8b"
    think: bool = False
    keep_alive: str = "10m"
    num_ctx: int = 8192
    # Empty disables embeddings; memory then matches by keywords.
    embed_model: str = "embeddinggemma"
    # Notice lasting facts in conversation and save them without being asked.
    auto_memory: bool = True
    # Reads screenshots. Empty disables looking at the screen.
    vision_model: str = "qwen3-vl:8b"
    # A screenshot plus a short answer fits in 4096 tokens. At 8192 qwen3-vl:8b did not
    # fit the 8 GB GPU (20% ran on the CPU) and answers took 9.5 s instead of 3.4 s.
    vision_num_ctx: int = 4096
    # Web tools in NOVA's own Edge window (a separate, signed-out profile).
    browser: bool = True
    # Voice input and output. The microphone only opens when the user turns it on.
    voice: bool = True
    models_dir: Path = field(default_factory=_default_models_dir)
    # Speech recognition on the CPU, from <models_dir>/whisper: tiny.en spots "Hey Nova"; the
    # command model hears what follows and the phone app's recordings. small.en replaced base.en
    # after base.en misheard the user's phone recordings; base.en is used when small.en is absent.
    whisper_wake_model: str = "tiny.en"
    whisper_command_model: str = "small.en"
    whisper_command_fallback: str = "base.en"
    # Phone access (off until the user turns it on): the setup page's port and the app's HTTPS
    # port on the home network, an address to use instead of the one NOVA picks, and where the
    # built phone app is.
    phone_port: int = 8766
    phone_secure_port: int = 8767
    phone_host: str | None = None
    phone_ui_dir: Path = field(default_factory=_default_phone_ui_dir)
    # The phone's pocket model (Phase 8), run in the phone's browser by web-llm when the PC is
    # away, and its compiled WebGPU program. Served from <models_dir>/pocket.
    pocket_model: str = "Qwen3-1.7B-q4f16_1-MLC"
    pocket_model_lib: str = "Qwen3-1.7B-q4f16_1_cs1k-webgpu.wasm"
    # For phones whose graphics chip lacks 16-bit maths (WebGPU shader-f16), as the user's does:
    # a smaller model in 32-bit maths.
    pocket_model_small: str = "Qwen3-0.6B-q4f32_1-MLC"
    pocket_model_small_lib: str = "Qwen3-0.6B-q4f32_1_cs1k-webgpu.wasm"
    max_steps: int = 8
    history_limit: int = 40
    confirm_timeout: float = 120.0
    allowed_origins: tuple[str, ...] = DEFAULT_ORIGINS
    parent_pid: int | None = None

    def whisper_models(self) -> tuple[str, str]:
        """The wake and command models to use: the configured command model if it is on disk,
        else the fallback, so an older install keeps working without the bigger download."""
        from nova.voice.stt import models_present

        command = self.whisper_command_model
        if models_present(self.models_dir, (command,)) and not models_present(self.models_dir, (self.whisper_command_fallback,)):
            command = self.whisper_command_fallback
        return self.whisper_wake_model, command

    @property
    def database_path(self) -> Path:
        return self.data_dir / "nova.db"

    @classmethod
    def from_env(cls) -> Settings:
        parent_pid = os.environ.get("NOVA_PARENT_PID")
        origins = os.environ.get("NOVA_ALLOWED_ORIGINS")
        return cls(
            api_token=os.environ.get("NOVA_API_TOKEN") or secrets.token_urlsafe(32),
            data_dir=Path(os.environ.get("NOVA_DATA_DIR") or _default_data_dir()),
            host=os.environ.get("NOVA_HOST", cls.host),
            port=int(os.environ.get("NOVA_PORT", cls.port)),
            ollama_url=os.environ.get("NOVA_OLLAMA_URL", cls.ollama_url),
            model=os.environ.get("NOVA_MODEL", cls.model),
            think=_env_bool("NOVA_THINK", cls.think),
            keep_alive=os.environ.get("NOVA_KEEP_ALIVE", cls.keep_alive),
            num_ctx=int(os.environ.get("NOVA_NUM_CTX", cls.num_ctx)),
            embed_model=os.environ.get("NOVA_EMBED_MODEL", cls.embed_model),
            auto_memory=_env_bool("NOVA_AUTO_MEMORY", cls.auto_memory),
            vision_model=os.environ.get("NOVA_VISION_MODEL", cls.vision_model),
            browser=_env_bool("NOVA_BROWSER", cls.browser),
            voice=_env_bool("NOVA_VOICE", cls.voice),
            whisper_command_model=os.environ.get("NOVA_WHISPER_COMMAND_MODEL", cls.whisper_command_model),
            models_dir=Path(os.environ["NOVA_MODELS_DIR"]) if os.environ.get("NOVA_MODELS_DIR") else _default_models_dir(),
            phone_port=int(os.environ.get("NOVA_PHONE_PORT", cls.phone_port)),
            phone_secure_port=int(os.environ.get("NOVA_PHONE_SECURE_PORT", cls.phone_secure_port)),
            phone_host=os.environ.get("NOVA_PHONE_HOST") or None,
            pocket_model=os.environ.get("NOVA_POCKET_MODEL", cls.pocket_model),
            pocket_model_lib=os.environ.get("NOVA_POCKET_MODEL_LIB", cls.pocket_model_lib),
            pocket_model_small=os.environ.get("NOVA_POCKET_MODEL_SMALL", cls.pocket_model_small),
            pocket_model_small_lib=os.environ.get("NOVA_POCKET_MODEL_SMALL_LIB", cls.pocket_model_small_lib),
            phone_ui_dir=Path(os.environ["NOVA_PHONE_UI_DIR"]) if os.environ.get("NOVA_PHONE_UI_DIR") else _default_phone_ui_dir(),
            allowed_origins=(
                tuple(o.strip() for o in origins.split(",") if o.strip())
                if origins
                else DEFAULT_ORIGINS
            ),
            parent_pid=int(parent_pid) if parent_pid else None,
        )
