"""Standards-complete ephemeral TLS material for local compatibility fixtures."""

from __future__ import annotations

import ipaddress
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def write_tls_material(
    directory: Path,
    *,
    common_name: str,
    dns_name: str,
    ip_address: str = "127.0.0.1",
    client_identities: Mapping[str, str] | None = None,
    expired_client_identities: Mapping[str, str] | None = None,
    revoked_client_identities: Mapping[str, str] | None = None,
    intermediate_client_ca: bool = False,
    rotated_crl_identity_names: tuple[str, ...] = (),
) -> None:
    """Write a one-hour CA/server chain and optional URI-SAN client identities.

    ``expired_client_identities`` are signed by the same CA as
    ``client_identities`` but carry a validity window that already closed,
    proving the receiver rejects an otherwise-trusted identity once its
    certificate has expired rather than only rejecting an untrusted issuer.

    ``revoked_client_identities`` are signed by the same CA with an
    otherwise-valid validity window, but their serial numbers are listed on
    a CA-signed CRL written to ``ca.crl``, proving the receiver rejects an
    identity a customer has explicitly revoked even though its chain and
    validity window both check out. Distinct from expiry: a deployment can
    revoke a workload identity immediately without waiting for its
    certificate's natural validity window to close.

    When revoked identities are requested, ``expired-ca.crl`` contains the
    same revocations but has a closed ``nextUpdate`` window. It proves a
    receiver cannot continue trusting stale revocation state.

    ``intermediate_client_ca`` issues client certificates and CRLs from a
    root-authorized intermediate CA. Client certificate files contain the
    leaf followed by the intermediate, while ``client-ca.crt`` contains the
    root and intermediate verification bundle. ``ca.crt`` remains the root
    used to verify the server certificate.

    ``rotated_crl_identity_names`` identifies initially valid client
    certificates that are added to ``rotated-ca.crl`` together with the
    original revocations. Activating that file and restarting the receiver
    proves a newer customer CRL takes effect without changing identity scope.
    """

    if len(set(rotated_crl_identity_names)) != len(rotated_crl_identity_names):
        raise ValueError("rotated CRL identities must be unique")
    if rotated_crl_identity_names and revoked_client_identities is None:
        raise ValueError("rotated CRL requires CRL fixture generation")

    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "IIP compatibility test CA")]
    )
    ca_certificate = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.BasicConstraints(
                ca=True,
                path_length=1 if intermediate_client_ca else 0,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    server_certificate = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.IPAddress(ipaddress.ip_address(ip_address)),
                    x509.DNSName(dns_name),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    (directory / "ca.crt").write_bytes(
        ca_certificate.public_bytes(serialization.Encoding.PEM)
    )
    (directory / "server.crt").write_bytes(
        server_certificate.public_bytes(serialization.Encoding.PEM)
    )
    (directory / "server.key").write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    for name in ("ca.crt", "server.crt", "server.key"):
        os.chmod(directory / name, 0o644)

    client_issuer_key = ca_key
    client_issuer_name = ca_name
    client_issuer_certificate = ca_certificate
    if intermediate_client_ca:
        client_issuer_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        client_issuer_name = x509.Name(
            [
                x509.NameAttribute(
                    NameOID.COMMON_NAME,
                    "IIP compatibility test client intermediate CA",
                )
            ]
        )
        client_issuer_certificate = (
            x509.CertificateBuilder()
            .subject_name(client_issuer_name)
            .issuer_name(ca_name)
            .public_key(client_issuer_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(hours=1))
            .add_extension(
                x509.BasicConstraints(ca=True, path_length=0),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(
                    client_issuer_key.public_key()
                ),
                critical=False,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    ca_key.public_key()
                ),
                critical=False,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=False,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .sign(ca_key, hashes.SHA256())
        )
        (directory / "client-intermediate-ca.crt").write_bytes(
            client_issuer_certificate.public_bytes(serialization.Encoding.PEM)
        )
        os.chmod(directory / "client-intermediate-ca.crt", 0o644)

    (directory / "client-ca.crt").write_bytes(
        ca_certificate.public_bytes(serialization.Encoding.PEM)
        + (
            client_issuer_certificate.public_bytes(serialization.Encoding.PEM)
            if intermediate_client_ca
            else b""
        )
    )
    os.chmod(directory / "client-ca.crt", 0o644)

    client_certificates: dict[str, x509.Certificate] = {}

    def write_client_identity(
        prefix: str,
        uri_san: str,
        *,
        not_valid_before: datetime,
        not_valid_after: datetime,
    ) -> x509.Certificate:
        if (
            re.fullmatch(r"[a-z][a-z0-9-]{0,63}", prefix) is None
            or not uri_san.startswith("spiffe://")
            or len(uri_san) > 2048
        ):
            raise ValueError("invalid compatibility client identity")
        client_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        client_name = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, f"{prefix}.fixture")]
        )
        client_certificate = (
            x509.CertificateBuilder()
            .subject_name(client_name)
            .issuer_name(client_issuer_name)
            .public_key(client_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(not_valid_before)
            .not_valid_after(not_valid_after)
            .add_extension(
                x509.SubjectAlternativeName(
                    [x509.UniformResourceIdentifier(uri_san)]
                ),
                critical=False,
            )
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None), critical=True
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(client_key.public_key()),
                critical=False,
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    client_issuer_key.public_key()
                ),
                critical=False,
            )
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=False,
                    crl_sign=False,
                    encipher_only=False,
                    decipher_only=False,
                ),
                critical=True,
            )
            .add_extension(
                x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
                critical=False,
            )
            .sign(client_issuer_key, hashes.SHA256())
        )
        leaf_pem = client_certificate.public_bytes(serialization.Encoding.PEM)
        (directory / f"{prefix}-leaf.crt").write_bytes(leaf_pem)
        (directory / f"{prefix}.crt").write_bytes(
            leaf_pem
            + (
                client_issuer_certificate.public_bytes(serialization.Encoding.PEM)
                if intermediate_client_ca
                else b""
            )
        )
        (directory / f"{prefix}.key").write_bytes(
            client_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        os.chmod(directory / f"{prefix}-leaf.crt", 0o644)
        os.chmod(directory / f"{prefix}.crt", 0o644)
        os.chmod(directory / f"{prefix}.key", 0o600)
        client_certificates[prefix] = client_certificate
        return client_certificate

    for prefix, uri_san in (client_identities or {}).items():
        write_client_identity(
            prefix,
            uri_san,
            not_valid_before=now - timedelta(minutes=1),
            not_valid_after=now + timedelta(hours=1),
        )
    for prefix, uri_san in (expired_client_identities or {}).items():
        write_client_identity(
            prefix,
            uri_san,
            not_valid_before=now - timedelta(days=2),
            not_valid_after=now - timedelta(days=1),
        )

    revoked_serials = []
    for prefix, uri_san in (revoked_client_identities or {}).items():
        revoked_serials.append(
            write_client_identity(
                prefix,
                uri_san,
                not_valid_before=now - timedelta(minutes=1),
                not_valid_after=now + timedelta(hours=1),
            ).serial_number
        )
    if revoked_client_identities is not None:
        unknown_rotation_names = set(rotated_crl_identity_names).difference(
            client_identities or {}
        )
        if unknown_rotation_names:
            raise ValueError("rotated CRL identity is not a generated client")

        def write_crl(
            name: str,
            last_update: datetime,
            next_update: datetime,
            serial_numbers: list[int],
        ) -> None:
            crl_builder = (
                x509.CertificateRevocationListBuilder()
                .issuer_name(client_issuer_name)
                .last_update(last_update)
                .next_update(next_update)
            )
            for serial_number in serial_numbers:
                crl_builder = crl_builder.add_revoked_certificate(
                    x509.RevokedCertificateBuilder()
                    .serial_number(serial_number)
                    .revocation_date(now - timedelta(seconds=30))
                    .build()
                )
            crl = crl_builder.sign(client_issuer_key, hashes.SHA256())
            (directory / name).write_bytes(
                crl.public_bytes(serialization.Encoding.PEM)
            )
            os.chmod(directory / name, 0o644)

        write_crl(
            "ca.crl",
            now - timedelta(minutes=1),
            now + timedelta(hours=1),
            revoked_serials,
        )
        write_crl(
            "expired-ca.crl",
            now - timedelta(hours=2),
            now - timedelta(hours=1),
            revoked_serials,
        )
        if rotated_crl_identity_names:
            write_crl(
                "rotated-ca.crl",
                now - timedelta(seconds=30),
                now + timedelta(hours=1),
                revoked_serials
                + [
                    client_certificates[name].serial_number
                    for name in rotated_crl_identity_names
                ],
            )
