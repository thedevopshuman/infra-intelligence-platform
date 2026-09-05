"""Shared defensive validation for AI attribution ledger adapters."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping

from iip.application.attribute_ai_usage import (
    ENGINE_VERSION,
    InvalidAiAttributionInputError,
    validate_ai_attribution_policy,
    validate_ai_usage_attribution_record,
)
from iip.application.ports import ActorContext, PersistenceError
from iip.domain.models import PlatformEvent


_TENANT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_WORKER_ACTOR = re.compile(r"ai-attribution-worker:[A-Za-z0-9._:-]{1,256}")
_POLICY_ID = re.compile(r"aap_[a-f0-9]{32}")
_TRACE_ID = re.compile(r"[a-f0-9]{32}")
_BASE_EVENT_KEYS = {
    "attributionRecordId",
    "usageRecordId",
    "policyId",
    "policyVersion",
    "status",
}


@dataclass(frozen=True)
class PreparedAiAttributionPolicy:
    document: Mapping[str, object]
    document_hash: str
    tenant_id: str
    policy_id: str
    policy_version: str
    source_hash: str
    published_at: str


@dataclass(frozen=True)
class PreparedAiAttributionWrite:
    document: Mapping[str, object]
    event: PlatformEvent
    document_hash: str
    tenant_id: str
    attribution_record_id: str
    usage_record_id: str
    policy_id: str
    policy_version: str
    policy_source_hash: str
    engine_version: str
    status: str
    application_id: str | None
    team_id: str | None
    effective_at: str
    resolved_at: str


def prepare_ai_attribution_policy(
    actor: ActorContext,
    document: Mapping[str, object],
) -> PreparedAiAttributionPolicy:
    _validate_actor(actor)
    try:
        policy = validate_ai_attribution_policy(document)
        if policy.tenant_id != actor.tenant_id:
            raise ValueError
        copied = _json_copy(policy.document)
    except (TypeError, ValueError, InvalidAiAttributionInputError):
        raise PersistenceError("storage.request.invalid") from None
    return PreparedAiAttributionPolicy(
        copied,
        PlatformEvent.canonical_hash(copied),
        policy.tenant_id,
        policy.policy_id,
        policy.version,
        policy.source_hash,
        policy.published_at,
    )


def prepare_ai_attribution_writes(
    actor: ActorContext,
    records: tuple[Mapping[str, object], ...],
    events: tuple[PlatformEvent, ...],
) -> tuple[PreparedAiAttributionWrite, ...]:
    _validate_actor(actor)
    if (
        not isinstance(records, tuple)
        or not isinstance(events, tuple)
        or not 1 <= len(records) <= 1000
        or len(records) != len(events)
    ):
        raise PersistenceError("storage.request.invalid")
    prepared = tuple(
        _prepare_one(actor, record, event)
        for record, event in zip(records, events)
    )
    record_ids = tuple(item.attribution_record_id for item in prepared)
    identities = tuple(
        (item.usage_record_id, item.policy_id, item.engine_version)
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


def validate_ai_attribution_actor(
    actor: ActorContext,
    *,
    policy_id: object | None = None,
    engine_version: object | None = None,
    limit: object | None = None,
) -> None:
    _validate_actor(actor)
    if policy_id is not None and (
        not isinstance(policy_id, str) or not _POLICY_ID.fullmatch(policy_id)
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


def _prepare_one(
    actor: ActorContext,
    document: Mapping[str, object],
    event: PlatformEvent,
) -> PreparedAiAttributionWrite:
    try:
        copied = validate_ai_usage_attribution_record(document)
        metadata = copied["metadata"]
        spec = copied["spec"]
        assert isinstance(metadata, Mapping) and isinstance(spec, Mapping)
        policy = spec["policy"]
        resolution = spec["resolution"]
        assert isinstance(policy, Mapping) and isinstance(resolution, Mapping)
        tenant_id = metadata["tenantId"]
        attribution_id = metadata["id"]
        usage_id = spec["usageRecordId"]
        policy_id = policy["id"]
        policy_version = policy["version"]
        policy_source_hash = policy["sourceHash"]
        engine_version = spec["engineVersion"]
        status = resolution["status"]
        effective_at = spec["effectiveAt"]
        resolved_at = metadata["resolvedAt"]
        application_id = None
        team_id = None
        if status == "allocated":
            application = resolution["application"]
            team = resolution["team"]
            assert isinstance(application, Mapping) and isinstance(team, Mapping)
            application_id = application["id"]
            team_id = team["id"]
        expected_event_keys = set(_BASE_EVENT_KEYS)
        if status == "allocated":
            expected_event_keys.update({"applicationId", "teamId"})
        if (
            tenant_id != actor.tenant_id
            or not isinstance(event, PlatformEvent)
            or event.tenant_id != tenant_id
            or event.subject != attribution_id
            or event.event_type != "io.iip.ai.usage-attributed.v1"
            or event.source != "urn:iip:ai-attribution:" + ENGINE_VERSION
            or event.correlation_id is None
            or not _TRACE_ID.fullmatch(event.correlation_id)
            or event.causation_id != usage_id
            or event.time != resolved_at
            or set(event.data) != expected_event_keys
            or event.data["attributionRecordId"] != attribution_id
            or event.data["usageRecordId"] != usage_id
            or event.data["policyId"] != policy_id
            or event.data["policyVersion"] != policy_version
            or event.data["status"] != status
            or (
                status == "allocated"
                and (
                    event.data["applicationId"] != application_id
                    or event.data["teamId"] != team_id
                )
            )
        ):
            raise ValueError
        hash_document = _json_copy(copied)
        hash_metadata = hash_document["metadata"]
        assert isinstance(hash_metadata, dict)
        del hash_metadata["resolvedAt"]
    except (KeyError, TypeError, ValueError, InvalidAiAttributionInputError):
        raise PersistenceError("storage.request.invalid") from None
    return PreparedAiAttributionWrite(
        copied,
        event,
        PlatformEvent.canonical_hash(hash_document),
        str(tenant_id),
        str(attribution_id),
        str(usage_id),
        str(policy_id),
        str(policy_version),
        str(policy_source_hash),
        str(engine_version),
        str(status),
        str(application_id) if application_id is not None else None,
        str(team_id) if team_id is not None else None,
        str(effective_at),
        str(resolved_at),
    )


def _validate_actor(actor: ActorContext) -> None:
    if (
        not isinstance(actor, ActorContext)
        or not isinstance(actor.actor_id, str)
        or not _WORKER_ACTOR.fullmatch(actor.actor_id)
        or not isinstance(actor.tenant_id, str)
        or not _TENANT_ID.fullmatch(actor.tenant_id)
        or actor.roles != ("ai-attribution:resolve",)
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
    "PreparedAiAttributionPolicy",
    "PreparedAiAttributionWrite",
    "prepare_ai_attribution_policy",
    "prepare_ai_attribution_writes",
    "validate_ai_attribution_actor",
]
