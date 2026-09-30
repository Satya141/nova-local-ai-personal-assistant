"""Text embeddings for finding memories by meaning."""

from __future__ import annotations

import logging
import math
from array import array
from typing import Literal, Protocol

import httpx

log = logging.getLogger(__name__)

Kind = Literal["query", "document"]


class Embedder(Protocol):
    name: str

    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]] | None:
        """Vectors for `texts`, or None when embeddings are unavailable right now."""
        ...

    async def aclose(self) -> None: ...


class OllamaEmbedder:
    """Embeds with a local Ollama model, on the CPU.

    The chat model fills the GPU. Loading the embedder there too would make
    Ollama evict one of them on every turn; on the CPU both stay loaded.
    """

    def __init__(self, base_url: str, model: str, *, keep_alive: str = "10m") -> None:
        self.name = model
        self._keep_alive = keep_alive
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=httpx.Timeout(60.0, connect=5.0))
        self._warned = False

    def _prompt(self, text: str, kind: Kind) -> str:
        # EmbeddingGemma was trained with these task prompts; other models ignore them harmlessly.
        if "embeddinggemma" in self.name:
            return f"task: search result | query: {text}" if kind == "query" else f"title: none | text: {text}"
        return text

    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]] | None:
        if not texts:
            return []
        payload = {
            "model": self.name,
            "input": [self._prompt(text, kind) for text in texts],
            "keep_alive": self._keep_alive,
            "options": {"num_gpu": 0},
        }
        try:
            response = await self._client.post("/api/embed", json=payload)
            response.raise_for_status()
            vectors = response.json()["embeddings"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            if not self._warned:
                log.warning("Embeddings unavailable (%s); memory falls back to keyword matching", exc)
                self._warned = True
            return None
        self._warned = False
        return vectors

    async def aclose(self) -> None:
        await self._client.aclose()


def pack(vector: list[float]) -> bytes:
    return array("f", vector).tobytes()


def unpack(blob: bytes) -> list[float]:
    values = array("f")
    values.frombytes(blob)
    return values.tolist()


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0
