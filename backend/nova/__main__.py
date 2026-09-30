"""Entry point: `python -m nova`. Normally started by the desktop shell."""

from __future__ import annotations

import ctypes
import os
import sys
import threading

import uvicorn

from nova.api import create_app
from nova.config import Settings


def _exit_with_parent(pid: int) -> None:
    """Exit when the desktop shell does, so a crashed shell never leaves the backend running.

    A venv's python.exe is a launcher that starts the real interpreter as a
    child, so the shell cannot reliably kill this process by handle.
    """
    if sys.platform != "win32":
        return
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]

    synchronize, infinite = 0x00100000, 0xFFFFFFFF
    handle = kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        # The parent is already gone.
        os._exit(0)

    def wait() -> None:
        kernel32.WaitForSingleObject(handle, infinite)
        os._exit(0)

    threading.Thread(target=wait, name="parent-watchdog", daemon=True).start()


def main() -> None:
    settings = Settings.from_env()
    if settings.parent_pid is not None:
        _exit_with_parent(settings.parent_pid)
    if "NOVA_API_TOKEN" not in os.environ:
        print(f"NOVA_API_TOKEN not set; generated one for this run: {settings.api_token}", flush=True)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
