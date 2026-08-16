"""Tenant-bound, bounded ingestion of normalized OTLP log batches."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping, Protocol

from iip.application.collect_evidence import CollectEvidenceCommand, EvidenceCollectionService
from iip.application.ingest_otlp_metrics import (
    OtlpReceiverConfigurationError,
)
from iip.application.ports import ActorContext, Clock, RawEvidenceArtifact


_CHANNEL_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_TENANT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_INTEGRATION_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_RESOURCE_UID = re.compile(r"res_[a-f0-9]{32}")
_RECORD_ID = re.compile(r"log_[a-f0-9]{32}")
_SERVICE = re.compile(r"[A-Za-z][A-Za-z0-9_.:/-]{0,127}")
_ATTRIBUTE = re.compile(r"[A-Za-z_][A-Za-z0-9_.:/-]{0,127}")
_TRACE_ID = re.compile(r"[a-f0-9]{32}")
_SPAN_ID = re.compile(r"[a-f0-9]{16}")
_SEVERITIES = frozenset(
    {"trace", "debug", "info", "warn", "error", "fatal", "unspecified"}
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:authorization|password|passwd|token|secret|api[_-]?key|"
    r"access[_-]?key)\s*[:=]\s*\S+|bearer\s+\S+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
)


class InvalidOtlpLogsRequestError(ValueError):
    """An OTLP logs request cannot be safely normalized for its channel."""


class OtlpLogsPayloadTooLargeError(InvalidOtlpLogsRequestError):
    """The encoded, decoded, body, or normalized request exceeded a bound."""


@dataclass(frozen=True)
class OtlpLogsChannelLimits:
    """Admission budgets fixed by protected logs-channel configuration."""

    max_request_bytes: int
    max_artifact_bytes: int
    max_log_records: int
    max_attributes_per_record: int
    max_body_bytes: int
    max_age_seconds: int
    max_clock_skew_seconds: int
    max_processing_seconds: int


@dataclass(frozen=True)
class OtlpLogsChannel:
    """Authenticated logs channel without its presented credential."""

    actor: ActorContext
    channel_id: str
    integration_id: str
    resource_uid: str
    service_names: tuple[str, ...]
    limits: OtlpLogsChannelLimits
    sensitivity: str
    retention_class: str


@dataclass(frozen=True)
class OtlpLogRecord:
    """One normalized untrusted OTLP log record."""

    record_id: str
    resource_uid: str
    timestamp: str
    severity: str
    service_name: str
    body: str
    attributes: tuple[tuple[str, str], ...]
    observed_timestamp: str | None = None
    trace_id: str | None = None
    span_id: str | None = None


@dataclass(frozen=True)
class OtlpLogsBatch:
    """Untrusted adapter output before application validation and persistence."""

    records: tuple[OtlpLogRecord, ...]


class OtlpLogsReceiverAdapter(Protocol):
    """Protocol/authentication adapter kept outside application semantics."""

    def authenticate_bearer(self, token: str) -> OtlpLogsChannel:
        """Resolve one channel credential to protected tenant and resource scope."""

    def decode_logs(
        self,
        channel: OtlpLogsChannel,
        payload: bytes,
        *,
        content_encoding: str,
    ) -> OtlpLogsBatch:
        """Decode and normalize one bounded OTLP/HTTP protobuf request."""


class OtlpLogsIngestionService:
    """Authenticate a logs channel and record normalized immutable evidence."""

    def __init__(
        self,
        receiver: OtlpLogsReceiverAdapter,
        evidence: EvidenceCollectionService,
        clock: Clock,
    ) -> None:
        self._receiver = receiver
        self._evidence = evidence
        self._clock = clock

    def authenticate_bearer(self, token: str) -> OtlpLogsChannel:
        channel = self._receiver.authenticate_bearer(token)
        validate_logs_channel_context(channel)
        return channel

    def ingest(
        self,
        channel: OtlpLogsChannel,
        payload: bytes,
        *,
        content_encoding: str = "identity",
    ) -> Mapping[str, object]:
        validate_logs_channel_context(channel)
        received_at = self._now()
        if not isinstance(payload, bytes):
            raise InvalidOtlpLogsRequestError("otlp.request.invalid")
        if len(payload) > channel.limits.max_request_bytes:
            raise OtlpLogsPayloadTooLargeError("otlp.request.too-large")
        batch = self._receiver.decode_logs(
            channel,
            payload,
            content_encoding=content_encoding,
        )
        if isinstance(batch, OtlpLogsBatch) and batch.records == ():
            return {}
        document, latest = self._artifact_document(channel, batch, received_at)
        content = json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(content) > channel.limits.max_artifact_bytes:
            raise OtlpLogsPayloadTooLargeError("otlp.artifact.too-large")
        deadline = received_at + timedelta(seconds=channel.limits.max_processing_seconds)
        return self._evidence.record_artifact(
            CollectEvidenceCommand(
                actor=channel.actor,
                provider="otlp-logs-receiver",
                integration_id=channel.integration_id,
                evidence_type="telemetry.logs.push",
                resource_uids=(channel.resource_uid,),
                locator=f"otlp://{channel.channel_id}/v1/logs",
                deadline=self._format_time(deadline),
                max_bytes=channel.limits.max_artifact_bytes,
                sensitivity=channel.sensitivity,
                retention_class=channel.retention_class,
            ),
            RawEvidenceArtifact(
                content=content,
                media_type="application/json",
                observed_at=self._format_time(latest),
                summary=(
                    "Accepted "
                    f"{document['spec']['summary']['recordCount']} OTLP log record(s) "
                    "from an authenticated channel."
                ),
            ),
        )

    def _artifact_document(
        self,
        channel: OtlpLogsChannel,
        batch: object,
        received_at: datetime,
    ) -> tuple[dict[str, object], datetime]:
        if not isinstance(batch, OtlpLogsBatch) or not isinstance(batch.records, tuple):
            raise InvalidOtlpLogsRequestError("otlp.request.invalid")
        if not 1 <= len(batch.records) <= channel.limits.max_log_records:
            raise InvalidOtlpLogsRequestError("otlp.log-record.limit")

        documents: list[dict[str, object]] = []
        record_ids: set[str] = set()
        times: list[datetime] = []
        services: set[str] = set()
        for record in batch.records:
            document, instant = self._validate_record(
                channel, record, received_at, record_ids
            )
            documents.append(document)
            record_ids.add(record.record_id)
            times.append(instant)
            services.add(record.service_name)
        if documents != sorted(documents, key=lambda item: (item["timestamp"], item["id"])):
            raise InvalidOtlpLogsRequestError("otlp.log-record.order.invalid")
        error_count = sum(item["severity"] in ("error", "fatal") for item in documents)
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "OtlpLogsEvidence",
            "metadata": {
                "tenantId": channel.actor.tenant_id,
                "integrationId": channel.integration_id,
                "channelId": channel.channel_id,
                "receivedAt": self._format_time(received_at),
            },
            "spec": {
                "signal": "logs",
                "protocol": "otlp/http-protobuf",
                "timeRange": {
                    "start": self._format_time(min(times)),
                    "end": self._format_time(max(times)),
                },
                "records": documents,
                "summary": {
                    "serviceCount": len(services),
                    "recordCount": len(documents),
                    "errorCount": error_count,
                },
            },
        }, max(times)

    def _validate_record(
        self,
        channel: OtlpLogsChannel,
        record: object,
        received_at: datetime,
        record_ids: set[str],
    ) -> tuple[dict[str, object], datetime]:
        if not isinstance(record, OtlpLogRecord):
            raise InvalidOtlpLogsRequestError("otlp.log-record.invalid")
        timestamp = self._parse_time(record.timestamp)
        oldest = received_at - timedelta(seconds=channel.limits.max_age_seconds)
        newest = received_at + timedelta(seconds=channel.limits.max_clock_skew_seconds)
        observed = (
            self._parse_time(record.observed_timestamp)
            if record.observed_timestamp is not None
            else None
        )
        if (
            not isinstance(record.record_id, str)
            or not _RECORD_ID.fullmatch(record.record_id)
            or record.record_id in record_ids
            or record.resource_uid != channel.resource_uid
            or not oldest <= timestamp <= newest
            or (observed is not None and not timestamp <= observed <= newest)
            or record.severity not in _SEVERITIES
            or record.service_name not in channel.service_names
            or not self._safe_body(record.body, channel.limits.max_body_bytes)
            or (record.trace_id is None) != (record.span_id is None)
            or (record.trace_id is not None and not _TRACE_ID.fullmatch(record.trace_id))
            or (record.span_id is not None and not _SPAN_ID.fullmatch(record.span_id))
            or not isinstance(record.attributes, tuple)
            or len(record.attributes) > min(channel.limits.max_attributes_per_record, 16)
            or tuple(sorted(record.attributes)) != record.attributes
            or len({name for name, _ in record.attributes}) != len(record.attributes)
        ):
            raise InvalidOtlpLogsRequestError("otlp.log-record.invalid")
        attributes: dict[str, str] = {}
        for name, value in record.attributes:
            if (
                not isinstance(name, str)
                or not _ATTRIBUTE.fullmatch(name)
                or not self._safe_attribute(value)
            ):
                raise InvalidOtlpLogsRequestError("otlp.attribute.invalid")
            attributes[name] = value
        document: dict[str, object] = {
            "id": record.record_id,
            "resourceRef": record.resource_uid,
            "timestamp": record.timestamp,
            "severity": record.severity,
            "serviceName": record.service_name,
            "body": record.body,
            "attributes": attributes,
        }
        if record.observed_timestamp is not None:
            document["observedTimestamp"] = record.observed_timestamp
        if record.trace_id is not None:
            document["traceId"] = record.trace_id
            document["spanId"] = record.span_id
        return document, timestamp

    @staticmethod
    def _safe_body(value: object, maximum_bytes: int) -> bool:
        return bool(
            isinstance(value, str)
            and value
            and len(value) <= 4096
            and len(value.encode("utf-8")) <= maximum_bytes
            and not any(
                (ord(character) < 32 and ord(character) not in (9, 10, 13))
                or ord(character) == 127
                for character in value
            )
        )

    @staticmethod
    def _safe_attribute(value: object) -> bool:
        return bool(
            isinstance(value, str)
            and 1 <= len(value) <= 256
            and not any(ord(character) < 32 or ord(character) == 127 for character in value)
            and not _SENSITIVE_TEXT.search(value)
        )

    def _now(self) -> datetime:
        try:
            return self._parse_time(self._clock.now())
        except InvalidOtlpLogsRequestError:
            raise InvalidOtlpLogsRequestError("otlp.clock.invalid") from None

    @staticmethod
    def _parse_time(value: object) -> datetime:
        if not isinstance(value, str):
            raise InvalidOtlpLogsRequestError("otlp.log-record.time.invalid")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise InvalidOtlpLogsRequestError("otlp.log-record.time.invalid") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise InvalidOtlpLogsRequestError("otlp.log-record.time.invalid")
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")


def validate_logs_channel_context(channel: OtlpLogsChannel) -> None:
    """Validate protected adapter output before a surface trusts its body limit."""

    if not isinstance(channel, OtlpLogsChannel) or not isinstance(
        channel.limits, OtlpLogsChannelLimits
    ):
        raise OtlpReceiverConfigurationError("otlp.configuration.invalid")
    limits = channel.limits
    numbers = (
        limits.max_request_bytes,
        limits.max_artifact_bytes,
        limits.max_log_records,
        limits.max_attributes_per_record,
        limits.max_body_bytes,
        limits.max_age_seconds,
        limits.max_clock_skew_seconds,
        limits.max_processing_seconds,
    )
    if (
        not isinstance(channel.channel_id, str)
        or not _CHANNEL_ID.fullmatch(channel.channel_id)
        or not isinstance(channel.actor, ActorContext)
        or channel.actor.actor_id != f"otlp-logs-channel:{channel.channel_id}"
        or not isinstance(channel.actor.tenant_id, str)
        or not _TENANT_ID.fullmatch(channel.actor.tenant_id)
        or channel.actor.roles != ("telemetry-ingest",)
        or not isinstance(channel.integration_id, str)
        or not _INTEGRATION_ID.fullmatch(channel.integration_id)
        or not isinstance(channel.resource_uid, str)
        or not _RESOURCE_UID.fullmatch(channel.resource_uid)
        or not isinstance(channel.service_names, tuple)
        or not 1 <= len(channel.service_names) <= 128
        or any(not isinstance(name, str) or not _SERVICE.fullmatch(name) for name in channel.service_names)
        or tuple(sorted(channel.service_names)) != channel.service_names
        or len(set(channel.service_names)) != len(channel.service_names)
        or any(isinstance(value, bool) or not isinstance(value, int) for value in numbers)
        or not 1 <= limits.max_request_bytes <= 16 * 1024 * 1024
        or not 1 <= limits.max_artifact_bytes <= 16 * 1024 * 1024
        or not 1 <= limits.max_log_records <= 1000
        or not 1 <= limits.max_attributes_per_record <= 16
        or not 1 <= limits.max_body_bytes <= 16_384
        or not 1 <= limits.max_age_seconds <= 7 * 24 * 60 * 60
        or not 0 <= limits.max_clock_skew_seconds <= 300
        or not 1 <= limits.max_processing_seconds <= 60
        or channel.sensitivity not in ("internal", "confidential", "restricted")
        or channel.retention_class not in ("ephemeral", "standard", "extended")
    ):
        raise OtlpReceiverConfigurationError("otlp.configuration.invalid")
