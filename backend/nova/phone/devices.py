"""Phones paired with this NOVA, and the one-time codes that pair them.

A phone proves who it is with a random key it received when it was paired. Only a SHA-256
hash of that key is stored, so the database alone cannot be used to pose as a phone.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass

from nova.database import Database, timestamp

CODE_SECONDS = 300
CODE_ATTEMPTS = 5


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class Device:
    id: int
    name: str
    created_at: str
    last_seen: str | None

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "created_at": self.created_at, "last_seen": self.last_seen}


class DeviceStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def add(self, name: str) -> tuple[Device, str]:
        """Pair a new phone. Returns it and its key, which is shown to the phone once and never kept."""
        token = secrets.token_urlsafe(32)
        device_id = self._db.run(
            "INSERT INTO devices (name, token_hash, created_at) VALUES (?, ?, ?)",
            (name.strip()[:60] or "Phone", _hash(token), timestamp()),
        )
        return self.get(device_id), token

    def get(self, device_id: int) -> Device | None:
        row = self._db.fetch_one("SELECT * FROM devices WHERE id = ?", (device_id,))
        return Device(row["id"], row["name"], row["created_at"], row["last_seen"]) if row else None

    def all(self) -> list[Device]:
        rows = self._db.fetch("SELECT * FROM devices ORDER BY id")
        return [Device(r["id"], r["name"], r["created_at"], r["last_seen"]) for r in rows]

    def authenticate(self, token: str) -> Device | None:
        if not token:
            return None
        row = self._db.fetch_one("SELECT * FROM devices WHERE token_hash = ?", (_hash(token),))
        if row is None:
            return None
        self._db.run("UPDATE devices SET last_seen = ? WHERE id = ?", (timestamp(), row["id"]))
        return Device(row["id"], row["name"], row["created_at"], row["last_seen"])

    def remove(self, device_id: int) -> bool:
        if self.get(device_id) is None:
            return False
        self._db.run("DELETE FROM devices WHERE id = ?", (device_id,))
        return True


class PairingCode:
    """One six-digit code at a time, shown on the PC, typed on the phone.

    It lasts five minutes and survives five wrong guesses: someone else on the Wi-Fi gets
    five tries at a million numbers before the code is gone.
    """

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._code: str | None = None
        self._expires = 0.0
        self._attempts = 0

    def new(self) -> tuple[str, int]:
        self._code = f"{secrets.randbelow(1_000_000):06d}"
        self._expires = self._clock() + CODE_SECONDS
        self._attempts = 0
        return self._code, CODE_SECONDS

    def clear(self) -> None:
        self._code = None

    def redeem(self, attempt: str) -> bool:
        """True once for the right code within its time. Wrong guesses use up the code."""
        if self._code is None or self._clock() > self._expires:
            self._code = None
            return False
        self._attempts += 1
        if secrets.compare_digest(attempt.strip(), self._code):
            self._code = None
            return True
        if self._attempts >= CODE_ATTEMPTS:
            self._code = None
        return False
