"""Protected OTLP/HTTP protobuf metrics receiver adapter."""

from __future__ import annotations

import gzip
import hashlib
import hmac
import io
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping

from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.metrics.v1 import metrics_pb2

from iip.application.ingest_otlp_metrics import (
    InvalidOtlpMetricsRequestError,
    OtlpChannelLimits,
    OtlpMetricPoint,
    OtlpMetricSeries,
    OtlpMetricsBatch,
    OtlpMetricsChannel,
    OtlpPayloadTooLargeError,
    OtlpReceiverAuthenticationError,
    OtlpReceiverConfigurationError,
)
from iip.application.ports import ActorContext


_SHA256 = re.compile(r"sha256:[a-f0-9]{64}")
_CHANNEL_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_TENANT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_INTEGRATION_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_RESOURCE_UID = re.compile(r"res_[a-f0-9]{32}")
_METRIC = re.compile(r"[A-Za-z_:][A-Za-z0-9_.:/-]{0,255}")
_ATTRIBUTE = re.compile(r"[A-Za-z_][A-Za-z0-9_.:/-]{0,127}")
_SENSITIVE_NAME = re.compile(
    r"(?:authorization|password|passwd|token|secret|apikey|accesskey|privatekey)$",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:authorization|password|passwd|token|secret|api[_-]?key|"
    r"access[_-]?key)\s*[:=]\s*\S+|bearer\s+\S+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


@dataclass(frozen=True)
class _MetricProfile:
    otlp_name: str
    metric: str
    unit: str
    attributes: Mapping[str, str]


@dataclass(frozen=True)
class _ChannelProfile:
    channel: OtlpMetricsChannel
    metrics: Mapping[str, _MetricProfile]


class ConfiguredOtlpMetricsReceiver:
    """Authenticate channels and normalize only explicitly catalogued metrics."""

    def __init__(
        self,
        verifiers: Mapping[str, str],
        profiles: Mapping[str, _ChannelProfile],
    ) -> None:
        self._verifiers = dict(verifiers)
        self._profiles = dict(profiles)

    @classmethod
    def from_json(cls, raw: str) -> "ConfiguredOtlpMetricsReceiver":
        """Load strict protected configuration without retaining raw credentials."""

        try:
            if not isinstance(raw, str) or not 2 <= len(raw) <= 1_048_576:
                raise ValueError
            document = json.loads(raw)
            if not isinstance(document, dict) or set(document) != {"channels"}:
                raise ValueError
            entries = document["channels"]
            if not isinstance(entries, list) or not 1 <= len(entries) <= 64:
                raise ValueError

            verifiers: dict[str, str] = {}
            profiles: dict[str, _ChannelProfile] = {}
            for entry in entries:
                channel, verifier, metrics = cls._parse_channel(entry)
                if verifier in verifiers or channel.channel_id in profiles:
                    raise ValueError
                verifiers[verifier] = channel.channel_id
                profiles[channel.channel_id] = _ChannelProfile(channel, metrics)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise OtlpReceiverConfigurationError("otlp.configuration.invalid") from None
        return cls(verifiers, profiles)

    @staticmethod
    def token_sha256(token: str) -> str:
        """Return the verifier representation expected in protected config."""

        return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()

    def authenticate_bearer(self, token: str) -> OtlpMetricsChannel:
        """Resolve one opaque channel token with a stable fail-closed error."""

        if (
            not isinstance(token, str)
            or not 32 <= len(token) <= 4096
            or any(character.isspace() or ord(character) < 33 for character in token)
        ):
            raise OtlpReceiverAuthenticationError("otlp.authentication.invalid")
        presented = self.token_sha256(token)
        channel_id = None
        for verifier, candidate in self._verifiers.items():
            if hmac.compare_digest(presented, verifier):
                channel_id = candidate
        if channel_id is None:
            raise OtlpReceiverAuthenticationError("otlp.authentication.invalid")
        return self._profiles[channel_id].channel

    def decode_metrics(
        self,
        channel: OtlpMetricsChannel,
        payload: bytes,
        *,
        content_encoding: str,
    ) -> OtlpMetricsBatch:
        """Decode binary Protobuf and normalize gauge/sum data atomically."""

        profile = self._profiles.get(channel.channel_id)
        if profile is None or profile.channel != channel:
            raise OtlpReceiverConfigurationError("otlp.configuration.invalid")
        decoded = self._decode_content(
            payload,
            content_encoding,
            channel.limits.max_request_bytes,
        )
        request = ExportMetricsServiceRequest()
        try:
            request.ParseFromString(decoded)
        except DecodeError:
            raise InvalidOtlpMetricsRequestError("otlp.protobuf.invalid") from None

        grouped: dict[
            tuple[str, str, str | None, bool | None, tuple[tuple[str, str], ...]],
            list[OtlpMetricPoint],
        ] = {}
        point_count = 0
        for resource_metrics in request.resource_metrics:
            resource = resource_metrics.resource
            if resource.dropped_attributes_count:
                raise InvalidOtlpMetricsRequestError("otlp.attributes.dropped")
            resource_attributes = self._attribute_values(
                resource.attributes,
                channel.limits.max_attributes_per_point,
            )
            for scope_metrics in resource_metrics.scope_metrics:
                scope = scope_metrics.scope
                if scope.dropped_attributes_count:
                    raise InvalidOtlpMetricsRequestError("otlp.attributes.dropped")
                scope_attributes = self._attribute_values(
                    scope.attributes,
                    channel.limits.max_attributes_per_point,
                )
                for metric in scope_metrics.metrics:
                    metric_profile = profile.metrics.get(metric.name)
                    if metric_profile is None:
                        raise InvalidOtlpMetricsRequestError(
                            "otlp.metric.not-allowlisted"
                        )
                    if metric.unit != metric_profile.unit or metric.metadata:
                        raise InvalidOtlpMetricsRequestError("otlp.metric.invalid")
                    kind = metric.WhichOneof("data")
                    if kind not in ("gauge", "sum"):
                        raise InvalidOtlpMetricsRequestError(
                            "otlp.metric.kind.unsupported"
                        )
                    temporality: str | None = None
                    monotonic: bool | None = None
                    if kind == "gauge":
                        data_points = metric.gauge.data_points
                    else:
                        data_points = metric.sum.data_points
                        temporality = self._temporality(
                            metric.sum.aggregation_temporality
                        )
                        monotonic = bool(metric.sum.is_monotonic)
                    if not data_points:
                        raise InvalidOtlpMetricsRequestError(
                            "otlp.data-point.invalid"
                        )

                    for data_point in data_points:
                        point_count += 1
                        if point_count > channel.limits.max_data_points:
                            raise InvalidOtlpMetricsRequestError(
                                "otlp.data-point.limit"
                            )
                        if data_point.flags or data_point.exemplars:
                            raise InvalidOtlpMetricsRequestError(
                                "otlp.data-point.unsupported"
                            )
                        point_attributes = self._attribute_values(
                            data_point.attributes,
                            channel.limits.max_attributes_per_point,
                        )
                        attributes = self._mapped_attributes(
                            metric_profile,
                            resource_attributes,
                            scope_attributes,
                            point_attributes,
                        )
                        identity = (
                            metric_profile.metric,
                            kind,
                            temporality,
                            monotonic,
                            attributes,
                        )
                        points = grouped.setdefault(identity, [])
                        if len(grouped) > channel.limits.max_series:
                            raise InvalidOtlpMetricsRequestError("otlp.series.limit")
                        points.append(self._point(data_point))

        series = []
        for identity, points in grouped.items():
            metric, kind, temporality, monotonic, attributes = identity
            series.append(
                OtlpMetricSeries(
                    metric=metric,
                    unit=profile.metrics[
                        self._otlp_name_for(profile, metric)
                    ].unit,
                    kind=kind,
                    temporality=temporality,
                    monotonic=monotonic,
                    attributes=attributes,
                    points=tuple(sorted(points, key=lambda point: point.timestamp)),
                )
            )
        series.sort(
            key=lambda item: (item.metric, item.kind, item.attributes)
        )
        return OtlpMetricsBatch(tuple(series))

    @classmethod
    def _parse_channel(
        cls, entry: object
    ) -> tuple[OtlpMetricsChannel, str, Mapping[str, _MetricProfile]]:
        required = {
            "channelId",
            "tokenSha256",
            "tenantId",
            "integrationId",
            "resourceRefs",
            "metrics",
            "limits",
            "handling",
        }
        if not isinstance(entry, dict) or set(entry) != required:
            raise ValueError
        channel_id = entry["channelId"]
        verifier = entry["tokenSha256"]
        tenant_id = entry["tenantId"]
        integration_id = entry["integrationId"]
        resource_uids = entry["resourceRefs"]
        if (
            not isinstance(channel_id, str)
            or not _CHANNEL_ID.fullmatch(channel_id)
            or not isinstance(verifier, str)
            or not _SHA256.fullmatch(verifier)
            or not isinstance(tenant_id, str)
            or not _TENANT_ID.fullmatch(tenant_id)
            or not isinstance(integration_id, str)
            or not _INTEGRATION_ID.fullmatch(integration_id)
            or not isinstance(resource_uids, list)
            or not 1 <= len(resource_uids) <= 256
            or any(
                not isinstance(uid, str) or not _RESOURCE_UID.fullmatch(uid)
                for uid in resource_uids
            )
            or len(set(resource_uids)) != len(resource_uids)
        ):
            raise ValueError

        limits = cls._parse_limits(entry["limits"])
        sensitivity, retention_class = cls._parse_handling(entry["handling"])
        metrics = cls._parse_metrics(entry["metrics"])
        channel = OtlpMetricsChannel(
            actor=ActorContext(
                actor_id=f"otlp-channel:{channel_id}",
                tenant_id=tenant_id,
                roles=("telemetry-ingest",),
            ),
            channel_id=channel_id,
            integration_id=integration_id,
            resource_uids=tuple(resource_uids),
            limits=limits,
            sensitivity=sensitivity,
            retention_class=retention_class,
        )
        return channel, verifier, metrics

    @staticmethod
    def _parse_limits(value: object) -> OtlpChannelLimits:
        names = {
            "maxRequestBytes",
            "maxArtifactBytes",
            "maxSeries",
            "maxDataPoints",
            "maxAttributesPerPoint",
            "maxAgeSeconds",
            "maxClockSkewSeconds",
            "maxProcessingSeconds",
        }
        if not isinstance(value, dict) or set(value) != names:
            raise ValueError
        if any(
            isinstance(value[name], bool) or not isinstance(value[name], int)
            for name in names
        ):
            raise ValueError
        limits = OtlpChannelLimits(
            max_request_bytes=value["maxRequestBytes"],
            max_artifact_bytes=value["maxArtifactBytes"],
            max_series=value["maxSeries"],
            max_data_points=value["maxDataPoints"],
            max_attributes_per_point=value["maxAttributesPerPoint"],
            max_age_seconds=value["maxAgeSeconds"],
            max_clock_skew_seconds=value["maxClockSkewSeconds"],
            max_processing_seconds=value["maxProcessingSeconds"],
        )
        if not (
            1 <= limits.max_request_bytes <= 16 * 1024 * 1024
            and 1 <= limits.max_artifact_bytes <= 16 * 1024 * 1024
            and 1 <= limits.max_series <= 100
            and 1 <= limits.max_data_points <= 10_000
            and 1 <= limits.max_attributes_per_point <= 16
            and 1 <= limits.max_age_seconds <= 7 * 24 * 60 * 60
            and 0 <= limits.max_clock_skew_seconds <= 300
            and 1 <= limits.max_processing_seconds <= 60
        ):
            raise ValueError
        return limits

    @staticmethod
    def _parse_handling(value: object) -> tuple[str, str]:
        if not isinstance(value, dict) or set(value) != {
            "sensitivity",
            "retentionClass",
        }:
            raise ValueError
        sensitivity = value["sensitivity"]
        retention = value["retentionClass"]
        if sensitivity not in ("internal", "confidential", "restricted"):
            raise ValueError
        if retention not in ("ephemeral", "standard", "extended"):
            raise ValueError
        return sensitivity, retention

    @staticmethod
    def _parse_metrics(value: object) -> Mapping[str, _MetricProfile]:
        if not isinstance(value, list) or not 1 <= len(value) <= 128:
            raise ValueError
        metrics: dict[str, _MetricProfile] = {}
        logical_names: set[str] = set()
        for item in value:
            if not isinstance(item, dict) or set(item) != {
                "otlpName",
                "metric",
                "unit",
                "attributes",
            }:
                raise ValueError
            otlp_name = item["otlpName"]
            metric = item["metric"]
            unit = item["unit"]
            attributes = item["attributes"]
            if (
                not isinstance(otlp_name, str)
                or not _METRIC.fullmatch(otlp_name)
                or not isinstance(metric, str)
                or not _METRIC.fullmatch(metric)
                or not isinstance(unit, str)
                or not 1 <= len(unit) <= 64
                or any(ord(character) < 32 or ord(character) == 127 for character in unit)
                or _SENSITIVE_TEXT.search(unit)
                or not isinstance(attributes, dict)
                or len(attributes) > 16
                or any(
                    not isinstance(source, str)
                    or not _ATTRIBUTE.fullmatch(source)
                    or not isinstance(target, str)
                    or not _ATTRIBUTE.fullmatch(target)
                    or ConfiguredOtlpMetricsReceiver._sensitive_name(source)
                    or ConfiguredOtlpMetricsReceiver._sensitive_name(target)
                    for source, target in attributes.items()
                )
                or len(set(attributes.values())) != len(attributes)
                or otlp_name in metrics
                or metric in logical_names
            ):
                raise ValueError
            metrics[otlp_name] = _MetricProfile(
                otlp_name=otlp_name,
                metric=metric,
                unit=unit,
                attributes=dict(attributes),
            )
            logical_names.add(metric)
        return metrics

    @staticmethod
    def _sensitive_name(value: str) -> bool:
        normalized = re.sub(r"[^a-z0-9]", "", value.lower())
        return bool(_SENSITIVE_NAME.search(normalized))

    @staticmethod
    def _decode_content(payload: bytes, encoding: str, limit: int) -> bytes:
        if encoding in ("", "identity"):
            decoded = payload
        elif encoding == "gzip":
            try:
                with gzip.GzipFile(fileobj=io.BytesIO(payload), mode="rb") as stream:
                    decoded = stream.read(limit + 1)
            except (EOFError, OSError):
                raise InvalidOtlpMetricsRequestError("otlp.compression.invalid") from None
        else:
            raise InvalidOtlpMetricsRequestError("otlp.compression.unsupported")
        if len(decoded) > limit:
            raise OtlpPayloadTooLargeError("otlp.request.too-large")
        return decoded

    @staticmethod
    def _attribute_values(
        attributes: object,
        limit: int,
    ) -> Mapping[str, AnyValue]:
        if len(attributes) > limit:
            raise InvalidOtlpMetricsRequestError("otlp.attribute.limit")
        result: dict[str, AnyValue] = {}
        for attribute in attributes:
            if not isinstance(attribute, KeyValue) or not attribute.key:
                raise InvalidOtlpMetricsRequestError("otlp.attribute.invalid")
            if attribute.key in result:
                raise InvalidOtlpMetricsRequestError("otlp.attribute.duplicate")
            result[attribute.key] = attribute.value
        return result

    @classmethod
    def _mapped_attributes(
        cls,
        profile: _MetricProfile,
        *layers: Mapping[str, AnyValue],
    ) -> tuple[tuple[str, str], ...]:
        normalized: dict[str, str] = {}
        for layer in layers:
            for source, target in profile.attributes.items():
                value = layer.get(source)
                if value is None:
                    continue
                scalar = cls._scalar_attribute(value)
                previous = normalized.get(target)
                if previous is not None and previous != scalar:
                    raise InvalidOtlpMetricsRequestError("otlp.attribute.ambiguous")
                normalized[target] = scalar
        return tuple(sorted(normalized.items()))

    @staticmethod
    def _scalar_attribute(value: AnyValue) -> str:
        kind = value.WhichOneof("value")
        if kind == "string_value":
            result = value.string_value
        elif kind == "bool_value":
            result = "true" if value.bool_value else "false"
        elif kind == "int_value":
            result = str(value.int_value)
        elif kind == "double_value" and math.isfinite(value.double_value):
            result = format(value.double_value, ".17g")
        else:
            raise InvalidOtlpMetricsRequestError("otlp.attribute.type.unsupported")
        if (
            not 1 <= len(result) <= 256
            or any(ord(character) < 32 or ord(character) == 127 for character in result)
        ):
            raise InvalidOtlpMetricsRequestError("otlp.attribute.invalid")
        return result

    @staticmethod
    def _point(data_point: object) -> OtlpMetricPoint:
        timestamp = data_point.time_unix_nano
        value_kind = data_point.WhichOneof("value")
        if not isinstance(timestamp, int) or timestamp <= 0:
            raise InvalidOtlpMetricsRequestError("otlp.data-point.time.invalid")
        if value_kind == "as_int":
            value: int | float = data_point.as_int
            if abs(value) > 9_007_199_254_740_991:
                raise InvalidOtlpMetricsRequestError("otlp.data-point.invalid")
        elif value_kind == "as_double" and math.isfinite(data_point.as_double):
            value = data_point.as_double
        else:
            raise InvalidOtlpMetricsRequestError("otlp.data-point.invalid")
        try:
            seconds, nanos = divmod(timestamp, 1_000_000_000)
            instant = datetime.fromtimestamp(seconds, timezone.utc).replace(
                microsecond=nanos // 1000
            )
        except (OverflowError, OSError, ValueError):
            raise InvalidOtlpMetricsRequestError("otlp.data-point.time.invalid") from None
        return OtlpMetricPoint(
            instant.isoformat().replace("+00:00", "Z"),
            value,
        )

    @staticmethod
    def _temporality(value: int) -> str:
        if value == metrics_pb2.AGGREGATION_TEMPORALITY_DELTA:
            return "delta"
        if value == metrics_pb2.AGGREGATION_TEMPORALITY_CUMULATIVE:
            return "cumulative"
        raise InvalidOtlpMetricsRequestError("otlp.temporality.unsupported")

    @staticmethod
    def _otlp_name_for(profile: _ChannelProfile, logical_name: str) -> str:
        for otlp_name, metric in profile.metrics.items():
            if metric.metric == logical_name:
                return otlp_name
        raise OtlpReceiverConfigurationError("otlp.configuration.invalid")
