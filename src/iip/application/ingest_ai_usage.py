"""Tenant-bound ingestion of normalized metadata-only generative-AI spans."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping, Protocol

from iip.application.ingest_otlp_metrics import (
    OtlpReceiverAuthenticationError,
    OtlpReceiverConfigurationError,
)
from iip.application.ports import ActorContext, AiUsageLedger, Clock
from iip.domain.models import PlatformEvent


_CHANNEL_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_TENANT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_INTEGRATION_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_RESOURCE_UID = re.compile(r"res_[a-f0-9]{32}")
_PROVIDER = re.compile(r"[a-z0-9][a-z0-9._-]{1,63}")
_OPERATION = re.compile(r"[a-z][a-z0-9._-]{1,63}")
_REGION = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")
_TRACE_ID = re.compile(r"[a-f0-9]{32}")
_SPAN_ID = re.compile(r"[a-f0-9]{16}")
_SHA256 = re.compile(r"sha256:[a-f0-9]{64}")
_SAFE_TEXT = re.compile(r"[^\x00-\x1f\x7f]{1,256}")
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:authorization|password|passwd|token|secret|api[_-]?key|"
    r"access[_-]?key)\s*[:=]\s*\S+|bearer\s+\S+|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
_USAGE_FIELDS = (
    "inputTokens",
    "outputTokens",
    "cacheReadInputTokens",
    "cacheWriteInputTokens",
    "reasoningOutputTokens",
)
_TIERS = frozenset({"default", "standard", "flex", "priority", "reserved", "unknown"})
_ROUTING = frozenset({"in-region", "geographic", "global", "unknown"})
_PURCHASE = frozenset({"on-demand", "batch", "provisioned-throughput", "unknown"})


class InvalidAiUsageRequestError(ValueError):
    """A trace request cannot be represented as safe canonical AI usage."""


class AiUsagePayloadTooLargeError(InvalidAiUsageRequestError):
    """An encoded, decoded, or normalized trace request exceeded a bound."""


@dataclass(frozen=True)
class AiUsageChannelLimits:
    """Protected admission limits applied before usage persistence."""

    max_request_bytes: int
    max_spans: int
    max_attributes_per_span: int
    max_age_seconds: int
    max_clock_skew_seconds: int
    max_processing_seconds: int


@dataclass(frozen=True)
class AiUsageChannel:
    """Authenticated channel scope with no presented credential or endpoint."""

    actor: ActorContext
    channel_id: str
    integration_id: str
    provider: str
    semantic_convention_version: str
    service_names: tuple[str, ...]
    model_ids: tuple[str, ...]
    operations: tuple[str, ...]
    regions: tuple[str, ...]
    instrumentation_scopes: tuple[str, ...]
    service_tier: str
    routing_mode: str
    purchase_mode: str
    limits: AiUsageChannelLimits


@dataclass(frozen=True)
class AiUsageSpan:
    """Provider-neutral invocation metadata returned by the OTLP adapter."""

    provider: str
    operation_name: str
    request_model: str
    response_model: str | None
    region: str
    service_tier: str
    routing_mode: str
    purchase_mode: str
    started_at: str
    duration_millis: int
    outcome: str
    error_type: str | None
    trace_id: str
    span_id: str
    request_id_hash: str | None
    retry_count: int | None
    service_name: str
    service_namespace: str | None
    deployment_environment: str | None
    resource_uids: tuple[str, ...]
    input_tokens: int | None
    output_tokens: int | None
    cache_read_input_tokens: int | None
    cache_write_input_tokens: int | None
    reasoning_output_tokens: int | None
    reported_by: str
    completeness: str
    missing_fields: tuple[str, ...]
    instrumentation_scope_name: str
    instrumentation_scope_version: str | None
    semantic_convention_version: str
    dropped_attribute_count: int


@dataclass(frozen=True)
class AiUsageBatch:
    spans: tuple[AiUsageSpan, ...]


class AiUsageReceiverAdapter(Protocol):
    """Decode provider telemetry without leaking OTLP types into the use case."""

    def authenticate_bearer(self, token: str) -> AiUsageChannel:
        """Resolve one credential to protected channel scope."""

    def decode_traces(
        self,
        channel: AiUsageChannel,
        payload: bytes,
        *,
        content_encoding: str,
    ) -> AiUsageBatch:
        """Return only allowlisted metadata from a bounded OTLP trace export."""


class AiUsageIngestionService:
    """Validate normalized invocations and atomically commit their ledger facts."""

    def __init__(
        self,
        receiver: AiUsageReceiverAdapter,
        ledger: AiUsageLedger,
        clock: Clock,
    ) -> None:
        self._receiver = receiver
        self._ledger = ledger
        self._clock = clock

    def authenticate_bearer(self, token: str) -> AiUsageChannel:
        channel = self._receiver.authenticate_bearer(token)
        validate_ai_usage_channel(channel)
        return channel

    def ingest(
        self,
        channel: AiUsageChannel,
        payload: bytes,
        *,
        content_encoding: str = "identity",
    ) -> tuple[Mapping[str, object], ...]:
        validate_ai_usage_channel(channel)
        if not isinstance(payload, bytes):
            raise InvalidAiUsageRequestError("otlp.request.invalid")
        if len(payload) > channel.limits.max_request_bytes:
            raise AiUsagePayloadTooLargeError("otlp.request.too-large")
        received_at = self._now()
        batch = self._receiver.decode_traces(
            channel,
            payload,
            content_encoding=content_encoding,
        )
        if not isinstance(batch, AiUsageBatch) or not isinstance(batch.spans, tuple):
            raise InvalidAiUsageRequestError("otlp.request.invalid")
        if batch.spans == ():
            return ()
        if len(batch.spans) > channel.limits.max_spans:
            raise InvalidAiUsageRequestError("otlp.span.limit")

        records: list[Mapping[str, object]] = []
        events: list[PlatformEvent] = []
        seen: set[str] = set()
        for span in batch.spans:
            record, event = self._record_and_event(channel, span, received_at)
            spec = record["spec"]
            assert isinstance(spec, Mapping)
            deduplication_key = spec["deduplicationKey"]
            assert isinstance(deduplication_key, str)
            if deduplication_key in seen:
                raise InvalidAiUsageRequestError("otlp.span.duplicate")
            seen.add(deduplication_key)
            records.append(record)
            events.append(event)
        return self._ledger.commit_usage_batch(
            channel.actor,
            tuple(records),
            tuple(events),
        )

    def _record_and_event(
        self,
        channel: AiUsageChannel,
        span: object,
        received_at: datetime,
    ) -> tuple[Mapping[str, object], PlatformEvent]:
        if not isinstance(span, AiUsageSpan):
            raise InvalidAiUsageRequestError("otlp.span.invalid")
        started = self._parse_time(span.started_at)
        oldest = received_at - timedelta(seconds=channel.limits.max_age_seconds)
        newest = received_at + timedelta(seconds=channel.limits.max_clock_skew_seconds)
        counts = (
            span.input_tokens,
            span.output_tokens,
            span.cache_read_input_tokens,
            span.cache_write_input_tokens,
            span.reasoning_output_tokens,
        )
        present_counts = tuple(value for value in counts if value is not None)
        if (
            not isinstance(span.provider, str)
            or span.provider != channel.provider
            or not isinstance(span.operation_name, str)
            or span.service_name not in channel.service_names
            or span.request_model not in channel.model_ids
            or (
                span.response_model is not None
                and span.response_model not in channel.model_ids
            )
            or span.operation_name not in channel.operations
            or span.region not in channel.regions
            or span.instrumentation_scope_name not in channel.instrumentation_scopes
            or span.semantic_convention_version != channel.semantic_convention_version
            or span.service_tier != channel.service_tier
            or span.routing_mode != channel.routing_mode
            or span.purchase_mode != channel.purchase_mode
            or not oldest <= started <= newest
            or isinstance(span.duration_millis, bool)
            or not isinstance(span.duration_millis, int)
            or not 0 <= span.duration_millis <= 86_400_000
            or span.outcome not in ("success", "error", "cancelled")
            or (span.outcome == "error") != (span.error_type is not None)
            or not isinstance(span.trace_id, str)
            or not _TRACE_ID.fullmatch(span.trace_id)
            or not isinstance(span.span_id, str)
            or not _SPAN_ID.fullmatch(span.span_id)
            or (
                span.request_id_hash is not None
                and (
                    not isinstance(span.request_id_hash, str)
                    or not _SHA256.fullmatch(span.request_id_hash)
                )
            )
            or (
                span.retry_count is not None
                and (
                    isinstance(span.retry_count, bool)
                    or not isinstance(span.retry_count, int)
                    or not 0 <= span.retry_count <= 100
                )
            )
            or span.input_tokens is None and span.output_tokens is None
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= 9007199254740991
                for value in present_counts
            )
            or span.completeness not in ("complete", "partial")
            or span.reported_by not in ("provider", "instrumentation", "derived")
            or not isinstance(span.missing_fields, tuple)
            or any(field not in _USAGE_FIELDS for field in span.missing_fields)
            or tuple(sorted(span.missing_fields)) != span.missing_fields
            or len(set(span.missing_fields)) != len(span.missing_fields)
            or not isinstance(span.dropped_attribute_count, int)
            or not 0 <= span.dropped_attribute_count <= 100000
        ):
            raise InvalidAiUsageRequestError("otlp.span.invalid")
        absent_fields = tuple(
            field
            for field, value in zip(_USAGE_FIELDS, counts)
            if value is None
        )
        if (
            span.completeness == "complete"
            and (
                span.input_tokens is None
                or span.output_tokens is None
                or span.missing_fields
            )
        ) or (
            span.completeness == "partial"
            and (
                not span.missing_fields
                or span.missing_fields != absent_fields
            )
        ):
            raise InvalidAiUsageRequestError("otlp.usage.completeness.invalid")
        if span.input_tokens is not None:
            cache_total = (span.cache_read_input_tokens or 0) + (
                span.cache_write_input_tokens or 0
            )
            if cache_total > span.input_tokens:
                raise InvalidAiUsageRequestError("otlp.usage.breakdown.invalid")
        if (
            span.output_tokens is not None
            and (span.reasoning_output_tokens or 0) > span.output_tokens
        ):
            raise InvalidAiUsageRequestError("otlp.usage.breakdown.invalid")
        self._safe_text(span.service_name)
        self._safe_optional(span.service_namespace)
        self._safe_optional(span.deployment_environment)
        self._safe_optional(span.response_model)
        self._safe_optional(span.error_type, maximum=128)
        self._safe_optional(span.instrumentation_scope_version, maximum=64)
        if (
            not isinstance(span.resource_uids, tuple)
            or len(span.resource_uids) > 64
            or tuple(sorted(span.resource_uids)) != span.resource_uids
            or len(set(span.resource_uids)) != len(span.resource_uids)
            or any(
                not isinstance(uid, str) or not _RESOURCE_UID.fullmatch(uid)
                for uid in span.resource_uids
            )
        ):
            raise InvalidAiUsageRequestError("otlp.span.invalid")

        identity = {
            "tenantId": channel.actor.tenant_id,
            "channelId": channel.channel_id,
            "traceId": span.trace_id,
            "spanId": span.span_id,
            "provider": span.provider,
            "operationName": span.operation_name,
            "requestModel": span.request_model,
        }
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        recorded_at = self._format_time(received_at)
        invocation: dict[str, object] = {
            "provider": span.provider,
            "operationName": span.operation_name,
            "requestModel": span.request_model,
            "region": span.region,
            "serviceTier": span.service_tier,
            "routingMode": span.routing_mode,
            "purchaseMode": span.purchase_mode,
            "startedAt": self._format_time(started),
            "durationMillis": span.duration_millis,
            "outcome": span.outcome,
            "traceId": span.trace_id,
            "spanId": span.span_id,
        }
        optional_invocation = {
            "responseModel": span.response_model,
            "errorType": span.error_type,
            "requestIdHash": span.request_id_hash,
            "retryCount": span.retry_count,
        }
        invocation.update(
            {key: value for key, value in optional_invocation.items() if value is not None}
        )
        attribution: dict[str, object] = {
            "serviceName": span.service_name,
            "resourceRefs": list(span.resource_uids),
        }
        if span.service_namespace is not None:
            attribution["serviceNamespace"] = span.service_namespace
        if span.deployment_environment is not None:
            attribution["deploymentEnvironment"] = span.deployment_environment
        usage: dict[str, object] = {
            "reportedBy": span.reported_by,
            "completeness": span.completeness,
            "missingFields": list(span.missing_fields),
        }
        for key, value in (
            ("inputTokens", span.input_tokens),
            ("outputTokens", span.output_tokens),
            ("cacheReadInputTokens", span.cache_read_input_tokens),
            ("cacheWriteInputTokens", span.cache_write_input_tokens),
            ("reasoningOutputTokens", span.reasoning_output_tokens),
        ):
            if value is not None:
                usage[key] = value
        instrumentation: dict[str, object] = {
            "scopeName": span.instrumentation_scope_name
        }
        if span.instrumentation_scope_version is not None:
            instrumentation["scopeVersion"] = span.instrumentation_scope_version
        record: Mapping[str, object] = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "AiUsageRecord",
            "metadata": {
                "id": "aiu_" + digest[:32],
                "tenantId": channel.actor.tenant_id,
                "recordedAt": recorded_at,
            },
            "spec": {
                "source": {
                    "integrationId": channel.integration_id,
                    "channelId": channel.channel_id,
                    "transport": "otlp",
                    "signal": "traces",
                    "semanticConventionVersion": span.semantic_convention_version,
                    "instrumentation": instrumentation,
                },
                "invocation": invocation,
                "attribution": attribution,
                "usage": usage,
                "privacy": {
                    "contentPolicy": "metadata-only",
                    "contentCaptured": False,
                    "rawPayloadPersisted": False,
                    "droppedAttributeCount": span.dropped_attribute_count,
                },
                "deduplicationKey": "sha256:" + digest,
            },
        }
        model_id = span.response_model or span.request_model
        event = PlatformEvent(
            event_id="ai-usage-" + digest,
            event_type="io.iip.ai.usage-recorded.v1",
            source=f"urn:iip:ai-usage:{channel.integration_id}",
            time=recorded_at,
            subject=record["metadata"]["id"],
            tenant_id=channel.actor.tenant_id,
            correlation_id=span.trace_id,
            data={
                "usageRecordId": record["metadata"]["id"],
                "deduplicationKey": record["spec"]["deduplicationKey"],
                "provider": span.provider,
                "modelId": model_id,
                "serviceName": span.service_name,
                "outcome": span.outcome,
            },
        )
        return record, event

    def _now(self) -> datetime:
        try:
            return self._parse_time(self._clock.now())
        except InvalidAiUsageRequestError:
            raise InvalidAiUsageRequestError("otlp.clock.invalid") from None

    @staticmethod
    def _parse_time(value: object) -> datetime:
        if not isinstance(value, str):
            raise InvalidAiUsageRequestError("otlp.span.time.invalid")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise InvalidAiUsageRequestError("otlp.span.time.invalid") from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise InvalidAiUsageRequestError("otlp.span.time.invalid")
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _safe_text(value: object, *, maximum: int = 256) -> None:
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or not _SAFE_TEXT.fullmatch(value)
            or _SENSITIVE_TEXT.search(value)
        ):
            raise InvalidAiUsageRequestError("otlp.span.invalid")

    @classmethod
    def _safe_optional(cls, value: object, *, maximum: int = 256) -> None:
        if value is not None:
            cls._safe_text(value, maximum=maximum)


def validate_ai_usage_channel(channel: AiUsageChannel) -> None:
    """Validate protected adapter output before trusting its body limits."""

    if (
        not isinstance(channel, AiUsageChannel)
        or not isinstance(channel.limits, AiUsageChannelLimits)
        or not isinstance(channel.actor, ActorContext)
    ):
        raise OtlpReceiverConfigurationError("otlp.configuration.invalid")
    limits = channel.limits
    numbers = (
        limits.max_request_bytes,
        limits.max_spans,
        limits.max_attributes_per_span,
        limits.max_age_seconds,
        limits.max_clock_skew_seconds,
        limits.max_processing_seconds,
    )
    sequences = (
        (channel.service_names, 128),
        (channel.model_ids, 256),
        (channel.operations, 64),
        (channel.regions, 64),
        (channel.instrumentation_scopes, 64),
    )
    valid_sequences = all(
        isinstance(items, tuple)
        and 1 <= len(items) <= maximum
        and all(isinstance(item, str) for item in items)
        and tuple(sorted(items)) == items
        and len(set(items)) == len(items)
        for items, maximum in sequences
    )
    if (
        not isinstance(channel.channel_id, str)
        or not _CHANNEL_ID.fullmatch(channel.channel_id)
        or not isinstance(channel.actor.actor_id, str)
        or channel.actor.actor_id != f"ai-usage-channel:{channel.channel_id}"
        or not isinstance(channel.actor.tenant_id, str)
        or not _TENANT_ID.fullmatch(channel.actor.tenant_id)
        or channel.actor.roles != ("telemetry-ingest",)
        or not isinstance(channel.integration_id, str)
        or not _INTEGRATION_ID.fullmatch(channel.integration_id)
        or not isinstance(channel.provider, str)
        or not _PROVIDER.fullmatch(channel.provider)
        or not isinstance(channel.semantic_convention_version, str)
        or not 1 <= len(channel.semantic_convention_version) <= 64
        or channel.service_tier not in _TIERS
        or channel.routing_mode not in _ROUTING
        or channel.purchase_mode not in _PURCHASE
        or not valid_sequences
        or any(
            not isinstance(value, str) or not _SAFE_TEXT.fullmatch(value)
            for value in channel.service_names
        )
        or any(
            not isinstance(value, str) or not _SAFE_TEXT.fullmatch(value)
            for value in channel.model_ids
        )
        or any(
            not _OPERATION.fullmatch(value)
            for value in channel.operations
            if isinstance(value, str)
        )
        or any(
            not _REGION.fullmatch(value)
            for value in channel.regions
            if isinstance(value, str)
        )
        or any(
            not isinstance(value, str) or not _SAFE_TEXT.fullmatch(value)
            for value in channel.instrumentation_scopes
        )
        or any(isinstance(value, bool) or not isinstance(value, int) for value in numbers)
        or not 1 <= limits.max_request_bytes <= 16 * 1024 * 1024
        or not 1 <= limits.max_spans <= 1000
        or not 1 <= limits.max_attributes_per_span <= 128
        or not 1 <= limits.max_age_seconds <= 7 * 24 * 60 * 60
        or not 0 <= limits.max_clock_skew_seconds <= 300
        or not 1 <= limits.max_processing_seconds <= 60
    ):
        raise OtlpReceiverConfigurationError("otlp.configuration.invalid")


__all__ = [
    "AiUsageBatch",
    "AiUsageChannel",
    "AiUsageChannelLimits",
    "AiUsageIngestionService",
    "AiUsagePayloadTooLargeError",
    "AiUsageReceiverAdapter",
    "AiUsageSpan",
    "InvalidAiUsageRequestError",
    "OtlpReceiverAuthenticationError",
    "validate_ai_usage_channel",
]
