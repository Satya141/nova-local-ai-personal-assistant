"""Capture what the user is looking at: the window they were working in.

NOVA's launcher sits on top of that window when the user asks about it, so a
plain screenshot would mostly show NOVA. Instead this finds the first ordinary
window below NOVA's own in the stacking order and asks it to draw itself with
PrintWindow(PW_RENDERFULLCONTENT), which works even where the launcher covers
it. Some apps cannot draw themselves that way and come back blank; then the
window's area is copied from the screen instead.

Nothing here runs unless the user asked NOVA to look, and nothing is saved.
"""

from __future__ import annotations

import ctypes
import functools
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

from nova.tools._windows import Window, list_windows

if TYPE_CHECKING:
    from PIL import Image

# Windows that belong to NOVA itself and must never be what it "sees".
_OWN_PROCESSES = {"nova"}
_MIN_SIZE = 120  # px; smaller windows are widgets and pop-ups, not what the user means
_PW_RENDERFULLCONTENT = 0x2
_DWMWA_EXTENDED_FRAME_BOUNDS = 9


class CaptureFailed(RuntimeError):
    """Nothing could be captured (no suitable window, or not on Windows)."""


@dataclass(frozen=True)
class Capture:
    image: Image.Image
    title: str
    app: str
    # "window": the window drew itself; "screen": copied from the screen (NOVA may overlap it).
    method: str


class _Rect(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


@functools.cache
def _api():
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    dwmapi = ctypes.WinDLL("dwmapi")
    # Real pixel sizes on scaled displays. Harmless for the rest of the backend, which draws no UI.
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        pass

    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_Rect)]
    user32.GetWindowDC.argtypes = [wintypes.HWND]
    user32.GetWindowDC.restype = wintypes.HDC
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
    ]
    dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
    return user32, gdi32, dwmapi


def _bounds(hwnd: int) -> tuple[_Rect, _Rect]:
    """The window's full rectangle and its visible frame (without the invisible resize border)."""
    user32, _, dwmapi = _api()
    outer, visible = _Rect(), _Rect()
    user32.GetWindowRect(hwnd, ctypes.byref(outer))
    if dwmapi.DwmGetWindowAttribute(hwnd, _DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(visible), ctypes.sizeof(visible)):
        visible = outer
    return outer, visible


def pick_target(windows: list[Window], own: set[str] = _OWN_PROCESSES) -> Window | None:
    """The window the user was working in: the first ordinary window below NOVA's, in stacking order."""
    for window in windows:
        if window.process.lower() in own:
            continue
        return window
    return None


def _ordinary_windows() -> list[Window]:
    user32, _, _ = _api()
    result = []
    for window in list_windows():
        if user32.IsIconic(window.hwnd):
            continue
        _, rect = _bounds(window.hwnd)
        if rect.right - rect.left < _MIN_SIZE or rect.bottom - rect.top < _MIN_SIZE:
            continue
        result.append(window)
    return result


def _print_window(hwnd: int) -> Image.Image | None:
    from PIL import Image

    user32, gdi32, _ = _api()
    outer, visible = _bounds(hwnd)
    width, height = outer.right - outer.left, outer.bottom - outer.top
    if width <= 0 or height <= 0:
        return None

    window_dc = user32.GetWindowDC(hwnd)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    previous = gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(hwnd, memory_dc, _PW_RENDERFULLCONTENT):
            return None
        # BITMAPINFOHEADER for a top-down 32-bit image.
        header = (ctypes.c_uint32 * 10)(40, width, (-height) & 0xFFFFFFFF, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
        buffer = ctypes.create_string_buffer(width * height * 4)
        if not gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, header, 0):
            return None
        image = Image.frombuffer("RGB", (width, height), buffer, "raw", "BGRX", 0, 1)
    finally:
        gdi32.SelectObject(memory_dc, previous)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(hwnd, window_dc)

    # Trim the invisible resize border Windows adds around most windows.
    return image.crop((visible.left - outer.left, visible.top - outer.top, visible.right - outer.left, visible.bottom - outer.top))


def _blank(image: Image.Image) -> bool:
    """A window that could not draw itself comes back as one flat colour."""
    extrema = image.convert("L").getextrema()
    return extrema[1] - extrema[0] < 8


def capture_window(window: Window) -> Capture:
    from PIL import ImageGrab

    image = _print_window(window.hwnd)
    if image is not None and not _blank(image):
        return Capture(image, window.title, window.process, "window")
    _, rect = _bounds(window.hwnd)
    image = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom), all_screens=True)
    return Capture(image, window.title, window.process, "screen")


def capture_user_window() -> Capture:
    """Capture the window the user is working in. Raises CaptureFailed if there is none."""
    if sys.platform != "win32":
        raise CaptureFailed("Looking at the screen is only implemented on Windows so far.")
    target = pick_target(_ordinary_windows())
    if target is None:
        raise CaptureFailed("There is no open window to look at.")
    return capture_window(target)
