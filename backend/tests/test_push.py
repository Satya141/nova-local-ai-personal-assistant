"""Reminders ringing on a locked phone: Web Push, sealed for the phone and signed by the PC."""

from __future__ import annotations

import asyncio
import json
import ssl

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from test_phone import DESKTOP, desktop  # noqa: F401 (the fixture)

from nova.events import EventBus
from nova.phone.devices import DeviceStore
from nova.phone.push import PushKeys, PushStore, Pusher, allowed_endpoint, b64url, encrypt, unb64url

ENDPOINT = "https://fcm.googleapis.com/fcm/send/abc123"


def private_key(d: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(unb64url(d), "big"), ec.SECP256R1())


def test_encryption_matches_the_worked_example_in_rfc_8291():
    body = encrypt(
        b"When I grow up, I want to be a watermelon",
        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
        "BTBZMqHH6r4Tts7J_aSIgg",
        sender=private_key("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"),
        salt=unb64url("DGv6ra1nlYgDCS1FRnbzlw"),
    )
    assert b64url(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_"
        "yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN"
    )


def decrypt(body: bytes, receiver: ec.EllipticCurvePrivateKey, auth: bytes) -> bytes:
    """What the phone's browser does with a message."""
    salt, size, sender_public, sealed = body[:16], body[20], body[21:86], body[86:]
    assert size == 65
    shared = receiver.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), sender_public))
    receiver_public = unb64url(public(receiver))
    ikm = HKDF(hashes.SHA256(), 32, auth, b"WebPush: info\x00" + receiver_public + sender_public).derive(shared)
    key = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    plain = AESGCM(key).decrypt(nonce, sealed, None)
    assert plain.endswith(b"\x02")
    return plain[:-1]


def public(key: ec.EllipticCurvePrivateKey) -> str:
    from cryptography.hazmat.primitives import serialization

    return b64url(key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint))


def test_each_message_is_sealed_afresh_and_only_the_phone_can_open_it():
    phone, auth = ec.generate_private_key(ec.SECP256R1()), b"0123456789abcdef"
    first = encrypt(b"Drink water", public(phone), b64url(auth))
    second = encrypt(b"Drink water", public(phone), b64url(auth))
    assert first != second and b"water" not in first
    assert decrypt(first, phone, auth) == decrypt(second, phone, auth) == b"Drink water"
    with pytest.raises(Exception):
        decrypt(first, ec.generate_private_key(ec.SECP256R1()), auth)


def test_the_pc_signs_its_messages_with_a_key_kept_in_the_vault(tmp_path):
    keys = PushKeys(tmp_path)
    header = keys.authorization(ENDPOINT, now=1_000_000)
    token, key = header.removeprefix("vapid t=").split(", k=")
    assert key == keys.public_key() == PushKeys(tmp_path).public_key(), "the same key after a restart"

    head, claims, signature = token.split(".")
    assert json.loads(unb64url(head)) == {"typ": "JWT", "alg": "ES256"}
    assert json.loads(unb64url(claims))["aud"] == "https://fcm.googleapis.com"
    assert json.loads(unb64url(claims))["exp"] == 1_000_000 + 12 * 3600
    raw = unb64url(signature)
    verifier = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64url(key))
    verifier.verify(
        encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
        f"{head}.{claims}".encode(),
        ec.ECDSA(hashes.SHA256()),
    )


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        (ENDPOINT, True),
        ("https://updates.push.services.mozilla.com/wpush/v2/x", True),
        ("https://web.push.apple.com/x", True),
        ("http://fcm.googleapis.com/fcm/send/x", False),
        ("https://192.168.1.1/x", False),
        ("https://googleapis.com.evil.example/x", False),
        ("https://localhost/x", False),
    ],
)
def test_the_pc_only_posts_to_browsers_push_services(url, ok):
    assert allowed_endpoint(url) is ok


def subscribe(store: PushStore, device_id: int, endpoint: str = ENDPOINT) -> ec.EllipticCurvePrivateKey:
    phone = ec.generate_private_key(ec.SECP256R1())
    store.save(device_id, endpoint, public(phone), b64url(b"0123456789abcdef"))
    return phone


def test_one_subscription_per_phone_and_it_goes_with_the_phone(db):
    devices, store = DeviceStore(db), PushStore(db)
    phone, _ = devices.add("Pixel")
    subscribe(store, phone.id)
    subscribe(store, phone.id, ENDPOINT + "-new")
    assert [s.endpoint for s in store.all()] == [ENDPOINT + "-new"]
    with pytest.raises(ValueError):
        store.save(phone.id, "https://192.168.1.5/push", store.all()[0].p256dh, store.all()[0].auth)
    with pytest.raises(ValueError):
        store.save(phone.id, ENDPOINT, b64url(b"\x04" + b"\x01" * 64), store.all()[0].auth)
    devices.remove(phone.id)
    assert store.all() == []


class PushService:
    def __init__(self, status: int = 201) -> None:
        self.status = status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(self.status)


def pusher_for(db, tmp_path, service: PushService, active=lambda: True) -> Pusher:
    return Pusher(PushStore(db), PushKeys(tmp_path), active, httpx.AsyncClient(transport=httpx.MockTransport(service)))


def reminder(pending: bool = True) -> dict:
    return {"id": 7, "text": "Drink water", "pending_ack": pending, "fired_at": "2026-10-03T05:02:44+00:00", "result": None}


async def deliver(pusher: Pusher, *events: dict) -> None:
    bus = EventBus()
    pusher.start(bus)
    await asyncio.sleep(0)
    for event in events:
        bus.publish(event)
    for _ in range(50):
        await asyncio.sleep(0.01)
    await pusher.stop()


async def test_a_reminder_that_rings_on_the_pc_rings_on_the_phone(db, tmp_path):
    service = PushService()
    pusher = pusher_for(db, tmp_path, service)
    phone_id = DeviceStore(db).add("Pixel")[0].id
    phone = subscribe(pusher.store, phone_id)
    await deliver(
        pusher,
        {"type": "reminder", "reminder": reminder()},
        {"type": "reminder", "reminder": reminder(pending=False)},  # a change, not a ring
        {"type": "memory_saved", "memory": {}},
    )
    assert len(service.requests) == 1
    sent = service.requests[0]
    assert str(sent.url) == ENDPOINT and sent.headers["urgency"] == "high" and sent.headers["content-encoding"] == "aes128gcm"
    assert sent.headers["authorization"].startswith("vapid t=")
    message = json.loads(decrypt(sent.content, phone, b"0123456789abcdef"))
    assert message == {"type": "reminder", "id": 7, "text": "Drink water", "result": None, "fired_at": "2026-10-03T05:02:44+00:00"}


async def test_nothing_is_sent_while_phone_access_is_off(db, tmp_path):
    service = PushService()
    pusher = pusher_for(db, tmp_path, service, active=lambda: False)
    subscribe(pusher.store, DeviceStore(db).add("Pixel")[0].id)
    await deliver(pusher, {"type": "reminder", "reminder": reminder()})
    assert service.requests == []


async def test_a_subscription_the_browser_dropped_is_forgotten(db, tmp_path):
    pusher = pusher_for(db, tmp_path, PushService(status=410))
    subscribe(pusher.store, DeviceStore(db).add("Pixel")[0].id)
    assert await pusher.send(b"{}") == 0
    assert pusher.store.all() == []
    await pusher.stop()


async def test_no_internet_is_not_an_error(db, tmp_path):
    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    pusher = Pusher(PushStore(db), PushKeys(tmp_path), lambda: True, httpx.AsyncClient(transport=httpx.MockTransport(offline)))
    subscribe(pusher.store, DeviceStore(db).add("Pixel")[0].id)
    assert await pusher.send(b"{}") == 0
    assert len(pusher.store.all()) == 1, "kept for when the PC is back online"
    await pusher.stop()


def test_a_phone_signs_up_for_notifications_over_its_own_listener(desktop):  # noqa: F811
    assert desktop.get("/api/push", headers=DESKTOP).status_code == 404, "set up from the phone"
    status = desktop.post("/api/phone", json={"enabled": True}, headers=DESKTOP).json()
    trusted = ssl.create_default_context(cadata=desktop.app.state.phone.certificates.authority_der())
    code = desktop.post("/api/phone/code", headers=DESKTOP).json()["code"]
    with httpx.Client(base_url=status["app_url"], timeout=10, verify=trusted) as phone:
        key = {"Authorization": f"Bearer {phone.post('/api/pair', json={'code': code, 'name': 'Pixel'}).json()['token']}"}
        info = phone.get("/api/push", headers=key).json()
        assert info["subscribed"] is False and len(unb64url(info["public_key"])) == 65

        browser = ec.generate_private_key(ec.SECP256R1())
        subscription = {"endpoint": ENDPOINT, "expirationTime": None, "keys": {"p256dh": public(browser), "auth": b64url(b"0123456789abcdef")}}
        assert phone.post("/api/push", json=subscription, headers=key).json() == {"subscribed": True}
        assert phone.get("/api/push", headers=key).json()["subscribed"] is True
        elsewhere = {**subscription, "endpoint": "https://192.168.1.10/push"}
        assert phone.post("/api/push", json=elsewhere, headers=key).status_code == 400

        assert phone.delete("/api/push", headers=key).json() == {"subscribed": False}
        assert phone.get("/api/push", headers=key).json()["subscribed"] is False
