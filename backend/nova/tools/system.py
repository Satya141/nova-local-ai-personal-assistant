"""Application control: open and close apps.

The model only ever supplies an app *name*. Opening resolves that name against
the Start menu index and launches the matching entry; nothing the model writes
is executed as a command.
"""

from __future__ import annotations

import difflib
import json
import re
import subprocess
import time
from dataclasses import dataclass

from pydantic import BaseModel, Field

from nova.tools import _windows
from nova.tools.base import Risk, Tool, ToolResult

# Spoken names that do not appear in the Start menu entry's name.
_ALIASES = {
    "vs code": "visual studio code",
    "vscode": "visual studio code",
    "code": "visual studio code",
    "explorer": "file explorer",
    "files": "file explorer",
    "cmd": "command prompt",
    "edge": "microsoft edge",
    "chrome": "google chrome",
}

_INDEX_TTL = 300.0
_TYPO_RATIO = 0.88
_GUESS = 3  # match_app rank for a typo-tolerant match
_NOT_WINDOWS = ToolResult(False, "Application control is only implemented on Windows so far.")


@dataclass(frozen=True)
class App:
    name: str
    app_id: str


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def match_app(query: str, apps: list[App]) -> tuple[App | None, bool, list[str]]:
    """Resolve a spoken app name.

    Returns (match, is_guess, suggestions). `is_guess` means the match came from
    typo tolerance, so the caller must say which app it really is.
    """
    wanted = _norm(query)
    wanted = _ALIASES.get(wanted, wanted)
    if not wanted:
        return None, False, []

    def rank(app: App) -> int | None:
        name = _norm(app.name)
        # A trailing version or year is not part of how people say the name:
        # "Visual Studio" means "Visual Studio 2022", not "Visual Studio Code".
        if name == wanted or re.sub(r"( \d+)+$", "", name) == wanted:
            return 0
        if name.startswith(wanted + " "):
            return 1
        if set(wanted.split()) <= set(name.split()):
            return 2
        # Typo tolerance only. It must stay tight: at 0.8 "photoshop" matched "Photos".
        if difflib.SequenceMatcher(None, wanted, name).ratio() >= _TYPO_RATIO:
            return _GUESS
        return None

    ranked = [(score, len(app.name), app) for app in apps if (score := rank(app)) is not None]
    if ranked:
        # Best rank first; among equals prefer the shortest name, so
        # "Visual Studio Code" beats "Visual Studio Code - Insiders".
        ranked.sort(key=lambda item: (item[0], item[1]))
        score, _, app = ranked[0]
        return app, score == _GUESS, []

    by_norm = {_norm(app.name): app.name for app in apps}
    close = difflib.get_close_matches(wanted, list(by_norm), n=5, cutoff=0.5)
    return None, False, [by_norm[name] for name in close]


class _AppIndex:
    def __init__(self) -> None:
        self._apps: list[App] = []
        self._loaded_at = 0.0

    async def get(self) -> list[App]:
        if not self._apps or time.monotonic() - self._loaded_at > _INDEX_TTL:
            rows = await _windows.powershell_json("Get-StartApps | Select-Object Name,AppID")
            self._apps = [
                App(row["Name"], row["AppID"])
                for row in rows
                # Start menu links to web pages have a URL as their AppID.
                if row.get("Name") and row.get("AppID") and "://" not in row["AppID"]
            ]
            self._loaded_at = time.monotonic()
        return self._apps


_index = _AppIndex()


class OpenApplicationArgs(BaseModel):
    name: str = Field(description="Name of the application as the user said it, e.g. 'VS Code' or 'Notepad'.")


async def _open_application(args: OpenApplicationArgs) -> ToolResult:
    if not _windows.IS_WINDOWS:
        return _NOT_WINDOWS
    app, is_guess, suggestions = match_app(args.name, await _index.get())
    if app is None:
        hint = f" Closest installed apps: {', '.join(suggestions)}." if suggestions else ""
        return ToolResult(
            False, f"'{args.name}' is not installed on this computer. Nothing was opened.{hint}"
        )
    subprocess.Popen(
        ["explorer.exe", f"shell:AppsFolder\\{app.app_id}"],
        creationflags=_windows.NO_WINDOW,
    )
    outcome = {"opened": app.name}
    if is_guess:
        outcome["note"] = (
            f"No app is named exactly '{args.name}'. Opened the closest match, '{app.name}'. "
            "Tell the user which app was opened."
        )
    return ToolResult(True, json.dumps(outcome))


class CloseApplicationArgs(BaseModel):
    name: str = Field(description="Name of the running application to close, e.g. 'Notepad'.")


def match_windows(query: str, windows: list[_windows.Window]) -> list[_windows.Window]:
    """Pick the open windows a spoken app name refers to."""
    spoken = _norm(query)
    if not spoken:
        return []
    wanted = _ALIASES.get(spoken, spoken)
    process_names = {spoken, wanted, spoken.replace(" ", ""), wanted.replace(" ", "")}
    matches = []
    for window in windows:
        title = _norm(window.title)
        # Apps put their name last in the title ("notes.txt - Notepad"). Matching
        # only there keeps "close chrome" away from a document that mentions Chrome.
        if _norm(window.process) in process_names or title == wanted or title.endswith(" " + wanted):
            matches.append(window)
    return matches


async def _close_application(args: CloseApplicationArgs) -> ToolResult:
    if not _windows.IS_WINDOWS:
        return _NOT_WINDOWS
    matches = match_windows(args.name, _windows.list_windows())
    if not matches:
        return ToolResult(False, f"No open window matches '{args.name}'.")
    # Closing the window, not killing the process: the app can still prompt
    # about unsaved work, and a Store app's shared host process is left alone.
    asked = [window.title for window in matches if _windows.request_close(window.hwnd)]
    if not asked:
        return ToolResult(False, f"Windows refused to close '{args.name}'.")
    return ToolResult(
        True,
        json.dumps(
            {
                "closed": asked,
                "note": "Done; the user already approved this. An app with unsaved work shows its own save prompt.",
            }
        ),
    )


open_application = Tool(
    name="open_application",
    description="Open (launch) an application installed on this computer by name.",
    args_model=OpenApplicationArgs,
    handler=_open_application,
    describe=lambda args: f"Open {args.name}",
)

close_application = Tool(
    name="close_application",
    description=(
        "Close a running application by name. NOVA shows the user its own confirmation "
        "before this runs, so call it directly."
    ),
    args_model=CloseApplicationArgs,
    handler=_close_application,
    describe=lambda args: f"Close {args.name}. Unsaved work in it could be lost.",
    risk=Risk.MEDIUM,
    requires_confirmation=True,
)
