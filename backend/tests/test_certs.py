from __future__ import annotations

import datetime as dt
import ipaddress

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.verification import PolicyBuilder, Store, VerificationError

from nova.phone.certs import Certificates


def verify(authority: x509.Certificate, leaf: x509.Certificate, address: str) -> None:
    verifier = (
        PolicyBuilder()
        .store(Store([authority]))
        .time(dt.datetime.now(dt.UTC))
        .build_server_verifier(x509.IPAddress(ipaddress.ip_address(address)))
    )
    verifier.verify(leaf, [])


def test_the_authority_vouches_for_nova_on_the_home_network(tmp_path):
    certificates = Certificates(tmp_path)
    authority, _ = certificates.authority()
    leaf, _ = certificates.server_certificate("192.168.29.104")
    verify(authority, leaf, "192.168.29.104")
    with pytest.raises(VerificationError):
        verify(authority, leaf, "192.168.29.105")


def test_the_authority_cannot_vouch_for_websites(tmp_path):
    """If its key ever leaked, it still could not impersonate a site to the phone."""
    certificates = Certificates(tmp_path)
    authority, authority_key = certificates.authority()
    public, _ = certificates.server_certificate("8.8.8.8")
    with pytest.raises(VerificationError):
        verify(authority, public, "8.8.8.8")

    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.UTC)
    website = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([]))
        .issuer_name(authority.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("bank.example")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(authority_key.public_key()), critical=False)
        .sign(authority_key, hashes.SHA256())
    )
    verifier = PolicyBuilder().store(Store([authority])).time(now).build_server_verifier(x509.DNSName("bank.example"))
    with pytest.raises(VerificationError):
        verifier.verify(website, [])


def test_the_authority_is_kept_and_its_key_is_never_a_plain_file(tmp_path):
    first = Certificates(tmp_path).fingerprint()
    assert Certificates(tmp_path).fingerprint() == first, "the phone installs it once"
    for path in tmp_path.rglob("*"):
        if path.is_file():
            assert b"PRIVATE KEY" not in path.read_bytes(), path


def test_a_damaged_authority_is_replaced(tmp_path):
    first = Certificates(tmp_path).fingerprint()
    (tmp_path / "nova-authority.crt").write_text("damaged", encoding="utf-8")
    assert Certificates(tmp_path).fingerprint() != first


def test_the_server_context_loads(tmp_path):
    context = Certificates(tmp_path).server_context("192.168.1.20")
    assert context.minimum_version.name == "TLSv1_2"
