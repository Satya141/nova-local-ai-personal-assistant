"""User settings that persist across restarts, such as whether NOVA listens for its name."""

from __future__ import annotations

from nova.database import Database


class SettingsStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self._db.fetch_one("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.get(key)
        return default if value is None else value == "1"

    def set(self, key: str, value: str) -> None:
        self._db.run(
            "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )

    def set_bool(self, key: str, value: bool) -> None:
        self.set(key, "1" if value else "0")
