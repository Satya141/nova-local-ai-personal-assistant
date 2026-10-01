from __future__ import annotations

import socket
import ssl

import httpx
import pytest
from conftest import FakeProvider, say
from fastapi.testclient import TestClient

from nova.api import create_app
from nova.config import Settings
from nova.phone.devices import CODE_ATTEMPTS, DeviceStore, PairingCode
from nova.phone.service import _rank

TOKEN = "desktop-token"
DESKTOP = {"Authorization": f"Bearer {TOKEN}"}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_pairing_code_works_once_within_five_minutes():
    clock = Clock()
    pairing = PairingCode(clock)
    code, seconds = pairing.new()
    assert len(code) == 6 and code.isdigit() and seconds == 300
    assert pairing.redeem(code)
    assert not pairing.redeem(code), "used up"

    code, _ = pairing.new()
    clock.now += 301
    assert not pairing.redeem(code), "expired"


def test_wrong_guesses_use_up_the_code():
    pairing = PairingCode()
    code, _ = pairing.new()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(CODE_ATTEMPTS):
        assert not pairing.redeem(wrong)
    assert not pairing.redeem(code), "after five wrong guesses even the right code is refused"


def test_only_a_hash_of_a_phones_key_is_stored(db):
    devices = DeviceStore(db)
    phone, token = devices.add("Pixel")
    stored = db.fetch_one("SELECT token_hash FROM devices WHERE id = ?", (phone.id,))["token_hash"]
    assert token not in stored and len(stored) == 64
    assert devices.authenticate(token).name == "Pixel"
    assert devices.authenticate("guess") is None and devices.authenticate("") is None
    assert devices.remove(phone.id) and devices.authenticate(token) is None


@pytest.mark.parametrize(
    ("address", "rank"),
    [("192.168.29.104", 0), ("10.0.0.5", 1), ("172.16.0.2", 2), ("127.0.0.1", -1), ("169.254.3.4", -1), ("8.8.8.8", -1)],
)
def test_the_home_network_address_is_preferred_over_vpns(address, rank):
    assert _rank(address) == rank


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def desktop(tmp_path):
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "phone.html").write_text("<!doctype html><title>NOVA</title>", encoding="utf-8")
    settings = Settings(
        api_token=TOKEN,
        data_dir=tmp_path,
        embed_model="",
        voice=False,
        vision_model="",
        browser=False,
        phone_host="127.0.0.1",
        phone_port=free_port(),
        phone_secure_port=free_port(),
        phone_ui_dir=ui,
    )
    provider = FakeProvider([[say("Hi from NOVA")] for _ in range(5)])
    with TestClient(create_app(settings, provider)) as client:
        yield client
        client.post("/api/phone", json={"enabled": False}, headers=DESKTOP)


def test_phone_access_end_to_end(desktop):
    assert desktop.get("/api/phone", headers=DESKTOP).json()["enabled"] is False
    status = desktop.post("/api/phone", json={"enabled": True}, headers=DESKTOP).json()
    assert status["running"] and status["url"].startswith("http://127.0.0.1:")
    assert status["app_url"].startswith("https://127.0.0.1:")
    authority = desktop.app.state.phone.certificates.authority_der()
    trusted = ssl.create_default_context(cadata=authority)

    with httpx.Client(base_url=status["url"], timeout=10) as setup:
        page = setup.get("/")
        assert page.status_code == 200 and status["app_url"] in page.text
        assert status["certificate"]["fingerprint"] in page.text
        assert setup.get("/NOVA.crt").content == authority
        for path in ("/api/health", "/phone", "/api/pair"):
            assert setup.get(path, follow_redirects=False).headers["location"] == "/", "only setup over plain HTTP"

    with pytest.raises(httpx.ConnectError, match="CERTIFICATE_VERIFY_FAILED"):
        httpx.get(status["app_url"] + "/phone", timeout=10)

    with httpx.Client(base_url=status["app_url"], timeout=10, verify=trusted) as phone:
        page = phone.get("/phone")
        assert page.status_code == 200 and "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert phone.get("/", follow_redirects=False).headers["location"] == "/phone"

        assert phone.get("/api/health").status_code == 401, "not paired yet"
        assert phone.get("/api/health", headers=DESKTOP).status_code == 401, "the desktop's key does not work here"
        assert phone.post("/api/pair", json={"code": "123456", "name": "Pixel"}).status_code == 403

        code = desktop.post("/api/phone/code", headers=DESKTOP).json()["code"]
        paired = phone.post("/api/pair", json={"code": code, "name": "Pixel"}).json()
        key = {"Authorization": f"Bearer {paired['token']}"}
        assert paired["device"]["name"] == "Pixel"
        assert phone.post("/api/pair", json={"code": code, "name": "Again"}).status_code == 403, "one phone per code"

        assert phone.get("/api/health", headers=key).status_code == 200
        reply = phone.post("/api/chat", json={"message": "hi", "screen": True, "voice": True}, headers=key)
        assert "Hi from NOVA" in reply.text
        for path in ("/api/connections", "/api/voice", "/api/phone"):
            assert phone.get(path, headers=key).status_code == 403, path
        assert phone.post("/api/phone/code", headers=key).status_code == 403, "a phone cannot pair more phones"

        devices = desktop.get("/api/phone", headers=DESKTOP).json()["devices"]
        assert [d["name"] for d in devices] == ["Pixel"] and devices[0]["last_seen"]
        desktop.delete(f"/api/phone/devices/{devices[0]['id']}", headers=DESKTOP)
        assert phone.get("/api/health", headers=key).status_code == 401, "a removed phone is locked out at once"

    assert desktop.post("/api/pair", json={"code": "123456"}, headers=DESKTOP).status_code == 404, "pairing only on the phone listener"
    desktop.post("/api/phone", json={"enabled": False}, headers=DESKTOP)
    # Switched off means the port is closed (free to bind again), not just refusing.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", int(status["url"].rsplit(":", 1)[1])))
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", int(status["app_url"].rsplit(":", 1)[1])))


def test_a_pairing_code_comes_with_a_qr_code_that_carries_it(desktop):
    desktop.post("/api/phone", json={"enabled": True}, headers=DESKTOP)
    fresh = desktop.post("/api/phone/code", headers=DESKTOP).json()
    assert fresh["qr"].startswith("data:image/svg+xml")
    assert desktop.app.state.phone.pairing_link(fresh["code"]) == f"{fresh['url']}/#{fresh['code']}"


def test_phone_access_is_remembered_and_codes_need_it_on(desktop):
    assert desktop.post("/api/phone/code", headers=DESKTOP).status_code == 409
    desktop.post("/api/phone", json={"enabled": True}, headers=DESKTOP)
    from nova.settings import SettingsStore

    assert desktop.app.state.phone.enabled
    assert isinstance(desktop.app.state.phone._settings, SettingsStore)
