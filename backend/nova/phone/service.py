"""Phone access: NOVA's API and phone app, offered on the home network, only while switched on.

The desktop keeps its own loopback listener and launch token. Phones get listeners on the PC's
home-network address: a plain-HTTP setup page that hands out NOVA's certificate, and the app
over HTTPS, where requests must come from a private address and carry the key of a paired
phone. It is off until the user turns it on, and remembered across restarts.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import ssl
from collections.abc import Callable
from typing import Any

import segno
import uvicorn
from cryptography.x509.oid import NameOID

from nova.phone.certs import Certificates
from nova.phone.devices import DeviceStore, PairingCode
from nova.phone.discovery import Responder, network_profile, nova_name
from nova.phone.setup import setup_app
from nova.settings import SettingsStore

log = logging.getLogger(__name__)

SETTING = "phone_access"


def _rank(address: str) -> int:
    # Home routers hand out 192.168.x.x, sometimes 10.x; VPNs (Cloudflare WARP, for one) and
    # virtual switches like 172.16-31.x. Lower is better; -1 means unusable.
    ip = ipaddress.ip_address(address)
    if ip.is_loopback or ip.is_link_local or not ip.is_private:
        return -1
    if address.startswith("192.168."):
        return 0
    if address.startswith("10."):
        return 1
    return 2


def home_network_address() -> str | None:
    """The PC's address on the home network, as a phone on the same Wi-Fi would reach it."""
    found: set[str] = set()
    try:
        found |= {info[4][0] for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
    except OSError:
        pass
    try:
        # The address the PC would use to reach the internet; nothing is sent.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            found.add(probe.getsockname()[0])
    except OSError:
        pass
    usable = sorted((a for a in found if _rank(a) >= 0), key=lambda a: (_rank(a), a))
    return usable[0] if usable else None


def qr_svg(text: str) -> str:
    """A QR code as an SVG data address, for an <img> on the PC."""
    return segno.make(text, error="m").svg_data_uri(scale=6, border=2, dark="#1d1b19", light="#ffffff")


class _Listener:
    """One uvicorn server on a socket NOVA binds itself (uvicorn ends the process when it cannot)."""

    def __init__(self, app: Any, name: str, ssl_context: ssl.SSLContext | None = None) -> None:
        factory = (lambda config, default: ssl_context) if ssl_context else None
        config = uvicorn.Config(
            app, lifespan="off", log_level="warning", access_log=False, ssl_context_factory=factory
        )
        self._server = uvicorn.Server(config)
        self._name = name
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, sock: socket.socket, on_error: Callable[[str], None]) -> None:
        async def serve() -> None:
            try:
                await self._server.serve(sockets=[sock])
            except Exception as exc:
                log.exception("The %s stopped", self._name)
                on_error(f"Phone access stopped: {exc}")
            finally:
                sock.close()

        self._task = asyncio.create_task(serve(), name=self._name)

    async def stop(self) -> None:
        self._server.should_exit = True
        if self._task is not None:
            # Shielded, so that cancelling whoever is stopping us (the network watcher, when
            # phone access is switched off mid-change) cancels them, and is not swallowed here.
            try:
                await asyncio.wait_for(asyncio.shield(self._task), 5)
            except TimeoutError:
                self._task.cancel()



WATCH_SECONDS = 5
# How often (in watch rounds) to re-read whether Windows calls the network Public or Private.
PROFILE_EVERY = 6
OFFLINE = "Not connected to a network. Phone access continues by itself when the PC joins one."


class PhoneAccess:
    """Listeners on the PC's current network: a plain-HTTP setup page that hands out NOVA's
    certificate, and the phone app and API over HTTPS. While switched on, NOVA follows the PC
    from network to network, and answers for its .local name on each."""

    def __init__(
        self,
        app: Any,
        settings: SettingsStore,
        devices: DeviceStore,
        certificates: Certificates,
        port: int,
        secure_port: int,
        host: str | None = None,
        name: str | None = None,
    ) -> None:
        self._app = app
        self._settings = settings
        self.devices = devices
        self.certificates = certificates
        self.pairing = PairingCode()
        self.name = name or nova_name()
        self._port = port
        self._secure_port = secure_port
        self._host_override = host
        self._listeners: list[_Listener] = []
        self._responder: Responder | None = None
        self._watcher: asyncio.Task | None = None
        self.address: tuple[str, int] | None = None
        self.network: dict[str, str] | None = None
        self.error: str | None = None
        # Called when something the PC's panel shows changes on its own (the network, say).
        self.on_change: Callable[[], None] = lambda: None

    @property
    def enabled(self) -> bool:
        return self._settings.get_bool(SETTING)

    @property
    def running(self) -> bool:
        return bool(self._listeners) and all(listener.running for listener in self._listeners)

    @property
    def url(self) -> str | None:
        """The setup page: what the phone opens first, by QR code or by typing it."""
        return f"http://{self.address[0]}:{self._port}" if self.address and self.running else None

    @property
    def app_url(self) -> str | None:
        """The phone app at this network's address, over HTTPS."""
        return f"https://{self.address[0]}:{self.address[1]}" if self.address and self.running else None

    @property
    def name_url(self) -> str | None:
        """The phone app at NOVA's .local name: the same on every network, so pairing carries over."""
        return f"https://{self.name}:{self._secure_port}" if self._responder is not None and self.running else None

    def serves(self, server: tuple[str, int] | None) -> bool:
        """Did this request arrive on the phone app's (HTTPS) listener?"""
        return self.running and server is not None and tuple(server) == self.address

    def _authority(self) -> tuple[str, str, bytes]:
        certificate, _ = self.certificates.authority()
        name = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        return str(name), self.certificates.fingerprint(), self.certificates.authority_der()

    def status(self) -> dict[str, Any]:
        running = self.running
        name, fingerprint, _ = self._authority() if running else ("", "", b"")
        return {
            "enabled": self.enabled,
            "running": running,
            "url": self.url,
            "app_url": self.app_url,
            "name_url": self.name_url,
            "network": self.network,
            "certificate": {"name": name, "fingerprint": fingerprint[:11]} if running else None,
            "error": self.error,
            "devices": [d.to_dict() for d in self.devices.all()],
        }

    def pairing_link(self, code: str) -> str | None:
        """What the QR code on the PC holds: the setup page, carrying the code to the phone app."""
        return f"{self.url}/#{code}" if self.url else None

    def _app_urls(self) -> list[str]:
        return [url for url in (self.name_url, self.app_url) if url]

    async def set_enabled(self, on: bool) -> None:
        self._settings.set_bool(SETTING, on)
        if on:
            await self.start()
        else:
            await self.stop()

    async def start(self) -> None:
        if self._watcher is not None or self.running:
            return
        self.error = None
        host = self._host_override or await asyncio.to_thread(home_network_address)
        if host is None:
            self.error = OFFLINE
        else:
            await self._open(host)
        if self._host_override is None:
            self._watcher = asyncio.create_task(self._watch(), name="phone-network-watch")

    async def _watch(self) -> None:
        """Follow the PC to whichever network it is on: home, office, a friend's Wi-Fi."""
        rounds = 0
        while True:
            await asyncio.sleep(WATCH_SECONDS)
            rounds += 1
            try:
                host = await asyncio.to_thread(home_network_address)
                current = self.address[0] if self.address else None
                if host != current or (host is not None and not self.running):
                    log.info("Phone access: network changed (%s -> %s)", current, host)
                    await self._close()
                    if host is None:
                        self.error = OFFLINE
                    else:
                        await self._open(host)
                    self.on_change()
                elif host is not None and rounds % PROFILE_EVERY == 0:
                    profile = await asyncio.to_thread(network_profile, host)
                    if profile != self.network:
                        self.network = profile
                        self.on_change()
            except Exception:
                log.exception("Phone access: following the network failed")

    async def _open(self, host: str) -> None:
        self.error = None
        announce = not ipaddress.ip_address(host).is_loopback
        names = (self.name,) if announce else ()
        try:
            context = await asyncio.to_thread(self.certificates.server_context, host, names)
        except Exception as exc:
            log.exception("Could not make the phone certificate")
            self.error = f"Could not set up encryption for phone access: {exc}"
            return
        sockets: list[socket.socket] = []
        for port in (self._port, self._secure_port):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind((host, port))
            except OSError as exc:
                sock.close()
                for bound in sockets:
                    bound.close()
                self.error = f"Could not listen on {host}:{port} ({exc.strerror or exc})."
                return
            sock.listen(64)
            sock.setblocking(False)
            sockets.append(sock)
        self.address = (host, self._secure_port)
        setup = setup_app(self._app_urls, self._authority)
        self._listeners = [_Listener(setup, "phone-setup"), _Listener(self._app, "phone-app", context)]
        for listener, sock in zip(self._listeners, sockets, strict=True):
            listener.start(sock, self._failed)
        if announce:
            responder = Responder(self.name, host)
            self._responder = responder if await asyncio.to_thread(responder.start) else None
            self.network = await asyncio.to_thread(network_profile, host)
        log.info("Phone access on http://%s:%s (setup) and https://%s:%s", host, self._port, host, self._secure_port)

    def _failed(self, message: str) -> None:
        self.error = message

    async def _close(self) -> None:
        self.pairing.clear()
        if self._responder is not None:
            await asyncio.to_thread(self._responder.stop)
            self._responder = None
        for listener in self._listeners:
            await listener.stop()
        self._listeners = []
        self.address = None
        self.network = None

    async def stop(self) -> None:
        if self._watcher is not None:
            self._watcher.cancel()
            try:
                await self._watcher
            except asyncio.CancelledError:
                pass
            self._watcher = None
        await self._close()