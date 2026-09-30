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
    # Voice input and output. The microphone only opens when the user turns it on.
    voice: bool = True
    models_dir: Path = field(default_factory=_default_models_dir)
    max_steps: int = 8
    history_limit: int = 40
    confirm_timeout: float = 120.0
    allowed_origins: tuple[str, ...] = DEFAULT_ORIGINS
    parent_pid: int | None = None

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
            voice=_env_bool("NOVA_VOICE", cls.voice),
            models_dir=Path(os.environ["NOVA_MODELS_DIR"]) if os.environ.get("NOVA_MODELS_DIR") else _default_models_dir(),
            allowed_origins=(
                tuple(o.strip() for o in origins.split(",") if o.strip())
                if origins
                else DEFAULT_ORIGINS
            ),
            parent_pid=int(parent_pid) if parent_pid else None,
        )
