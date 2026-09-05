"""TLS and workload-identity policy for the isolated OTLP listener."""

from __future__ import annotations

import os
import re
import ssl
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from cryptography import x509

from iip.application.ingest_otlp_metrics import (
    OtlpReceiverAuthenticationError,
    OtlpReceiverConfigurationError,
)


_CHANNEL_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_TRUST_DOMAIN = re.compile(
    r"[a-z0-9](?:[a-z0-9.-]{0,252}[a-z0-9])?"
)
_SPIFFE_PATH = re.compile(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/-]{1,1023}")
_TLS_ENVIRONMENT_KEYS = (
    "IIP_OTLP_TLS_CERTIFICATE_PATH",
    "IIP_OTLP_TLS_PRIVATE_KEY_PATH",
    "IIP_OTLP_TLS_CLIENT_CA_PATH",
    "IIP_OTLP_MTLS_IDENTITIES_JSON",
    "IIP_OTLP_TLS_CLIENT_CRL_PATH",
)
_MAX_CLIENT_CRL_BYTES = 1_048_576
_CRL_BEGIN = b"-----BEGIN X509 CRL-----"
_CRL_END = b"-----END X509 CRL-----"


def _absolute_path(value: object) -> str | None:
    if (
        not isinstance(value, str)
        or not value.startswith("/")
        or len(value) > 4096
    ):
        return None
    return value


def _valid_spiffe_id(value: object) -> bool:
    if not isinstance(value, str) or not 16 <= len(value) <= 2048:
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "spiffe"
        and parsed.username is None
        and parsed.password is None
        and port is None
        and parsed.hostname is not None
        and parsed.netloc == parsed.hostname
        and _TRUST_DOMAIN.fullmatch(parsed.hostname) is not None
        and _SPIFFE_PATH.fullmatch(parsed.path) is not None
        and "//" not in parsed.path
        and all(segment not in (".", "..") for segment in parsed.path.split("/"))
        and not parsed.query
        and not parsed.fragment
    )


@dataclass(frozen=True)
class SpiffeClientIdentityRegistry:
    """Bind one verified client URI SAN to an explicit channel subset."""

    channels_by_spiffe_id: Mapping[str, frozenset[str]]

    @classmethod
    def from_json(cls, raw: str) -> "SpiffeClientIdentityRegistry":
        import json

        try:
            if not isinstance(raw, str) or not 2 <= len(raw) <= 262_144:
                raise ValueError
            document = json.loads(raw)
            if not isinstance(document, dict) or set(document) != {"identities"}:
                raise ValueError
            entries = document["identities"]
            if not isinstance(entries, list) or not 1 <= len(entries) <= 256:
                raise ValueError
            identities: dict[str, frozenset[str]] = {}
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {
                    "spiffeId",
                    "channelIds",
                }:
                    raise ValueError
                spiffe_id = entry["spiffeId"]
                channel_ids = entry["channelIds"]
                if (
                    not _valid_spiffe_id(spiffe_id)
                    or not isinstance(channel_ids, list)
                    or not 1 <= len(channel_ids) <= 64
                    or any(
                        not isinstance(channel_id, str)
                        or _CHANNEL_ID.fullmatch(channel_id) is None
                        for channel_id in channel_ids
                    )
                    or len(set(channel_ids)) != len(channel_ids)
                    or spiffe_id in identities
                ):
                    raise ValueError
                identities[spiffe_id] = frozenset(channel_ids)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise OtlpReceiverConfigurationError(
                "otlp.tls.configuration.invalid"
            ) from None
        return cls(identities)

    def authorize(self, peer_certificate: Mapping[str, Any], channel_id: str) -> None:
        alternative_names = peer_certificate.get("subjectAltName")
        uri_names = (
            [
                value
                for kind, value in alternative_names
                if kind == "URI" and isinstance(value, str)
            ]
            if isinstance(alternative_names, (list, tuple))
            else []
        )
        if len(uri_names) != 1 or not _valid_spiffe_id(uri_names[0]):
            raise OtlpReceiverAuthenticationError("otlp.authentication.invalid")
        allowed_channels = self.channels_by_spiffe_id.get(uri_names[0])
        if allowed_channels is None or channel_id not in allowed_channels:
            raise OtlpReceiverAuthenticationError("otlp.authentication.invalid")


@dataclass(frozen=True)
class ClientCrlFreshnessPolicy:
    """Fail closed when a configured client CRL is not currently valid."""

    last_update: datetime
    next_update: datetime
    pem_data: str = field(default="", repr=False, compare=False)

    def __post_init__(self) -> None:
        if (
            self.last_update.tzinfo is None
            or self.next_update.tzinfo is None
            or self.last_update >= self.next_update
        ):
            raise OtlpReceiverConfigurationError(
                "otlp.tls.configuration.invalid"
            )

    @classmethod
    def from_pem_file(
        cls,
        path: str,
        *,
        issuer_ca_path: str,
        evaluated_at: datetime | None = None,
    ) -> "ClientCrlFreshnessPolicy":
        try:
            crl_path = Path(path)
            with crl_path.open("rb") as stream:
                payload = stream.read(_MAX_CLIENT_CRL_BYTES + 1)
            stripped = payload.strip()
            if (
                not 1 <= len(payload) <= _MAX_CLIENT_CRL_BYTES
                or stripped.count(_CRL_BEGIN) != 1
                or stripped.count(_CRL_END) != 1
                or not stripped.startswith(_CRL_BEGIN)
                or not stripped.endswith(_CRL_END)
            ):
                raise ValueError
            crl = x509.load_pem_x509_crl(payload)
            issuer_certificates = x509.load_pem_x509_certificates(
                Path(issuer_ca_path).read_bytes()
            )
            issuer_valid = False
            for certificate in issuer_certificates:
                if certificate.subject != crl.issuer:
                    continue
                try:
                    constraints = certificate.extensions.get_extension_for_class(
                        x509.BasicConstraints
                    ).value
                    key_usage = certificate.extensions.get_extension_for_class(
                        x509.KeyUsage
                    ).value
                except x509.ExtensionNotFound:
                    continue
                if (
                    constraints.ca
                    and key_usage.crl_sign
                    and crl.is_signature_valid(certificate.public_key())
                ):
                    issuer_valid = True
                    break
            if not issuer_valid:
                raise ValueError
            next_update = crl.next_update_utc
            if next_update is None:
                raise ValueError
            policy = cls(
                crl.last_update_utc,
                next_update,
                payload.decode("ascii"),
            )
            policy.assert_current(evaluated_at=evaluated_at)
            return policy
        except OtlpReceiverConfigurationError:
            raise
        except (OSError, TypeError, ValueError):
            raise OtlpReceiverConfigurationError(
                "otlp.tls.configuration.invalid"
            ) from None

    def assert_current(self, *, evaluated_at: datetime | None = None) -> None:
        now = evaluated_at or datetime.now(timezone.utc)
        if now.tzinfo is None:
            raise OtlpReceiverConfigurationError(
                "otlp.tls.configuration.invalid"
            )
        normalized = now.astimezone(timezone.utc)
        if (
            self.last_update.astimezone(timezone.utc) > normalized
            or self.next_update.astimezone(timezone.utc) <= normalized
        ):
            raise OtlpReceiverConfigurationError(
                "otlp.tls.configuration.invalid"
            )


@dataclass(frozen=True)
class OtlpTlsConfiguration:
    """Closed listener TLS profile derived only from protected environment."""

    mode: str
    certificate_path: str | None = None
    private_key_path: str | None = None
    client_ca_path: str | None = None
    client_identities: SpiffeClientIdentityRegistry | None = None
    client_crl_path: str | None = None

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "OtlpTlsConfiguration":
        selected = environment if environment is not None else os.environ
        mode = selected.get("IIP_OTLP_TLS_MODE", "disabled")
        if mode == "disabled":
            if any(selected.get(name) for name in _TLS_ENVIRONMENT_KEYS):
                raise OtlpReceiverConfigurationError(
                    "otlp.tls.configuration.invalid"
                )
            return cls(mode="disabled")
        if mode not in ("server", "mutual-spiffe"):
            raise OtlpReceiverConfigurationError("otlp.tls.configuration.invalid")
        certificate_path = _absolute_path(
            selected.get("IIP_OTLP_TLS_CERTIFICATE_PATH")
        )
        private_key_path = _absolute_path(
            selected.get("IIP_OTLP_TLS_PRIVATE_KEY_PATH")
        )
        if certificate_path is None or private_key_path is None:
            raise OtlpReceiverConfigurationError("otlp.tls.configuration.invalid")
        if mode == "server":
            if (
                selected.get("IIP_OTLP_TLS_CLIENT_CA_PATH")
                or selected.get("IIP_OTLP_MTLS_IDENTITIES_JSON")
                or selected.get("IIP_OTLP_TLS_CLIENT_CRL_PATH")
            ):
                raise OtlpReceiverConfigurationError(
                    "otlp.tls.configuration.invalid"
                )
            return cls(mode, certificate_path, private_key_path)
        client_ca_path = _absolute_path(
            selected.get("IIP_OTLP_TLS_CLIENT_CA_PATH")
        )
        identities = selected.get("IIP_OTLP_MTLS_IDENTITIES_JSON")
        if client_ca_path is None or identities is None:
            raise OtlpReceiverConfigurationError("otlp.tls.configuration.invalid")
        # The CRL is optional even in mutual-spiffe mode: a deployment can run
        # mTLS without a revocation list, the same way it can run without one
        # for years before the first revocation. Absent, the receiver checks
        # only the chain and validity window, as before.
        raw_client_crl_path = selected.get("IIP_OTLP_TLS_CLIENT_CRL_PATH")
        client_crl_path = (
            _absolute_path(raw_client_crl_path)
            if raw_client_crl_path
            else None
        )
        if raw_client_crl_path and client_crl_path is None:
            raise OtlpReceiverConfigurationError("otlp.tls.configuration.invalid")
        return cls(
            mode,
            certificate_path,
            private_key_path,
            client_ca_path,
            SpiffeClientIdentityRegistry.from_json(identities),
            client_crl_path,
        )

    @property
    def scheme(self) -> str:
        return "http" if self.mode == "disabled" else "https"

    def client_crl_freshness_policy(self) -> ClientCrlFreshnessPolicy | None:
        if self.client_crl_path is None:
            return None
        assert self.client_ca_path is not None
        return ClientCrlFreshnessPolicy.from_pem_file(
            self.client_crl_path,
            issuer_ca_path=self.client_ca_path,
        )

    def ssl_context(
        self,
        *,
        client_crl_freshness: ClientCrlFreshnessPolicy | None = None,
    ) -> ssl.SSLContext | None:
        if self.mode == "disabled":
            return None
        assert self.certificate_path is not None
        assert self.private_key_path is not None
        try:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(
                certfile=self.certificate_path,
                keyfile=self.private_key_path,
            )
            context.set_alpn_protocols(["http/1.1"])
            if self.mode == "mutual-spiffe":
                assert self.client_ca_path is not None
                context.load_verify_locations(cafile=self.client_ca_path)
                if self.client_crl_path is not None:
                    freshness = (
                        client_crl_freshness
                        or self.client_crl_freshness_policy()
                    )
                    assert freshness is not None
                    freshness.assert_current()
                    if not freshness.pem_data:
                        raise OtlpReceiverConfigurationError(
                            "otlp.tls.configuration.invalid"
                        )
                    # Python/OpenSSL accepts a CRL only through a CA file, not
                    # cadata. Re-read after loading and reject an atomic Secret
                    # rotation race rather than pairing different validity
                    # metadata with the trust-store bytes.
                    context.load_verify_locations(cafile=self.client_crl_path)
                    loaded = self.client_crl_freshness_policy()
                    if loaded is None or loaded.pem_data != freshness.pem_data:
                        raise OtlpReceiverConfigurationError(
                            "otlp.tls.configuration.invalid"
                        )
                    context.verify_flags |= ssl.VERIFY_CRL_CHECK_LEAF
                # Health and readiness expose only stable status and stay probeable
                # without a client certificate. OTLP POST routes enforce a verified
                # certificate and exact SPIFFE-to-channel binding in the handler.
                context.verify_mode = ssl.CERT_OPTIONAL
            return context
        except (OSError, ssl.SSLError):
            raise OtlpReceiverConfigurationError(
                "otlp.tls.configuration.invalid"
            ) from None


def peer_certificate(connection: object) -> Mapping[str, Any]:
    getter = getattr(connection, "getpeercert", None)
    if not callable(getter):
        raise OtlpReceiverAuthenticationError("otlp.authentication.invalid")
    certificate = getter()
    if not isinstance(certificate, dict) or not certificate:
        raise OtlpReceiverAuthenticationError("otlp.authentication.invalid")
    return certificate
