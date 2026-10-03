"""Reading and operating the user's open apps through Windows UI Automation.

Most Windows apps describe their buttons, boxes and menus to UI Automation, for screen readers.
NOVA reads that description of one window, numbers what can be used, and clicks or types by
number, the way the web tools work with a page. Nothing here moves the mouse to guess a spot:
buttons are invoked, boxes are set or pasted into, and keys come from a fixed list.

Some windows are off limits whatever the model asks (`blocked_reason`): terminals, because typing
there runs commands; Windows Security and sign-in prompts; password managers; the user's web
browsers, which hold their signed-in sessions (NOVA has its own browser); and NOVA itself.
Password boxes are never typed into.

UI Automation is COM, bound to the thread that started it, so every call runs on one worker thread.
"""

from __future__ import annotations

import asyncio
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from nova.tools import _windows


class AppError(RuntimeError):
    """Something the user should hear about, in their words."""


# Executable names (without .exe, lowercase) NOVA never operates.
_BLOCKED = {
    # Typing into a terminal runs commands (rule 3).
    "cmd": "a command prompt", "powershell": "PowerShell", "pwsh": "PowerShell", "windowsterminal": "a terminal",
    "wt": "a terminal", "conhost": "a terminal", "openconsole": "a terminal", "wsl": "a Linux terminal",
    "bash": "a terminal", "mintty": "a terminal", "regedit": "the Registry Editor", "mmc": "a system console",
    "taskmgr": "Task Manager",
    # Security and signing in.
    "sechealthui": "Windows Security", "securityhealthhost": "Windows Security", "consent": "an admin prompt",
    "credentialuibroker": "a sign-in prompt", "lockapp": "the lock screen", "logonui": "the sign-in screen",
    "1password": "a password manager", "bitwarden": "a password manager", "keepass": "a password manager",
    "keepassxc": "a password manager", "dashlane": "a password manager", "lastpass": "a password manager",
    # The user's own browsers hold their signed-in sessions; NOVA uses its own browser window instead.
    "msedge": "your web browser", "chrome": "your web browser", "firefox": "your web browser",
    "brave": "your web browser", "opera": "your web browser", "vivaldi": "your web browser",
    "iexplore": "your web browser", "arc": "your web browser",
    # NOVA itself, and the assistant the user is talking to here.
    "nova": "NOVA itself", "claude": "the Claude app",
}
_BLOCKED_TITLES = re.compile(r"\b(windows security|user account control|administrator:)\b", re.I)


def blocked_reason(process: str, title: str) -> str | None:
    """Why NOVA must not operate this window, or None if it may."""
    what = _BLOCKED.get(process.lower())
    if what is None and _BLOCKED_TITLES.search(title):
        what = "a security window"
    return f"NOVA does not operate {what}." if what else None


# Controls worth listing: things a person can click, type into or choose.
_KINDS = {
    "ButtonControl": "button", "SplitButtonControl": "button", "EditControl": "box", "DocumentControl": "document",
    "CheckBoxControl": "checkbox", "RadioButtonControl": "option", "ComboBoxControl": "dropdown",
    "MenuItemControl": "menu item", "TabItemControl": "tab", "ListItemControl": "item", "TreeItemControl": "item",
    "DataItemControl": "item", "HyperlinkControl": "link", "SliderControl": "slider",
}
# Window chrome every app has; listing it wastes the model's context.
_CHROME = {"minimize", "maximize", "restore", "close", "system", "system menu"}
SHOWN = 60  # how many controls the model sees; each costs context
_MAX_NODES = 4000
_READ_SECONDS = 4.0

# Clicking something labelled like this changes things beyond the window: always ask first.
_CONSEQUENTIAL = re.compile(
    r"\b(send|submit|post|publish|share|forward|delete|remove|erase|clear|discard|uninstall|reset|format|"
    r"buy|pay|order|purchase|checkout|subscribe|donate|transfer|book|reserve|"
    r"accept|agree|allow|install|update|upgrade|restart|shut ?down|sign ?out|log ?out|sign ?in|log ?in|"
    r"yes|ok|apply|save|replace|overwrite|don't save|call|video call|block|report|leave|exit)\b",
    re.I,
)
# Switches and choices change a setting by being clicked.
_SETTING_KINDS = {"checkbox", "option", "slider", "dropdown"}


@dataclass
class Element:
    id: int
    kind: str
    name: str
    value: str = ""
    password: bool = False
    enabled: bool = True
    control: Any = field(default=None, repr=False, compare=False)

    def line(self) -> str:
        """'12: button “Send”'"""
        text = f"{self.id}: {self.kind} “{self.name}”"
        if self.value and not self.password:
            text += f" = “{self.value[:60]}”"
        if not self.enabled:
            text += " (disabled)"
        return text


def needs_confirmation_to_click(element: Element | None) -> bool:
    """Plain buttons, tabs, items and links are like navigating; anything labelled like an action,
    and anything that flips a setting, asks the user first."""
    if element is None:
        return True
    return bool(_CONSEQUENTIAL.search(element.name)) or element.kind in _SETTING_KINDS


# The only keys NOVA presses: names the model may use, and what is sent.
KEYS: dict[str, list[str]] = {
    "Enter": ["VK_RETURN"], "Escape": ["VK_ESCAPE"], "Tab": ["VK_TAB"], "Shift+Tab": ["VK_SHIFT", "VK_TAB"],
    "Up": ["VK_UP"], "Down": ["VK_DOWN"], "Left": ["VK_LEFT"], "Right": ["VK_RIGHT"],
    "PageUp": ["VK_PRIOR"], "PageDown": ["VK_NEXT"], "Home": ["VK_HOME"], "End": ["VK_END"],
    "Backspace": ["VK_BACK"], "Delete": ["VK_DELETE"], "Space": ["VK_SPACE"],
    "Ctrl+A": ["VK_CONTROL", "VK_A"], "Ctrl+C": ["VK_CONTROL", "VK_C"], "Ctrl+V": ["VK_CONTROL", "VK_V"],
    "Ctrl+X": ["VK_CONTROL", "VK_X"], "Ctrl+Z": ["VK_CONTROL", "VK_Z"], "Ctrl+Y": ["VK_CONTROL", "VK_Y"],
    "Ctrl+S": ["VK_CONTROL", "VK_S"], "Ctrl+F": ["VK_CONTROL", "VK_F"], "Ctrl+N": ["VK_CONTROL", "VK_N"],
    "Ctrl+T": ["VK_CONTROL", "VK_T"], "Ctrl+Enter": ["VK_CONTROL", "VK_RETURN"], "F5": ["VK_F5"],
}
# Keys that can send, submit, save or delete: the user confirms them.
CONSEQUENTIAL_KEYS = {"Enter", "Ctrl+Enter", "Delete", "Ctrl+S", "Ctrl+X", "Backspace"}


def find_window(query: str, windows: list[_windows.Window]) -> _windows.Window | None:
    """The open window the user means: by title or program name, closest first."""
    wanted = re.sub(r"[^a-z0-9]", "", query.lower())
    if not wanted:
        return None
    scored = []
    for window in windows:
        title = re.sub(r"[^a-z0-9]", "", window.title.lower())
        process = re.sub(r"[^a-z0-9]", "", window.process.lower())
        if wanted == title or wanted == process:
            scored.append((0, window))
        elif title.startswith(wanted) or process.startswith(wanted):
            scored.append((1, window))
        elif wanted in title or wanted in process:
            scored.append((2, window))
    return min(scored, key=lambda pair: pair[0])[1] if scored else None


def _visible(rect: Any, window: Any) -> bool:
    """A control with a size, at least partly inside its window."""
    if rect.width() <= 0 or rect.height() <= 0:
        return False
    return rect.right > window.left and rect.left < window.right and rect.bottom > window.top and rect.top < window.bottom


class AppControl:
    """One app window at a time, as the web tools have one page: read it, then act by number."""

    def __init__(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="nova-uia", initializer=self._start_com)
        self.window: _windows.Window | None = None
        # The app's main window while a dialog of its own (a file picker) is being worked in.
        self._main: _windows.Window | None = None
        self.elements: dict[int, Element] = {}

    @staticmethod
    def _start_com() -> None:
        import os

        import uiautomation as auto

        # The library writes "@AutomationLog.txt" into the working directory; names of the user's
        # controls (chat names) do not belong in a file.
        auto.Logger.SetLogFile(os.devnull)
        auto.InitializeUIAutomationInCurrentThread()

    async def _run(self, function, *args):
        return await asyncio.get_running_loop().run_in_executor(self._pool, function, *args)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # --- reading -----------------------------------------------------------------

    async def read(self, app: str) -> dict[str, Any]:
        if not _windows.IS_WINDOWS:
            raise AppError("Operating apps only works on Windows.")
        window = find_window(app, await asyncio.to_thread(_windows.list_windows))
        if window is None:
            raise AppError(f"No open window matches '{app}'. Open the app first (open_application), then read it.")
        if reason := blocked_reason(window.process, window.title):
            raise AppError(reason)
        self.window, self._main = window, None
        # In front and not minimized: a minimized app (WhatsApp, any web-based app) describes almost
        # nothing to UI Automation, and clicks need it on screen anyway. The user sees NOVA work.
        await self._run(self._bring_forward)
        return await self._run(self._read_window)

    async def reread(self) -> dict[str, Any]:
        """The window read last, as it is now (after a click or typing). If the click opened a dialog
        of the same app (WhatsApp's Attach → Document opens Windows' Open dialog), that is read
        instead; when the dialog closes, the app's main window again."""
        if self.window is None:
            raise AppError("Read the app first (read_app).")
        await asyncio.sleep(0.5)  # a dialog takes a moment to appear
        await asyncio.to_thread(self._follow_dialog)
        return await self._run(self._read_window)

    def _follow_dialog(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        if self._main is not None and not user32.IsWindow(self.window.hwnd):
            self.window, self._main = self._main, None  # the dialog closed
        foreground = user32.GetForegroundWindow()
        if not foreground or foreground == self.window.hwnd:
            return
        pid, own = wintypes.DWORD(), wintypes.DWORD()
        user32.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
        user32.GetWindowThreadProcessId(self.window.hwnd, ctypes.byref(own))
        if pid.value != own.value:
            return  # another app came to the front: not ours to follow
        length = user32.GetWindowTextLengthW(foreground)
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(foreground, title, length + 1)
        if blocked_reason(self.window.process, title.value):
            return
        if self._main is None:
            self._main = self.window
        self.window = _windows.Window(foreground, title.value, self.window.process)

    def _read_window(self) -> dict[str, Any]:
        import uiautomation as auto

        root = auto.ControlFromHandle(self.window.hwnd)
        if root is None:
            raise AppError("That window closed.")
        elements: list[Element] = []
        texts: list[str] = []
        window_rect = root.BoundingRectangle
        seen: set[tuple] = set()
        started, visited = time.monotonic(), 0
        stack = [(root, 0)]
        while stack and visited < _MAX_NODES and time.monotonic() - started < _READ_SECONDS:
            control, depth = stack.pop()
            visited += 1
            try:
                children = control.GetChildren()
            except Exception:  # A control can vanish while being read.
                continue
            for child in reversed(children):
                stack.append((child, depth + 1))
            if control is root:
                continue
            try:
                kind = _KINDS.get(control.ControlTypeName)
                name = " ".join((control.Name or "").split())[:80]
                if kind is None:
                    if control.ControlTypeName == "TextControl" and name and len(texts) < 40:
                        texts.append(name)
                    continue
                if control.IsOffscreen or name.lower() in _CHROME:
                    continue
                # Only what is really on screen. WhatsApp's window also carries the hidden buttons of
                # the Edge engine it is built on ("App available. Install", "Search instead"); listed
                # first, the model typed into them.
                if (control.ClassName or "").startswith("Edge") or not _visible(control.BoundingRectangle, window_rect):
                    continue
                if not name and kind != "box":
                    continue
                if kind == "document" and control.AutomationId == "RootWebArea":
                    continue  # a web page's container, not something to type into
                rect = control.BoundingRectangle
                # The same control reached twice (WhatsApp exposes its page twice) is listed once.
                where = (kind, name, rect.left, rect.top, rect.right, rect.bottom)
                if where in seen:
                    continue
                seen.add(where)
                password = bool(control.IsPassword)
                value = ""
                if kind in ("box", "dropdown") and not password:
                    pattern = control.GetPattern(auto.PatternId.ValuePattern)
                    value = (pattern.Value or "") if pattern else ""
                elements.append(Element(len(elements) + 1, kind, name or "(no label)", value, password,
                                        bool(control.IsEnabled), control))
            except Exception:
                continue
        self.elements = {element.id: element for element in elements}
        shown = [element.line() for element in elements[:SHOWN]]
        result: dict[str, Any] = {"app": self.window.title, "controls": shown}
        if len(elements) > SHOWN:
            result["more_controls"] = len(elements) - SHOWN
        if texts:
            result["text"] = " · ".join(texts)[:1500]
        return result

    # --- acting ------------------------------------------------------------------

    def _element(self, element_id: int) -> Element:
        element = self.elements.get(element_id)
        if element is None or self.window is None:
            raise AppError(f"There is no control {element_id}. Read the app again (read_app) for current numbers.")
        return element

    def _bring_forward(self) -> None:
        import uiautomation as auto

        if self._minimized():
            auto.ShowWindow(self.window.hwnd, auto.SW.Restore)
            time.sleep(0.6)  # a restored web view takes a moment to describe itself again
        auto.SetForegroundWindow(self.window.hwnd)
        time.sleep(0.15)

    def _minimized(self) -> bool:
        import ctypes

        return bool(ctypes.windll.user32.IsIconic(self.window.hwnd))

    async def click(self, element_id: int) -> str:
        return await self._run(self._click, element_id)

    def _click(self, element_id: int) -> str:
        import uiautomation as auto

        element = self._element(element_id)
        control = element.control
        if not element.enabled:
            raise AppError(f"“{element.name}” is disabled.")
        for pattern_id, act in (
            (auto.PatternId.InvokePattern, lambda p: p.Invoke()),
            (auto.PatternId.TogglePattern, lambda p: p.Toggle()),
            (auto.PatternId.ExpandCollapsePattern, lambda p: p.Expand()),
        ):
            pattern = control.GetPattern(pattern_id)
            if pattern is not None:
                act(pattern)
                time.sleep(0.6)
                return f"Clicked “{element.name}”."
        # A real click on the control's own rectangle. "Selecting" a chat row in WhatsApp did not open
        # the chat; clicking it does.
        self._bring_forward()
        rect = control.BoundingRectangle
        if rect.width() > 0 and rect.height() > 0:
            control.Click(simulateMove=False)
        else:
            pattern = control.GetPattern(auto.PatternId.SelectionItemPattern)
            if pattern is None:
                raise AppError(f"“{element.name}” is not on screen. Scroll to it or read the app again.")
            pattern.Select()
        time.sleep(0.8)
        return f"Clicked “{element.name}”."

    async def type(self, element_id: int, text: str, enter: bool) -> str:
        return await self._run(self._type, element_id, text, enter)

    def _type(self, element_id: int, text: str, enter: bool) -> str:
        import uiautomation as auto

        element = self._element(element_id)
        if element.password:
            raise AppError("That is a password box. NOVA never types passwords; the user types those.")
        if element.kind not in ("box", "document", "dropdown"):
            raise AppError(f"“{element.name}” is a {element.kind}, not a box to type into.")
        control = element.control
        pattern = control.GetPattern(auto.PatternId.ValuePattern)
        if pattern is not None and not pattern.IsReadOnly and element.kind != "document":
            pattern.SetValue(text)
        else:
            # Rich text boxes (a chat's message box) take a paste. The text goes through the clipboard,
            # never through key sequences, so nothing in it can act as a key; the clipboard is restored.
            self._bring_forward()
            control.SetFocus()
            paste_text(text)
        if enter:
            self._bring_forward()
            control.SetFocus()
            auto.SendKey(auto.Keys.VK_RETURN)
        time.sleep(0.3)
        return f"Typed into “{element.name}”" + (" and pressed Enter." if enter else ".")

    async def press(self, keys: str) -> str:
        if keys not in KEYS:
            raise AppError(f"NOVA presses only these keys: {', '.join(KEYS)}.")
        if self.window is None:
            raise AppError("Read the app first (read_app), so NOVA knows which window the keys go to.")
        return await self._run(self._press, keys)

    def _press(self, keys: str) -> str:
        import uiautomation as auto

        self._bring_forward()
        codes = [getattr(auto.Keys, name) for name in KEYS[keys]]
        for code in codes[:-1]:
            auto.PressKey(code)
        auto.SendKey(codes[-1])
        for code in reversed(codes[:-1]):
            auto.ReleaseKey(code)
        time.sleep(0.3)
        return f"Pressed {keys} in {self.window.title}."


def paste_text(text: str) -> None:
    """Paste `text` where the keyboard focus is, then put back what the clipboard held."""
    import uiautomation as auto

    try:
        previous = auto.GetClipboardText()
    except Exception:
        previous = None
    auto.SetClipboardText(text)
    auto.PressKey(auto.Keys.VK_CONTROL)
    auto.SendKey(auto.Keys.VK_V)
    auto.ReleaseKey(auto.Keys.VK_CONTROL)
    time.sleep(0.3)
    if previous is not None:
        auto.SetClipboardText(previous)
