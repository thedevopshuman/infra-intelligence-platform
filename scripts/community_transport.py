"""Owner-only TLS material for the single-host community preview.

This is an installer helper, not a serving certificate authority. The signing
key is never persisted; replacing this trust domain requires a stopped-stack
rotation, not a silent startup regeneration.
"""

from __future__ import annotations

import ipaddress
import json
import os
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


COLLECTOR_SPIFFE_ID = "spiffe://iip.community/collector/bedrock"
HEALTH_SPIFFE_ID = "spiffe://iip.community/receiver/health"
CHANNEL_ID = "bedrock-community"
LEAF_VALIDITY_DAYS = 365
TRANSPORT_FILES = (
    "ca.crt",
    "postgres.crt", "postgres.key",
    "receiver.crt", "receiver.key",
    "collector.crt", "collector.key",
    "collector-input.crt", "collector-input.key",
    "receiver-health.crt", "receiver-health.key",
)
_LEAF_IDENTITIES: dict[str, tuple[bool, tuple[x509.GeneralName, ...]]] = {
    "postgres": (False, (x509.DNSName("postgres"), x509.DNSName("localhost"))),
    "receiver": (False, (x509.DNSName("ai-usage-receiver"), x509.DNSName("localhost"))),
    "collector-input": (False, (
        x509.DNSName("otel-collector"), x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
    )),
    "collector": (True, (x509.UniformResourceIdentifier(COLLECTOR_SPIFFE_ID),)),
    "receiver-health": (True, (x509.UniformResourceIdentifier(HEALTH_SPIFFE_ID),)),
}


def _write_private(path: Path, value: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(value)


def transport_environment() -> dict[str, str]:
    """Return public container-side paths and the exact allowed workload ID."""
    return {
        "IIP_DATABASE_TRANSPORT_MODE": "verify-full",
        "IIP_DATABASE_CA_PATH": "/run/iip/ca.crt",
        "IIP_OTLP_TLS_MODE": "mutual-spiffe",
        "IIP_OTLP_TLS_CERTIFICATE_PATH": "/run/iip/receiver.crt",
        "IIP_OTLP_TLS_PRIVATE_KEY_PATH": "/run/iip/receiver.key",
        "IIP_OTLP_TLS_CLIENT_CA_PATH": "/run/iip/ca.crt",
        "IIP_OTLP_MTLS_IDENTITIES_JSON": json.dumps({"identities": [{
            "spiffeId": COLLECTOR_SPIFFE_ID,
            "channelIds": [CHANNEL_ID],
        }]}, separators=(",", ":")),
    }


def write_transport(state_dir: Path) -> dict[str, str]:
    """Create a new private trust domain under an existing owner-only state dir.

    Refuse to replace material, follow a state-directory symlink, or loosen
    existing permissions. The unregistered health client can complete TLS but
    has no ingestion channel and receives no Bearer credential.
    """
    if not state_dir.is_absolute() or state_dir.is_symlink():
        raise ValueError("community.transport.state.invalid")
    state = state_dir.stat()
    if (
        not stat.S_ISDIR(state.st_mode)
        or stat.S_IMODE(state.st_mode) != 0o700
        or state.st_uid != os.getuid()
    ):
        raise ValueError("community.transport.state.invalid")
    directory = state_dir / "transport"
    directory.mkdir(mode=0o700)
    now = datetime.now(timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "IIP community local CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=LEAF_VALIDITY_DAYS + 1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
        .sign(ca_key, hashes.SHA256())
    )
    _write_private(directory / "ca.crt", ca.public_bytes(serialization.Encoding.PEM))

    def leaf(name: str, names: list[x509.GeneralName], *, client: bool = False) -> None:
        key = ec.generate_private_key(ec.SECP256R1())
        certificate = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(ca_name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=LEAF_VALIDITY_DAYS))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(x509.SubjectAlternativeName(names), False)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), False)
            .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), True)
            .add_extension(x509.ExtendedKeyUsage([
                ExtendedKeyUsageOID.CLIENT_AUTH if client else ExtendedKeyUsageOID.SERVER_AUTH
            ]), False)
            .sign(ca_key, hashes.SHA256())
        )
        _write_private(directory / f"{name}.crt", certificate.public_bytes(serialization.Encoding.PEM))
        _write_private(directory / f"{name}.key", key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ))

    for name, (client, names) in _LEAF_IDENTITIES.items():
        leaf(name, list(names), client=client)
    return transport_environment()


def validate_transport(state_dir: Path) -> None:
    """Fail closed on stale, substituted, symlinked, or overexposed material."""
    try:
        directory = state_dir / "transport"
        for path in (state_dir, directory):
            info = path.lstat()
            if (
                not stat.S_ISDIR(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o700
                or info.st_uid != os.getuid()
            ):
                raise ValueError
        if set(path.name for path in directory.iterdir()) != set(TRANSPORT_FILES):
            raise ValueError
        for name in TRANSPORT_FILES:
            info = (directory / name).lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_uid != os.getuid()
                or not 1 <= info.st_size <= 65536
            ):
                raise ValueError
        ca = x509.load_pem_x509_certificate((directory / "ca.crt").read_bytes())
        ca.verify_directly_issued_by(ca)
        if not ca.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
            raise ValueError
        now = datetime.now(timezone.utc)
        if not ca.not_valid_before_utc <= now < ca.not_valid_after_utc:
            raise ValueError
        for name, (client, names) in _LEAF_IDENTITIES.items():
            certificate = x509.load_pem_x509_certificate((directory / f"{name}.crt").read_bytes())
            key = serialization.load_pem_private_key((directory / f"{name}.key").read_bytes(), password=None)
            certificate.verify_directly_issued_by(ca)
            if (
                not certificate.not_valid_before_utc <= now < certificate.not_valid_after_utc
                or certificate.extensions.get_extension_for_class(x509.BasicConstraints).value.ca
                or set(certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value) != set(names)
                or set(certificate.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value) != {
                    ExtendedKeyUsageOID.CLIENT_AUTH if client else ExtendedKeyUsageOID.SERVER_AUTH
                }
                or certificate.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
                != key.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
            ):
                raise ValueError
    except Exception:
        raise ValueError("community.transport.invalid-or-expired") from None
