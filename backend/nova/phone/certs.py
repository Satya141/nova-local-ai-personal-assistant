"""NOVA's own certificates, so phones talk to NOVA over HTTPS.

A small certificate authority, made once per PC, signs a fresh certificate for the PC's
home-network address every time phone access starts. A phone installs the authority once;
after that Chrome trusts NOVA without warnings, and nobody else on the Wi-Fi can read the
traffic or pretend to be NOVA.

A certificate authority installed on a phone is powerful, so this one is name-constrained to
private IP addresses: even if its key leaked, it could not vouch for any website. The key is
kept in the DPAPI vault, never in a plain file.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import ipaddress
import secrets
import socket
import ssl
import tempfile
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from nova.integrations.vault import Vault

AUTHORITY_DAYS = 3650
# Chrome refuses longer from public authorities; NOVA holds itself to the same.
SERVER_DAYS = 397
# Make a new authority this long before the old one expires (the phone installs it again).
RENEW_DAYS = 30
# The only addresses and names NOVA's authority may vouch for: private IP addresses, and names
# under .local, which exist only on the local network and never on the internet. (Some DNS
# entry must be listed: with none at all, every DNS name would be permitted.)
PERMITTED_NETWORKS = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8")
PERMITTED_DOMAIN = "local"
_KEY_NAME = "phone-authority"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Certificates:
    def __init__(self, folder: Path, vault: Vault | None = None) -> None:
        self._folder = folder
        self._vault = vault or Vault(folder)
        self._authority: tuple[x509.Certificate, ec.EllipticCurvePrivateKey] | None = None

    @property
    def _authority_path(self) -> Path:
        return self._folder / "nova-authority.crt"

    def authority(self) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
        """NOVA's certificate authority, made on first use and kept until it nears expiry."""
        if self._authority is None or self._expiring(self._authority[0]):
            self._authority = self._load() or self._create()
        return self._authority

    def authority_der(self) -> bytes:
        """The authority's certificate, as the phone downloads and installs it."""
        return self.authority()[0].public_bytes(serialization.Encoding.DER)

    def fingerprint(self) -> str:
        """The authority's SHA-256 fingerprint, as Android shows it under the certificate's details."""
        digest = hashlib.sha256(self.authority_der()).hexdigest().upper()
        return ":".join(digest[i : i + 2] for i in range(0, len(digest), 2))

    @staticmethod
    def _expiring(certificate: x509.Certificate) -> bool:
        return certificate.not_valid_after_utc - _now() < dt.timedelta(days=RENEW_DAYS)

    def _load(self) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey] | None:
        stored = self._vault.load(_KEY_NAME)
        if not stored or not self._authority_path.is_file():
            return None
        try:
            certificate = x509.load_pem_x509_certificate(self._authority_path.read_bytes())
            key = serialization.load_pem_private_key(stored["key"].encode("ascii"), password=None)
        except (ValueError, KeyError, TypeError):
            return None
        if not isinstance(key, ec.EllipticCurvePrivateKey) or key.public_key() != certificate.public_key():
            return None
        if self._expiring(certificate) or not self._constraints_current(certificate):
            return None
        return certificate, key

    @staticmethod
    def _constraints_current(certificate: x509.Certificate) -> bool:
        """An authority made under older rules is replaced (the phone installs the new one)."""
        try:
            constraints = certificate.extensions.get_extension_for_class(x509.NameConstraints).value
        except x509.ExtensionNotFound:
            return False
        return x509.DNSName(PERMITTED_DOMAIN) in (constraints.permitted_subtrees or [])

    def _create(self) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
        key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, f"NOVA on {socket.gethostname()}"[:64]),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "NOVA"),
            ]
        )
        now = _now()
        constraints = x509.NameConstraints(
            permitted_subtrees=[x509.IPAddress(ipaddress.ip_network(n)) for n in PERMITTED_NETWORKS]
            + [x509.DNSName(PERMITTED_DOMAIN)],
            excluded_subtrees=None,
        )
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(now + dt.timedelta(days=AUTHORITY_DAYS))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=False, content_commitment=False, key_encipherment=False,
                    data_encipherment=False, key_agreement=False, key_cert_sign=True, crl_sign=True,
                    encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(constraints, critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256())
        )
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        self._vault.save(_KEY_NAME, {"key": pem.decode("ascii")})
        self._folder.mkdir(parents=True, exist_ok=True)
        self._authority_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
        return certificate, key

    def server_certificate(
        self, address: str, names: tuple[str, ...] = ()
    ) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
        """A certificate for NOVA at this address and these .local names, signed by the authority."""
        authority, authority_key = self.authority()
        key = ec.generate_private_key(ec.SECP256R1())
        now = _now()
        certificate = (
            x509.CertificateBuilder()
            # No common name: only the alternative names count.
            .subject_name(x509.Name([x509.NameAttribute(NameOID.ORGANIZATION_NAME, "NOVA")]))
            .issuer_name(authority.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(minutes=5))
            .not_valid_after(min(now + dt.timedelta(days=SERVER_DAYS), authority.not_valid_after_utc))
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.IPAddress(ipaddress.ip_address(address))] + [x509.DNSName(name) for name in names]
                ),
                critical=False,
            )
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True, content_commitment=False, key_encipherment=False,
                    data_encipherment=False, key_agreement=False, key_cert_sign=False, crl_sign=False,
                    encipher_only=False, decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(authority_key.public_key()), critical=False
            )
            .sign(authority_key, hashes.SHA256())
        )
        return certificate, key

    def server_context(self, address: str, names: tuple[str, ...] = ()) -> ssl.SSLContext:
        """A TLS context for the phone listener at this address and names."""
        certificate, key = self.server_certificate(address, names)
        # Python's ssl only loads keys from files, so the key goes through one, encrypted with a
        # password that lives only in memory, and the folder is gone before this returns.
        password = secrets.token_bytes(32)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        with tempfile.TemporaryDirectory(prefix="nova-") as folder:
            cert_file, key_file = Path(folder) / "server.crt", Path(folder) / "server.key"
            cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
            key_file.write_bytes(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.BestAvailableEncryption(password),
                )
            )
            context.load_cert_chain(cert_file, key_file, password=password)
        return context
