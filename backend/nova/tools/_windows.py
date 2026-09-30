"""Helpers for calling Windows from tools without flashing a console window."""

from __future__ import annotations

import asyncio
import ctypes
import functools
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IS_WINDOWS = sys.platform == "win32"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(args: list[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(args, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)


async def powershell_json(script: str, timeout: float = 15.0) -> list[dict[str, Any]]:
    """Run a fixed PowerShell script and parse its JSON output as a list.

    `script` must be a constant: never interpolate model or user text into it.
    """
    command = f"[Console]::OutputEncoding=[Text.Encoding]::UTF8; {script} | ConvertTo-Json -Compress"
    result = await asyncio.to_thread(
        _run, ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], timeout
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace").strip()[:300])
    text = result.stdout.decode("utf-8", "replace").strip()
    if not text:
        return []
    data = json.loads(text)
    # ConvertTo-Json emits a bare object, not an array, for a single item.
    return data if isinstance(data, list) else [data]


@dataclass(frozen=True)
class Window:
    hwnd: int
    title: str
    # Executable name without extension, e.g. "notepad". Store apps all report
    # "ApplicationFrameHost", which is why windows are matched by title too.
    process: str


@functools.cache
def _win32():
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    return user32, kernel32, dwmapi, enum_proc, wintypes


def _process_name(pid: int) -> str:
    _, kernel32, _, _, wintypes = _win32()
    query_limited_information = 0x1000
    handle = kernel32.OpenProcess(query_limited_information, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return ""
        return Path(buffer.value).stem
    finally:
        kernel32.CloseHandle(handle)


def list_windows() -> list[Window]:
    """The top-level windows a user would recognise as open apps."""
    user32, _, dwmapi, enum_proc, wintypes = _win32()
    gwl_exstyle, ws_ex_toolwindow, dwmwa_cloaked = -20, 0x80, 14
    windows: list[Window] = []

    def visit(hwnd: int, _: int) -> bool:
        length = user32.GetWindowTextLengthW(hwnd)
        if not user32.IsWindowVisible(hwnd) or length == 0:
            return True
        if user32.GetWindowLongW(hwnd, gwl_exstyle) & ws_ex_toolwindow:
            return True
        # Suspended Store apps keep a "visible" window that DWM has cloaked.
        cloaked = wintypes.DWORD()
        dwmapi.DwmGetWindowAttribute(hwnd, dwmwa_cloaked, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if cloaked.value:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        if title.value == "Program Manager":  # The desktop itself.
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        windows.append(Window(hwnd, title.value, _process_name(pid.value)))
        return True

    user32.EnumWindows(enum_proc(visit), 0)
    return windows


def request_close(hwnd: int) -> bool:
    """Ask one window to close, as if the user clicked its X. The app may still prompt to save."""
    user32 = _win32()[0]
    wm_close = 0x0010
    return bool(user32.PostMessageW(hwnd, wm_close, 0, 0))
