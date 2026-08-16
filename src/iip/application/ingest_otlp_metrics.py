"""Tenant-bound, bounded ingestion of normalized OTLP metric batches."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping, Protocol

from iip.application.collect_evidence import (
    CollectEvidenceCommand,
    EvidenceCollectionService,
)
from iip.application.ports import ActorContext, Clock, RawEvidenceArtifact


_CHANNEL_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_TENANT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_INTEGRATION_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_RESOURCE_UID = re.compile(r"res_[a-f0-9]{32}")
_METRIC = re.compile(r"[A-Za-z_:][A-Za-z0-9_.:/-]{0,255}")
_ATTRIBUTE = re.compile(r"[A-Za-z_][A-Za-z0-9_.:/-]{0,127}")
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:authorization|password|passwd|token|secret|api[_-]?key|"
    r"access[_-]?key)\s*[:=]\s*\S+|bearer\s+\S+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


class OtlpReceiverAuthenticationError(PermissionError):
    """The receiver channel credential is absent, malformed, or unknown."""


class OtlpReceiverConfigurationError(RuntimeError):
    """Protected receiver configuration is missing or invalid."""


class InvalidOtlpMetricsRequestError(ValueError):
    """An OTLP request cannot be safely normalized under its channel profile."""


class OtlpPayloadTooLargeError(InvalidOtlpMetricsRequestError):
    """The encoded, decoded, or normalized request exceeded a channel bound."""


@dataclass(frozen=True)
class OtlpChannelLimits:
    """Admission budgets fixed by protected channel configuration."""

    max_request_bytes: int
    max_artifact_bytes: int
    max_series: int
    max_data_points: int
    max_attributes_per_point: int
    max_age_seconds: int
    max_clock_skew_seconds: int
    max_processing_seconds: int


@dataclass(frozen=True)
class OtlpMetricsChannel:
    """Authenticated tenant scope without the presented credential."""

    actor: ActorContext
    channel_id: str
    integration_id: str
    resource_uids: tuple[str, ...]
    limits: OtlpChannelLimits
    sensitivity: str
    retention_class: str


@dataclass(frozen=True)
class OtlpMetricPoint:
    """One normalized finite numeric point."""

    timestamp: str
    value: int | float


@dataclass(frozen=True)
class OtlpMetricSeries:
    """One normalized gauge or sum series from an allowlisted metric."""

    metric: str
    unit: str
    kind: str
    attributes: tuple[tuple[str, str], ...]
    points: tuple[OtlpMetricPoint, ...]
    temporality: str | None = None
    monotonic: bool | None = None


@dataclass(frozen=True)
class OtlpMetricsBatch:
    """Untrusted adapter output before application validation and persistence."""

    series: tuple[OtlpMetricSeries, ...]


class OtlpMetricsReceiverAdapter(Protocol):
    """Protocol/authentication adapter kept outside application semantics."""

    def authenticate_bearer(self, token: str) -> OtlpMetricsChannel:
        """Resolve one channel credential to its protected tenant scope."""

    def decode_metrics(
        self,
        channel: OtlpMetricsChannel,
        payload: bytes,
        *,
        content_encoding: str,
    ) -> OtlpMetricsBatch:
        """Decode and normalize one bounded OTLP/HTTP protobuf request."""


class OtlpMetricsIngestionService:
    """Authenticate a push channel and record normalized immutable evidence."""

    def __init__(
        self,
        receiver: OtlpMetricsReceiverAdapter,
        evidence: EvidenceCollectionService,
        clock: Clock,
    ) -> None:
        self._receiver = receiver
        self._evidence = evidence
        self._clock = clock

    def authenticate_bearer(self, token: str) -> OtlpMetricsChannel:
        """Authenticate independently from interactive control-plane identities."""

        channel = self._receiver.authenticate_bearer(token)
        validate_channel_context(channel)
        return channel

    def ingest(
        self,
        channel: OtlpMetricsChannel,
        payload: bytes,
        *,
        content_encoding: str = "identity",
    ) -> Mapping[str, object]:
        """Normalize one request and persist it through the Evidence boundary."""

        validate_channel_context(channel)
        received_at = self._now()
        if not isinstance(payload, bytes):
            raise InvalidOtlpMetricsRequestError("otlp.request.invalid")
        if len(payload) > channel.limits.max_request_bytes:
            raise OtlpPayloadTooLargeError("otlp.request.too-large")

        batch = self._receiver.decode_metrics(
            channel,
            payload,
            content_encoding=content_encoding,
        )
        if isinstance(batch, OtlpMetricsBatch) and batch.series == ():
            # OTLP recommends treating an empty export as a successful no-op.
            return {}
        document = self._artifact_document(channel, batch, received_at)
        content = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(content) > channel.limits.max_artifact_bytes:
            raise OtlpPayloadTooLargeError("otlp.artifact.too-large")

        deadline = received_at + timedelta(
            seconds=channel.limits.max_processing_seconds
        )
        evidence = self._evidence.record_artifact(
            CollectEvidenceCommand(
                actor=channel.actor,
                provider="otlp-receiver",
                integration_id=channel.integration_id,
                evidence_type="telemetry.metrics.push",
                resource_uids=channel.resource_uids,
                locator=f"otlp://{channel.channel_id}/v1/metrics",
                deadline=self._format_time(deadline),
                max_bytes=channel.limits.max_artifact_bytes,
                sensitivity=channel.sensitivity,
                retention_class=channel.retention_class,
            ),
            RawEvidenceArtifact(
                content=content,
                media_type="application/json",
                observed_at=self._format_time(received_at),
                summary=(
                    "Accepted "
                    f"{document['spec']['summary']['dataPointCount']} OTLP metric "
                    "data point(s) from an authenticated channel."
                ),
            ),
        )
        return evidence

    def _artifact_document(
        self,
        channel: OtlpMetricsChannel,
        batch: object,
        received_at: datetime,
    ) -> dict[str, object]:
        if not isinstance(batch, OtlpMetricsBatch) or not isinstance(
            batch.series, tuple
        ):
            raise InvalidOtlpMetricsRequestError("otlp.request.invalid")
        if not 1 <= len(batch.series) <= channel.limits.max_series:
            raise InvalidOtlpMetricsRequestError("otlp.series.limit")

        series_documents: list[dict[str, object]] = []
        identities: set[tuple[object, ...]] = set()
        all_times: list[datetime] = []
        point_count = 0
        metric_names: set[str] = set()
        for series in batch.series:
            series_document, timestamps, identity = self._validate_series(
                series,
                channel,
                received_at,
            )
            if identity in identities:
                raise InvalidOtlpMetricsRequestError("otlp.series.duplicate")
            identities.add(identity)
            point_count += len(timestamps)
            if point_count > channel.limits.max_data_points:
                raise InvalidOtlpMetricsRequestError("otlp.data-point.limit")
            all_times.extend(timestamps)
            metric_names.add(series.metric)
            series_documents.append(series_document)

        expected_order = sorted(
            series_documents,
            key=lambda item: (
                item["metric"],
                item["kind"],
                tuple(sorted(item["attributes"].items())),
            ),
        )
        if series_documents != expected_order:
            raise InvalidOtlpMetricsRequestError("otlp.series.order.invalid")

        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "OtlpMetricsEvidence",
            "metadata": {
                "tenantId": channel.actor.tenant_id,
                "integrationId": channel.integration_id,
                "channelId": channel.channel_id,
                "receivedAt": self._format_time(received_at),
            },
            "spec": {
                "signal": "metrics",
                "protocol": "otlp/http-protobuf",
                "timeRange": {
                    "start": self._format_time(min(all_times)),
                    "end": self._format_time(max(all_times)),
                },
                "series": series_documents,
                "summary": {
                    "metricCount": len(metric_names),
                    "seriesCount": len(series_documents),
                    "dataPointCount": point_count,
                },
            },
        }

    def _validate_series(
        self,
        series: object,
        channel: OtlpMetricsChannel,
        received_at: datetime,
    ) -> tuple[dict[str, object], list[datetime], tuple[object, ...]]:
        if not isinstance(series, OtlpMetricSeries):
            raise InvalidOtlpMetricsRequestError("otlp.request.invalid")
        if not isinstance(series.metric, str) or not _METRIC.fullmatch(series.metric):
            raise InvalidOtlpMetricsRequestError("otlp.metric.invalid")
        self._safe_text(series.unit, maximum=64)
        if series.kind not in ("gauge", "sum"):
            raise InvalidOtlpMetricsRequestError("otlp.metric.kind.unsupported")
        if series.kind == "gauge":
            if series.temporality is not None or series.monotonic is not None:
                raise InvalidOtlpMetricsRequestError("otlp.metric.invalid")
        elif (
            series.temporality not in ("delta", "cumulative")
            or not isinstance(series.monotonic, bool)
        ):
            raise InvalidOtlpMetricsRequestError("otlp.metric.invalid")

        if (
            not isinstance(series.attributes, tuple)
            or len(series.attributes) > min(channel.limits.max_attributes_per_point, 16)
            or tuple(sorted(series.attributes)) != series.attributes
            or len({name for name, _ in series.attributes}) != len(series.attributes)
        ):
            raise InvalidOtlpMetricsRequestError("otlp.attribute.invalid")
        attributes: dict[str, str] = {}
        for name, value in series.attributes:
            if not isinstance(name, str) or not _ATTRIBUTE.fullmatch(name):
                raise InvalidOtlpMetricsRequestError("otlp.attribute.invalid")
            self._safe_text(value, maximum=256)
            attributes[name] = value

        if not isinstance(series.points, tuple) or not series.points:
            raise InvalidOtlpMetricsRequestError("otlp.data-point.invalid")
        timestamps: list[datetime] = []
        point_documents: list[dict[str, object]] = []
        oldest = received_at - timedelta(seconds=channel.limits.max_age_seconds)
        newest = received_at + timedelta(
            seconds=channel.limits.max_clock_skew_seconds
        )
        for point in series.points:
            if not isinstance(point, OtlpMetricPoint):
                raise InvalidOtlpMetricsRequestError("otlp.data-point.invalid")
            timestamp = self._parse_time(point.timestamp)
            if timestamp < oldest or timestamp > newest:
                raise InvalidOtlpMetricsRequestError("otlp.data-point.time.invalid")
            if isinstance(point.value, bool) or not isinstance(
                point.value, (int, float)
            ):
                raise InvalidOtlpMetricsRequestError("otlp.data-point.invalid")
            if isinstance(point.value, int):
                finite = abs(point.value) <= 9_007_199_254_740_991
            else:
                finite = math.isfinite(point.value)
            if not finite:
                raise InvalidOtlpMetricsRequestError("otlp.data-point.invalid")
            timestamps.append(timestamp)
            point_documents.append(
                {
                    "timestamp": self._format_time(timestamp),
                    "value": point.value,
                }
            )
        if timestamps != sorted(timestamps) or len(set(timestamps)) != len(timestamps):
            raise InvalidOtlpMetricsRequestError("otlp.data-point.order.invalid")

        result: dict[str, object] = {
            "metric": series.metric,
            "unit": series.unit,
            "kind": series.kind,
            "attributes": attributes,
            "points": point_documents,
        }
        if series.kind == "sum":
            result["temporality"] = series.temporality
            result["monotonic"] = series.monotonic
        identity = (
            series.metric,
            series.kind,
            series.attributes,
        )
        return result, timestamps, identity

    @staticmethod
    def _safe_text(value: object, *, maximum: int) -> None:
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
            or _SENSITIVE_TEXT.search(value)
        ):
            raise InvalidOtlpMetricsRequestError("otlp.request.invalid")

    def _now(self) -> datetime:
        try:
            return self._parse_time(self._clock.now())
        except InvalidOtlpMetricsRequestError:
            raise InvalidOtlpMetricsRequestError("otlp.clock.invalid") from None

    @staticmethod
    def _parse_time(value: object) -> datetime:
        if not isinstance(value, str):
            raise InvalidOtlpMetricsRequestError("otlp.time.invalid")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise InvalidOtlpMetricsRequestError("otlp.time.invalid") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise InvalidOtlpMetricsRequestError("otlp.time.invalid")
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")


def validate_channel_context(channel: OtlpMetricsChannel) -> None:
    """Validate protected adapter output before a surface trusts its body limit."""

    if not isinstance(channel, OtlpMetricsChannel) or not isinstance(
        channel.limits, OtlpChannelLimits
    ):
        raise OtlpReceiverConfigurationError("otlp.configuration.invalid")
    limits = channel.limits
    numeric_limits = (
        limits.max_request_bytes,
        limits.max_artifact_bytes,
        limits.max_series,
        limits.max_data_points,
        limits.max_attributes_per_point,
        limits.max_age_seconds,
        limits.max_clock_skew_seconds,
        limits.max_processing_seconds,
    )
    if (
        not isinstance(channel.channel_id, str)
        or not _CHANNEL_ID.fullmatch(channel.channel_id)
        or not isinstance(channel.actor, ActorContext)
        or channel.actor.actor_id != f"otlp-channel:{channel.channel_id}"
        or not isinstance(channel.actor.tenant_id, str)
        or not _TENANT_ID.fullmatch(channel.actor.tenant_id)
        or channel.actor.roles != ("telemetry-ingest",)
        or not isinstance(channel.integration_id, str)
        or not _INTEGRATION_ID.fullmatch(channel.integration_id)
        or not isinstance(channel.resource_uids, tuple)
        or not 1 <= len(channel.resource_uids) <= 256
        or any(
            not isinstance(uid, str) or not _RESOURCE_UID.fullmatch(uid)
            for uid in channel.resource_uids
        )
        or len(set(channel.resource_uids)) != len(channel.resource_uids)
        or any(isinstance(value, bool) or not isinstance(value, int) for value in numeric_limits)
        or not 1 <= limits.max_request_bytes <= 16 * 1024 * 1024
        or not 1 <= limits.max_artifact_bytes <= 16 * 1024 * 1024
        or not 1 <= limits.max_series <= 100
        or not 1 <= limits.max_data_points <= 10_000
        or not 1 <= limits.max_attributes_per_point <= 16
        or not 1 <= limits.max_age_seconds <= 7 * 24 * 60 * 60
        or not 0 <= limits.max_clock_skew_seconds <= 300
        or not 1 <= limits.max_processing_seconds <= 60
        or channel.sensitivity not in ("internal", "confidential", "restricted")
        or channel.retention_class not in ("ephemeral", "standard", "extended")
    ):
        raise OtlpReceiverConfigurationError("otlp.configuration.invalid")
