"""The user's accounts (Google for Gmail and Calendar, GitHub) and the tools they unlock.

A service's tools are registered only while it is connected: every tool definition costs
context on every turn, and the model should not reach for something that cannot work. In their
place is one small tool per service that says it is not connected: with only a line in the
prompt, qwen3 asked to look at the screen for "any unread email?" 3/3 and made up an empty
calendar 3/3.
"""

from __future__ import annotations

from pathlib import Path

from nova.integrations.github import GitHubAccount
from nova.integrations.google import GoogleAccount, IntegrationError
from nova.integrations.vault import Vault
from pydantic import BaseModel, Field

from nova.tools.base import Tool, ToolRegistry, ToolResult
from nova.tools.gcalendar import calendar_tools
from nova.tools.github import github_tools
from nova.tools.gmail import gmail_tools

__all__ = ["Connections", "GitHubAccount", "GoogleAccount", "IntegrationError", "Vault"]


class Connections:
    def __init__(self, data_dir: Path, google: GoogleAccount | None = None, github: GitHubAccount | None = None) -> None:
        vault = Vault(data_dir / "connections")
        self.google = google or GoogleAccount(vault)
        self.github = github or GitHubAccount(vault)
        self._google_tools = [*gmail_tools(self.google), *calendar_tools(self.google)]
        self._github_tools = github_tools(self.github)
        self._google_missing, self._github_missing = not_connected_tools()

    def sync(self, registry: ToolRegistry) -> None:
        """Make the registry match what is connected right now."""
        for connected, tools, missing in (
            (self.google.connected, self._google_tools, self._google_missing),
            (self.github.connected, self._github_tools, self._github_missing),
        ):
            for tool in (*tools, *missing):
                registry.unregister(tool.name)
            for tool in tools if connected else missing:
                registry.register(tool)

    def status(self) -> dict:
        return {"google": self.google.status(), "github": self.github.status()}

    def prompt_note(self) -> str:
        return not_connected_note(google=self.google.connected, github=self.github.connected)


def not_connected_note(google: bool, github: bool) -> str:
    """One line for the system prompt, so "check my email" gets an honest answer when it cannot."""
    missing = [name for name, connected in (("Gmail and Google Calendar", google), ("GitHub", github)) if not connected]
    if not missing:
        return ""
    return (
        f"Not connected: {' and '.join(missing)}. If the user asks for them, say they can connect them "
        "with Ctrl M in the launcher, under Connections."
    )


class _AnyArgs(BaseModel):
    request: str = Field(default="", description="What the user asked for, in their words.")


def _missing(name: str, service: str, account: str, covers: str) -> Tool:
    async def explain(args: _AnyArgs) -> ToolResult:
        return ToolResult(
            False,
            f"{service} is not connected, so NOVA cannot see or change it. Tell the user plainly, and that they can "
            f"connect their {account} with Ctrl M in the launcher, under Connections. Do not guess an answer, "
            "and do not look for it anywhere else (not on the screen, not on the web).",
        )

    return Tool(
        name=name,
        description=f"{covers} Not connected yet: use this for any such request, and it says how to connect.",
        args_model=_AnyArgs,
        handler=explain,
        describe=lambda args: f"{service} isn't connected",
        read_only=True,
    )


def not_connected_tools() -> tuple[list[Tool], list[Tool]]:
    """Stand-ins for a disconnected Google account and GitHub account, in that order."""
    return (
        [
            _missing("email_not_connected", "Gmail", "Google account", "The user's Gmail: unread, search, read, reply, send."),
            _missing("calendar_not_connected", "Google Calendar", "Google account", "The user's Google Calendar: events, schedule, adding."),
        ],
        [_missing("github_not_connected", "GitHub", "GitHub account", "The user's GitHub: notifications, pull requests, issues.")],
    )
