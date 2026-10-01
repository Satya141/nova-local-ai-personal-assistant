"""The user's GitHub account.

Connected with a token the user creates on github.com and pastes into NOVA, or, on the user's
click, the token of the GitHub CLI they are already signed in to. Either way the token is
checked against GitHub, then kept in the vault.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from collections.abc import Callable
from typing import Any

import httpx

from nova.integrations.google import IntegrationError
from nova.integrations.vault import Vault

log = logging.getLogger(__name__)

API = "https://api.github.com"
_HEADERS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}


def cli_available() -> bool:
    return shutil.which("gh") is not None


def _cli_token() -> str:
    gh = shutil.which("gh")
    if gh is None:
        raise IntegrationError("The GitHub CLI (gh) is not installed.")
    # A fixed argument list, no shell: nothing from the user or the model reaches this command.
    done = subprocess.run(
        [gh, "auth", "token"],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
        # No console window flashing up from NOVA's background process.
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    token = done.stdout.strip()
    if done.returncode != 0 or not token:
        raise IntegrationError("The GitHub CLI is not signed in. Run `gh auth login`, or paste a token instead.")
    return token


class GitHubAccount:
    def __init__(self, vault: Vault, http: Callable[[], httpx.AsyncClient] | None = None) -> None:
        self._vault = vault
        self._http = http or (lambda: httpx.AsyncClient(base_url=API, headers=_HEADERS, timeout=30))

    def status(self) -> dict[str, Any]:
        saved = self._vault.load("github")
        return {"connected": saved is not None, "account": saved.get("login") if saved else None, "cli": cli_available()}

    @property
    def connected(self) -> bool:
        return self._vault.load("github") is not None

    async def connect(self, token: str | None = None, from_cli: bool = False) -> str:
        """Check the token with GitHub and keep it. Returns the account's login."""
        if from_cli:
            token = await asyncio.to_thread(_cli_token)
        token = (token or "").strip()
        if not token:
            raise IntegrationError("Paste a GitHub token first.")
        async with self._http() as http:
            try:
                response = await http.get("/user", headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPError as exc:
                raise IntegrationError(f"Could not reach GitHub: {exc}") from exc
        if response.status_code == 401:
            raise IntegrationError("GitHub did not accept that token.")
        if response.status_code != 200:
            raise IntegrationError(f"GitHub returned an error ({response.status_code}).")
        login = response.json()["login"]
        self._vault.save("github", {"token": token, "login": login})
        return login

    def disconnect(self) -> None:
        self._vault.delete("github")

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        saved = self._vault.load("github")
        if saved is None:
            raise IntegrationError("GitHub is not connected. The user can connect it with Ctrl M → Connections.")
        async with self._http() as http:
            try:
                response = await http.request(method, path, headers={"Authorization": f"Bearer {saved['token']}"}, **kwargs)
            except httpx.HTTPError as exc:
                raise IntegrationError(f"Could not reach GitHub: {exc}") from exc
        if response.status_code == 401:
            raise IntegrationError("GitHub no longer accepts NOVA's token. The user can connect again with Ctrl M → Connections.")
        if response.status_code >= 400:
            try:
                detail = response.json().get("message")
            except ValueError:
                detail = None
            raise IntegrationError(f"GitHub returned an error ({response.status_code}): {detail or response.text[:200]}")
        return response.json() if response.content else None
