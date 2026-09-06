"""External HTTPS credential broker authenticated by projected workload identity."""

from __future__ import annotations

import json
import re
import ssl
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Mapping, Protocol
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from iip.application.ports import (
    Clock,
    CredentialLease,
    CredentialLeaseRequest,
)


_TENANT_ID = re.compile(r"[a-zA-Z0-9._-]{1,128}")
_INTEGRATION_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_CREDENTIAL_REF = re.compile(r"credential://[A-Za-z0-9._~:/-]{1,2020}")
_PROVIDER = re.compile(r"[a-z][a-z0-9._-]{1,63}")
_SCOPE = re.compile(r"[a-z][a-z0-9._/-]{0,63}:[a-z][a-z0-9._/-]{0,63}")
_REQUEST_ID = re.compile(r"crq_[a-f0-9]{32}")
_MAX_CONFIGURATION_BYTES = 65_536
_MAX_TOKEN_BYTES = 16_384
_MAX_REQUEST_BYTES = 32_768


class CredentialBrokerConfigurationError(RuntimeError):
    """External broker configuration is invalid or incomplete."""


class CredentialBrokerUnavailableError(RuntimeError):
    """The external broker could not safely issue the requested lease."""


@dataclass(frozen=True)
class ExternalCredentialBrokerConfiguration:
    """Protected endpoint, trust, identity, and lease bounds for one broker."""

    endpoint: str
    ca_bundle_path: str | None
    workload_identity_token_path: str
    request_timeout_seconds: int
    max_response_bytes: int
    max_lease_seconds: int
    max_clock_skew_seconds: int

    @classmethod
    def from_json(cls, encoded: str) -> "ExternalCredentialBrokerConfiguration":
        if (
            not isinstance(encoded, str)
            or not 1 <= len(encoded.encode("utf-8")) <= _MAX_CONFIGURATION_BYTES
        ):
            raise cls._invalid()
        try:
            value = json.loads(encoded)
        except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            raise cls._invalid() from None
        expected = {
            "endpoint",
            "caBundlePath",
            "workloadIdentityTokenPath",
            "requestTimeoutSeconds",
            "maxResponseBytes",
            "maxLeaseSeconds",
            "maxClockSkewSeconds",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise cls._invalid()
        endpoint = value["endpoint"]
        ca_bundle_path = value["caBundlePath"]
        token_path = value["workloadIdentityTokenPath"]
        try:
            _validate_endpoint(endpoint)
            _validate_absolute_path(ca_bundle_path, optional=True)
            _validate_absolute_path(token_path, optional=False)
        except (TypeError, ValueError):
            raise cls._invalid() from None
        if (
            not _integer(value["requestTimeoutSeconds"], 1, 120)
            or not _integer(value["maxResponseBytes"], 1_024, 1_048_576)
            or not _integer(value["maxLeaseSeconds"], 60, 3_600)
            or not _integer(value["maxClockSkewSeconds"], 0, 300)
        ):
            raise cls._invalid()
        return cls(
            endpoint=endpoint.rstrip("/"),
            ca_bundle_path=ca_bundle_path,
            workload_identity_token_path=token_path,
            request_timeout_seconds=value["requestTimeoutSeconds"],
            max_response_bytes=value["maxResponseBytes"],
            max_lease_seconds=value["maxLeaseSeconds"],
            max_clock_skew_seconds=value["maxClockSkewSeconds"],
        )

    @staticmethod
    def _invalid() -> CredentialBrokerConfigurationError:
        return CredentialBrokerConfigurationError(
            "credential.broker.configuration.invalid"
        )


class WorkloadIdentityTokenSource:
    """Read an explicitly configured token file for every broker request."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    def __repr__(self) -> str:
        return "WorkloadIdentityTokenSource(path=<protected>)"

    def read(self) -> str:
        try:
            with self._path.open("rb") as stream:
                content = stream.read(_MAX_TOKEN_BYTES + 2)
        except OSError:
            raise CredentialBrokerUnavailableError(
                "credential.broker.identity.unavailable"
            ) from None
        if content.endswith(b"\n"):
            content = content[:-1]
        if len(content) > _MAX_TOKEN_BYTES:
            raise CredentialBrokerUnavailableError(
                "credential.broker.identity.unavailable"
            )
        try:
            token = content.decode("ascii")
        except UnicodeDecodeError:
            raise CredentialBrokerUnavailableError(
                "credential.broker.identity.unavailable"
            ) from None
        if not _safe_secret(token):
            raise CredentialBrokerUnavailableError(
                "credential.broker.identity.unavailable"
            )
        return token


class CredentialBrokerHttpTransport(Protocol):
    def post(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        *,
        ca_bundle_path: str | None,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        """Execute one bounded, no-redirect credential broker request."""


class NoCredentialBrokerRedirectHandler(HTTPRedirectHandler):
    """Prevent workload identity from being forwarded to another endpoint."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        return None


class UrllibCredentialBrokerHttpTransport:
    """TLS-verifying standard-library transport for the external broker."""

    def post(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        *,
        ca_bundle_path: str | None,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        try:
            context = ssl.create_default_context(cafile=ca_bundle_path)
            opener = build_opener(
                ProxyHandler({}),
                HTTPSHandler(context=context),
                NoCredentialBrokerRedirectHandler(),
            )
            request = Request(url, data=body, headers=dict(headers), method="POST")
            with opener.open(request, timeout=timeout_seconds) as response:
                if response.status != 200:
                    raise CredentialBrokerUnavailableError(
                        "credential.broker.upstream.unavailable"
                    )
                if response.headers.get_content_type() != "application/json":
                    raise CredentialBrokerUnavailableError(
                        "credential.broker.response.invalid"
                    )
                content = response.read(max_response_bytes + 1)
        except HTTPError as error:
            status = error.code
            error.close()
            if status == 403:
                raise CredentialBrokerUnavailableError(
                    "credential.broker.request.denied"
                ) from None
            raise CredentialBrokerUnavailableError(
                "credential.broker.upstream.unavailable"
            ) from None
        except CredentialBrokerUnavailableError:
            raise
        except Exception:
            raise CredentialBrokerUnavailableError(
                "credential.broker.upstream.unavailable"
            ) from None
        if len(content) > max_response_bytes:
            raise CredentialBrokerUnavailableError(
                "credential.broker.response.limited"
            )
        return content


class ExternalHttpCredentialBroker:
    """Exchange exact request scope for one short-lived Bearer lease."""

    def __init__(
        self,
        configuration: ExternalCredentialBrokerConfiguration,
        clock: Clock,
        *,
        transport: CredentialBrokerHttpTransport | None = None,
        token_source: WorkloadIdentityTokenSource | None = None,
        request_id: Callable[[], str] | None = None,
    ) -> None:
        self._configuration = configuration
        self._clock = clock
        self._transport = transport or UrllibCredentialBrokerHttpTransport()
        self._token_source = token_source or WorkloadIdentityTokenSource(
            configuration.workload_identity_token_path
        )
        self._request_id = request_id or (lambda: "crq_" + uuid.uuid4().hex)

    def __repr__(self) -> str:
        return "ExternalHttpCredentialBroker(configuration=<protected>)"

    def resolve(self, request: CredentialLeaseRequest) -> CredentialLease:
        requested_at_text = self._clock.now()
        requested_at = _parse_time(
            requested_at_text, "credential.broker.request.invalid"
        )
        deadline = self._validate_request(request, requested_at)
        request_id = self._request_id()
        if not isinstance(request_id, str) or not _REQUEST_ID.fullmatch(request_id):
            raise CredentialBrokerUnavailableError(
                "credential.broker.request.invalid"
            )
        document = {
            "apiVersion": "iip.broker/v1alpha1",
            "kind": "CredentialLeaseRequest",
            "metadata": {
                "requestId": request_id,
                "tenantId": request.tenant_id,
                "actorId": request.actor_id,
                "requestedAt": requested_at_text,
            },
            "spec": {
                "integrationId": request.integration_id,
                "credentialRef": request.credential_ref,
                "provider": request.provider,
                "scopes": list(request.scopes),
                "deadline": request.deadline,
            },
        }
        body = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(body) > _MAX_REQUEST_BYTES:
            raise CredentialBrokerUnavailableError(
                "credential.broker.request.invalid"
            )
        workload_token = self._token_source.read()
        remaining = (deadline - requested_at).total_seconds()
        content = self._transport.post(
            self._configuration.endpoint + "/v1/credential-leases",
            body,
            {
                "Accept": "application/json",
                "Authorization": f"Bearer {workload_token}",
                "Content-Type": "application/json",
                "User-Agent": "iip-credential-broker-client/0.1.0",
            },
            ca_bundle_path=self._configuration.ca_bundle_path,
            timeout_seconds=min(
                float(self._configuration.request_timeout_seconds), remaining
            ),
            max_response_bytes=self._configuration.max_response_bytes,
        )
        return self._lease(
            content,
            request_id=request_id,
            request_deadline=deadline,
            requested_at=requested_at,
        )

    def _validate_request(
        self, request: CredentialLeaseRequest, requested_at: datetime
    ) -> datetime:
        if (
            not isinstance(request, CredentialLeaseRequest)
            or not _TENANT_ID.fullmatch(request.tenant_id)
            or not _safe_text(request.actor_id, 256)
            or not _INTEGRATION_ID.fullmatch(request.integration_id)
            or not _CREDENTIAL_REF.fullmatch(request.credential_ref)
            or not _PROVIDER.fullmatch(request.provider)
            or not isinstance(request.scopes, tuple)
            or not 1 <= len(request.scopes) <= 16
            or len(request.scopes) != len(set(request.scopes))
            or any(not _SCOPE.fullmatch(scope) for scope in request.scopes)
        ):
            raise CredentialBrokerUnavailableError(
                "credential.broker.request.invalid"
            )
        deadline = _parse_time(request.deadline, "credential.broker.request.invalid")
        if deadline <= requested_at:
            raise CredentialBrokerUnavailableError(
                "credential.broker.deadline.exceeded"
            )
        return deadline

    def _lease(
        self,
        content: bytes,
        *,
        request_id: str,
        request_deadline: datetime,
        requested_at: datetime,
    ) -> CredentialLease:
        try:
            document = json.loads(content.decode("utf-8"))
            if (
                not isinstance(document, dict)
                or set(document) != {"apiVersion", "kind", "metadata", "spec"}
                or document["apiVersion"] != "iip.broker/v1alpha1"
                or document["kind"] != "CredentialLease"
                or not isinstance(document["metadata"], dict)
                or set(document["metadata"]) != {"requestId", "issuedAt"}
                or document["metadata"]["requestId"] != request_id
                or not isinstance(document["spec"], dict)
                or set(document["spec"]) != {"scheme", "secret", "expiresAt"}
                or document["spec"]["scheme"] != "bearer"
                or not _safe_secret(document["spec"]["secret"])
            ):
                raise TypeError
            issued_at = _parse_time(
                document["metadata"]["issuedAt"],
                "credential.broker.response.invalid",
            )
            expires_at = _parse_time(
                document["spec"]["expiresAt"],
                "credential.broker.response.invalid",
            )
        except CredentialBrokerUnavailableError:
            raise
        except (
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            raise CredentialBrokerUnavailableError(
                "credential.broker.response.invalid"
            ) from None
        response_time = _parse_time(
            self._clock.now(), "credential.broker.response.invalid"
        )
        skew = timedelta(seconds=self._configuration.max_clock_skew_seconds)
        if (
            response_time > request_deadline
            or issued_at < requested_at - skew
            or issued_at > response_time + skew
            or expires_at < request_deadline
            or expires_at <= issued_at
            or expires_at - issued_at
            > timedelta(seconds=self._configuration.max_lease_seconds)
        ):
            raise CredentialBrokerUnavailableError(
                "credential.broker.lease.invalid"
            )
        return CredentialLease(
            scheme="bearer",
            secret=document["spec"]["secret"],
            expires_at=document["spec"]["expiresAt"],
        )


def build_external_credential_broker_from_environment(
    environment: Mapping[str, str], clock: Clock
) -> ExternalHttpCredentialBroker:
    encoded = environment.get("IIP_CREDENTIAL_BROKER_CONFIG_JSON")
    if encoded is None:
        raise CredentialBrokerConfigurationError(
            "credential.broker.configuration.required"
        )
    return ExternalHttpCredentialBroker(
        ExternalCredentialBrokerConfiguration.from_json(encoded),
        clock,
    )


def _validate_endpoint(value: object) -> None:
    if not isinstance(value, str):
        raise TypeError
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        raise TypeError from None
    if (
        not 1 <= len(value) <= 2_048
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65_535)
        or any(character.isspace() for character in value)
    ):
        raise TypeError


def _validate_absolute_path(value: object, *, optional: bool) -> None:
    if value is None and optional:
        return
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 4_096
        or not Path(value).is_absolute()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise TypeError


def _integer(value: object, minimum: int, maximum: int) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    )


def _safe_text(value: object, maximum: int) -> bool:
    return bool(
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _safe_secret(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and 16 <= len(value) <= 16_384
        and all(33 <= ord(character) <= 126 for character in value)
    )


def _parse_time(value: object, error_code: str) -> datetime:
    if not isinstance(value, str):
        raise CredentialBrokerUnavailableError(error_code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise CredentialBrokerUnavailableError(error_code) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CredentialBrokerUnavailableError(error_code)
    return parsed
