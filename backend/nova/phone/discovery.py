"""Letting the phone find NOVA on whatever Wi-Fi the two share.

The PC's address changes from network to network, so the phone app lives at a name instead:
`nova-<pc name>.local`. NOVA answers multicast DNS (RFC 6762) questions for that name itself,
on the network it is listening on, because Windows does not reliably answer for its own name.
Android 12 and later look up `.local` names this way.

It also reads how Windows classifies the network (on a "Public" network the firewall turns the
phone away, and only the user can decide to mark a network Private), and finds Tailscale, which
lets the phone reach NOVA from anywhere.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import re
import socket
import struct
import subprocess
import sys
import threading

log = logging.getLogger(__name__)

GROUP = "224.0.0.251"
PORT = 5353
TTL = 120
_A, _AAAA, _NSEC, _ANY = 1, 28, 47, 255
_CACHE_FLUSH = 0x8000


def nova_name(hostname: str | None = None) -> str:
    """NOVA's name on the local network, e.g. nova-satya-laptop.local."""
    label = re.sub(r"[^a-z0-9-]+", "-", (hostname or socket.gethostname()).lower()).strip("-")[:50]
    return f"nova-{label}.local" if label else "nova.local"


def _encode_name(name: str) -> bytes:
    return b"".join(bytes([len(part)]) + part.encode("ascii") for part in name.split(".") if part) + b"\x00"


def _read_name(packet: bytes, offset: int, depth: int = 0) -> tuple[str, int]:
    """A DNS name at `offset` (following compression pointers), and the offset after it."""
    labels: list[str] = []
    while True:
        if offset >= len(packet) or depth > 10:
            raise ValueError("bad name")
        length = packet[offset]
        if length == 0:
            return ".".join(labels), offset + 1
        if length & 0xC0 == 0xC0:
            pointer = struct.unpack_from(">H", packet, offset)[0] & 0x3FFF
            rest, _ = _read_name(packet, pointer, depth + 1)
            labels.append(rest)
            return ".".join(labels), offset + 2
        labels.append(packet[offset + 1 : offset + 1 + length].decode("ascii", "replace"))
        offset += 1 + length


def questions(packet: bytes) -> list[tuple[str, int, bool]]:
    """The (name, type, wants-unicast-reply) questions in an mDNS query; [] for anything else."""
    if len(packet) < 12:
        return []
    _, flags, count = struct.unpack_from(">HHH", packet, 0)
    if flags & 0x8000:  # a response, not a query
        return []
    found, offset = [], 12
    try:
        for _ in range(min(count, 32)):
            name, offset = _read_name(packet, offset)
            qtype, qclass = struct.unpack_from(">HH", packet, offset)
            offset += 4
            found.append((name.lower(), qtype, bool(qclass & 0x8000)))
    except (ValueError, struct.error):
        return []
    return found


def answer(name: str, address: str, qtype: int, query_id: int = 0, echo: bytes = b"") -> bytes:
    """A response saying `name` is at `address`. For an IPv6 question, say there is no IPv6
    address (an NSEC record), so the phone does not wait for one."""
    encoded = _encode_name(name)
    if qtype == _AAAA:
        # NSEC: the next name (ourselves) and a bitmap with only type A (window 0, 1 byte, bit 1).
        rdata = encoded + b"\x00\x01\x40"
        record = encoded + struct.pack(">HHIH", _NSEC, 1 | _CACHE_FLUSH, TTL, len(rdata)) + rdata
    else:
        record = encoded + struct.pack(">HHIH", _A, 1 | _CACHE_FLUSH, TTL, 4) + socket.inet_aton(address)
    # Legacy unicast replies echo the query's id and question; multicast ones carry neither.
    question_count = 1 if echo else 0
    return struct.pack(">HHHHHH", query_id, 0x8400, question_count, 1, 0, 0) + echo + record


class Responder:
    """Answers "where is nova-….local?" on one network interface, in a background thread."""

    def __init__(self, name: str, address: str) -> None:
        self.name = name.lower()
        self.address = address
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> bool:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            # Windows' own DNS client also listens on 5353; share the port with it.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", PORT))
            membership = socket.inet_aton(GROUP) + socket.inet_aton(self.address)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(self.address))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            sock.settimeout(0.5)
        except OSError as exc:
            log.warning("Could not answer for %s on the network: %s", self.name, exc)
            return False
        self._socket = sock
        self._thread = threading.Thread(target=self._run, name="mdns-responder", daemon=True)
        self._thread.start()
        self._send(answer(self.name, self.address, _A), (GROUP, PORT))  # announce, for caches
        return True

    def _send(self, packet: bytes, to: tuple[str, int]) -> None:
        try:
            if self._socket is not None:
                self._socket.sendto(packet, to)
        except OSError:
            pass

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                packet, sender = self._socket.recvfrom(9000)
            except TimeoutError:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                continue
            for name, qtype, unicast in questions(packet):
                if name != self.name or qtype not in (_A, _AAAA, _ANY):
                    continue
                if sender[1] != PORT:
                    # A one-shot ("legacy") resolver: reply to it directly, echoing its question.
                    query_id = struct.unpack_from(">H", packet, 0)[0]
                    echo = _encode_name(name) + struct.pack(">HH", qtype, 1)
                    self._send(answer(self.name, self.address, qtype, query_id, echo), sender)
                else:
                    self._send(answer(self.name, self.address, qtype), sender if unicast else (GROUP, PORT))

    def stop(self) -> None:
        self._stop.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._socket = self._thread = None


# Tailscale gives each device an address here (the "shared address space" of RFC 6598), the
# same wherever the device is. Other VPNs use it too, so the interface must also be Tailscale's.
OVERLAY = ipaddress.ip_network("100.64.0.0/10")

# A fixed script: nothing from the user or the model is ever put into it.
_INTERFACES = (
    "Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue | ForEach-Object { "
    "$p = Get-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex -ErrorAction SilentlyContinue; "
    "[pscustomobject]@{ address = $_.IPAddress; alias = $_.InterfaceAlias; "
    "name = [string]$p.Name; category = [string]$p.NetworkCategory } "
    "} | ConvertTo-Json -Compress"
)


def interfaces() -> list[dict[str, str]]:
    """The PC's IPv4 addresses, each with its interface and how Windows classifies its network
    ("Public", "Private" or "DomainAuthenticated")."""
    if sys.platform != "win32":
        return []
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _INTERFACES],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        rows = json.loads(result.stdout or "null")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return []
    rows = rows if isinstance(rows, list) else [rows] if rows else []
    return [
        {key: str(row.get(key) or "") for key in ("address", "alias", "name", "category")}
        for row in rows
        if isinstance(row, dict)
    ]


def network_profile(address: str, rows: list[dict[str, str]]) -> dict[str, str] | None:
    """{"name", "category"} of the network the PC reaches through `address`."""
    for row in rows:
        if row["address"] == address and row["category"]:
            return {"name": row["name"], "category": row["category"]}
    return None


def tailscale_address(rows: list[dict[str, str]]) -> str | None:
    """The PC's Tailscale address, if Tailscale is installed and connected."""
    for row in rows:
        try:
            ip = ipaddress.ip_address(row["address"])
        except ValueError:
            continue
        if ip in OVERLAY and "tailscale" in row["alias"].lower():
            return row["address"]
    return None