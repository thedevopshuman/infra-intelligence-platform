"""Shared defensive validation for AI price and cost ledger adapters."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Mapping

from iip.application.calculate_ai_cost import (
    ENGINE_VERSION,
    InvalidAiCostInputError,
    validate_ai_cost_record,
    validate_ai_price_catalog,
)
from iip.application.ports import ActorContext, PersistenceError
from iip.domain.models import PlatformEvent


_TENANT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_WORKER_ACTOR = re.compile(r"ai-cost-worker:[A-Za-z0-9._:-]{1,256}")
_TRACE_ID = re.compile(r"[a-f0-9]{32}")
_EVENT_DATA_KEYS = {
    "costRecordId",
    "usageRecordId",
    "catalogId",
    "catalogVersion",
    "costStatus",
    "provider",
    "modelId",
    "serviceName",
}


@dataclass(frozen=True)
class PreparedAiPriceCatalog:
    document: Mapping[str, object]
    document_hash: str
    tenant_id: str
    catalog_id: str
    catalog_version: str
    source_hash: str
    published_at: str


@dataclass(frozen=True)
class PreparedAiCostWrite:
    document: Mapping[str, object]
    event: PlatformEvent
    document_hash: str
    tenant_id: str
    cost_record_id: str
    usage_record_id: str
    catalog_id: str
    catalog_version: str
    catalog_source_hash: str
    engine_version: str
    cost_status: str
    currency: str | None
    currency_scale: int | None
    total_subunits: int | None
    calculated_at: str
    warnings: tuple[str, ...]


def prepare_ai_price_catalog(
    actor: ActorContext,
    document: Mapping[str, object],
) -> PreparedAiPriceCatalog:
    _validate_actor(actor)
    try:
        catalog = validate_ai_price_catalog(document)
        if catalog.tenant_id != actor.tenant_id:
            raise ValueError
        copied = _json_copy(catalog.document)
    except (TypeError, ValueError, InvalidAiCostInputError):
        raise PersistenceError("storage.request.invalid") from None
    return PreparedAiPriceCatalog(
        copied,
        PlatformEvent.canonical_hash(copied),
        catalog.tenant_id,
        catalog.catalog_id,
        catalog.version,
        catalog.source_hash,
        catalog.published_at,
    )


def prepare_ai_cost_writes(
    actor: ActorContext,
    records: tuple[Mapping[str, object], ...],
    events: tuple[PlatformEvent, ...],
) -> tuple[PreparedAiCostWrite, ...]:
    _validate_actor(actor)
    if (
        not isinstance(records, tuple)
        or not isinstance(events, tuple)
        or not 1 <= len(records) <= 1000
        or len(records) != len(events)
    ):
        raise PersistenceError("storage.request.invalid")
    prepared = tuple(
        _prepare_cost(actor, record, event)
        for record, event in zip(records, events)
    )
    record_ids = tuple(item.cost_record_id for item in prepared)
    identities = tuple(
        (item.usage_record_id, item.catalog_id, item.engine_version)
        for item in prepared
    )
    event_ids = tuple((item.event.source, item.event.event_id) for item in prepared)
    if (
        len(set(record_ids)) != len(record_ids)
        or len(set(identities)) != len(identities)
        or len(set(event_ids)) != len(event_ids)
    ):
        raise PersistenceError("storage.request.invalid")
    return prepared


def validate_ai_cost_actor(
    actor: ActorContext,
    *,
    catalog_id: object | None = None,
    engine_version: object | None = None,
    limit: object | None = None,
) -> None:
    _validate_actor(actor)
    if catalog_id is not None and (
        not isinstance(catalog_id, str)
        or not re.fullmatch(r"apc_[a-f0-9]{32}", catalog_id)
    ):
        raise PersistenceError("storage.request.invalid")
    if engine_version is not None and engine_version != ENGINE_VERSION:
        raise PersistenceError("storage.request.invalid")
    if limit is not None and (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 1000
    ):
        raise PersistenceError("storage.request.invalid")


def _prepare_cost(
    actor: ActorContext,
    document: Mapping[str, object],
    event: PlatformEvent,
) -> PreparedAiCostWrite:
    try:
        copied = validate_ai_cost_record(document)
        metadata = copied["metadata"]
        spec = copied["spec"]
        assert isinstance(metadata, Mapping)
        assert isinstance(spec, Mapping)
        calculation = spec["calculation"]
        result = spec["result"]
        assert isinstance(calculation, Mapping)
        assert isinstance(result, Mapping)
        tenant_id = metadata["tenantId"]
        cost_record_id = metadata["id"]
        calculated_at = metadata["calculatedAt"]
        usage_record_id = spec["usageRecordId"]
        catalog_id = calculation["catalogId"]
        catalog_version = calculation["catalogVersion"]
        engine_version = calculation["engineVersion"]
        cost_status = result["costStatus"]
        if (
            tenant_id != actor.tenant_id
            or not all(
                isinstance(item, str)
                for item in (
                    tenant_id,
                    cost_record_id,
                    calculated_at,
                    usage_record_id,
                    catalog_id,
                    catalog_version,
                    engine_version,
                    cost_status,
                )
            )
        ):
            raise ValueError
        currency = result.get("currency")
        currency_scale = result.get("currencyScale")
        total_subunits = result.get("totalSubunits")
        warnings_value = result["warnings"]
        if cost_status != "priced" and any(
            value is not None for value in (currency, currency_scale, total_subunits)
        ):
            raise ValueError
        if not isinstance(event, PlatformEvent):
            raise ValueError
        event_data = event.data
        identity = {
            "tenantId": tenant_id,
            "usageRecordId": usage_record_id,
            "catalogId": catalog_id,
            "catalogVersion": catalog_version,
            "engineVersion": engine_version,
        }
        digest = hashlib.sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()
        if (
            cost_record_id != "aic_" + digest[:32]
            or event.event_id != "ai-cost-" + digest
            or event.tenant_id != tenant_id
            or event.subject != cost_record_id
            or event.event_type != "io.iip.ai.cost-calculated.v1"
            or event.source != "urn:iip:ai-cost:" + engine_version
            or event.correlation_id is None
            or not _TRACE_ID.fullmatch(event.correlation_id)
            or event.causation_id != usage_record_id
            or event.time != calculated_at
            or set(event_data) != _EVENT_DATA_KEYS
            or event_data["costRecordId"] != cost_record_id
            or event_data["usageRecordId"] != usage_record_id
            or event_data["catalogId"] != catalog_id
            or event_data["catalogVersion"] != catalog_version
            or event_data["costStatus"] != cost_status
            or not isinstance(warnings_value, list)
        ):
            raise ValueError
        for name in ("provider", "modelId", "serviceName"):
            value = event_data[name]
            if not isinstance(value, str) or not value or len(value) > 256:
                raise ValueError
        hash_document = _json_copy(copied)
        hash_metadata = hash_document["metadata"]
        assert isinstance(hash_metadata, dict)
        del hash_metadata["calculatedAt"]
    except (KeyError, TypeError, ValueError, InvalidAiCostInputError):
        raise PersistenceError("storage.request.invalid") from None
    return PreparedAiCostWrite(
        copied,
        event,
        PlatformEvent.canonical_hash(hash_document),
        tenant_id,
        cost_record_id,
        usage_record_id,
        catalog_id,
        catalog_version,
        calculation["catalogSourceHash"],
        engine_version,
        cost_status,
        currency if isinstance(currency, str) else None,
        currency_scale if isinstance(currency_scale, int) else None,
        total_subunits if isinstance(total_subunits, int) else None,
        calculated_at,
        tuple(warnings_value),
    )


def validate_ai_cost_usage_binding(
    item: PreparedAiCostWrite,
    usage_document: object,
) -> None:
    """Bind a cost event's routing fields to the immutable usage it cites."""

    try:
        document = _json_copy(usage_document)
        metadata = document["metadata"]
        spec = document["spec"]
        if not isinstance(metadata, Mapping) or not isinstance(spec, Mapping):
            raise ValueError
        invocation = spec["invocation"]
        attribution = spec["attribution"]
        if not isinstance(invocation, Mapping) or not isinstance(
            attribution, Mapping
        ):
            raise ValueError
        response_model = invocation.get("responseModel")
        model_id = (
            response_model
            if response_model is not None
            else invocation["requestModel"]
        )
        if (
            metadata["tenantId"] != item.tenant_id
            or metadata["id"] != item.usage_record_id
            or item.event.correlation_id != invocation["traceId"]
            or item.event.data["provider"] != invocation["provider"]
            or item.event.data["modelId"] != model_id
            or item.event.data["serviceName"] != attribution["serviceName"]
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError, OverflowError):
        raise PersistenceError("storage.request.invalid") from None


def _validate_actor(actor: ActorContext) -> None:
    if (
        not isinstance(actor, ActorContext)
        or not isinstance(actor.actor_id, str)
        or not _WORKER_ACTOR.fullmatch(actor.actor_id)
        or not isinstance(actor.tenant_id, str)
        or not _TENANT_ID.fullmatch(actor.tenant_id)
        or actor.roles != ("ai-cost:calculate",)
    ):
        raise PersistenceError("storage.request.invalid")


def _json_copy(value: object) -> Mapping[str, object]:
    copied = json.loads(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    )
    if not isinstance(copied, dict):
        raise ValueError
    return copied


__all__ = [
    "PreparedAiCostWrite",
    "PreparedAiPriceCatalog",
    "prepare_ai_cost_writes",
    "prepare_ai_price_catalog",
    "validate_ai_cost_actor",
    "validate_ai_cost_usage_binding",
]
