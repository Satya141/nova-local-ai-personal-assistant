from __future__ import annotations

import asyncio
import socket
import struct

import pytest

from nova.api.app import event_stream
from nova.events import EventBus
from nova.phone import service
from nova.phone.certs import Certificates
from nova.phone.devices import DeviceStore
from nova.phone.discovery import _encode_name, answer, nova_name, questions
from nova.settings import SettingsStore


def query(name: str, qtype: int = 1, unicast: bool = False) -> bytes:
    return struct.pack(">HHHHHH", 7, 0, 1, 0, 0, 0) + _encode_name(name) + struct.pack(">HH", qtype, 1 | (0x8000 if unicast else 0))


def test_nova_has_a_name_that_is_the_same_on_every_network():
    assert nova_name("Satya-laptop") == "nova-satya-laptop.local"
    assert nova_name("DESKTOP_42 (work)") == "nova-desktop-42-work.local"
    assert nova_name("___") == "nova.local"


def test_questions_are_read_and_answers_point_at_nova():
    assert questions(query("NOVA-Satya-Laptop.local", unicast=True)) == [("nova-satya-laptop.local", 1, True)]
    assert questions(b"\x00" * 5) == []
    response = bytearray(query("x.local"))
    response[2] |= 0x80
    assert questions(bytes(response)) == [], "responses are not questions"

    packet = answer("nova-pc.local", "192.168.1.20", 1)
    assert packet.endswith(socket.inet_aton("192.168.1.20"))
    _, flags, qd, an = struct.unpack_from(">HHHH", packet)
    assert flags & 0x8400 == 0x8400 and qd == 0 and an == 1
    no_ipv6 = answer("nova-pc.local", "192.168.1.20", 28)
    assert struct.unpack_from(">H", no_ipv6, 12 + len(_encode_name("nova-pc.local")))[0] == 47, "NSEC, not an address"


def test_the_certificate_covers_novas_local_name(tmp_path):
    from cryptography import x509
    from cryptography.x509.verification import PolicyBuilder, Store

    certificates = Certificates(tmp_path)
    authority, _ = certificates.authority()
    leaf, _ = certificates.server_certificate("192.168.1.20", ("nova-pc.local",))
    PolicyBuilder().store(Store([authority])).build_server_verifier(x509.DNSName("nova-pc.local")).verify(leaf, [])


def test_an_authority_from_older_rules_is_replaced(tmp_path, monkeypatch):
    from nova.phone import certs

    monkeypatch.setattr(certs, "PERMITTED_DOMAIN", "nova.invalid")
    old = Certificates(tmp_path).fingerprint()
    monkeypatch.undo()
    assert Certificates(tmp_path).fingerprint() != old


async def test_phone_events_skip_what_is_only_for_the_pc():
    class NoReminders:
        def pending(self):
            return []

    bus = EventBus()
    stream = event_stream(bus, NoReminders(), heartbeat=5, phone=True)
    first = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0.05)
    bus.publish({"type": "phones", "status": {"devices": [{"name": "Pixel"}]}})
    bus.publish({"type": "ping"})
    assert '"ping"' in await asyncio.wait_for(first, 2)
    await stream.aclose()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_phone_access_follows_the_pc_from_network_to_network(db, tmp_path, monkeypatch):
    network = {"address": "127.0.0.1"}
    monkeypatch.setattr(service, "home_network_address", lambda: network["address"])
    monkeypatch.setattr(service, "network_profile", lambda address: None)
    monkeypatch.setattr(service, "WATCH_SECONDS", 0.05)

    async def app(scope, receive, send):  # a stand-in for NOVA's API
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 204, "headers": []})
            await send({"type": "http.response.body", "body": b""})

    phone = service.PhoneAccess(
        app, SettingsStore(db), DeviceStore(db), Certificates(tmp_path), free_port(), free_port()
    )
    changes = []
    phone.on_change = lambda: changes.append(phone.status()["running"])
    await phone.set_enabled(True)
    try:
        assert phone.running and phone.url.startswith("http://127.0.0.1:")

        network["address"] = None  # the PC leaves the Wi-Fi
        for _ in range(40):
            await asyncio.sleep(0.05)
            if phone.error == service.OFFLINE:
                break
        assert not phone.running and phone.error == service.OFFLINE and phone.enabled

        network["address"] = "127.0.0.1"  # and joins another
        for _ in range(40):
            await asyncio.sleep(0.05)
            if phone.running and phone.error is None:
                break
        assert phone.running and phone.error is None
        assert changes[:1] == [False] and changes[-1] is True
    finally:
        await phone.stop()
    assert not phone.running


async def test_switching_off_in_the_middle_of_a_network_change_does_not_hang(db, tmp_path, monkeypatch):
    network = {"address": "127.0.0.1"}
    monkeypatch.setattr(service, "home_network_address", lambda: network["address"])
    monkeypatch.setattr(service, "network_profile", lambda address: None)
    monkeypatch.setattr(service, "WATCH_SECONDS", 0.01)

    async def app(scope, receive, send):
        pass

    phone = service.PhoneAccess(
        app, SettingsStore(db), DeviceStore(db), Certificates(tmp_path), free_port(), free_port()
    )
    await phone.set_enabled(True)
    for flip in range(20):
        network["address"] = None if flip % 2 == 0 else "127.0.0.1"
        await asyncio.sleep(0.013 * (flip % 4))
    await asyncio.wait_for(phone.set_enabled(False), 10)
    assert not phone.running and not phone.enabled


@pytest.fixture
def db(tmp_path):
    from nova.database import Database

    database = Database(tmp_path / "nova.db")
    yield database
    database.close()
