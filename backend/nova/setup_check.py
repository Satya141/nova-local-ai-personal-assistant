"""First run: is Ollama there, and does it have NOVA's models? Fetch them when the user asks.

An installed NOVA brings its own Python, libraries and voice models, but not Ollama or the
language models (several gigabytes, and Ollama is a separate program). The launcher shows what
is missing and offers a button per model; nothing is downloaded until it is pressed. Only the
models named in the settings can be fetched this way, never a name sent by a page.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx

log = logging.getLogger(__name__)

# What each model is for, in the user's words. Only "chat" is required.
PURPOSES = {
    "chat": "Understands you and acts. Required.",
    "vision": "Reads your screen when you ask. Optional.",
    "embed": "Finds memories by meaning. Optional.",
}


def _tag(name: str) -> str:
    return name if ":" in name else f"{name}:latest"


class SetupCheck:
    def __init__(self, ollama_url: str, models: dict[str, str], client: httpx.AsyncClient | None = None) -> None:
        self._url = ollama_url
        self._models = {role: name for role, name in models.items() if name}
        self._client = client or httpx.AsyncClient(base_url=ollama_url, timeout=None)
        self._pulling: set[str] = set()

    async def status(self) -> dict[str, Any]:
        try:
            response = await self._client.get("/api/tags", timeout=3.0)
            response.raise_for_status()
            installed = {m.get("name") for m in response.json().get("models", [])}
            running = True
        except (httpx.HTTPError, ValueError):
            installed, running = set(), False
        models = [
            {
                "role": role,
                "name": name,
                "purpose": PURPOSES.get(role, ""),
                "required": role == "chat",
                "present": _tag(name) in installed,
                "downloading": role in self._pulling,
            }
            for role, name in self._models.items()
        ]
        return {
            "ollama": running,
            "ollama_url": self._url,
            "models": models,
            "ready": running and all(m["present"] for m in models if m["required"]),
        }

    async def pull(self, role: str) -> AsyncIterator[dict[str, Any]]:
        """Download one of NOVA's models through Ollama, as progress events."""
        name = self._models.get(role)
        if name is None:
            yield {"error": f"NOVA has no {role} model set."}
            return
        if role in self._pulling:
            yield {"error": f"{name} is already downloading."}
            return
        self._pulling.add(role)
        try:
            async with self._client.stream("POST", "/api/pull", json={"model": name, "stream": True}) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "replace")
                    yield {"error": f"Ollama refused: {body[:200]}"}
                    return
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if "error" in event:
                        yield {"error": str(event["error"])}
                        return
                    yield {
                        "status": str(event.get("status", "")),
                        "total": int(event.get("total") or 0),
                        "completed": int(event.get("completed") or 0),
                    }
            yield {"status": "success", "done": True}
        except httpx.HTTPError as exc:
            log.warning("Downloading %s failed: %s", name, exc)
            yield {"error": f"Could not reach Ollama at {self._url}. Is it running?"}
        finally:
            self._pulling.discard(role)

    async def aclose(self) -> None:
        await self._client.aclose()
