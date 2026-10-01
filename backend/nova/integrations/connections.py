"""The user's accounts (Google for Gmail and Calendar, GitHub) and the tools they unlock.

A service's tools are registered only while it is connected: every tool definition costs
context on every turn, and the model should not reach for something that cannot work.
"""

from __future__ import annotations

from pathlib import Path

from nova.integrations.github import GitHubAccount
from nova.integrations.google import GoogleAccount, IntegrationError
from nova.integrations.vault import Vault
from nova.tools.base import ToolRegistry
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

    def sync(self, registry: ToolRegistry) -> None:
        """Make the registry match what is connected right now."""
        for connected, tools in ((self.google.connected, self._google_tools), (self.github.connected, self._github_tools)):
            for tool in tools:
                registry.unregister(tool.name)
                if connected:
                    registry.register(tool)

    def status(self) -> dict:
        return {"google": self.google.status(), "github": self.github.status()}

    def prompt_note(self) -> str:
        """One line for the system prompt, so "check my email" gets an honest answer when it cannot."""
        missing = [
            name
            for name, connected in (("Gmail and Google Calendar", self.google.connected), ("GitHub", self.github.connected))
            if not connected
        ]
        if not missing:
            return ""
        return (
            f"Not connected: {' and '.join(missing)}. If the user asks for them, say they can connect them "
            "with Ctrl M in the launcher, under Connections."
        )
