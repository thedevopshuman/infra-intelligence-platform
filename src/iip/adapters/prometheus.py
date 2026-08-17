"""Prometheus-compatible historical metrics adapter and protected configuration."""

from __future__ import annotations

import json
import math
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
    TelemetryMetricPoint,
    TelemetryMetricSeries,
    TelemetryMetricsQuery,
    TelemetryMetricsResult,
)


_TENANT_ID = re.compile(r"[a-zA-Z0-9._-]{1,128}")
_INTEGRATION_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_LOGICAL_METRIC = re.compile(r"[A-Za-z_:][A-Za-z0-9_.:/-]{0,255}")
_LOGICAL_ATTRIBUTE = re.compile(r"[A-Za-z_][A-Za-z0-9_.:/-]{0,127}")
_PROMETHEUS_NAME = re.compile(r"[A-Za-z_:][A-Za-z0-9_:]*")
_PROMETHEUS_LABEL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CREDENTIAL_REF = re.compile(r"credential://[A-Za-z0-9._~:/-]{1,2020}")
_AGGREGATIONS = frozenset({"avg", "min", "max", "sum", "count"})
_QUANTILES = {"p50": "0.5", "p95": "0.95", "p99": "0.99"}
_MAX_CONFIGURATION_BYTES = 1_048_576
_MAX_INTEGRATIONS = 128
_MAX_METRICS = 256
_MAX_CREDENTIALS = 256


class PrometheusConfigurationError(RuntimeError):
    """Protected Prometheus or credential configuration is invalid."""


class PrometheusBackendError(RuntimeError):
    """Prometheus could not safely satisfy a normalized metrics query."""


@dataclass(frozen=True)
class PrometheusMetricBinding:
    """Allowlisted translation from one logical metric to Prometheus names."""

    logical_metric: str
    backend_metric: str
    unit: str
    attributes: tuple[tuple[str, str], ...]

    def backend_label(self, logical_attribute: str) -> str:
        for logical, backend in self.attributes:
            if logical == logical_attribute:
                return backend
        raise PrometheusBackendError("telemetry.backend.query.unsupported")


@dataclass(frozen=True)
class PrometheusIntegration:
    """One protected tenant/integration endpoint and bounded metric catalog."""

    tenant_id: str
    integration_id: str
    endpoint: str
    credential_ref: str | None
    enabled: bool
    request_timeout_seconds: int
    max_response_bytes: int
    metrics: tuple[PrometheusMetricBinding, ...]

    def binding(self, logical_metric: str) -> PrometheusMetricBinding:
        for binding in self.metrics:
            if binding.logical_metric == logical_metric:
                return binding
        raise PrometheusBackendError("telemetry.backend.metric.unavailable")


class PrometheusIntegrationRegistry:
    """Validated in-process view of non-secret integration configuration."""

    def __init__(self, integrations: tuple[PrometheusIntegration, ...]) -> None:
        self._integrations = {
            (integration.tenant_id, integration.integration_id): integration
            for integration in integrations
        }

    @classmethod
    def from_json(cls, encoded: str) -> "PrometheusIntegrationRegistry":
        document = _json_document(encoded)
        try:
            if set(document) != {"integrations"}:
                raise KeyError
            items = document["integrations"]
            if not isinstance(items, list) or not 1 <= len(items) <= _MAX_INTEGRATIONS:
                raise TypeError
            integrations = tuple(cls._integration(item) for item in items)
        except (KeyError, TypeError, ValueError):
            raise PrometheusConfigurationError(
                "telemetry.backend.configuration.invalid"
            ) from None
        keys = {(item.tenant_id, item.integration_id) for item in integrations}
        if len(keys) != len(integrations):
            raise PrometheusConfigurationError(
                "telemetry.backend.configuration.invalid"
            )
        return cls(integrations)

    @classmethod
    def _integration(cls, value: object) -> PrometheusIntegration:
        if not isinstance(value, dict) or set(value) != {
            "tenantId",
            "integrationId",
            "provider",
            "endpoint",
            "credentialRef",
            "enabled",
            "requestTimeoutSeconds",
            "maxResponseBytes",
            "metrics",
        }:
            raise TypeError
        tenant_id = value["tenantId"]
        integration_id = value["integrationId"]
        endpoint = value["endpoint"]
        credential_ref = value["credentialRef"]
        enabled = value["enabled"]
        request_timeout = value["requestTimeoutSeconds"]
        max_response_bytes = value["maxResponseBytes"]
        metrics = value["metrics"]
        if (
            not isinstance(tenant_id, str)
            or not _TENANT_ID.fullmatch(tenant_id)
            or not isinstance(integration_id, str)
            or not _INTEGRATION_ID.fullmatch(integration_id)
            or value["provider"] != "prometheus"
            or not isinstance(enabled, bool)
            or not _integer(request_timeout, 1, 120)
            or not _integer(max_response_bytes, 1_024, 16_777_216)
            or not isinstance(metrics, list)
            or not 1 <= len(metrics) <= _MAX_METRICS
        ):
            raise TypeError
        _validate_endpoint(endpoint)
        if credential_ref is not None and (
            not isinstance(credential_ref, str)
            or not _CREDENTIAL_REF.fullmatch(credential_ref)
        ):
            raise TypeError
        bindings = tuple(cls._binding(item) for item in metrics)
        logical_metrics = {binding.logical_metric for binding in bindings}
        if len(logical_metrics) != len(bindings):
            raise TypeError
        return PrometheusIntegration(
            tenant_id=tenant_id,
            integration_id=integration_id,
            endpoint=endpoint.rstrip("/"),
            credential_ref=credential_ref,
            enabled=enabled,
            request_timeout_seconds=request_timeout,
            max_response_bytes=max_response_bytes,
            metrics=bindings,
        )

    @staticmethod
    def _binding(value: object) -> PrometheusMetricBinding:
        if not isinstance(value, dict) or set(value) != {
            "name",
            "backendMetric",
            "unit",
            "attributes",
        }:
            raise TypeError
        logical_metric = value["name"]
        backend_metric = value["backendMetric"]
        unit = value["unit"]
        attributes = value["attributes"]
        if (
            not isinstance(logical_metric, str)
            or not _LOGICAL_METRIC.fullmatch(logical_metric)
            or not isinstance(backend_metric, str)
            or not _PROMETHEUS_NAME.fullmatch(backend_metric)
            or not _safe_text(unit, 64)
            or not isinstance(attributes, dict)
            or len(attributes) > 32
        ):
            raise TypeError
        pairs: list[tuple[str, str]] = []
        for logical, backend in attributes.items():
            if (
                not isinstance(logical, str)
                or not _LOGICAL_ATTRIBUTE.fullmatch(logical)
                or not isinstance(backend, str)
                or not _PROMETHEUS_LABEL.fullmatch(backend)
                or backend == "__name__"
            ):
                raise TypeError
            pairs.append((logical, backend))
        if len({backend for _, backend in pairs}) != len(pairs):
            raise TypeError
        return PrometheusMetricBinding(
            logical_metric,
            backend_metric,
            unit,
            tuple(sorted(pairs)),
        )

    def resolve(self, tenant_id: str, integration_id: str) -> PrometheusIntegration:
        integration = self._integrations.get((tenant_id, integration_id))
        if integration is None or not integration.enabled:
            raise PrometheusBackendError("telemetry.backend.integration.unavailable")
        return integration


class StaticBearerCredentialBroker:
    """Local protected-config broker; production brokers should issue short leases."""

    def __init__(
        self,
        credentials: Mapping[tuple[str, str, str], CredentialLease],
    ) -> None:
        self._credentials = dict(credentials)

    def __repr__(self) -> str:
        return f"StaticBearerCredentialBroker(credentials={len(self._credentials)})"

    @classmethod
    def empty(cls) -> "StaticBearerCredentialBroker":
        return cls({})

    @classmethod
    def from_json(cls, encoded: str) -> "StaticBearerCredentialBroker":
        try:
            document = _json_document(encoded)
        except PrometheusConfigurationError:
            raise PrometheusConfigurationError(
                "telemetry.credential.configuration.invalid"
            ) from None
        try:
            if set(document) != {"credentials"}:
                raise KeyError
            items = document["credentials"]
            if not isinstance(items, list) or len(items) > _MAX_CREDENTIALS:
                raise TypeError
            credentials: dict[tuple[str, str, str], CredentialLease] = {}
            for item in items:
                if not isinstance(item, dict) or set(item) != {
                    "tenantId",
                    "integrationId",
                    "credentialRef",
                    "bearerToken",
                    "expiresAt",
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
                    or (
                        expires_at is not None
                        and not isinstance(expires_at, str)
                    )
                ):
                    raise TypeError
                if expires_at is not None:
                    _parse_time(expires_at)
                key = (tenant_id, integration_id, credential_ref)
                if key in credentials:
                    raise TypeError
                credentials[key] = CredentialLease("bearer", token, expires_at)
        except (KeyError, TypeError, ValueError, PrometheusBackendError):
            raise PrometheusConfigurationError(
                "telemetry.credential.configuration.invalid"
            ) from None
        return cls(credentials)

    def resolve(self, request: CredentialLeaseRequest) -> CredentialLease:
        if (
            request.provider != "prometheus"
            or request.scopes != ("metrics:read",)
        ):
            raise PrometheusBackendError("telemetry.credential.unavailable")
        lease = self._credentials.get(
            (request.tenant_id, request.integration_id, request.credential_ref)
        )
        if lease is None:
            raise PrometheusBackendError("telemetry.credential.unavailable")
        if (
            lease.expires_at is not None
            and _parse_time(lease.expires_at) < _parse_time(request.deadline)
        ):
            raise PrometheusBackendError("telemetry.credential.unavailable")
        return lease


class PrometheusHttpTransport(Protocol):
    def post(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        """Execute one bounded no-redirect Prometheus HTTP request."""


class NoRedirectHandler(HTTPRedirectHandler):
    """Disable urllib's default redirect behavior for credential-bearing calls."""

    def redirect_request(self, *args: object, **kwargs: object) -> None:
        del args, kwargs
        return None


class UrllibPrometheusHttpTransport:
    """Standard-library HTTP transport that refuses credential-bearing redirects."""

    def __init__(self) -> None:
        self._opener = build_opener(NoRedirectHandler())

    def post(
        self,
        url: str,
        body: bytes,
        headers: Mapping[str, str],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        request = Request(url, data=body, headers=dict(headers), method="POST")
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                if response.status != 200:
                    raise PrometheusBackendError("telemetry.backend.unavailable")
                content_type = response.headers.get_content_type()
                if content_type != "application/json":
                    raise PrometheusBackendError("telemetry.backend.response.invalid")
                content = response.read(max_response_bytes + 1)
        except PrometheusBackendError:
            raise
        except Exception:
            raise PrometheusBackendError("telemetry.backend.unavailable") from None
        if len(content) > max_response_bytes:
            raise PrometheusBackendError("telemetry.backend.response.limited")
        return content


class PrometheusTelemetryMetricsBackend:
    """Translate bounded logical metric queries to Prometheus query_range calls."""

    def __init__(
        self,
        registry: PrometheusIntegrationRegistry,
        credentials: CredentialBroker,
        clock: Clock,
        transport: PrometheusHttpTransport | None = None,
    ) -> None:
        self._registry = registry
        self._credentials = credentials
        self._clock = clock
        self._transport = transport or UrllibPrometheusHttpTransport()

    def query_metrics(self, request: TelemetryMetricsQuery) -> TelemetryMetricsResult:
        integration = self._registry.resolve(
            request.tenant_id, request.integration_id
        )
        binding = integration.binding(request.metric)
        expression = self._expression(request, binding)
        timeout_seconds = self._timeout(request, integration)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "iip-prometheus-adapter/0.19.0",
        }
        if integration.credential_ref is not None:
            try:
                lease = self._credentials.resolve(
                    CredentialLeaseRequest(
                        tenant_id=request.tenant_id,
                        actor_id=request.actor_id,
                        integration_id=request.integration_id,
                        credential_ref=integration.credential_ref,
                        provider="prometheus",
                        scopes=("metrics:read",),
                        deadline=request.deadline,
                    )
                )
            except Exception:
                raise PrometheusBackendError(
                    "telemetry.credential.unavailable"
                ) from None
            if lease.scheme != "bearer" or not _safe_secret(lease.secret):
                raise PrometheusBackendError("telemetry.credential.unavailable")
            headers["Authorization"] = f"Bearer {lease.secret}"
        body = urlencode(
            {
                "query": expression,
                "start": request.start,
                "end": request.end,
                "step": f"{request.step_seconds}s",
                "timeout": f"{max(1, math.ceil(timeout_seconds))}s",
                "limit": str(request.max_series + 1),
            }
        ).encode("utf-8")
        content = self._transport.post(
            f"{integration.endpoint}/api/v1/query_range",
            body,
            headers,
            timeout_seconds=timeout_seconds,
            max_response_bytes=integration.max_response_bytes,
        )
        return self._result(request, binding, content)

    def _expression(
        self,
        request: TelemetryMetricsQuery,
        binding: PrometheusMetricBinding,
    ) -> str:
        matchers = []
        for logical, operator, value in request.filters:
            label = binding.backend_label(logical)
            if operator not in ("eq", "neq"):
                raise PrometheusBackendError("telemetry.backend.query.unsupported")
            if operator == "neq":
                matchers.append(f'{label}!=""')
            matchers.append(
                f"{label}{'=' if operator == 'eq' else '!='}"
                f"{json.dumps(value, ensure_ascii=False)}"
            )
        selector = binding.backend_metric
        if matchers:
            selector += "{" + ",".join(matchers) + "}"
        group_by = tuple(binding.backend_label(name) for name in request.group_by)
        grouping = f" by ({','.join(group_by)})" if group_by else ""
        if request.aggregation in _AGGREGATIONS:
            return f"{request.aggregation}{grouping} ({selector})"
        quantile = _QUANTILES.get(request.aggregation)
        if quantile is not None:
            return f"quantile{grouping} ({quantile},{selector})"
        raise PrometheusBackendError("telemetry.backend.query.unsupported")

    def _timeout(
        self,
        request: TelemetryMetricsQuery,
        integration: PrometheusIntegration,
    ) -> float:
        remaining = (
            _parse_time(request.deadline) - _parse_time(self._clock.now())
        ).total_seconds()
        if remaining <= 0:
            raise PrometheusBackendError("telemetry.backend.deadline.exceeded")
        return min(float(integration.request_timeout_seconds), remaining)

    def _result(
        self,
        request: TelemetryMetricsQuery,
        binding: PrometheusMetricBinding,
        content: bytes,
    ) -> TelemetryMetricsResult:
        try:
            document = json.loads(content.decode("utf-8"))
            if (
                not isinstance(document, dict)
                or document.get("status") != "success"
                or not isinstance(document.get("data"), dict)
                or document["data"].get("resultType") != "matrix"
                or not isinstance(document["data"].get("result"), list)
            ):
                raise TypeError
            raw_series = document["data"]["result"]
            warnings = document.get("warnings", [])
            if not isinstance(warnings, list) or any(
                not isinstance(value, str) for value in warnings
            ):
                raise TypeError
            if len(raw_series) > request.max_series:
                raise PrometheusBackendError("telemetry.backend.response.limited")
            rendered = tuple(
                self._series(request, binding, item) for item in raw_series
            )
        except PrometheusBackendError:
            raise
        except (
            KeyError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ):
            raise PrometheusBackendError("telemetry.backend.response.invalid") from None
        stable_warnings = ("backend-partial",) if warnings else ()
        status = "partial" if stable_warnings else "complete" if rendered else "no-data"
        return TelemetryMetricsResult(
            executed_at=self._clock.now(),
            status=status,
            series=tuple(sorted(rendered, key=lambda item: item.attributes)),
            warnings=stable_warnings,
        )

    @staticmethod
    def _series(
        request: TelemetryMetricsQuery,
        binding: PrometheusMetricBinding,
        value: object,
    ) -> TelemetryMetricSeries:
        if not isinstance(value, dict) or set(value) != {"metric", "values"}:
            raise TypeError
        labels = value["metric"]
        values = value["values"]
        if not isinstance(labels, dict) or not isinstance(values, list) or not values:
            raise TypeError
        attributes = []
        for logical in request.group_by:
            backend = binding.backend_label(logical)
            label_value = labels.get(backend)
            if not isinstance(label_value, str):
                raise TypeError
            attributes.append((logical, label_value))
        points = []
        for sample in values:
            if (
                not isinstance(sample, list)
                or len(sample) != 2
                or isinstance(sample[0], bool)
                or not isinstance(sample[0], (int, float))
                or not isinstance(sample[1], str)
            ):
                raise TypeError
            numeric = float(sample[1])
            if not math.isfinite(sample[0]) or not math.isfinite(numeric):
                raise TypeError
            points.append(
                TelemetryMetricPoint(_format_unix_timestamp(float(sample[0])), numeric)
            )
        return TelemetryMetricSeries(
            metric=request.metric,
            unit=binding.unit,
            attributes=tuple(attributes),
            points=tuple(points),
        )


def build_prometheus_backend_from_environment(
    environment: Mapping[str, str],
    clock: Clock,
    credential_broker: CredentialBroker | None = None,
) -> PrometheusTelemetryMetricsBackend:
    """Build the adapter from protected runtime configuration."""

    encoded_integrations = environment.get("IIP_PROMETHEUS_INTEGRATIONS_JSON")
    if encoded_integrations is None:
        raise PrometheusConfigurationError(
            "telemetry.backend.configuration.required"
        )
    encoded_credentials = environment.get(
        "IIP_PROMETHEUS_CREDENTIALS_JSON", '{"credentials":[]}'
    ) or '{"credentials":[]}'
    return PrometheusTelemetryMetricsBackend(
        PrometheusIntegrationRegistry.from_json(encoded_integrations),
        credential_broker
        if credential_broker is not None
        else StaticBearerCredentialBroker.from_json(encoded_credentials),
        clock,
    )


def _json_document(encoded: object) -> dict[str, object]:
    if not isinstance(encoded, str):
        raise PrometheusConfigurationError("telemetry.backend.configuration.invalid")
    encoded_size = len(encoded.encode("utf-8"))
    if not 1 <= encoded_size <= _MAX_CONFIGURATION_BYTES:
        raise PrometheusConfigurationError("telemetry.backend.configuration.invalid")
    try:
        document = json.loads(encoded)
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        raise PrometheusConfigurationError(
            "telemetry.backend.configuration.invalid"
        ) from None
    if not isinstance(document, dict):
        raise PrometheusConfigurationError("telemetry.backend.configuration.invalid")
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
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    )


def _safe_text(value: object, maximum: int) -> bool:
    return bool(
        isinstance(value, str)
        and 1 <= len(value) <= maximum
        and not any(
            ord(character) < 32 or ord(character) == 127
            for character in value
        )
    )


def _safe_secret(value: object) -> bool:
    return bool(
        isinstance(value, str)
        and 16 <= len(value) <= 8_192
        and not any(ord(character) < 33 or ord(character) == 127 for character in value)
    )


def _parse_time(value: object) -> datetime:
    if not isinstance(value, str):
        raise PrometheusBackendError("telemetry.backend.time.invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise PrometheusBackendError("telemetry.backend.time.invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PrometheusBackendError("telemetry.backend.time.invalid")
    return parsed


def _format_unix_timestamp(value: float) -> str:
    try:
        parsed = datetime.fromtimestamp(value, timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise TypeError from None
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")
