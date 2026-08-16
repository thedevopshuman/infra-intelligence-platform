"""Protected OTLP/HTTP protobuf logs receiver adapter."""

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
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue

from iip.application.ingest_otlp_logs import (
    InvalidOtlpLogsRequestError,
    OtlpLogRecord,
    OtlpLogsBatch,
    OtlpLogsChannel,
    OtlpLogsChannelLimits,
    OtlpLogsPayloadTooLargeError,
)
from iip.application.ingest_otlp_metrics import (
    OtlpReceiverAuthenticationError,
    OtlpReceiverConfigurationError,
)
from iip.application.ports import ActorContext


_SHA256 = re.compile(r"sha256:[a-f0-9]{64}")
_CHANNEL_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_TENANT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_INTEGRATION_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_RESOURCE_UID = re.compile(r"res_[a-f0-9]{32}")
_SERVICE = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]{0,127}")
_ATTRIBUTE = re.compile(r"[A-Za-z_][A-Za-z0-9_.:/-]{0,127}")
_SENSITIVE_NAME = re.compile(
    r"(?:authorization|password|passwd|token|secret|apikey|accesskey|privatekey)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _ServiceProfile:
    otlp_name: str
    service_name: str
    attributes: Mapping[str, str]


@dataclass(frozen=True)
class _LogsChannelProfile:
    channel: OtlpLogsChannel
    services: Mapping[str, _ServiceProfile]


class ConfiguredOtlpLogsReceiver:
    """Authenticate channels and normalize only explicitly catalogued services."""

    def __init__(
        self,
        verifiers: Mapping[str, str],
        profiles: Mapping[str, _LogsChannelProfile],
    ) -> None:
        self._verifiers = dict(verifiers)
        self._profiles = dict(profiles)

    @classmethod
    def from_json(cls, raw: str) -> "ConfiguredOtlpLogsReceiver":
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
            profiles: dict[str, _LogsChannelProfile] = {}
            for entry in entries:
                channel, verifier, services = cls._parse_channel(entry)
                if verifier in verifiers or channel.channel_id in profiles:
                    raise ValueError
                verifiers[verifier] = channel.channel_id
                profiles[channel.channel_id] = _LogsChannelProfile(channel, services)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise OtlpReceiverConfigurationError("otlp.configuration.invalid") from None
        return cls(verifiers, profiles)

    @staticmethod
    def token_sha256(token: str) -> str:
        return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()

    def authenticate_bearer(self, token: str) -> OtlpLogsChannel:
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

    def decode_logs(
        self,
        channel: OtlpLogsChannel,
        payload: bytes,
        *,
        content_encoding: str,
    ) -> OtlpLogsBatch:
        profile = self._profiles.get(channel.channel_id)
        if profile is None or profile.channel != channel:
            raise OtlpReceiverConfigurationError("otlp.configuration.invalid")
        decoded = self._decode_content(
            payload, content_encoding, channel.limits.max_request_bytes
        )
        request = ExportLogsServiceRequest()
        try:
            request.ParseFromString(decoded)
        except DecodeError:
            raise InvalidOtlpLogsRequestError("otlp.protobuf.invalid") from None

        records: list[OtlpLogRecord] = []
        for resource_logs in request.resource_logs:
            resource = resource_logs.resource
            if resource.dropped_attributes_count:
                raise InvalidOtlpLogsRequestError("otlp.attributes.dropped")
            resource_attributes = self._attribute_values(
                resource.attributes, channel.limits.max_attributes_per_record
            )
            service_value = resource_attributes.get("service.name")
            if service_value is None or service_value.WhichOneof("value") != "string_value":
                raise InvalidOtlpLogsRequestError("otlp.service.not-allowlisted")
            service_profile = profile.services.get(service_value.string_value)
            if service_profile is None:
                raise InvalidOtlpLogsRequestError("otlp.service.not-allowlisted")
            for scope_logs in resource_logs.scope_logs:
                scope = scope_logs.scope
                if scope.dropped_attributes_count:
                    raise InvalidOtlpLogsRequestError("otlp.attributes.dropped")
                scope_attributes = self._attribute_values(
                    scope.attributes, channel.limits.max_attributes_per_record
                )
                for source in scope_logs.log_records:
                    if len(records) >= channel.limits.max_log_records:
                        raise InvalidOtlpLogsRequestError("otlp.log-record.limit")
                    if source.dropped_attributes_count:
                        raise InvalidOtlpLogsRequestError("otlp.attributes.dropped")
                    if getattr(source, "event_name", ""):
                        raise InvalidOtlpLogsRequestError(
                            "otlp.log-record.unsupported"
                        )
                    record_attributes = self._attribute_values(
                        source.attributes, channel.limits.max_attributes_per_record
                    )
                    attributes = self._mapped_attributes(
                        service_profile,
                        resource_attributes,
                        scope_attributes,
                        record_attributes,
                    )
                    body = self._body(source.body, channel.limits.max_body_bytes)
                    timestamp = self._timestamp(source.time_unix_nano)
                    observed = (
                        self._timestamp(source.observed_time_unix_nano)
                        if source.observed_time_unix_nano
                        else None
                    )
                    trace_id, span_id = self._trace_context(source.trace_id, source.span_id)
                    identity = {
                        "channelId": channel.channel_id,
                        "resourceRef": channel.resource_uid,
                        "timestamp": timestamp,
                        "observedTimestamp": observed,
                        "severity": self._severity(source.severity_number),
                        "serviceName": service_profile.service_name,
                        "body": body,
                        "attributes": attributes,
                        "traceId": trace_id,
                        "spanId": span_id,
                    }
                    digest = hashlib.sha256(
                        json.dumps(
                            identity,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                    ).hexdigest()
                    records.append(
                        OtlpLogRecord(
                            record_id="log_" + digest[:32],
                            resource_uid=channel.resource_uid,
                            timestamp=timestamp,
                            observed_timestamp=observed,
                            severity=identity["severity"],
                            service_name=service_profile.service_name,
                            body=body,
                            attributes=attributes,
                            trace_id=trace_id,
                            span_id=span_id,
                        )
                    )
        records.sort(key=lambda item: (item.timestamp, item.record_id))
        return OtlpLogsBatch(tuple(records))

    @classmethod
    def _parse_channel(
        cls, entry: object
    ) -> tuple[OtlpLogsChannel, str, Mapping[str, _ServiceProfile]]:
        required = {
            "channelId",
            "tokenSha256",
            "tenantId",
            "integrationId",
            "resourceRef",
            "services",
            "limits",
            "handling",
        }
        if not isinstance(entry, dict) or set(entry) != required:
            raise ValueError
        channel_id = entry["channelId"]
        verifier = entry["tokenSha256"]
        tenant_id = entry["tenantId"]
        integration_id = entry["integrationId"]
        resource_uid = entry["resourceRef"]
        if (
            not isinstance(channel_id, str)
            or not _CHANNEL_ID.fullmatch(channel_id)
            or not isinstance(verifier, str)
            or not _SHA256.fullmatch(verifier)
            or not isinstance(tenant_id, str)
            or not _TENANT_ID.fullmatch(tenant_id)
            or not isinstance(integration_id, str)
            or not _INTEGRATION_ID.fullmatch(integration_id)
            or not isinstance(resource_uid, str)
            or not _RESOURCE_UID.fullmatch(resource_uid)
        ):
            raise ValueError
        limits = cls._parse_limits(entry["limits"])
        sensitivity, retention = cls._parse_handling(entry["handling"])
        services = cls._parse_services(entry["services"])
        channel = OtlpLogsChannel(
            actor=ActorContext(
                actor_id=f"otlp-logs-channel:{channel_id}",
                tenant_id=tenant_id,
                roles=("telemetry-ingest",),
            ),
            channel_id=channel_id,
            integration_id=integration_id,
            resource_uid=resource_uid,
            service_names=tuple(sorted(item.service_name for item in services.values())),
            limits=limits,
            sensitivity=sensitivity,
            retention_class=retention,
        )
        return channel, verifier, services

    @staticmethod
    def _parse_limits(value: object) -> OtlpLogsChannelLimits:
        names = {
            "maxRequestBytes",
            "maxArtifactBytes",
            "maxLogRecords",
            "maxAttributesPerRecord",
            "maxBodyBytes",
            "maxAgeSeconds",
            "maxClockSkewSeconds",
            "maxProcessingSeconds",
        }
        if (
            not isinstance(value, dict)
            or set(value) != names
            or any(isinstance(value[name], bool) or not isinstance(value[name], int) for name in names)
        ):
            raise ValueError
        limits = OtlpLogsChannelLimits(
            max_request_bytes=value["maxRequestBytes"],
            max_artifact_bytes=value["maxArtifactBytes"],
            max_log_records=value["maxLogRecords"],
            max_attributes_per_record=value["maxAttributesPerRecord"],
            max_body_bytes=value["maxBodyBytes"],
            max_age_seconds=value["maxAgeSeconds"],
            max_clock_skew_seconds=value["maxClockSkewSeconds"],
            max_processing_seconds=value["maxProcessingSeconds"],
        )
        if not (
            1 <= limits.max_request_bytes <= 16 * 1024 * 1024
            and 1 <= limits.max_artifact_bytes <= 16 * 1024 * 1024
            and 1 <= limits.max_log_records <= 1000
            and 1 <= limits.max_attributes_per_record <= 16
            and 1 <= limits.max_body_bytes <= 16_384
            and 1 <= limits.max_age_seconds <= 7 * 24 * 60 * 60
            and 0 <= limits.max_clock_skew_seconds <= 300
            and 1 <= limits.max_processing_seconds <= 60
        ):
            raise ValueError
        return limits

    @staticmethod
    def _parse_handling(value: object) -> tuple[str, str]:
        if not isinstance(value, dict) or set(value) != {"sensitivity", "retentionClass"}:
            raise ValueError
        if value["sensitivity"] not in ("internal", "confidential", "restricted"):
            raise ValueError
        if value["retentionClass"] not in ("ephemeral", "standard", "extended"):
            raise ValueError
        return value["sensitivity"], value["retentionClass"]

    @classmethod
    def _parse_services(cls, value: object) -> Mapping[str, _ServiceProfile]:
        if not isinstance(value, list) or not 1 <= len(value) <= 128:
            raise ValueError
        services: dict[str, _ServiceProfile] = {}
        logical_names: set[str] = set()
        for item in value:
            if not isinstance(item, dict) or set(item) != {
                "otlpName",
                "serviceName",
                "attributes",
            }:
                raise ValueError
            otlp_name = item["otlpName"]
            service_name = item["serviceName"]
            attributes = item["attributes"]
            if (
                not isinstance(otlp_name, str)
                or not _SERVICE.fullmatch(otlp_name)
                or not isinstance(service_name, str)
                or not _SERVICE.fullmatch(service_name)
                or not isinstance(attributes, dict)
                or len(attributes) > 16
                or any(
                    not isinstance(source, str)
                    or not _ATTRIBUTE.fullmatch(source)
                    or not isinstance(target, str)
                    or not _ATTRIBUTE.fullmatch(target)
                    or cls._sensitive_name(source)
                    or cls._sensitive_name(target)
                    for source, target in attributes.items()
                )
                or len(set(attributes.values())) != len(attributes)
                or otlp_name in services
                or service_name in logical_names
            ):
                raise ValueError
            services[otlp_name] = _ServiceProfile(
                otlp_name=otlp_name,
                service_name=service_name,
                attributes=dict(attributes),
            )
            logical_names.add(service_name)
        return services

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
                raise InvalidOtlpLogsRequestError("otlp.compression.invalid") from None
        else:
            raise InvalidOtlpLogsRequestError("otlp.compression.unsupported")
        if len(decoded) > limit:
            raise OtlpLogsPayloadTooLargeError("otlp.request.too-large")
        return decoded

    @staticmethod
    def _attribute_values(attributes: object, limit: int) -> Mapping[str, AnyValue]:
        if len(attributes) > limit:
            raise InvalidOtlpLogsRequestError("otlp.attribute.limit")
        result: dict[str, AnyValue] = {}
        for attribute in attributes:
            if not isinstance(attribute, KeyValue) or not attribute.key:
                raise InvalidOtlpLogsRequestError("otlp.attribute.invalid")
            if attribute.key in result:
                raise InvalidOtlpLogsRequestError("otlp.attribute.duplicate")
            result[attribute.key] = attribute.value
        return result

    @classmethod
    def _mapped_attributes(
        cls,
        profile: _ServiceProfile,
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
                    raise InvalidOtlpLogsRequestError("otlp.attribute.ambiguous")
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
            raise InvalidOtlpLogsRequestError("otlp.attribute.type.unsupported")
        if (
            not 1 <= len(result) <= 256
            or any(ord(character) < 32 or ord(character) == 127 for character in result)
        ):
            raise InvalidOtlpLogsRequestError("otlp.attribute.invalid")
        return result

    @staticmethod
    def _body(value: AnyValue, maximum_bytes: int) -> str:
        if value.WhichOneof("value") != "string_value":
            raise InvalidOtlpLogsRequestError("otlp.log-body.type.unsupported")
        body = value.string_value
        if (
            not body
            or len(body) > 4096
            or len(body.encode("utf-8")) > maximum_bytes
            or any(
                (ord(character) < 32 and ord(character) not in (9, 10, 13))
                or ord(character) == 127
                for character in body
            )
        ):
            raise InvalidOtlpLogsRequestError("otlp.log-body.invalid")
        return body

    @staticmethod
    def _timestamp(value: int) -> str:
        if not isinstance(value, int) or value <= 0:
            raise InvalidOtlpLogsRequestError("otlp.log-record.time.invalid")
        try:
            seconds, nanos = divmod(value, 1_000_000_000)
            instant = datetime.fromtimestamp(seconds, timezone.utc).replace(
                microsecond=nanos // 1000
            )
        except (OverflowError, OSError, ValueError):
            raise InvalidOtlpLogsRequestError("otlp.log-record.time.invalid") from None
        return instant.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _severity(value: int) -> str:
        if value == 0:
            return "unspecified"
        ranges = (
            (1, 4, "trace"),
            (5, 8, "debug"),
            (9, 12, "info"),
            (13, 16, "warn"),
            (17, 20, "error"),
            (21, 24, "fatal"),
        )
        for minimum, maximum, name in ranges:
            if minimum <= value <= maximum:
                return name
        raise InvalidOtlpLogsRequestError("otlp.log-severity.invalid")

    @staticmethod
    def _trace_context(trace_id: bytes, span_id: bytes) -> tuple[str | None, str | None]:
        if not trace_id and not span_id:
            return None, None
        if (
            len(trace_id) != 16
            or len(span_id) != 8
            or not any(trace_id)
            or not any(span_id)
        ):
            raise InvalidOtlpLogsRequestError("otlp.trace-context.invalid")
        return trace_id.hex(), span_id.hex()
