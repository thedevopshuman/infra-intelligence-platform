"""OpenSearch historical-log adapter with closed query translation and protected config."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping, Protocol
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.parse import urlsplit

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
_FIELD_NAME = re.compile(r"@?[A-Za-z_][A-Za-z0-9_.]{0,127}")
_INDEX_PATTERN = re.compile(r"[a-z0-9][a-z0-9._*-]{0,127}")
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


class OpenSearchConfigurationError(RuntimeError):
    """Protected OpenSearch integration or credential configuration is invalid."""


class OpenSearchBackendError(RuntimeError):
    """OpenSearch could not safely satisfy a normalized historical-log query."""


@dataclass(frozen=True)
class OpenSearchFieldBinding:
    """Allowlisted translation between public log fields and document fields."""

    resource_uid: str
    service: str
    severity: str
    timestamp: str
    body: str
    trace_id: str | None
    span_id: str | None
    attributes: tuple[tuple[str, str], ...]

    def attribute_field(self, logical_name: str) -> str:
        for logical, backend in self.attributes:
            if logical == logical_name:
                return backend
        raise OpenSearchBackendError("logs.backend.query.unsupported")


@dataclass(frozen=True)
class OpenSearchIntegration:
    """One tenant-bound endpoint and its closed index/field/value catalog."""

    tenant_id: str
    integration_id: str
    endpoint: str
    credential_ref: str | None
    index: str
    enabled: bool
    request_timeout_seconds: int
    max_response_bytes: int
    fields: OpenSearchFieldBinding
    services: tuple[tuple[str, str], ...]
    severities: tuple[tuple[str, str], ...]

    def backend_service(self, logical_name: str) -> str:
        for logical, backend in self.services:
            if logical == logical_name:
                return backend
        raise OpenSearchBackendError("logs.backend.service.unavailable")

    def logical_service(self, backend_name: str) -> str:
        for logical, backend in self.services:
            if backend == backend_name:
                return logical
        raise OpenSearchBackendError("logs.backend.response.invalid")

    def backend_severity(self, normalized: str) -> str:
        for logical, backend in self.severities:
            if logical == normalized:
                return backend
        raise OpenSearchBackendError("logs.backend.severity.unavailable")

    def logical_severity(self, backend_value: str) -> str:
        for logical, backend in self.severities:
            if backend == backend_value:
                return logical
        raise OpenSearchBackendError("logs.backend.response.invalid")


class OpenSearchIntegrationRegistry:
    """Validated non-secret OpenSearch configuration keyed by tenant and integration."""

    def __init__(self, integrations: tuple[OpenSearchIntegration, ...]) -> None:
        self._integrations = {
            (integration.tenant_id, integration.integration_id): integration
            for integration in integrations
        }

    @classmethod
    def from_json(cls, encoded: str) -> "OpenSearchIntegrationRegistry":
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
        except (KeyError, TypeError, ValueError, OpenSearchBackendError):
            raise OpenSearchConfigurationError(
                "logs.backend.configuration.invalid"
            ) from None
        return cls(integrations)

    @classmethod
    def _integration(cls, value: object) -> OpenSearchIntegration:
        required = {
            "tenantId",
            "integrationId",
            "provider",
            "endpoint",
            "credentialRef",
            "index",
            "enabled",
            "requestTimeoutSeconds",
            "maxResponseBytes",
            "fields",
            "services",
            "severities",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise TypeError
        tenant_id = value["tenantId"]
        integration_id = value["integrationId"]
        endpoint = value["endpoint"]
        credential_ref = value["credentialRef"]
        index = value["index"]
        if (
            not isinstance(tenant_id, str)
            or not _TENANT_ID.fullmatch(tenant_id)
            or not isinstance(integration_id, str)
            or not _INTEGRATION_ID.fullmatch(integration_id)
            or value["provider"] != "opensearch"
            or not isinstance(value["enabled"], bool)
            or not _integer(value["requestTimeoutSeconds"], 1, 120)
            or not _integer(value["maxResponseBytes"], 1_024, 16_777_216)
            or not isinstance(index, str)
            or not _INDEX_PATTERN.fullmatch(index)
        ):
            raise TypeError
        _validate_endpoint(endpoint)
        if credential_ref is not None and (
            not isinstance(credential_ref, str)
            or not _CREDENTIAL_REF.fullmatch(credential_ref)
        ):
            raise TypeError
        fields = cls._fields(value["fields"])
        services = cls._value_map(value["services"], allowed_keys=None)
        severities = cls._value_map(value["severities"], allowed_keys=_SEVERITIES)
        return OpenSearchIntegration(
            tenant_id=tenant_id,
            integration_id=integration_id,
            endpoint=endpoint.rstrip("/"),
            credential_ref=credential_ref,
            index=index,
            enabled=value["enabled"],
            request_timeout_seconds=value["requestTimeoutSeconds"],
            max_response_bytes=value["maxResponseBytes"],
            fields=fields,
            services=services,
            severities=severities,
        )

    @staticmethod
    def _fields(value: object) -> OpenSearchFieldBinding:
        keys = {
            "resourceUid",
            "service",
            "severity",
            "timestamp",
            "body",
            "traceId",
            "spanId",
            "attributes",
        }
        if not isinstance(value, dict) or set(value) != keys:
            raise TypeError
        required = (
            value["resourceUid"],
            value["service"],
            value["severity"],
            value["timestamp"],
            value["body"],
        )
        optional = (value["traceId"], value["spanId"])
        if any(not isinstance(item, str) or not _FIELD_NAME.fullmatch(item) for item in required):
            raise TypeError
        if (optional[0] is None) != (optional[1] is None):
            raise TypeError
        if any(item is not None and not _FIELD_NAME.fullmatch(item) for item in optional):
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
                or not _FIELD_NAME.fullmatch(backend)
            ):
                raise TypeError
            pairs.append((logical, backend))
        backend_fields = [*required, *(item for item in optional if item is not None), *(item[1] for item in pairs)]
        if len(backend_fields) != len(set(backend_fields)):
            raise TypeError
        return OpenSearchFieldBinding(
            resource_uid=required[0],
            service=required[1],
            severity=required[2],
            timestamp=required[3],
            body=required[4],
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

    def resolve(self, tenant_id: str, integration_id: str) -> OpenSearchIntegration:
        integration = self._integrations.get((tenant_id, integration_id))
        if integration is None or not integration.enabled:
            raise OpenSearchBackendError("logs.backend.integration.unavailable")
        return integration


class StaticOpenSearchCredentialBroker:
    """Local protected-config broker for OpenSearch Bearer credentials only."""

    def __init__(self, credentials: Mapping[tuple[str, str, str], CredentialLease]) -> None:
        self._credentials = dict(credentials)

    def __repr__(self) -> str:
        return f"StaticOpenSearchCredentialBroker(credentials={len(self._credentials)})"

    @classmethod
    def empty(cls) -> "StaticOpenSearchCredentialBroker":
        return cls({})

    @classmethod
    def from_json(cls, encoded: str) -> "StaticOpenSearchCredentialBroker":
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
        except (KeyError, TypeError, ValueError, OpenSearchBackendError):
            raise OpenSearchConfigurationError(
                "logs.credential.configuration.invalid"
            ) from None
        return cls(credentials)

    def resolve(self, request: CredentialLeaseRequest) -> CredentialLease:
        if request.provider != "opensearch" or request.scopes != ("logs:read",):
            raise OpenSearchBackendError("logs.credential.unavailable")
        lease = self._credentials.get(
            (request.tenant_id, request.integration_id, request.credential_ref)
        )
        if lease is None:
            raise OpenSearchBackendError("logs.credential.unavailable")
        if lease.expires_at is not None and _parse_time(lease.expires_at) < _parse_time(request.deadline):
            raise OpenSearchBackendError("logs.credential.unavailable")
        return lease


class OpenSearchHttpTransport(Protocol):
    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        """Execute one bounded no-redirect OpenSearch search request."""


class NoRedirectHandler(HTTPRedirectHandler):
    """Refuse every redirect so credentials cannot move to another endpoint."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        return None


class UrllibOpenSearchHttpTransport:
    """Standard-library OpenSearch transport with strict content and byte bounds."""

    def __init__(self) -> None:
        self._opener = build_opener(NoRedirectHandler())

    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        request = Request(url, data=body, headers=dict(headers), method="POST")
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                if response.status != 200:
                    raise OpenSearchBackendError("logs.backend.unavailable")
                if response.headers.get_content_type() != "application/json":
                    raise OpenSearchBackendError("logs.backend.response.invalid")
                content = response.read(max_response_bytes + 1)
        except OpenSearchBackendError:
            raise
        except Exception:
            raise OpenSearchBackendError("logs.backend.unavailable") from None
        if len(content) > max_response_bytes:
            raise OpenSearchBackendError("logs.backend.response.limited")
        return content


class OpenSearchTelemetryLogsBackend:
    """Translate closed logical selectors to OpenSearch `_search` queries and normalize results."""

    def __init__(
        self,
        registry: OpenSearchIntegrationRegistry,
        credentials: CredentialBroker,
        clock: Clock,
        transport: OpenSearchHttpTransport | None = None,
    ) -> None:
        self._registry = registry
        self._credentials = credentials
        self._clock = clock
        self._transport = transport or UrllibOpenSearchHttpTransport()

    def query_logs(self, request: TelemetryLogsQuery) -> TelemetryLogsResult:
        integration = self._registry.resolve(request.tenant_id, request.integration_id)
        body = self._request_body(request, integration)
        timeout = self._timeout(request, integration)
        encoded_body = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
        url = f"{integration.endpoint}/{integration.index}/_search"
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "iip-opensearch-adapter/0.84.1",
        }
        if integration.credential_ref is not None:
            try:
                lease = self._credentials.resolve(
                    CredentialLeaseRequest(
                        tenant_id=request.tenant_id,
                        actor_id=request.actor_id,
                        integration_id=request.integration_id,
                        credential_ref=integration.credential_ref,
                        provider="opensearch",
                        scopes=("logs:read",),
                        deadline=request.deadline,
                    )
                )
            except Exception:
                raise OpenSearchBackendError("logs.credential.unavailable") from None
            if lease.scheme != "bearer" or not _safe_secret(lease.secret):
                raise OpenSearchBackendError("logs.credential.unavailable")
            headers["Authorization"] = f"Bearer {lease.secret}"
        content = self._transport.post(
            url,
            headers,
            encoded_body,
            timeout_seconds=timeout,
            max_response_bytes=integration.max_response_bytes,
        )
        return self._result(request, integration, content)

    @staticmethod
    def _request_body(
        request: TelemetryLogsQuery, integration: OpenSearchIntegration
    ) -> dict[str, object]:
        fields = integration.fields
        filters: list[dict[str, object]] = [
            {
                "range": {
                    fields.timestamp: {
                        "gte": request.start,
                        "lte": request.end,
                        "format": "strict_date_optional_time",
                    }
                }
            },
            {"terms": {f"{fields.resource_uid}.keyword": list(request.resource_uids)}},
            {
                "terms": {
                    f"{fields.service}.keyword": [
                        integration.backend_service(name) for name in request.service_names
                    ]
                }
            },
        ]
        if request.severities:
            filters.append(
                {
                    "terms": {
                        f"{fields.severity}.keyword": [
                            integration.backend_severity(value)
                            for value in request.severities
                        ]
                    }
                }
            )
        must_not: list[dict[str, object]] = []
        for logical, operator, expected in request.filters:
            field = fields.attribute_field(logical)
            if operator == "eq":
                filters.append({"term": {f"{field}.keyword": expected}})
            elif operator == "neq":
                filters.append({"exists": {"field": field}})
                must_not.append({"term": {f"{field}.keyword": expected}})
            else:
                raise OpenSearchBackendError("logs.backend.query.unsupported")
        query: dict[str, object] = {"bool": {"filter": filters}}
        if must_not:
            query["bool"]["must_not"] = must_not
        return {
            "size": request.max_records + 1,
            "track_total_hits": False,
            "sort": [{fields.timestamp: "asc"}, {"_id": "asc"}],
            "query": query,
        }

    def _timeout(self, request: TelemetryLogsQuery, integration: OpenSearchIntegration) -> float:
        remaining = (_parse_time(request.deadline) - _parse_time(self._clock.now())).total_seconds()
        if remaining <= 0:
            raise OpenSearchBackendError("logs.backend.deadline.exceeded")
        return min(float(integration.request_timeout_seconds), remaining)

    def _result(
        self,
        request: TelemetryLogsQuery,
        integration: OpenSearchIntegration,
        content: bytes,
    ) -> TelemetryLogsResult:
        try:
            document = json.loads(content.decode("utf-8"))
            if (
                not isinstance(document, dict)
                or bool(document.get("timed_out")) is True
                or not isinstance(document.get("_shards"), dict)
                or not isinstance(document.get("hits"), dict)
                or not isinstance(document["hits"].get("hits"), list)
            ):
                raise TypeError
            shards = document["_shards"]
            failed_shards = shards.get("failed")
            if not isinstance(failed_shards, int) or isinstance(failed_shards, bool) or failed_shards < 0:
                raise TypeError
            hits = document["hits"]["hits"]
            if len(hits) > request.max_records + 1:
                raise OpenSearchBackendError("logs.backend.response.limited")
            limited = len(hits) > request.max_records
            hits = hits[: request.max_records]
            records = tuple(
                self._record(request, integration, hit, index)
                for index, hit in enumerate(hits)
            )
        except OpenSearchBackendError:
            raise
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            raise OpenSearchBackendError("logs.backend.response.invalid") from None
        warnings = []
        if failed_shards:
            warnings.append("backend-partial")
        if limited:
            warnings.append("record-limit")
        if warnings and not records:
            raise OpenSearchBackendError("logs.backend.response.invalid")
        status = "partial" if warnings else "complete" if records else "no-data"
        return TelemetryLogsResult(
            executed_at=self._clock.now(),
            status=status,
            records=records,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _record(
        request: TelemetryLogsQuery,
        integration: OpenSearchIntegration,
        hit: object,
        ordinal: int,
    ) -> TelemetryLogRecord:
        fields = integration.fields
        if (
            not isinstance(hit, dict)
            or not isinstance(hit.get("_id"), str)
            or not isinstance(hit.get("_source"), dict)
        ):
            raise OpenSearchBackendError("logs.backend.response.invalid")
        source = hit["_source"]
        resource_uid = source.get(fields.resource_uid)
        service_value = source.get(fields.service)
        severity_value = source.get(fields.severity)
        timestamp_value = source.get(fields.timestamp)
        body = source.get(fields.body)
        if (
            not isinstance(resource_uid, str)
            or not _RESOURCE_UID.fullmatch(resource_uid)
            or resource_uid not in request.resource_uids
            or not isinstance(service_value, str)
            or not isinstance(severity_value, str)
            or not isinstance(body, str)
            or not 1 <= len(body) <= 4096
        ):
            raise OpenSearchBackendError("logs.backend.response.invalid")
        normalized_timestamp = _normalize_timestamp(timestamp_value)
        logical_service = integration.logical_service(service_value)
        logical_severity = integration.logical_severity(severity_value)
        if (
            logical_service not in request.service_names
            or (request.severities and logical_severity not in request.severities)
            or not _parse_time(request.start) <= _parse_time(normalized_timestamp) <= _parse_time(request.end)
        ):
            raise OpenSearchBackendError("logs.backend.response.invalid")
        attributes = tuple(
            (logical, source[backend])
            for logical, backend in fields.attributes
            if backend in source and isinstance(source[backend], str)
        )
        if any(
            not _safe_text(value, 256) or _SENSITIVE_TEXT.search(value)
            for _, value in attributes
        ):
            raise OpenSearchBackendError("logs.backend.response.invalid")
        attribute_map = dict(attributes)
        for logical, operator, expected in request.filters:
            observed = attribute_map.get(logical)
            if (
                (operator == "eq" and observed != expected)
                or (operator == "neq" and (observed is None or observed == expected))
            ):
                raise OpenSearchBackendError("logs.backend.response.invalid")
        trace_id = source.get(fields.trace_id) if fields.trace_id is not None else None
        span_id = source.get(fields.span_id) if fields.span_id is not None else None
        if (trace_id is None) != (span_id is None):
            raise OpenSearchBackendError("logs.backend.response.invalid")
        if trace_id is not None and (
            not isinstance(trace_id, str)
            or not isinstance(span_id, str)
            or not _TRACE_ID.fullmatch(trace_id)
            or not _SPAN_ID.fullmatch(span_id)
        ):
            raise OpenSearchBackendError("logs.backend.response.invalid")
        identity = f"{request.integration_id}\n{hit['_id']}\n{ordinal}"
        record_id = "log_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        return TelemetryLogRecord(
            record_id=record_id,
            resource_uid=resource_uid,
            timestamp=normalized_timestamp,
            severity=logical_severity,
            service_name=logical_service,
            body=body,
            attributes=attributes,
            trace_id=trace_id,
            span_id=span_id,
        )


def build_opensearch_backend_from_environment(
    environment: Mapping[str, str],
    clock: Clock,
    credential_broker: CredentialBroker | None = None,
) -> OpenSearchTelemetryLogsBackend:
    """Build the OpenSearch adapter from protected runtime configuration."""

    integrations = environment.get("IIP_OPENSEARCH_INTEGRATIONS_JSON")
    if integrations is None:
        raise OpenSearchConfigurationError("logs.backend.configuration.required")
    credentials = (
        environment.get("IIP_OPENSEARCH_CREDENTIALS_JSON", '{"credentials":[]}')
        or '{"credentials":[]}'
    )
    return OpenSearchTelemetryLogsBackend(
        OpenSearchIntegrationRegistry.from_json(integrations),
        credential_broker
        if credential_broker is not None
        else StaticOpenSearchCredentialBroker.from_json(credentials),
        clock,
    )


def _json_document(encoded: object, error_code: str) -> dict[str, object]:
    if not isinstance(encoded, str) or not 1 <= len(encoded.encode("utf-8")) <= _MAX_CONFIGURATION_BYTES:
        raise OpenSearchConfigurationError(error_code)
    try:
        document = json.loads(encoded)
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise OpenSearchConfigurationError(error_code) from None
    if not isinstance(document, dict):
        raise OpenSearchConfigurationError(error_code)
    return document


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


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise OpenSearchBackendError("logs.backend.time.invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise OpenSearchBackendError("logs.backend.time.invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OpenSearchBackendError("logs.backend.time.invalid")
    return parsed.astimezone(timezone.utc)


def _normalize_timestamp(value: object) -> str:
    parsed = _parse_time(value)
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")
