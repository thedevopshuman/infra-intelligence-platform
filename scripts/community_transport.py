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
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID, SignatureAlgorithmOID


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
        stream.flush()
        os.fsync(stream.fileno())


def _private_directory(path: Path) -> int:
    """Open the exact private directory without following a final symlink."""
    observed = path.lstat()
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700
            or info.st_uid != os.getuid()
            or (observed.st_dev, observed.st_ino) != (info.st_dev, info.st_ino)
        ):
            raise ValueError
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _sync_directory(path: Path) -> None:
    descriptor = _private_directory(path)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


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
    _sync_directory(directory)
    _sync_directory(state_dir)
    return transport_environment()


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
        info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _read_material(directory: Path) -> dict[str, bytes]:
    descriptor = _private_directory(directory)
    try:
        before = os.fstat(descriptor)
        names = set()
        with os.scandir(descriptor) as entries:
            for entry in entries:
                if entry.name not in TRANSPORT_FILES or entry.name in names:
                    raise ValueError
                names.add(entry.name)
        if names != set(TRANSPORT_FILES):
            raise ValueError
        result = {}
        for name in TRANSPORT_FILES:
            leaf = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
            with os.fdopen(leaf, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_uid != os.getuid()
                    or info.st_nlink != 1
                    or not 1 <= info.st_size <= 65536
                ):
                    raise ValueError
                content = stream.read(65537)
                if (
                    len(content) != info.st_size
                    or _file_identity(info) != _file_identity(os.fstat(stream.fileno()))
                    or _file_identity(info) != _file_identity(os.stat(name, dir_fd=descriptor, follow_symlinks=False))
                ):
                    raise ValueError
                result[name] = content
        if (
            _file_identity(before) != _file_identity(os.fstat(descriptor))
            or _file_identity(before) != _file_identity(directory.lstat())
        ):
            raise ValueError
        return result
    finally:
        os.close(descriptor)


def _public_identity(key) -> bytes:
    if not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1):
        raise ValueError
    return key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)


def _certificate(content: bytes) -> x509.Certificate:
    certificate = x509.load_pem_x509_certificate(content)
    if (
        certificate.public_bytes(serialization.Encoding.PEM) != content
        or certificate.version != x509.Version.v3
        or certificate.signature_algorithm_oid != SignatureAlgorithmOID.ECDSA_WITH_SHA256
        or certificate.not_valid_before_utc >= certificate.not_valid_after_utc
    ):
        raise ValueError
    _public_identity(certificate.public_key())
    return certificate


def _extensions(certificate: x509.Certificate, expected: list[tuple[x509.ExtensionType, bool]]) -> None:
    if {extension.oid for extension in certificate.extensions} != {value.oid for value, _ in expected}:
        raise ValueError
    for value, critical in expected:
        extension = certificate.extensions.get_extension_for_oid(value.oid)
        if extension.critical != critical or extension.value != value:
            raise ValueError


def validate_transport_directory(
    directory: Path, *, require_current: bool = True, now: datetime | None = None,
) -> dict[str, str]:
    """Validate one exact generated trust domain without exposing key material.

    ``require_current=False`` permits structurally valid expired or future
    material for offline rescue/inspection only; it never relaxes identity,
    signature, file protection, or key-authority checks. No CA key is required
    or accepted. The returned validity window is the intersection of all six
    certificate windows, and the fingerprint identifies only the public CA.
    """
    try:
        if type(require_current) is not bool:
            raise ValueError
        selected_now = now if now is not None else datetime.now(timezone.utc)
        if selected_now.tzinfo is None or selected_now.utcoffset() is None:
            raise ValueError
        material = _read_material(directory)
        ca = _certificate(material["ca.crt"])
        ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "IIP community local CA")])
        if ca.subject != ca_name or ca.issuer != ca_name:
            raise ValueError
        ca.verify_directly_issued_by(ca)
        ca_identity = _public_identity(ca.public_key())
        authority = x509.AuthorityKeyIdentifier.from_issuer_public_key(ca.public_key())
        _extensions(ca, [
            (x509.BasicConstraints(ca=True, path_length=0), True),
            (x509.SubjectKeyIdentifier.from_public_key(ca.public_key()), False),
            (authority, False),
            (x509.KeyUsage(False, False, False, False, False, True, True, False, False), True),
        ])
        certificates = [ca]
        keys = {ca_identity}
        for name, (client, names) in _LEAF_IDENTITIES.items():
            certificate = _certificate(material[f"{name}.crt"])
            key_content = material[f"{name}.key"]
            key = serialization.load_pem_private_key(key_content, password=None)
            if not isinstance(key, ec.EllipticCurvePrivateKey):
                raise ValueError
            identity = _public_identity(key.public_key())
            certificate.verify_directly_issued_by(ca)
            if (
                certificate.subject != x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
                or certificate.issuer != ca.subject
                or certificate.not_valid_before_utc < ca.not_valid_before_utc
                or certificate.not_valid_after_utc > ca.not_valid_after_utc
                or _public_identity(certificate.public_key()) != identity
                or identity in keys
                or key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                     serialization.NoEncryption()) != key_content
            ):
                raise ValueError
            _extensions(certificate, [
                (x509.BasicConstraints(ca=False, path_length=None), True),
                (x509.SubjectAlternativeName(list(names)), False),
                (x509.SubjectKeyIdentifier.from_public_key(certificate.public_key()), False),
                (authority, False),
                (x509.KeyUsage(True, False, False, False, False, False, False, False, False), True),
                (x509.ExtendedKeyUsage([
                    ExtendedKeyUsageOID.CLIENT_AUTH if client else ExtendedKeyUsageOID.SERVER_AUTH
                ]), False),
            ])
            keys.add(identity)
            certificates.append(certificate)
        not_before = max(certificate.not_valid_before_utc for certificate in certificates)
        not_after = min(certificate.not_valid_after_utc for certificate in certificates)
        if not_before >= not_after or (require_current and not not_before <= selected_now < not_after):
            raise ValueError
        return {
            "notBefore": not_before.isoformat().replace("+00:00", "Z"),
            "notAfter": not_after.isoformat().replace("+00:00", "Z"),
            "caSha256": ca.fingerprint(hashes.SHA256()).hex(),
        }
    except Exception:
        raise ValueError("community.transport.invalid-or-expired") from None


def validate_transport(state_dir: Path) -> None:
    """Legacy current-validity check for a state's original transport directory."""
    try:
        descriptor = _private_directory(state_dir)
        os.close(descriptor)
        validate_transport_directory(state_dir / "transport")
    except Exception:
        raise ValueError("community.transport.invalid-or-expired") from None
