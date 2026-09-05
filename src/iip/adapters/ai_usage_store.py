"""Shared defensive validation for AI usage ledger adapters."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from iip.application.ports import ActorContext, PersistenceError
from iip.domain.models import PlatformEvent


_TENANT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_USAGE_ID = re.compile(r"aiu_[a-f0-9]{32}")
_CHANNEL_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_SHA256 = re.compile(r"sha256:[a-f0-9]{64}")
_METADATA_KEYS = {"id", "tenantId", "recordedAt"}
_SPEC_KEYS = {
    "source",
    "invocation",
    "attribution",
    "usage",
    "privacy",
    "deduplicationKey",
}
_SOURCE_KEYS = {
    "integrationId",
    "channelId",
    "transport",
    "signal",
    "semanticConventionVersion",
    "instrumentation",
}
_INVOCATION_REQUIRED = {
    "provider",
    "operationName",
    "requestModel",
    "region",
    "serviceTier",
    "routingMode",
    "purchaseMode",
    "startedAt",
    "durationMillis",
    "outcome",
    "traceId",
    "spanId",
}
_INVOCATION_ALLOWED = _INVOCATION_REQUIRED | {
    "responseModel",
    "errorType",
    "requestIdHash",
    "retryCount",
}
_ATTRIBUTION_REQUIRED = {"serviceName", "resourceRefs"}
_ATTRIBUTION_ALLOWED = _ATTRIBUTION_REQUIRED | {
    "serviceNamespace",
    "deploymentEnvironment",
}
_USAGE_REQUIRED = {"reportedBy", "completeness", "missingFields"}
_USAGE_ALLOWED = _USAGE_REQUIRED | {
    "inputTokens",
    "outputTokens",
    "cacheReadInputTokens",
    "cacheWriteInputTokens",
    "reasoningOutputTokens",
}
_PRIVACY_KEYS = {
    "contentPolicy",
    "contentCaptured",
    "rawPayloadPersisted",
    "droppedAttributeCount",
}
_EVENT_DATA_KEYS = {
    "usageRecordId",
    "deduplicationKey",
    "provider",
    "modelId",
    "serviceName",
    "outcome",
}


@dataclass(frozen=True)
class PreparedAiUsageWrite:
    document: Mapping[str, object]
    event: PlatformEvent
    document_hash: str
    tenant_id: str
    usage_record_id: str
    deduplication_key: str
    provider: str
    model_id: str
    service_name: str
    invocation_started_at: str
    recorded_at: str


def prepare_ai_usage_writes(
    actor: ActorContext,
    records: tuple[Mapping[str, object], ...],
    events: tuple[PlatformEvent, ...],
) -> tuple[PreparedAiUsageWrite, ...]:
    """Copy and validate an exact tenant batch before any adapter mutation."""

    if (
        not isinstance(actor, ActorContext)
        or not isinstance(actor.actor_id, str)
        or not actor.actor_id.startswith("ai-usage-channel:")
        or not isinstance(actor.tenant_id, str)
        or not _TENANT_ID.fullmatch(actor.tenant_id)
        or actor.roles != ("telemetry-ingest",)
        or not isinstance(records, tuple)
        or not isinstance(events, tuple)
        or not 1 <= len(records) <= 1000
        or len(records) != len(events)
    ):
        raise PersistenceError("storage.request.invalid")

    prepared = tuple(
        _prepare_one(actor, record, event)
        for record, event in zip(records, events)
    )
    identities = tuple(
        (item.usage_record_id, item.deduplication_key) for item in prepared
    )
    event_identities = tuple(
        (item.event.source, item.event.event_id) for item in prepared
    )
    if len(set(identities)) != len(identities) or len(set(event_identities)) != len(
        event_identities
    ):
        raise PersistenceError("storage.request.invalid")
    return prepared


def _prepare_one(
    actor: ActorContext,
    record: Mapping[str, object],
    event: PlatformEvent,
) -> PreparedAiUsageWrite:
    try:
        document = json.loads(
            json.dumps(
                record,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            )
        )
        if not isinstance(document, dict) or set(document) != {
            "apiVersion",
            "kind",
            "metadata",
            "spec",
        }:
            raise ValueError
        if (
            document["apiVersion"] != "iip.platform/v1alpha1"
            or document["kind"] != "AiUsageRecord"
        ):
            raise ValueError
        metadata = _closed_object(document["metadata"], _METADATA_KEYS, _METADATA_KEYS)
        spec = _closed_object(document["spec"], _SPEC_KEYS, _SPEC_KEYS)
        source = _closed_object(spec["source"], _SOURCE_KEYS, _SOURCE_KEYS)
        invocation = _closed_object(
            spec["invocation"], _INVOCATION_REQUIRED, _INVOCATION_ALLOWED
        )
        attribution = _closed_object(
            spec["attribution"], _ATTRIBUTION_REQUIRED, _ATTRIBUTION_ALLOWED
        )
        _closed_object(spec["usage"], _USAGE_REQUIRED, _USAGE_ALLOWED)
        privacy = _closed_object(spec["privacy"], _PRIVACY_KEYS, _PRIVACY_KEYS)
        instrumentation = _closed_object(
            source["instrumentation"], {"scopeName"}, {"scopeName", "scopeVersion"}
        )

        tenant_id = _string(metadata["tenantId"])
        usage_record_id = _string(metadata["id"])
        recorded_at = _timestamp(metadata["recordedAt"])
        deduplication_key = _string(spec["deduplicationKey"])
        channel_id = _string(source["channelId"])
        integration_id = _string(source["integrationId"])
        provider = _string(invocation["provider"])
        request_model = _string(invocation["requestModel"])
        response_model = invocation.get("responseModel")
        model_id = _string(response_model) if response_model is not None else request_model
        service_name = _string(attribution["serviceName"])
        invocation_started_at = _timestamp(invocation["startedAt"])
        _string(instrumentation["scopeName"])
        if instrumentation.get("scopeVersion") is not None:
            _string(instrumentation["scopeVersion"])
        if (
            tenant_id != actor.tenant_id
            or not _USAGE_ID.fullmatch(usage_record_id)
            or not _SHA256.fullmatch(deduplication_key)
            or not _CHANNEL_ID.fullmatch(channel_id)
            or actor.actor_id != f"ai-usage-channel:{channel_id}"
            or source["transport"] != "otlp"
            or source["signal"] != "traces"
            or privacy
            != {
                "contentPolicy": "metadata-only",
                "contentCaptured": False,
                "rawPayloadPersisted": False,
                "droppedAttributeCount": privacy["droppedAttributeCount"],
            }
            or isinstance(privacy["droppedAttributeCount"], bool)
            or not isinstance(privacy["droppedAttributeCount"], int)
            or not 0 <= privacy["droppedAttributeCount"] <= 100000
            or not isinstance(attribution["resourceRefs"], list)
        ):
            raise ValueError

        if not isinstance(event, PlatformEvent):
            raise ValueError
        event_data = event.data
        expected_event_source = f"urn:iip:ai-usage:{integration_id}"
        if (
            event.tenant_id != tenant_id
            or event.subject != usage_record_id
            or event.event_type != "io.iip.ai.usage-recorded.v1"
            or event.source != expected_event_source
            or event.correlation_id != invocation["traceId"]
            or set(event_data) != _EVENT_DATA_KEYS
            or event_data["usageRecordId"] != usage_record_id
            or event_data["deduplicationKey"] != deduplication_key
            or event_data["provider"] != provider
            or event_data["modelId"] != model_id
            or event_data["serviceName"] != service_name
            or event_data["outcome"] != invocation["outcome"]
            or event.time != recorded_at
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise PersistenceError("storage.request.invalid") from None

    hash_document = json.loads(
        json.dumps(
            document,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    )
    del hash_document["metadata"]["recordedAt"]
    return PreparedAiUsageWrite(
        document=document,
        event=event,
        document_hash=PlatformEvent.canonical_hash(hash_document),
        tenant_id=tenant_id,
        usage_record_id=usage_record_id,
        deduplication_key=deduplication_key,
        provider=provider,
        model_id=model_id,
        service_name=service_name,
        invocation_started_at=invocation_started_at,
        recorded_at=recorded_at,
    )


def _closed_object(
    value: object,
    required: set[str],
    allowed: set[str],
) -> dict[str, object]:
    if (
        not isinstance(value, dict)
        or not required.issubset(value)
        or not set(value).issubset(allowed)
    ):
        raise ValueError
    return value


def _string(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1024
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError
    return value


def _timestamp(value: object) -> str:
    result = _string(value)
    parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return result


__all__ = ["PreparedAiUsageWrite", "prepare_ai_usage_writes"]
