"""The user's Google account, for Gmail and Google Calendar.

Sign-in is Google's flow for installed apps: NOVA opens the consent page in the user's own
browser, and Google sends the browser back to a one-shot server on 127.0.0.1. The user signs
in on Google's page; NOVA never sees a password. PKCE and a random `state` tie the answer to
the request that asked for it.

The OAuth client (a "Desktop app" client from the user's own Google Cloud project) is the
user's to create; Google treats its secret as not confidential for installed apps.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import secrets
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from nova.integrations.vault import Vault

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
PROFILE_URL = "https://gmail.googleapis.com/gmail/v1/users/me/profile"
SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",  # search and read
    "https://www.googleapis.com/auth/gmail.compose",  # drafts and sending, each confirmed
    "https://www.googleapis.com/auth/calendar.events",  # read and add events
)
SIGN_IN_SECONDS = 300

_DONE_PAGE = (
    "<!doctype html><meta charset=utf-8><title>NOVA</title>"
    "<body style='font:16px system-ui;margin:3em;color:#222'><h1 style='font-size:20px'>{title}</h1><p>{text}</p>"
)


class IntegrationError(RuntimeError):
    """Something the user can act on; the message is shown to the model and the user."""


def parse_client(text: str) -> dict[str, str]:
    """Google's downloaded client file: {"installed": {"client_id": ..., "client_secret": ...}}."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise IntegrationError("That is not a Google client file (it is not JSON).") from exc
    section = data.get("installed") if isinstance(data, dict) else None
    if not isinstance(section, dict) or not section.get("client_id"):
        kind = next(iter(data), "unknown") if isinstance(data, dict) else "unknown"
        raise IntegrationError(
            f"That client file is of type '{kind}'. Create an OAuth client of type 'Desktop app' and download its JSON."
        )
    return {"client_id": section["client_id"], "client_secret": section.get("client_secret", "")}


class GoogleAccount:
    def __init__(self, vault: Vault, http: Callable[[], httpx.AsyncClient] | None = None) -> None:
        self._vault = vault
        self._http = http or (lambda: httpx.AsyncClient(timeout=30))
        self._lock = asyncio.Lock()
        self._signing_in: asyncio.Task | None = None
        self._client: httpx.AsyncClient | None = None
        self.last_error: str | None = None

    def _api(self) -> httpx.AsyncClient:
        """One client for API calls, kept open: each new one cost a fresh TLS handshake with Google,
        and a mail search makes eleven calls."""
        if self._client is None or self._client.is_closed:
            self._client = self._http()
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- state ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        token = self._vault.load("google")
        return {
            "client": self._vault.load("google-client") is not None,
            "connected": token is not None,
            "account": token.get("account") if token else None,
            "signing_in": self._signing_in is not None and not self._signing_in.done(),
            "error": self.last_error,
        }

    @property
    def connected(self) -> bool:
        return self._vault.load("google") is not None

    def set_client(self, text: str) -> None:
        self._vault.save("google-client", parse_client(text))

    # --- sign-in ----------------------------------------------------------------

    async def start_sign_in(self, on_done: Callable[[], None] = lambda: None) -> str:
        """Start listening for Google's answer and return the consent page's address."""
        client = self._vault.load("google-client")
        if client is None:
            raise IntegrationError("Add the Google client file first.")
        if self._signing_in and not self._signing_in.done():
            self._signing_in.cancel()
        verifier = secrets.token_urlsafe(64)
        state = secrets.token_urlsafe(24)
        answer: asyncio.Future[dict[str, str]] = asyncio.get_running_loop().create_future()

        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                line = (await asyncio.wait_for(reader.readline(), 10)).decode("latin-1")
                while (await asyncio.wait_for(reader.readline(), 10)) not in (b"\r\n", b"\n", b""):
                    pass
                parts = line.split(" ")
                target = urlparse(parts[1] if len(parts) > 1 else "/")
                params = {k: v[0] for k, v in parse_qs(target.query).items()}
                if target.path != "/callback":
                    title, text, status = "Not found", "", "404 Not Found"
                elif params.get("state") != state:
                    title, text, status = "Sign-in did not match", "Start again from NOVA.", "400 Bad Request"
                else:
                    if not answer.done():
                        answer.set_result(params)
                    ok = "code" in params
                    title = "NOVA is connected to Google" if ok else "Google sign-in was cancelled"
                    text = "You can close this tab." if ok else "Nothing was connected. You can close this tab."
                    status = "200 OK"
                body = _DONE_PAGE.format(title=title, text=text).encode("utf-8")
                writer.write(
                    f"HTTP/1.1 {status}\r\nContent-Type: text/html; charset=utf-8\r\n"
                    f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body
                )
                await writer.drain()
            except Exception:  # A stray or broken request must not end the sign-in.
                log.debug("Ignored a request to the sign-in listener", exc_info=True)
            finally:
                writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        redirect = f"http://127.0.0.1:{port}/callback"
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        url = AUTH_URL + "?" + urlencode(
            {
                "client_id": client["client_id"],
                "redirect_uri": redirect,
                "response_type": "code",
                "scope": " ".join(SCOPES),
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                # A refresh token, so NOVA stays connected without asking again.
                "access_type": "offline",
                "prompt": "consent",
            }
        )

        async def finish() -> None:
            self.last_error = None
            try:
                async with server:
                    params = await asyncio.wait_for(answer, SIGN_IN_SECONDS)
                if "code" not in params:
                    self.last_error = "Google sign-in was cancelled."
                    return
                await self._exchange(client, params["code"], verifier, redirect)
            except TimeoutError:
                self.last_error = "Google sign-in timed out. Try again."
            except IntegrationError as exc:
                self.last_error = str(exc)
            except Exception as exc:
                log.exception("Google sign-in failed")
                self.last_error = f"Google sign-in failed: {exc}"
            finally:
                on_done()

        self._signing_in = asyncio.create_task(finish(), name="google-sign-in")
        return url

    async def _exchange(self, client: dict[str, str], code: str, verifier: str, redirect: str) -> None:
        async with self._http() as http:
            response = await http.post(
                TOKEN_URL,
                data={
                    "client_id": client["client_id"],
                    "client_secret": client["client_secret"],
                    "code": code,
                    "code_verifier": verifier,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect,
                },
            )
            tokens = response.json()
            if response.status_code != 200 or "refresh_token" not in tokens:
                raise IntegrationError(f"Google did not grant access: {tokens.get('error_description') or tokens.get('error') or response.status_code}")
            granted = set(tokens.get("scope", "").split())
            missing = [s.rsplit("/", 1)[-1] for s in SCOPES if s not in granted]
            if missing:
                raise IntegrationError(f"Google sign-in did not allow: {', '.join(missing)}. Connect again and tick every box.")
            profile = await http.get(PROFILE_URL, headers={"Authorization": f"Bearer {tokens['access_token']}"})
            account = profile.json().get("emailAddress") if profile.status_code == 200 else None
        self._vault.save(
            "google",
            {
                "refresh_token": tokens["refresh_token"],
                "access_token": tokens["access_token"],
                "expires_at": time.time() + float(tokens.get("expires_in", 3600)),
                "account": account,
            },
        )

    async def disconnect(self) -> None:
        token = self._vault.load("google")
        self._vault.delete("google")
        if token:
            try:
                async with self._http() as http:
                    await http.post(REVOKE_URL, data={"token": token["refresh_token"]})
            except httpx.HTTPError:
                log.warning("Could not revoke the Google token; it was deleted locally")

    # --- calls ------------------------------------------------------------------

    async def _access_token(self, force_refresh: bool = False) -> str:
        async with self._lock:
            token = self._vault.load("google")
            client = self._vault.load("google-client")
            if token is None or client is None:
                raise IntegrationError("Google is not connected. The user can connect it with Ctrl M → Connections.")
            if not force_refresh and token["expires_at"] - 60 > time.time():
                return token["access_token"]
            async with self._http() as http:
                response = await http.post(
                    TOKEN_URL,
                    data={
                        "client_id": client["client_id"],
                        "client_secret": client["client_secret"],
                        "refresh_token": token["refresh_token"],
                        "grant_type": "refresh_token",
                    },
                )
            fresh = response.json()
            if response.status_code != 200:
                if fresh.get("error") == "invalid_grant":
                    self._vault.delete("google")
                    raise IntegrationError(
                        "Google sign-in has expired or was revoked. The user can connect again with Ctrl M → Connections."
                    )
                raise IntegrationError(f"Google refused to refresh the sign-in ({response.status_code}).")
            token.update(access_token=fresh["access_token"], expires_at=time.time() + float(fresh.get("expires_in", 3600)))
            self._vault.save("google", token)
            return token["access_token"]

    async def request(self, method: str, url: str, **kwargs: Any) -> Any:
        """A Google API call with the user's token; returns the decoded JSON (or None for an empty reply)."""
        for attempt in range(2):
            access = await self._access_token(force_refresh=attempt > 0)
            try:
                response = await self._api().request(method, url, headers={"Authorization": f"Bearer {access}"}, **kwargs)
            except httpx.HTTPError as exc:
                raise IntegrationError(f"Could not reach Google: {exc}") from exc
            if response.status_code == 401 and attempt == 0:
                continue
            if response.status_code >= 400:
                try:
                    detail = response.json().get("error", {}).get("message")
                except ValueError:
                    detail = None
                raise IntegrationError(f"Google returned an error ({response.status_code}): {detail or response.text[:200]}")
            return response.json() if response.content else None
        raise IntegrationError("Google did not accept NOVA's sign-in.")
