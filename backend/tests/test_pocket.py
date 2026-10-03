"""Phase 8, part 2: the PC hands its phones the pocket model."""

from __future__ import annotations

import pytest
from conftest import FakeProvider, say
from fastapi.testclient import TestClient

from nova.api import create_app
from nova.config import Settings

DESKTOP = {"Authorization": "Bearer t"}


@pytest.fixture
def models(tmp_path):
    return tmp_path / "models"


@pytest.fixture
def client(tmp_path, models):
    settings = Settings(
        api_token="t", data_dir=tmp_path, embed_model="", voice=False, vision_model="", browser=False,
        models_dir=models, pocket_model="Tiny-MLC", pocket_model_lib="tiny-webgpu.wasm",
        pocket_model_small="Small-MLC", pocket_model_small_lib="small-webgpu.wasm",
    )
    with TestClient(create_app(settings, FakeProvider([[say("hi")]]))) as test_client:
        yield test_client


def put_model(models, model: str = "Tiny-MLC", lib: str = "tiny-webgpu.wasm") -> None:
    folder = models / "pocket" / model
    folder.mkdir(parents=True)
    (folder / "mlc-chat-config.json").write_text("{}", encoding="utf-8")
    (folder / "params_shard_0.bin").write_bytes(b"\0" * 1000)
    (models / "pocket" / "libs").mkdir(exist_ok=True)
    (models / "pocket" / "libs" / lib).write_bytes(b"\0asm")


def test_a_phone_without_16_bit_maths_gets_the_small_model(client, models):
    """The user's phone lacks WebGPU shader-f16, which the main model needs."""
    put_model(models)
    assert client.get("/api/pocket?f16=false", headers=DESKTOP).json()["available"] is False
    put_model(models, "Small-MLC", "small-webgpu.wasm")
    info = client.get("/api/pocket?f16=false", headers=DESKTOP).json()
    assert info == {"model": "Small-MLC", "lib": "/pocket/libs/small-webgpu.wasm", "available": True, "size": 1006}
    assert client.get("/pocket/Small-MLC/resolve/main/params_shard_0.bin", headers=DESKTOP).status_code == 200
    assert client.get(info["lib"], headers=DESKTOP).status_code == 200
    assert client.get("/api/pocket", headers=DESKTOP).json()["model"] == "Tiny-MLC", "the main one stays the default"


def test_nothing_is_offered_until_the_model_is_on_the_pc(client):
    info = client.get("/api/pocket", headers=DESKTOP).json()
    assert info == {"model": "Tiny-MLC", "lib": "/pocket/libs/tiny-webgpu.wasm", "available": False, "size": 0}


def test_the_model_is_served_as_web_llm_asks_for_it(client, models):
    put_model(models)
    info = client.get("/api/pocket", headers=DESKTOP).json()
    assert info["available"] and info["size"] == 1000 + 2 + 4
    shard = client.get("/pocket/Tiny-MLC/resolve/main/params_shard_0.bin", headers=DESKTOP)
    assert shard.status_code == 200 and len(shard.content) == 1000 and "immutable" in shard.headers["cache-control"]
    lib = client.get(info["lib"], headers=DESKTOP)
    assert lib.headers["content-type"] == "application/wasm"


@pytest.mark.parametrize(
    "path",
    [
        "/pocket/Other-MLC/resolve/main/mlc-chat-config.json",  # only the configured models
        "/pocket/Tiny-MLC/resolve/main/..%2F..%2Fnova.db",  # nothing outside its folder
        "/pocket/Tiny-MLC/resolve/main/.hidden",
        "/pocket/libs/other.wasm",
    ],
)
def test_nothing_else_is_served(client, models, path):
    put_model(models)
    (models / "pocket" / "Tiny-MLC" / ".hidden").write_text("x", encoding="utf-8")
    assert client.get(path, headers=DESKTOP).status_code == 404


def test_the_pc_listener_still_needs_its_token(client, models):
    put_model(models)
    assert client.get("/pocket/Tiny-MLC/resolve/main/mlc-chat-config.json").status_code == 401


def test_phone_pages_may_run_webassembly(client):
    assert "'wasm-unsafe-eval'" in client.get("/api/pocket", headers=DESKTOP).headers["content-security-policy"]
