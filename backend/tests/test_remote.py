"""Phase 7: reaching NOVA from anywhere through Tailscale."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import ssl

import httpx
import pytest
from cryptography import x509
from cryptography.x509.verification import PolicyBuilder, Store, VerificationError

from nova.phone import service
from nova.phone.certs import Certificates
from nova.phone.devices import DeviceStore
from nova.phone.discovery import tailscale_address
from nova.settings import SettingsStore


def test_only_tailscales_own_interface_counts():
    rows = [
        {"address": "192.168.29.104", "alias": "Wi-Fi", "name": "Home", "category": "Private"},
        # Cloudflare WARP (Zero Trust) uses the same address space: not Tailscale.
        {"address": "100.96.0.7", "alias": "CloudflareWARP", "name": "", "category": "Public"},
    ]
    assert tailscale_address(rows) is None
    rows.append({"address": "100.101.102.103", "alias": "Tailscale", "name": "", "category": "Public"})
    assert tailscale_address(rows) == "100.101.102.103"


def test_the_certificate_covers_the_tailscale_address_but_still_no_public_one(tmp_path):
    certificates = Certificates(tmp_path)
    authority, _ = certificates.authority()
    leaf, _ = certificates.server_certificate(("192.168.1.20", "100.101.102.103"), ("nova-pc.local",))
    for address in ("192.168.1.20", "100.101.102.103"):
        PolicyBuilder().store(Store([authority])).build_server_verifier(
            x509.IPAddress(ipaddress.ip_address(address))
        ).verify(leaf, [])
    public, _ = certificates.server_certificate("100.128.0.1")  # just outside Tailscale's range
    with pytest.raises(VerificationError):
        PolicyBuilder().store(Store([authority])).build_server_verifier(
            x509.IPAddress(ipaddress.ip_address("100.128.0.1"))
        ).verify(public, [])


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def db(tmp_path):
    from nova.database import Database

    database = Database(tmp_path / "nova.db")
    yield database
    database.close()


@pytest.fixture
def places(monkeypatch):
    """Where the PC is: its local address, and its Tailscale address (127.0.0.2 stands in)."""
    where = {"home": "127.0.0.1", "remote": None}
    monkeypatch.setattr(service, "home_network_address", lambda: where["home"])
    monkeypatch.setattr(service, "interfaces", lambda: [])
    monkeypatch.setattr(service, "tailscale_address", lambda rows: where["remote"])
    monkeypatch.setattr(service, "WATCH_SECONDS", 0.05)
    monkeypatch.setattr(service, "PROFILE_EVERY", 1)
    return where


async def app(scope, receive, send):  # a stand-in for NOVA's API: who is asking
    if scope["type"] == "http":
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"hello"})


async def wait_for(condition) -> bool:
    for _ in range(60):
        if condition():
            return True
        await asyncio.sleep(0.05)
    return False


async def test_phone_access_opens_on_tailscale_too_and_only_for_tailscale_senders(db, tmp_path, places, monkeypatch):
    places["remote"] = "127.0.0.2"
    phone = service.PhoneAccess(app, SettingsStore(db), DeviceStore(db), Certificates(tmp_path), free_port(), free_port())
    await phone.set_enabled(True)
    try:
        status = phone.status()
        assert status["remote_url"] == f"https://127.0.0.2:{phone._secure_port}"
        assert phone._app_urls()[0] == status["remote_url"], "Tailscale first: it works everywhere"
        assert phone.serves(("127.0.0.2", phone._secure_port)) and phone.serves(("127.0.0.1", phone._secure_port))

        trusted = ssl.create_default_context(cadata=phone.certificates.authority_der())
        async with httpx.AsyncClient(verify=trusted, timeout=10) as client:
            # The request comes from 127.0.0.1, not a Tailscale address: refused on that listener.
            assert not phone.client_allowed(("127.0.0.2", phone._secure_port), ("127.0.0.1", 5000))
            setup = await client.get(f"http://127.0.0.2:{phone._port}/")
            assert setup.status_code == 403

            monkeypatch.setattr(service, "OVERLAY", ipaddress.ip_network("127.0.0.0/8"))
            assert phone.client_allowed(("127.0.0.2", phone._secure_port), ("127.0.0.1", 5000))
            assert (await client.get(f"http://127.0.0.2:{phone._port}/")).status_code == 200
            assert (await client.get(status["remote_url"] + "/")).text == "hello", "verified HTTPS on Tailscale"
    finally:
        await phone.stop()


async def test_tailscale_coming_and_going_is_noticed(db, tmp_path, places):
    phone = service.PhoneAccess(app, SettingsStore(db), DeviceStore(db), Certificates(tmp_path), free_port(), free_port())
    changes = []
    phone.on_change = lambda: changes.append(phone.remote_url)
    await phone.set_enabled(True)
    try:
        assert phone.running and phone.remote_url is None

        places["remote"] = "127.0.0.2"  # the user installs and signs in to Tailscale
        assert await wait_for(lambda: phone.remote_url is not None)
        assert changes and changes[-1] == phone.remote_url

        places["home"] = None  # the PC leaves the Wi-Fi but keeps Tailscale (a phone hotspot, say)
        assert await wait_for(lambda: phone.home is None and phone.running)
        assert phone.remote_url and phone.url == f"http://127.0.0.2:{phone._port}"

        places["remote"] = None  # and Tailscale is switched off
        assert await wait_for(lambda: phone.error == service.OFFLINE)
    finally:
        await phone.stop()
