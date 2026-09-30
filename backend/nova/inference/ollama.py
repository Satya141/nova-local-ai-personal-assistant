"""Model provider backed by a local Ollama server."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from nova.inference.base import ChatChunk, Message, ModelError, ModelStatus, ToolCall


def _to_wire(message: Message) -> dict[str, Any]:
    wire: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        wire["tool_calls"] = [
            {"function": {"name": call.name, "arguments": call.arguments}}
            for call in message.tool_calls
        ]
    if message.tool_name:
        wire["tool_name"] = message.tool_name
    return wire


def _parse_tool_calls(raw: list[dict[str, Any]] | None) -> tuple[ToolCall, ...]:
    calls = []
    for item in raw or []:
        function = item.get("function") or {}
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        calls.append(
            ToolCall(
                id=item.get("id") or uuid.uuid4().hex,
                name=function.get("name", ""),
                arguments=arguments if isinstance(arguments, dict) else {},
            )
        )
    return tuple(calls)


class OllamaProvider:
    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        think: bool = False,
        keep_alive: str = "10m",
        num_ctx: int = 8192,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._think = think
        self._keep_alive = keep_alive
        # Sent on every request, including warm-up: Ollama reloads the model
        # whenever the context size changes.
        self._options = {"num_ctx": num_ctx}
        # No read timeout: the first request after idle waits for the model to load.
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(connect=5.0, read=None, write=30.0, pool=5.0),
        )

    async def chat(
        self, messages: list[Message], tools: list[dict[str, Any]]
    ) -> AsyncIterator[ChatChunk]:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [_to_wire(m) for m in messages],
            "stream": True,
            "think": self._think,
            "keep_alive": self._keep_alive,
            "options": self._options,
        }
        if tools:
            payload["tools"] = tools

        try:
            async with self._client.stream("POST", "/api/chat", json=payload) as response:
                if response.status_code != 200:
                    raise ModelError(self._describe_failure(response.status_code, await response.aread()))
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    if "error" in data:
                        raise ModelError(f"Ollama error: {data['error']}")
                    message = data.get("message") or {}
                    text = message.get("content") or ""
                    calls = _parse_tool_calls(message.get("tool_calls"))
                    if text or calls:
                        yield ChatChunk(text=text, tool_calls=calls)
        except httpx.ConnectError as exc:
            raise ModelError(
                f"Cannot reach Ollama at {self._base_url}. Start Ollama and try again."
            ) from exc
        except httpx.HTTPError as exc:
            raise ModelError(f"Lost connection to Ollama: {exc}") from exc

    def _describe_failure(self, status: int, body: bytes) -> str:
        try:
            detail = json.loads(body).get("error", "")
        except (json.JSONDecodeError, AttributeError):
            detail = body.decode("utf-8", "replace")[:200]
        if status == 404:
            return f"Model '{self._model}' is not installed. Run: ollama pull {self._model}"
        return f"Ollama returned HTTP {status}: {detail}"

    async def complete_json(self, messages: list[Message], schema: dict[str, Any]) -> Any:
        payload = {
            "model": self._model,
            "messages": [_to_wire(m) for m in messages],
            "stream": False,
            "think": False,
            "format": schema,
            "keep_alive": self._keep_alive,
            # Deterministic: the same message should always yield the same memories.
            "options": {**self._options, "temperature": 0},
        }
        try:
            response = await self._client.post("/api/chat", json=payload)
        except httpx.HTTPError as exc:
            raise ModelError(f"Cannot reach Ollama: {exc}") from exc
        if response.status_code != 200:
            raise ModelError(self._describe_failure(response.status_code, response.content))
        try:
            return json.loads(response.json()["message"]["content"])
        except (KeyError, ValueError) as exc:
            raise ModelError(f"The model did not return valid JSON: {exc}") from exc

    async def warm(self) -> None:
        # A chat request with no messages loads the model and returns immediately.
        payload = {
            "model": self._model,
            "messages": [],
            "keep_alive": self._keep_alive,
            "options": self._options,
        }
        try:
            await self._client.post("/api/chat", json=payload, timeout=120.0)
        except httpx.HTTPError:
            pass  # Best effort; a real failure is reported on the next chat.

    async def status(self) -> ModelStatus:
        try:
            response = await self._client.get("/api/tags", timeout=3.0)
            response.raise_for_status()
        except httpx.HTTPError:
            return ModelStatus(False, self._model, f"Ollama is not reachable at {self._base_url}.")
        installed = {m.get("name") for m in response.json().get("models", [])}
        wanted = self._model if ":" in self._model else f"{self._model}:latest"
        if wanted not in installed:
            return ModelStatus(
                False, self._model, f"Model '{self._model}' is not installed. Run: ollama pull {self._model}"
            )
        return ModelStatus(True, self._model)

    async def aclose(self) -> None:
        await self._client.aclose()
