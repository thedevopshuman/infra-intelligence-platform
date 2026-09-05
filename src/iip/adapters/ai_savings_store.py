"""Defensive validation shared by AI savings ledger adapters."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from iip.application.calculate_ai_cost import ENGINE_VERSION as COST_ENGINE_VERSION
from iip.application.evaluate_ai_savings import (
    InvalidAiSavingsInputError,
    validate_ai_savings_finding,
)
from iip.application.ports import (
    ActorContext,
    AiSavingsCohortQuery,
    PersistenceError,
)
from iip.domain.models import PlatformEvent


_TENANT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_WORKER_ACTOR = re.compile(r"ai-savings-worker:[A-Za-z0-9._:-]{1,256}")
_PROVIDER = re.compile(r"[a-z0-9][a-z0-9._-]{1,63}")
_REGION = re.compile(r"[a-z0-9][a-z0-9-]{1,63}")
_CATALOG_ID = re.compile(r"apc_[a-f0-9]{32}")
_EVENT_DATA_KEYS = {
    "findingId",
    "ruleId",
    "ruleVersion",
    "category",
    "severity",
    "provider",
    "modelId",
    "serviceName",
}


@dataclass(frozen=True)
class PreparedAiSavingsWrite:
    document: Mapping[str, object]
    event: PlatformEvent
    document_hash: str
    tenant_id: str
    finding_id: str
    rule_id: str
    rule_version: str
    severity: str
    provider: str
    model_id: str
    candidate_model_id: str | None
    region: str
    service_name: str
    deployment_environment: str
    baseline_start: str
    baseline_end: str
    current_start: str
    current_end: str
    evaluated_at: str
    usage_record_ids: tuple[str, ...]
    cost_record_ids: tuple[str, ...]
    suitability_report_ids: tuple[str, ...]


def validate_ai_savings_actor(actor: ActorContext) -> None:
    if (
        not isinstance(actor, ActorContext)
        or not isinstance(actor.actor_id, str)
        or not _WORKER_ACTOR.fullmatch(actor.actor_id)
        or not isinstance(actor.tenant_id, str)
        or not _TENANT_ID.fullmatch(actor.tenant_id)
        or actor.roles != ("ai-savings:evaluate",)
    ):
        raise PersistenceError("storage.request.invalid")


def validate_ai_savings_query(
    actor: ActorContext,
    query: AiSavingsCohortQuery,
) -> None:
    validate_ai_savings_actor(actor)
    try:
        if (
            not isinstance(query, AiSavingsCohortQuery)
            or not _PROVIDER.fullmatch(query.provider)
            or not _REGION.fullmatch(query.region)
            or not _CATALOG_ID.fullmatch(query.catalog_id)
            or query.engine_version != COST_ENGINE_VERSION
            or any(
                not isinstance(value, str) or not 1 <= len(value) <= maximum
                for value, maximum in (
                    (query.model_id, 256),
                    (query.service_name, 256),
                    (query.deployment_environment, 128),
                )
            )
            or isinstance(query.limit, bool)
            or not isinstance(query.limit, int)
            or not 1 <= query.limit <= 101
            or _timestamp(query.start) >= _timestamp(query.end)
        ):
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        raise PersistenceError("storage.request.invalid") from None


def prepare_ai_savings_writes(
    actor: ActorContext,
    findings: tuple[Mapping[str, object], ...],
    events: tuple[PlatformEvent, ...],
) -> tuple[PreparedAiSavingsWrite, ...]:
    validate_ai_savings_actor(actor)
    if (
        not isinstance(findings, tuple)
        or not isinstance(events, tuple)
        or not 1 <= len(findings) <= 100
        or len(findings) != len(events)
    ):
        raise PersistenceError("storage.request.invalid")
    prepared = tuple(
        _prepare_one(actor, finding, event)
        for finding, event in zip(findings, events)
    )
    finding_ids = tuple(item.finding_id for item in prepared)
    event_ids = tuple((item.event.source, item.event.event_id) for item in prepared)
    if len(set(finding_ids)) != len(finding_ids) or len(set(event_ids)) != len(event_ids):
        raise PersistenceError("storage.request.invalid")
    return prepared


def _prepare_one(
    actor: ActorContext,
    finding: Mapping[str, object],
    event: PlatformEvent,
) -> PreparedAiSavingsWrite:
    try:
        document = validate_ai_savings_finding(finding)
        metadata = document["metadata"]
        spec = document["spec"]
        assert isinstance(metadata, Mapping) and isinstance(spec, Mapping)
        rule = spec["rule"]
        scope = spec["scope"]
        finding_value = spec["finding"]
        savings = spec["potentialSavings"]
        evidence = spec["evidenceRefs"]
        assert isinstance(rule, Mapping)
        assert isinstance(scope, Mapping)
        assert isinstance(finding_value, Mapping)
        assert isinstance(savings, Mapping)
        assert isinstance(evidence, list)
        baseline = scope["baselineWindow"]
        current = scope["currentWindow"]
        assert isinstance(baseline, Mapping) and isinstance(current, Mapping)
        tenant_id = metadata["tenantId"]
        finding_id = metadata["id"]
        evaluated_at = metadata["evaluatedAt"]
        provider = scope["provider"]
        model_id = scope["modelId"]
        candidate_model_id = scope.get("candidateModelId")
        service_name = scope["serviceName"]
        severity = finding_value["severity"]
        usage_ids = tuple(
            reference["id"]
            for reference in evidence
            if isinstance(reference, Mapping)
            and reference.get("type") == "ai-usage-record"
        )
        raw_cost_ids = savings.get("costRecordRefs", ())
        if not isinstance(raw_cost_ids, (list, tuple)):
            raise ValueError
        cost_ids = tuple(raw_cost_ids)
        suitability_report_ids = tuple(
            reference["id"]
            for reference in evidence
            if isinstance(reference, Mapping)
            and reference.get("type") == "ai-model-suitability-report"
        )
        digest = hashlib.sha256(
            json.dumps(
                spec,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        if (
            tenant_id != actor.tenant_id
            or not isinstance(event, PlatformEvent)
            or event.event_id != "ai-savings-" + digest
            or event.event_type != "io.iip.ai.savings-finding-recorded.v1"
            or event.source
            != f"urn:iip:ai-savings:{rule['id']}:{rule['version']}"
            or event.time != evaluated_at
            or event.subject != finding_id
            or event.tenant_id != tenant_id
            or event.correlation_id is not None
            or event.causation_id is not None
            or set(event.data) != _EVENT_DATA_KEYS
            or event.data
            != {
                "findingId": finding_id,
                "ruleId": rule["id"],
                "ruleVersion": rule["version"],
                "category": finding_value["category"],
                "severity": severity,
                "provider": provider,
                "modelId": model_id,
                "serviceName": service_name,
            }
            or not usage_ids
        ):
            raise ValueError
        hash_document = json.loads(
            json.dumps(document, ensure_ascii=False, sort_keys=True)
        )
        del hash_document["metadata"]["evaluatedAt"]
    except (
        KeyError,
        TypeError,
        ValueError,
        InvalidAiSavingsInputError,
    ):
        raise PersistenceError("storage.request.invalid") from None
    return PreparedAiSavingsWrite(
        document,
        event,
        PlatformEvent.canonical_hash(hash_document),
        str(tenant_id),
        str(finding_id),
        str(rule["id"]),
        str(rule["version"]),
        str(severity),
        str(provider),
        str(model_id),
        str(candidate_model_id) if candidate_model_id is not None else None,
        str(scope["region"]),
        str(service_name),
        str(scope["deploymentEnvironment"]),
        str(baseline["start"]),
        str(baseline["end"]),
        str(current["start"]),
        str(current["end"]),
        str(evaluated_at),
        tuple(str(item) for item in usage_ids),
        tuple(str(item) for item in cost_ids),
        tuple(str(item) for item in suitability_report_ids),
    )


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError
    return result


__all__ = [
    "PreparedAiSavingsWrite",
    "prepare_ai_savings_writes",
    "validate_ai_savings_actor",
    "validate_ai_savings_query",
]
