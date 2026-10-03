"""Ringing a phone whose screen is off: Web Push (RFC 8030), encrypted for the phone (RFC 8291)
and signed by this PC (VAPID, RFC 8292).

A locked phone's browser wakes only for a message from its own push service (Google's, for
Chrome on Android), so a reminder has to travel through it. It is sealed on the PC with keys
only that phone's browser holds: the push service carries it without being able to read it,
and learns only that something was sent, and when. The PC's signing key lives in the vault.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from nova.database import Database, timestamp
from nova.events import EventBus
from nova.integrations.vault import Vault

log = logging.getLogger(__name__)

_KEY_NAME = "phone-push-key"
# The push services of the browsers a phone may use. The PC only ever posts to these, so a
# paired phone cannot point it at anything else on the network.
PUSH_SERVICES = (".googleapis.com", ".push.services.mozilla.com", ".notify.windows.com", ".push.apple.com")
# A reminder still worth showing if the phone was out of reach for a while; after that, dropped.
TTL_SECONDS = 4 * 3600
_RECORD_SIZE = 4096


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64url(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _raw(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt(
    payload: bytes,
    p256dh: str,
    auth: str,
    *,
    sender: ec.EllipticCurvePrivateKey | None = None,
    salt: bytes | None = None,
) -> bytes:
    """`payload` sealed for one browser (RFC 8291, aes128gcm), as the body of the push request.

    `sender` and `salt` are fresh for every message; tests pass the RFC's example values.
    """
    receiver = _raw_public(p256dh)
    secret = unb64url(auth)
    sender = sender or ec.generate_private_key(ec.SECP256R1())
    salt = salt or os.urandom(16)
    sender_public = _raw(sender.public_key())
    shared = sender.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), receiver))
    ikm = _hkdf(secret, shared, b"WebPush: info\x00" + receiver + sender_public, 32)
    key = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    sealed = AESGCM(key).encrypt(nonce, payload + b"\x02", None)  # \x02: the last (and only) record
    header = salt + _RECORD_SIZE.to_bytes(4, "big") + bytes([len(sender_public)]) + sender_public
    return header + sealed


def _raw_public(p256dh: str) -> bytes:
    raw = unb64url(p256dh)
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)  # raises ValueError if not a P-256 point
    return raw


def allowed_endpoint(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host.endswith(suffix) for suffix in PUSH_SERVICES)


class PushKeys:
    """This PC's signing key for push messages, made on first use and kept in the vault."""

    def __init__(self, folder: Path, vault: Vault | None = None) -> None:
        self._vault = vault or Vault(folder)
        self._key: ec.EllipticCurvePrivateKey | None = None

    def key(self) -> ec.EllipticCurvePrivateKey:
        if self._key is None:
            stored = self._vault.load(_KEY_NAME)
            try:
                loaded = serialization.load_pem_private_key(stored["key"].encode("ascii"), password=None) if stored else None
            except (ValueError, KeyError, TypeError):
                loaded = None
            if not isinstance(loaded, ec.EllipticCurvePrivateKey):
                loaded = ec.generate_private_key(ec.SECP256R1())
                pem = loaded.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
                )
                self._vault.save(_KEY_NAME, {"key": pem.decode("ascii")})
            self._key = loaded
        return self._key

    def public_key(self) -> str:
        """What the phone's browser is given, so it accepts messages only from this PC."""
        return b64url(_raw(self.key().public_key()))

    def authorization(self, endpoint: str, now: float | None = None) -> str:
        parts = urlsplit(endpoint)
        claims = {
            "aud": f"{parts.scheme}://{parts.netloc}",
            "exp": int((now or time.time()) + 12 * 3600),
            "sub": "mailto:nova@localhost",
        }
        signing_input = b".".join(
            b64url(json.dumps(part, separators=(",", ":")).encode()).encode()
            for part in ({"typ": "JWT", "alg": "ES256"}, claims)
        )
        r, s = decode_dss_signature(self.key().sign(signing_input, ec.ECDSA(hashes.SHA256())))
        token = signing_input + b"." + b64url(r.to_bytes(32, "big") + s.to_bytes(32, "big")).encode()
        return f"vapid t={token.decode('ascii')}, k={self.public_key()}"


@dataclass(frozen=True)
class Subscription:
    device_id: int
    endpoint: str
    p256dh: str
    auth: str


class PushStore:
    """One push subscription per paired phone; removing the phone removes it."""

    def __init__(self, db: Database) -> None:
        self._db = db

    def save(self, device_id: int, endpoint: str, p256dh: str, auth: str) -> Subscription:
        if not allowed_endpoint(endpoint):
            raise ValueError("That is not a browser's push service")
        _raw_public(p256dh)
        if len(unb64url(auth)) != 16:
            raise ValueError("The subscription's auth secret must be 16 bytes")
        self._db.run(
            "INSERT INTO push_subscriptions (device_id, endpoint, p256dh, auth, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (device_id) DO UPDATE SET endpoint = excluded.endpoint, p256dh = excluded.p256dh, "
            "auth = excluded.auth, created_at = excluded.created_at",
            (device_id, endpoint, p256dh, auth, timestamp()),
        )
        return Subscription(device_id, endpoint, p256dh, auth)

    def get(self, device_id: int) -> Subscription | None:
        row = self._db.fetch_one("SELECT * FROM push_subscriptions WHERE device_id = ?", (device_id,))
        return Subscription(row["device_id"], row["endpoint"], row["p256dh"], row["auth"]) if row else None

    def all(self) -> list[Subscription]:
        rows = self._db.fetch("SELECT * FROM push_subscriptions ORDER BY device_id")
        return [Subscription(r["device_id"], r["endpoint"], r["p256dh"], r["auth"]) for r in rows]

    def remove(self, device_id: int) -> None:
        self._db.run("DELETE FROM push_subscriptions WHERE device_id = ?", (device_id,))


def reminder_message(reminder: dict) -> bytes:
    """What the phone shows. Kept small: a push message carries at most about 4 KB."""
    return json.dumps(
        {
            "type": "reminder",
            "id": reminder["id"],
            "text": reminder["text"][:300],
            "result": (reminder.get("result") or "")[:1500] or None,
            "fired_at": reminder.get("fired_at"),
        }
    ).encode()


class Pusher:
    """Sends each reminder that rings on the PC to every phone that asked for notifications."""

    def __init__(
        self,
        store: PushStore,
        keys: PushKeys,
        active: Callable[[], bool],
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.store = store
        self.keys = keys
        self._active = active
        self._client = client or httpx.AsyncClient(timeout=15)
        self._task: asyncio.Task | None = None
        self.last_problem: str | None = None  # why the last message did not go, in the user's words

    def start(self, bus: EventBus) -> None:
        self._task = asyncio.create_task(self._listen(bus), name="phone-push")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self._client.aclose()

    async def _listen(self, bus: EventBus) -> None:
        async with bus.subscribe() as queue:
            while True:
                event = await queue.get()
                if event.get("type") == "reminder" and event["reminder"].get("pending_ack") and self._active():
                    await self.send(reminder_message(event["reminder"]))

    async def send(self, message: bytes, device_id: int | None = None) -> int:
        """Send to every subscribed phone, or just one. Returns how many push services accepted it."""
        sent = 0
        subscriptions = self.store.all() if device_id is None else [s for s in [self.store.get(device_id)] if s]
        for subscription in subscriptions:
            try:
                response = await self._client.post(
                    subscription.endpoint,
                    content=encrypt(message, subscription.p256dh, subscription.auth),
                    headers={
                        "Authorization": self.keys.authorization(subscription.endpoint),
                        "Content-Encoding": "aes128gcm",
                        "Content-Type": "application/octet-stream",
                        "TTL": str(TTL_SECONDS),
                        "Urgency": "high",
                    },
                )
            except httpx.HTTPError as exc:
                # No internet right now: the phone rings from the app when it is next opened.
                self.last_problem = "Your PC could not reach the phone's notification service. It needs an internet connection."
                log.warning("Could not reach the push service for phone %s: %s", subscription.device_id, exc)
                continue
            if response.status_code in (404, 410):
                # The browser dropped this subscription (notifications turned off, site data cleared).
                self.store.remove(subscription.device_id)
                self.last_problem = "This phone's browser no longer accepts NOVA's notifications. Turn them on again."
            elif response.is_success:
                sent += 1
            else:
                self.last_problem = f"The notification service turned the message down (HTTP {response.status_code}: {response.text[:200]})."
                log.warning("Push service refused a message for phone %s: HTTP %s", subscription.device_id, response.status_code)
        return sent
