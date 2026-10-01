"""Phase 10: the first-run check, against a pretend Ollama."""

from __future__ import annotations

import json

import httpx

from nova.setup_check import SetupCheck

MODELS = {"chat": "qwen3:8b", "vision": "qwen3-vl:8b", "embed": "embeddinggemma"}


def ollama(installed: list[str], pulled: list[str] | None = None) -> httpx.AsyncClient:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": n} for n in installed]})
        if request.url.path == "/api/pull":
            name = json.loads(request.content)["model"]
            if pulled is not None:
                pulled.append(name)
            if name == "missing:model":
                return httpx.Response(200, text=json.dumps({"error": "pull model manifest: file does not exist"}) + "\n")
            lines = [
                {"status": "pulling manifest"},
                {"status": "pulling abc", "total": 1000, "completed": 400},
                {"status": "pulling abc", "total": 1000, "completed": 1000},
                {"status": "success"},
            ]
            return httpx.Response(200, text="".join(json.dumps(line) + "\n" for line in lines))
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handle), base_url="http://ollama")


async def test_a_first_run_lists_what_is_missing():
    check = SetupCheck("http://ollama", MODELS, ollama(["embeddinggemma:latest"]))
    status = await check.status()
    assert status["ollama"] and not status["ready"]
    by_role = {m["role"]: m for m in status["models"]}
    assert by_role["embed"]["present"], "a name without a tag means :latest"
    assert not by_role["chat"]["present"] and by_role["chat"]["required"]
    assert not by_role["vision"]["required"]


async def test_ready_once_the_chat_model_is_there():
    check = SetupCheck("http://ollama", MODELS, ollama(["qwen3:8b"]))
    assert (await check.status())["ready"], "the optional models may come later"


async def test_ollama_not_running_is_said_plainly():
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(refuse), base_url="http://ollama")
    status = await SetupCheck("http://ollama", MODELS, client).status()
    assert status == {**status, "ollama": False, "ready": False}
    events = [e async for e in SetupCheck("http://ollama", MODELS, client).pull("chat")]
    assert "Could not reach Ollama" in events[-1]["error"]


async def test_pulling_reports_progress_and_only_for_novas_own_models():
    pulled: list[str] = []
    check = SetupCheck("http://ollama", MODELS, ollama([], pulled))
    events = [e async for e in check.pull("chat")]
    assert pulled == ["qwen3:8b"]
    assert {"status": "pulling abc", "total": 1000, "completed": 400} in events
    assert events[-1] == {"status": "success", "done": True}

    refused = [e async for e in check.pull("anything-else")]
    assert "error" in refused[0] and pulled == ["qwen3:8b"], "no download for a role NOVA does not have"


async def test_an_ollama_error_ends_the_download():
    check = SetupCheck("http://ollama", {"chat": "missing:model"}, ollama([]))
    events = [e async for e in check.pull("chat")]
    assert events == [{"error": "pull model manifest: file does not exist"}]


async def test_switched_off_models_are_not_offered():
    check = SetupCheck("http://ollama", {"chat": "qwen3:8b", "vision": "", "embed": ""}, ollama(["qwen3:8b"]))
    assert [m["role"] for m in (await check.status())["models"]] == ["chat"]
