"""Loki historical-log adapter with closed query translation and protected config."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Protocol
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from iip.application.ports import (
    Clock,
    CredentialBroker,
    CredentialLease,
    CredentialLeaseRequest,
    TelemetryLogRecord,
    TelemetryLogsQuery,
    TelemetryLogsResult,
)


_TENANT_ID = re.compile(r"[a-zA-Z0-9._-]{1,128}")
_INTEGRATION_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_LOGICAL_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.:/-]{0,127}")
_LOKI_LABEL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_RESOURCE_UID = re.compile(r"res_[a-f0-9]{32}")
_TRACE_ID = re.compile(r"[a-f0-9]{32}")
_SPAN_ID = re.compile(r"[a-f0-9]{16}")
_CREDENTIAL_REF = re.compile(r"credential://[A-Za-z0-9._~:/-]{1,2020}")
_SEVERITIES = frozenset(
    {"trace", "debug", "info", "warn", "error", "fatal", "unspecified"}
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:authorization|password|passwd|token|secret|api[_-]?key|"
    r"access[_-]?key)\s*[:=]\s*\S+|bearer\s+\S+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
_MAX_CONFIGURATION_BYTES = 1_048_576
_MAX_INTEGRATIONS = 128
_MAX_BINDINGS = 256
_MAX_CREDENTIALS = 256
_MAX_URL_BYTES = 32_768


class LokiConfigurationError(RuntimeError):
    """Protected Loki integration or credential configuration is invalid."""


class LokiBackendError(RuntimeError):
    """Loki could not safely satisfy a normalized historical-log query."""


@dataclass(frozen=True)
class LokiLabelBinding:
    """Allowlisted translation between public log fields and Loki labels."""

    resource_uid: str
    service: str
    severity: str
    trace_id: str | None
    span_id: str | None
    attributes: tuple[tuple[str, str], ...]

    def attribute_label(self, logical_name: str) -> str:
        for logical, backend in self.attributes:
            if logical == logical_name:
                return backend
        raise LokiBackendError("logs.backend.query.unsupported")


@dataclass(frozen=True)
class LokiIntegration:
    """One tenant-bound endpoint and its closed label/value catalog."""

    tenant_id: str
    integration_id: str
    endpoint: str
    credential_ref: str | None
    organization_id: str | None
    enabled: bool
    request_timeout_seconds: int
    max_response_bytes: int
    labels: LokiLabelBinding
    services: tuple[tuple[str, str], ...]
    severities: tuple[tuple[str, str], ...]

    def backend_service(self, logical_name: str) -> str:
        for logical, backend in self.services:
            if logical == logical_name:
                return backend
        raise LokiBackendError("logs.backend.service.unavailable")

    def logical_service(self, backend_name: str) -> str:
        for logical, backend in self.services:
            if backend == backend_name:
                return logical
        raise LokiBackendError("logs.backend.response.invalid")

    def backend_severity(self, normalized: str) -> str:
        for logical, backend in self.severities:
            if logical == normalized:
                return backend
        raise LokiBackendError("logs.backend.severity.unavailable")

    def logical_severity(self, backend_value: str) -> str:
        for logical, backend in self.severities:
            if backend == backend_value:
                return logical
        raise LokiBackendError("logs.backend.response.invalid")


class LokiIntegrationRegistry:
    """Validated non-secret Loki configuration keyed by tenant and integration."""

    def __init__(self, integrations: tuple[LokiIntegration, ...]) -> None:
        self._integrations = {
            (integration.tenant_id, integration.integration_id): integration
            for integration in integrations
        }

    @classmethod
    def from_json(cls, encoded: str) -> "LokiIntegrationRegistry":
        document = _json_document(encoded, "logs.backend.configuration.invalid")
        try:
            if set(document) != {"integrations"}:
                raise KeyError
            items = document["integrations"]
            if not isinstance(items, list) or not 1 <= len(items) <= _MAX_INTEGRATIONS:
                raise TypeError
            integrations = tuple(cls._integration(item) for item in items)
            keys = {(item.tenant_id, item.integration_id) for item in integrations}
            if len(keys) != len(integrations):
                raise TypeError
        except (KeyError, TypeError, ValueError, LokiBackendError):
            raise LokiConfigurationError("logs.backend.configuration.invalid") from None
        return cls(integrations)

    @classmethod
    def _integration(cls, value: object) -> LokiIntegration:
        required = {
            "tenantId",
            "integrationId",
            "provider",
            "endpoint",
            "credentialRef",
            "organizationId",
            "enabled",
            "requestTimeoutSeconds",
            "maxResponseBytes",
            "labels",
            "services",
            "severities",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise TypeError
        tenant_id = value["tenantId"]
        integration_id = value["integrationId"]
        endpoint = value["endpoint"]
        credential_ref = value["credentialRef"]
        organization_id = value["organizationId"]
        if (
            not isinstance(tenant_id, str)
            or not _TENANT_ID.fullmatch(tenant_id)
            or not isinstance(integration_id, str)
            or not _INTEGRATION_ID.fullmatch(integration_id)
            or value["provider"] != "loki"
            or not isinstance(value["enabled"], bool)
            or not _integer(value["requestTimeoutSeconds"], 1, 120)
            or not _integer(value["maxResponseBytes"], 1_024, 16_777_216)
        ):
            raise TypeError
        _validate_endpoint(endpoint)
        if credential_ref is not None and (
            not isinstance(credential_ref, str)
            or not _CREDENTIAL_REF.fullmatch(credential_ref)
        ):
            raise TypeError
        if organization_id is not None and not _safe_text(organization_id, 256):
            raise TypeError
        labels = cls._labels(value["labels"])
        services = cls._value_map(value["services"], allowed_keys=None)
        severities = cls._value_map(value["severities"], allowed_keys=_SEVERITIES)
        return LokiIntegration(
            tenant_id=tenant_id,
            integration_id=integration_id,
            endpoint=endpoint.rstrip("/"),
            credential_ref=credential_ref,
            organization_id=organization_id,
            enabled=value["enabled"],
            request_timeout_seconds=value["requestTimeoutSeconds"],
            max_response_bytes=value["maxResponseBytes"],
            labels=labels,
            services=services,
            severities=severities,
        )

    @staticmethod
    def _labels(value: object) -> LokiLabelBinding:
        keys = {"resourceUid", "service", "severity", "traceId", "spanId", "attributes"}
        if not isinstance(value, dict) or set(value) != keys:
            raise TypeError
        required = (value["resourceUid"], value["service"], value["severity"])
        optional = (value["traceId"], value["spanId"])
        if any(not isinstance(item, str) or not _valid_loki_label(item) for item in required):
            raise TypeError
        if (optional[0] is None) != (optional[1] is None):
            raise TypeError
        if any(item is not None and not _valid_loki_label(item) for item in optional):
            raise TypeError
        attributes = value["attributes"]
        if not isinstance(attributes, dict) or len(attributes) > 16:
            raise TypeError
        pairs: list[tuple[str, str]] = []
        for logical, backend in attributes.items():
            if (
                not isinstance(logical, str)
                or not _LOGICAL_NAME.fullmatch(logical)
                or not isinstance(backend, str)
                or not _valid_loki_label(backend)
            ):
                raise TypeError
            pairs.append((logical, backend))
        backend_labels = [*required, *(item for item in optional if item is not None), *(item[1] for item in pairs)]
        if len(backend_labels) != len(set(backend_labels)):
            raise TypeError
        return LokiLabelBinding(
            resource_uid=required[0],
            service=required[1],
            severity=required[2],
            trace_id=optional[0],
            span_id=optional[1],
            attributes=tuple(sorted(pairs)),
        )

    @staticmethod
    def _value_map(
        value: object,
        *,
        allowed_keys: frozenset[str] | None,
    ) -> tuple[tuple[str, str], ...]:
        if not isinstance(value, dict) or not 1 <= len(value) <= _MAX_BINDINGS:
            raise TypeError
        pairs: list[tuple[str, str]] = []
        for logical, backend in value.items():
            if (
                not isinstance(logical, str)
                or not _LOGICAL_NAME.fullmatch(logical)
                or (allowed_keys is not None and logical not in allowed_keys)
                or not _safe_text(backend, 256)
            ):
                raise TypeError
            pairs.append((logical, backend))
        if len({item[1] for item in pairs}) != len(pairs):
            raise TypeError
        return tuple(sorted(pairs))

    def resolve(self, tenant_id: str, integration_id: str) -> LokiIntegration:
        integration = self._integrations.get((tenant_id, integration_id))
        if integration is None or not integration.enabled:
            raise LokiBackendError("logs.backend.integration.unavailable")
        return integration


class StaticLokiCredentialBroker:
    """Local protected-config broker for Loki Bearer credentials only."""

    def __init__(self, credentials: Mapping[tuple[str, str, str], CredentialLease]) -> None:
        self._credentials = dict(credentials)

    def __repr__(self) -> str:
        return f"StaticLokiCredentialBroker(credentials={len(self._credentials)})"

    @classmethod
    def empty(cls) -> "StaticLokiCredentialBroker":
        return cls({})

    @classmethod
    def from_json(cls, encoded: str) -> "StaticLokiCredentialBroker":
        document = _json_document(encoded, "logs.credential.configuration.invalid")
        try:
            if set(document) != {"credentials"}:
                raise KeyError
            items = document["credentials"]
            if not isinstance(items, list) or len(items) > _MAX_CREDENTIALS:
                raise TypeError
            credentials: dict[tuple[str, str, str], CredentialLease] = {}
            for item in items:
                if not isinstance(item, dict) or set(item) != {
                    "tenantId", "integrationId", "credentialRef", "bearerToken", "expiresAt"
                }:
                    raise TypeError
                tenant_id = item["tenantId"]
                integration_id = item["integrationId"]
                credential_ref = item["credentialRef"]
                token = item["bearerToken"]
                expires_at = item["expiresAt"]
                if (
                    not isinstance(tenant_id, str)
                    or not _TENANT_ID.fullmatch(tenant_id)
                    or not isinstance(integration_id, str)
                    or not _INTEGRATION_ID.fullmatch(integration_id)
                    or not isinstance(credential_ref, str)
                    or not _CREDENTIAL_REF.fullmatch(credential_ref)
                    or not _safe_secret(token)
                    or (expires_at is not None and not isinstance(expires_at, str))
                ):
                    raise TypeError
                if expires_at is not None:
                    _parse_time(expires_at)
                key = (tenant_id, integration_id, credential_ref)
                if key in credentials:
                    raise TypeError
                credentials[key] = CredentialLease("bearer", token, expires_at)
        except (KeyError, TypeError, ValueError, LokiBackendError):
            raise LokiConfigurationError("logs.credential.configuration.invalid") from None
        return cls(credentials)

    def resolve(self, request: CredentialLeaseRequest) -> CredentialLease:
        if request.provider != "loki" or request.scopes != ("logs:read",):
            raise LokiBackendError("logs.credential.unavailable")
        lease = self._credentials.get(
            (request.tenant_id, request.integration_id, request.credential_ref)
        )
        if lease is None:
            raise LokiBackendError("logs.credential.unavailable")
        if lease.expires_at is not None and _parse_time(lease.expires_at) < _parse_time(request.deadline):
            raise LokiBackendError("logs.credential.unavailable")
        return lease


class LokiHttpTransport(Protocol):
    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        """Execute one bounded no-redirect Loki HTTP request."""


class NoRedirectHandler(HTTPRedirectHandler):
    """Refuse every redirect so credentials cannot move to another endpoint."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        return None


class UrllibLokiHttpTransport:
    """Standard-library Loki transport with strict content and byte bounds."""

    def __init__(self) -> None:
        self._opener = build_opener(NoRedirectHandler())

    def get(
        self,
        url: str,
        headers: Mapping[str, str],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        request = Request(url, headers=dict(headers), method="GET")
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                if response.status != 200:
                    raise LokiBackendError("logs.backend.unavailable")
                if response.headers.get_content_type() != "application/json":
                    raise LokiBackendError("logs.backend.response.invalid")
                content = response.read(max_response_bytes + 1)
        except LokiBackendError:
            raise
        except Exception:
            raise LokiBackendError("logs.backend.unavailable") from None
        if len(content) > max_response_bytes:
            raise LokiBackendError("logs.backend.response.limited")
        return content


class LokiTelemetryLogsBackend:
    """Translate closed logical selectors to Loki range queries and normalize results."""

    def __init__(
        self,
        registry: LokiIntegrationRegistry,
        credentials: CredentialBroker,
        clock: Clock,
        transport: LokiHttpTransport | None = None,
    ) -> None:
        self._registry = registry
        self._credentials = credentials
        self._clock = clock
        self._transport = transport or UrllibLokiHttpTransport()

    def query_logs(self, request: TelemetryLogsQuery) -> TelemetryLogsResult:
        integration = self._registry.resolve(request.tenant_id, request.integration_id)
        expression = self._expression(request, integration)
        timeout = self._timeout(request, integration)
        query = urlencode(
            {
                "query": expression,
                "start": str(_nanoseconds(request.start)),
                "end": str(_nanoseconds(request.end)),
                "limit": str(request.max_records + 1),
                "direction": "forward",
            }
        )
        url = f"{integration.endpoint}/loki/api/v1/query_range?{query}"
        if len(url.encode("utf-8")) > _MAX_URL_BYTES:
            raise LokiBackendError("logs.backend.query.unsupported")
        headers = {"Accept": "application/json", "User-Agent": "iip-loki-adapter/0.58.0"}
        if integration.organization_id is not None:
            headers["X-Scope-OrgID"] = integration.organization_id
        if integration.credential_ref is not None:
            try:
                lease = self._credentials.resolve(
                    CredentialLeaseRequest(
                        tenant_id=request.tenant_id,
                        actor_id=request.actor_id,
                        integration_id=request.integration_id,
                        credential_ref=integration.credential_ref,
                        provider="loki",
                        scopes=("logs:read",),
                        deadline=request.deadline,
                    )
                )
            except Exception:
                raise LokiBackendError("logs.credential.unavailable") from None
            if lease.scheme != "bearer" or not _safe_secret(lease.secret):
                raise LokiBackendError("logs.credential.unavailable")
            headers["Authorization"] = f"Bearer {lease.secret}"
        content = self._transport.get(
            url,
            headers,
            timeout_seconds=timeout,
            max_response_bytes=integration.max_response_bytes,
        )
        return self._result(request, integration, content)

    @staticmethod
    def _expression(request: TelemetryLogsQuery, integration: LokiIntegration) -> str:
        labels = integration.labels
        matchers = [
            _regex_matcher(labels.resource_uid, request.resource_uids),
            _regex_matcher(
                labels.service,
                tuple(integration.backend_service(name) for name in request.service_names),
            ),
        ]
        if request.severities:
            matchers.append(
                _regex_matcher(
                    labels.severity,
                    tuple(integration.backend_severity(value) for value in request.severities),
                )
            )
        for logical, operator, expected in request.filters:
            label = labels.attribute_label(logical)
            if operator == "eq":
                matchers.append(f"{label}={json.dumps(expected, ensure_ascii=False)}")
            elif operator == "neq":
                matchers.append(f'{label}!=""')
                matchers.append(f"{label}!={json.dumps(expected, ensure_ascii=False)}")
            else:
                raise LokiBackendError("logs.backend.query.unsupported")
        return "{" + ",".join(matchers) + "}"

    def _timeout(self, request: TelemetryLogsQuery, integration: LokiIntegration) -> float:
        remaining = (_parse_time(request.deadline) - _parse_time(self._clock.now())).total_seconds()
        if remaining <= 0:
            raise LokiBackendError("logs.backend.deadline.exceeded")
        return min(float(integration.request_timeout_seconds), remaining)

    def _result(
        self,
        request: TelemetryLogsQuery,
        integration: LokiIntegration,
        content: bytes,
    ) -> TelemetryLogsResult:
        try:
            document = json.loads(content.decode("utf-8"))
            if (
                not isinstance(document, dict)
                or document.get("status") != "success"
                or not isinstance(document.get("data"), dict)
                or document["data"].get("resultType") != "streams"
                or not isinstance(document["data"].get("result"), list)
            ):
                raise TypeError
            provider_warnings = document.get("warnings", [])
            if not isinstance(provider_warnings, list) or any(
                not isinstance(item, str) for item in provider_warnings
            ):
                raise TypeError
            raw_records: list[tuple[int, str, Mapping[str, str], str]] = []
            for stream in document["data"]["result"]:
                raw_records.extend(self._stream(stream))
                if len(raw_records) > request.max_records + 1:
                    raise LokiBackendError("logs.backend.response.limited")
            raw_records.sort(key=lambda item: (item[0], item[1], item[3]))
            limited = len(raw_records) > request.max_records
            raw_records = raw_records[: request.max_records]
            records = tuple(
                self._record(request, integration, item, index)
                for index, item in enumerate(raw_records)
            )
        except LokiBackendError:
            raise
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            raise LokiBackendError("logs.backend.response.invalid") from None
        warnings = []
        if provider_warnings:
            warnings.append("backend-partial")
        if limited:
            warnings.append("record-limit")
        if warnings and not records:
            raise LokiBackendError("logs.backend.response.invalid")
        status = "partial" if warnings else "complete" if records else "no-data"
        return TelemetryLogsResult(
            executed_at=self._clock.now(),
            status=status,
            records=records,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _stream(value: object) -> list[tuple[int, str, Mapping[str, str], str]]:
        if not isinstance(value, dict) or set(value) != {"stream", "values"}:
            raise TypeError
        labels = value["stream"]
        values = value["values"]
        if (
            not isinstance(labels, dict)
            or any(not isinstance(key, str) or not isinstance(item, str) for key, item in labels.items())
            or not isinstance(values, list)
        ):
            raise TypeError
        label_digest = json.dumps(labels, separators=(",", ":"), sort_keys=True)
        rendered = []
        for item in values:
            if (
                not isinstance(item, list)
                or len(item) != 2
                or not isinstance(item[0], str)
                or not item[0].isdigit()
                or not isinstance(item[1], str)
            ):
                raise TypeError
            rendered.append((int(item[0]), label_digest, labels, item[1]))
        return rendered

    @staticmethod
    def _record(
        request: TelemetryLogsQuery,
        integration: LokiIntegration,
        raw: tuple[int, str, Mapping[str, str], str],
        ordinal: int,
    ) -> TelemetryLogRecord:
        timestamp_ns, label_digest, labels, body = raw
        binding = integration.labels
        resource_uid = labels.get(binding.resource_uid)
        service_value = labels.get(binding.service)
        severity_value = labels.get(binding.severity)
        if (
            not isinstance(resource_uid, str)
            or not _RESOURCE_UID.fullmatch(resource_uid)
            or resource_uid not in request.resource_uids
            or not isinstance(service_value, str)
            or not isinstance(severity_value, str)
            or not isinstance(body, str)
            or not 1 <= len(body) <= 4096
        ):
            raise LokiBackendError("logs.backend.response.invalid")
        logical_service = integration.logical_service(service_value)
        logical_severity = integration.logical_severity(severity_value)
        if (
            logical_service not in request.service_names
            or (request.severities and logical_severity not in request.severities)
            or not _nanoseconds(request.start) <= timestamp_ns <= _nanoseconds(request.end)
        ):
            raise LokiBackendError("logs.backend.response.invalid")
        attributes = tuple(
            (logical, labels[backend])
            for logical, backend in binding.attributes
            if backend in labels
        )
        if any(
            not _safe_text(value, 256) or _SENSITIVE_TEXT.search(value)
            for _, value in attributes
        ):
            raise LokiBackendError("logs.backend.response.invalid")
        attribute_map = dict(attributes)
        for logical, operator, expected in request.filters:
            observed = attribute_map.get(logical)
            if (
                (operator == "eq" and observed != expected)
                or (operator == "neq" and (observed is None or observed == expected))
            ):
                raise LokiBackendError("logs.backend.response.invalid")
        trace_id = labels.get(binding.trace_id) if binding.trace_id is not None else None
        span_id = labels.get(binding.span_id) if binding.span_id is not None else None
        if (trace_id is None) != (span_id is None):
            raise LokiBackendError("logs.backend.response.invalid")
        if trace_id is not None and (
            not _TRACE_ID.fullmatch(trace_id) or not _SPAN_ID.fullmatch(span_id or "")
        ):
            raise LokiBackendError("logs.backend.response.invalid")
        identity = f"{request.integration_id}\n{timestamp_ns}\n{label_digest}\n{body}\n{ordinal}"
        record_id = "log_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        return TelemetryLogRecord(
            record_id=record_id,
            resource_uid=resource_uid,
            timestamp=_timestamp_from_nanoseconds(timestamp_ns),
            severity=logical_severity,
            service_name=logical_service,
            body=body,
            attributes=attributes,
            trace_id=trace_id,
            span_id=span_id,
        )


def build_loki_backend_from_environment(
    environment: Mapping[str, str],
    clock: Clock,
    credential_broker: CredentialBroker | None = None,
) -> LokiTelemetryLogsBackend:
    """Build the Loki adapter from protected runtime configuration."""

    integrations = environment.get("IIP_LOKI_INTEGRATIONS_JSON")
    if integrations is None:
        raise LokiConfigurationError("logs.backend.configuration.required")
    credentials = environment.get("IIP_LOKI_CREDENTIALS_JSON", '{"credentials":[]}') or '{"credentials":[]}'
    return LokiTelemetryLogsBackend(
        LokiIntegrationRegistry.from_json(integrations),
        credential_broker
        if credential_broker is not None
        else StaticLokiCredentialBroker.from_json(credentials),
        clock,
    )


def _json_document(encoded: object, error_code: str) -> dict[str, object]:
    if not isinstance(encoded, str) or not 1 <= len(encoded.encode("utf-8")) <= _MAX_CONFIGURATION_BYTES:
        raise LokiConfigurationError(error_code)
    try:
        document = json.loads(encoded)
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise LokiConfigurationError(error_code) from None
    if not isinstance(document, dict):
        raise LokiConfigurationError(error_code)
    return document


def _valid_loki_label(value: str) -> bool:
    return bool(_LOKI_LABEL.fullmatch(value) and value != "__name__")


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
        or parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65_535)
        or any(character.isspace() for character in value)
    ):
        raise TypeError


def _integer(value: object, minimum: int, maximum: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and minimum <= value <= maximum


def _safe_text(value: object, maximum: int) -> bool:
    return bool(
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )


def _safe_secret(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and 16 <= len(value) <= 8_192
        and not any(ord(character) < 33 or ord(character) == 127 for character in value)
    )


def _regex_matcher(label: str, values: tuple[str, ...]) -> str:
    if not values:
        raise LokiBackendError("logs.backend.query.unsupported")
    expression = "|".join(_escape_re2_literal(value) for value in sorted(set(values)))
    return f"{label}=~{json.dumps(expression, ensure_ascii=False)}"


def _escape_re2_literal(value: str) -> str:
    return re.sub(r"([\\.*+?()|\[\]{}^$])", r"\\\1", value)


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise LokiBackendError("logs.backend.time.invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise LokiBackendError("logs.backend.time.invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise LokiBackendError("logs.backend.time.invalid")
    return parsed.astimezone(timezone.utc)


def _nanoseconds(value: str) -> int:
    parsed = _parse_time(value)
    delta = parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1_000


def _timestamp_from_nanoseconds(value: int) -> str:
    if value < 0:
        raise LokiBackendError("logs.backend.response.invalid")
    try:
        parsed = datetime.fromtimestamp(value // 1_000_000_000, timezone.utc).replace(
            microsecond=(value % 1_000_000_000) // 1_000
        )
    except (OverflowError, OSError, ValueError):
        raise LokiBackendError("logs.backend.response.invalid") from None
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")
