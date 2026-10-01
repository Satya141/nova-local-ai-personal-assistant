"""Where NOVA keeps account credentials: encrypted with Windows DPAPI, for this Windows user only.

A copied file is useless on another account or machine. Credentials never go into the
database, the logs, the action log or anything the model sees.
"""

from __future__ import annotations

import ctypes
import json
import re
import sys
from pathlib import Path
from typing import Any

_NAME = re.compile(r"^[a-z0-9-]{1,40}$")
# Mixed into the encryption, so another program using DPAPI cannot decrypt these by accident.
_ENTROPY = b"NOVA account credentials v1"


class VaultError(RuntimeError):
    pass


if sys.platform == "win32":
    from ctypes import wintypes

    class _Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    def _blob(data: bytes) -> _Blob:
        buffer = ctypes.create_string_buffer(data, len(data))
        return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))

    def _call(function, data: bytes) -> bytes:
        source, entropy, out = _blob(data), _blob(_ENTROPY), _Blob()
        # CRYPTPROTECT_UI_FORBIDDEN: never show a prompt.
        if not function(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 0x1, ctypes.byref(out)):
            raise VaultError(f"Windows could not {'protect' if function is _protect else 'unprotect'} the credentials.")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(out.pbData)

    _protect = ctypes.windll.crypt32.CryptProtectData
    _unprotect = ctypes.windll.crypt32.CryptUnprotectData

    def protect(data: bytes) -> bytes:
        return _call(_protect, data)

    def unprotect(data: bytes) -> bytes:
        return _call(_unprotect, data)

else:

    def protect(data: bytes) -> bytes:
        raise VaultError("Storing account credentials is only implemented on Windows so far.")

    def unprotect(data: bytes) -> bytes:
        raise VaultError("Storing account credentials is only implemented on Windows so far.")


class Vault:
    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def _path(self, name: str) -> Path:
        if not _NAME.match(name):
            raise ValueError(f"bad credential name {name!r}")
        return self._folder / f"{name}.bin"

    def save(self, name: str, value: dict[str, Any]) -> None:
        self._folder.mkdir(parents=True, exist_ok=True)
        path = self._path(name)
        temporary = path.with_suffix(".tmp")
        temporary.write_bytes(protect(json.dumps(value).encode("utf-8")))
        temporary.replace(path)

    def load(self, name: str) -> dict[str, Any] | None:
        path = self._path(name)
        if not path.exists():
            return None
        try:
            return json.loads(unprotect(path.read_bytes()).decode("utf-8"))
        except (VaultError, ValueError):
            # Unreadable (another Windows user, a damaged file): treat as not connected.
            return None

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)
