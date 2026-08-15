"""Backend-neutral metric evidence query and normalization boundary."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping

from iip.application.collect_evidence import (
    CollectEvidenceCommand,
    EvidenceCollectionService,
    InvalidEvidenceRequestError,
)
from iip.application.ports import (
    ActorContext,
    Clock,
    EvidenceProviderRequest,
    RawEvidenceArtifact,
    TelemetryMetricPoint,
    TelemetryMetricSeries,
    TelemetryMetricsBackend,
    TelemetryMetricsQuery,
    TelemetryMetricsResult,
)


MAX_QUERY_RANGE = timedelta(days=7)
MAX_DEADLINE_OFFSET = timedelta(minutes=5)
_REQUEST_ID = re.compile(r"teq_[a-f0-9]{32}")
_TENANT_ID = re.compile(r"[a-zA-Z0-9._-]{1,128}")
_INTEGRATION_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_RESOURCE_UID = re.compile(r"res_[a-f0-9]{32}")
_METRIC = re.compile(r"[A-Za-z_:][A-Za-z0-9_.:/-]{0,255}")
_ATTRIBUTE = re.compile(r"[A-Za-z_][A-Za-z0-9_.:/-]{0,127}")
_AGGREGATIONS = frozenset(
    {"avg", "min", "max", "sum", "count", "rate", "p50", "p95", "p99"}
)
_FILTER_OPERATORS = frozenset({"eq", "neq"})
_STATUSES = frozenset({"complete", "partial", "no-data"})
_WARNINGS = frozenset(
    {
        "backend-partial",
        "series-limit",
        "data-point-limit",
        "resolution-adjusted",
    }
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:authorization|password|passwd|token|secret|api[_-]?key|"
    r"access[_-]?key)\s*[:=]\s*\S+|bearer\s+\S+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


class InvalidTelemetryEvidenceRequestError(ValueError):
    """Public metric evidence request violated a stable boundary."""


@dataclass(frozen=True)
class CollectTelemetryEvidenceCommand:
    """Authenticated request to collect one normalized metric artifact."""

    actor: ActorContext
    request: Mapping[str, Any]


def canonical_digest(document: object) -> str:
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


class TelemetryEvidenceService:
    """Validate a public query and collect its normalized Evidence artifact."""

    def __init__(self, evidence: EvidenceCollectionService, clock: Clock) -> None:
        self._evidence = evidence
        self._clock = clock

    def execute(
        self, command: CollectTelemetryEvidenceCommand
    ) -> Mapping[str, object]:
        request, metadata, spec, time_range, query, limits = self._validate(command)
        provider_query = {
            "requestDigest": canonical_digest(request),
            "requestId": metadata["requestId"],
            "signal": "metrics",
            "timeRange": time_range,
            "query": query,
            "limits": limits,
        }
        encoded_query = json.dumps(
            provider_query,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(encoded_query) > 4096:
            raise self._invalid()
        return self._evidence.execute(
            CollectEvidenceCommand(
                actor=command.actor,
                provider="telemetry-query",
                integration_id=spec["integrationId"],
                evidence_type="telemetry.metrics",
                resource_uids=tuple(spec["resourceRefs"]),
                locator="telemetry://query/v1",
                query=encoded_query,
                deadline=spec["deadline"],
                max_bytes=limits["maxBytes"],
                sensitivity="internal",
                retention_class="ephemeral",
            )
        )

    def _validate(
        self, command: CollectTelemetryEvidenceCommand
    ) -> tuple[
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
        dict[str, Any],
    ]:
        try:
            request = dict(command.request)
            if set(request) != {"apiVersion", "kind", "metadata", "spec"}:
                raise KeyError
            if (
                request["apiVersion"] != "iip.platform/v1alpha1"
                or request["kind"] != "TelemetryEvidenceRequest"
            ):
                raise KeyError
            metadata = dict(request["metadata"])
            spec = dict(request["spec"])
            time_range = dict(spec["timeRange"])
            query = dict(spec["query"])
            aggregation = dict(query["aggregation"])
            limits = dict(spec["limits"])
        except (KeyError, TypeError, ValueError):
            raise self._invalid() from None

        if set(metadata) != {"requestId", "tenantId", "actorId", "requestedAt"}:
            raise self._invalid()
        if set(spec) != {
            "integrationId",
            "resourceRefs",
            "signal",
            "timeRange",
            "query",
            "limits",
            "deadline",
        }:
            raise self._invalid()
        if (
            metadata.get("tenantId") != command.actor.tenant_id
            or metadata.get("actorId") != command.actor.actor_id
            or not isinstance(metadata.get("requestId"), str)
            or not _REQUEST_ID.fullmatch(metadata["requestId"])
            or not isinstance(metadata.get("tenantId"), str)
            or not _TENANT_ID.fullmatch(metadata["tenantId"])
        ):
            raise self._invalid()
        if (
            not isinstance(spec.get("integrationId"), str)
            or not _INTEGRATION_ID.fullmatch(spec["integrationId"])
            or spec.get("signal") != "metrics"
        ):
            raise self._invalid()

        resource_refs = spec.get("resourceRefs")
        if (
            not isinstance(resource_refs, list)
            or not 1 <= len(resource_refs) <= 256
            or any(
                not isinstance(uid, str) or not _RESOURCE_UID.fullmatch(uid)
                for uid in resource_refs
            )
            or len(resource_refs) != len(set(resource_refs))
        ):
            raise self._invalid()

        requested_at = self._parse_time(metadata.get("requestedAt"))
        now = self._parse_time(self._clock.now())
        start = self._parse_time(time_range.get("start"))
        end = self._parse_time(time_range.get("end"))
        deadline = self._parse_time(spec.get("deadline"))
        if set(time_range) != {"start", "end"} or not (
            start < end <= requested_at <= now <= deadline
        ):
            raise self._invalid()
        if end - start > MAX_QUERY_RANGE or deadline - requested_at > MAX_DEADLINE_OFFSET:
            raise self._invalid()

        if set(query) != {"metric", "filters", "aggregation", "groupBy"}:
            raise self._invalid()
        metric = query.get("metric")
        if not isinstance(metric, str) or not _METRIC.fullmatch(metric):
            raise self._invalid()
        filters = query.get("filters")
        if not isinstance(filters, list) or len(filters) > 8:
            raise self._invalid()
        normalized_filters: list[dict[str, str]] = []
        for item in filters:
            if not isinstance(item, Mapping) or set(item) != {
                "attribute",
                "operator",
                "value",
            }:
                raise self._invalid()
            attribute = item.get("attribute")
            operator = item.get("operator")
            value = item.get("value")
            if (
                not isinstance(attribute, str)
                or not _ATTRIBUTE.fullmatch(attribute)
                or not isinstance(operator, str)
                or operator not in _FILTER_OPERATORS
                or not self._safe_text(value, maximum=128)
            ):
                raise self._invalid()
            normalized_filters.append(dict(item))
        query["filters"] = normalized_filters

        if set(aggregation) != {"function", "stepSeconds"}:
            raise self._invalid()
        function = aggregation.get("function")
        if (
            not isinstance(function, str)
            or function not in _AGGREGATIONS
            or not self._integer(
                aggregation.get("stepSeconds"), minimum=1, maximum=86_400
            )
        ):
            raise self._invalid()
        query["aggregation"] = aggregation

        group_by = query.get("groupBy")
        if (
            not isinstance(group_by, list)
            or len(group_by) > 4
            or any(
                not isinstance(attribute, str)
                or not _ATTRIBUTE.fullmatch(attribute)
                for attribute in group_by
            )
            or len(group_by) != len(set(group_by))
        ):
            raise self._invalid()

        if set(limits) != {"maxSeries", "maxDataPoints", "maxBytes"}:
            raise self._invalid()
        if not all(
            (
                self._integer(limits.get("maxSeries"), minimum=1, maximum=100),
                self._integer(
                    limits.get("maxDataPoints"), minimum=1, maximum=10_000
                ),
                self._integer(
                    limits.get("maxBytes"), minimum=1, maximum=16_777_216
                ),
            )
        ):
            raise self._invalid()
        return request, metadata, spec, time_range, query, limits

    @staticmethod
    def _integer(value: object, *, minimum: int, maximum: int) -> bool:
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and minimum <= value <= maximum
        )

    @staticmethod
    def _safe_text(value: object, *, maximum: int) -> bool:
        return bool(
            isinstance(value, str)
            and 1 <= len(value) <= maximum
            and not any(ord(character) < 32 or ord(character) == 127 for character in value)
            and not _SENSITIVE_TEXT.search(value)
        )

    @staticmethod
    def _parse_time(value: object) -> datetime:
        if not isinstance(value, str):
            raise InvalidTelemetryEvidenceRequestError(
                "telemetry.request.invalid"
            )
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise InvalidTelemetryEvidenceRequestError(
                "telemetry.request.invalid"
            ) from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise InvalidTelemetryEvidenceRequestError(
                "telemetry.request.invalid"
            )
        return parsed

    @staticmethod
    def _invalid() -> InvalidTelemetryEvidenceRequestError:
        return InvalidTelemetryEvidenceRequestError("telemetry.request.invalid")


class TelemetryMetricsEvidenceProvider:
    """Validate an untrusted backend result and render its public JSON artifact."""

    def __init__(self, backend: TelemetryMetricsBackend) -> None:
        self._backend = backend

    def fetch(self, request: EvidenceProviderRequest) -> RawEvidenceArtifact:
        query, request_digest = self._provider_query(request)
        result = self._backend.query_metrics(query)
        series, data_point_count = self._validated_result(query, result)
        document = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "TelemetryEvidenceResult",
            "metadata": {
                "requestId": query.request_id,
                "tenantId": query.tenant_id,
                "integrationId": query.integration_id,
                "createdAt": result.executed_at,
            },
            "spec": {
                "signal": "metrics",
                "requestDigest": request_digest,
                "timeRange": {"start": query.start, "end": query.end},
                "status": result.status,
                "series": series,
                "summary": {
                    "seriesCount": len(series),
                    "dataPointCount": data_point_count,
                },
                "warnings": list(result.warnings),
            },
        }
        content = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(content) > query.max_bytes:
            raise InvalidEvidenceRequestError("evidence.provider.output-limited")
        if result.status == "no-data":
            summary = "No metric series matched the bounded telemetry query."
        else:
            summary = (
                f"Normalized {len(series)} metric series with "
                f"{data_point_count} data point(s)."
            )
        return RawEvidenceArtifact(
            content=content,
            media_type="application/json",
            observed_at=query.end,
            summary=summary,
        )

    def _provider_query(
        self, request: EvidenceProviderRequest
    ) -> tuple[TelemetryMetricsQuery, str]:
        if (
            request.evidence_type != "telemetry.metrics"
            or request.locator != "telemetry://query/v1"
            or request.query is None
        ):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
        try:
            payload = json.loads(request.query)
            if not isinstance(payload, dict) or set(payload) != {
                "requestDigest",
                "requestId",
                "signal",
                "timeRange",
                "query",
                "limits",
            }:
                raise KeyError
            if payload["signal"] != "metrics":
                raise KeyError
            time_range = payload["timeRange"]
            metric_query = payload["query"]
            aggregation = metric_query["aggregation"]
            limits = payload["limits"]
            filters = tuple(
                (item["attribute"], item["operator"], item["value"])
                for item in metric_query["filters"]
            )
            query = TelemetryMetricsQuery(
                tenant_id=request.tenant_id,
                actor_id=request.actor_id,
                request_id=payload["requestId"],
                integration_id=request.integration_id,
                resource_uids=request.resource_uids,
                start=time_range["start"],
                end=time_range["end"],
                metric=metric_query["metric"],
                filters=filters,
                aggregation=aggregation["function"],
                step_seconds=aggregation["stepSeconds"],
                group_by=tuple(metric_query["groupBy"]),
                max_series=limits["maxSeries"],
                max_data_points=limits["maxDataPoints"],
                max_bytes=limits["maxBytes"],
                deadline=request.deadline,
            )
            request_digest = payload["requestDigest"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise InvalidEvidenceRequestError(
                "evidence.provider.output-invalid"
            ) from None
        if (
            not isinstance(request_digest, str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", request_digest)
        ):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
        return query, request_digest

    def _validated_result(
        self,
        query: TelemetryMetricsQuery,
        result: object,
    ) -> tuple[list[dict[str, object]], int]:
        if not isinstance(result, TelemetryMetricsResult):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
        executed_at = TelemetryEvidenceService._parse_time(result.executed_at)
        end = TelemetryEvidenceService._parse_time(query.end)
        deadline = TelemetryEvidenceService._parse_time(query.deadline)
        if not end <= executed_at <= deadline or result.status not in _STATUSES:
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
        if (
            not isinstance(result.series, tuple)
            or len(result.series) > query.max_series
            or not isinstance(result.warnings, tuple)
            or len(result.warnings) > 16
            or len(set(result.warnings)) != len(result.warnings)
            or any(warning not in _WARNINGS for warning in result.warnings)
        ):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
        if (
            (result.status == "complete" and (not result.series or result.warnings))
            or (result.status == "partial" and not result.warnings)
            or (result.status == "no-data" and (result.series or result.warnings))
        ):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")

        start = TelemetryEvidenceService._parse_time(query.start)
        rendered: list[dict[str, object]] = []
        total_points = 0
        for series in result.series:
            rendered_series, point_count = self._validated_series(
                query, series, start, end
            )
            total_points += point_count
            if total_points > query.max_data_points:
                raise InvalidEvidenceRequestError("evidence.provider.output-limited")
            rendered.append(rendered_series)
        return rendered, total_points

    def _validated_series(
        self,
        query: TelemetryMetricsQuery,
        series: object,
        start: datetime,
        end: datetime,
    ) -> tuple[dict[str, object], int]:
        if (
            not isinstance(series, TelemetryMetricSeries)
            or series.metric != query.metric
            or not _METRIC.fullmatch(series.metric)
            or not TelemetryEvidenceService._safe_text(series.unit, maximum=64)
            or not isinstance(series.attributes, tuple)
            or len(series.attributes) > 16
            or not isinstance(series.points, tuple)
            or not 1 <= len(series.points) <= 2000
        ):
            raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
        attributes: dict[str, str] = {}
        for item in series.attributes:
            if not isinstance(item, tuple) or len(item) != 2:
                raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
            name, value = item
            if (
                not isinstance(name, str)
                or not _ATTRIBUTE.fullmatch(name)
                or name in attributes
                or not TelemetryEvidenceService._safe_text(value, maximum=256)
            ):
                raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
            attributes[name] = value

        points: list[dict[str, object]] = []
        previous: datetime | None = None
        for point in series.points:
            if (
                not isinstance(point, TelemetryMetricPoint)
                or isinstance(point.value, bool)
                or not isinstance(point.value, (int, float))
                or not math.isfinite(point.value)
            ):
                raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
            timestamp = TelemetryEvidenceService._parse_time(point.timestamp)
            if not start <= timestamp <= end or (
                previous is not None and timestamp <= previous
            ):
                raise InvalidEvidenceRequestError("evidence.provider.output-invalid")
            previous = timestamp
            points.append({"timestamp": point.timestamp, "value": point.value})
        return {
            "metric": series.metric,
            "unit": series.unit,
            "attributes": attributes,
            "points": points,
        }, len(points)
