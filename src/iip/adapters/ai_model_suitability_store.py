"""Defensive admission for immutable AI model suitability reports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

from iip.application.ports import ActorContext, PersistenceError
from iip.application.validate_ai_model_suitability import (
    InvalidAiModelSuitabilityReportError,
    validate_ai_model_suitability_report,
)
from iip.domain.models import PlatformEvent


_TENANT_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
_WORKER_ACTOR = re.compile(r"ai-savings-worker:[A-Za-z0-9._:-]{1,256}")


@dataclass(frozen=True)
class PreparedAiModelSuitabilityReport:
    document: Mapping[str, object]
    document_hash: str
    tenant_id: str
    report_id: str
    source_kind: str
    source_hash: str
    provider: str
    reference_model_id: str
    candidate_model_id: str
    region: str
    service_name: str
    deployment_environment: str
    workload_profile_id: str
    evaluated_at: str
    valid_until: str


def prepare_ai_model_suitability_report(
    actor: ActorContext,
    document: Mapping[str, object],
    *,
    allow_test_fixtures: bool = False,
) -> PreparedAiModelSuitabilityReport:
    _validate_actor(actor)
    try:
        validated = validate_ai_model_suitability_report(
            document,
            allow_test_fixtures=allow_test_fixtures,
        )
        if validated.tenant_id != actor.tenant_id:
            raise ValueError
        copied = dict(validated.document)
    except (
        TypeError,
        ValueError,
        InvalidAiModelSuitabilityReportError,
    ):
        raise PersistenceError("storage.request.invalid") from None
    return PreparedAiModelSuitabilityReport(
        copied,
        PlatformEvent.canonical_hash(copied),
        validated.tenant_id,
        validated.report_id,
        validated.source_kind,
        validated.source_hash,
        validated.provider,
        validated.reference_model_id,
        validated.candidate_model_id,
        validated.region,
        validated.service_name,
        validated.deployment_environment,
        validated.workload_profile_id,
        validated.evaluated_at.isoformat().replace("+00:00", "Z"),
        validated.valid_until.isoformat().replace("+00:00", "Z"),
    )


def _validate_actor(actor: ActorContext) -> None:
    if (
        not isinstance(actor, ActorContext)
        or not isinstance(actor.actor_id, str)
        or not _WORKER_ACTOR.fullmatch(actor.actor_id)
        or not isinstance(actor.tenant_id, str)
        or not _TENANT_ID.fullmatch(actor.tenant_id)
        or actor.roles != ("ai-savings:evaluate",)
    ):
        raise PersistenceError("storage.request.invalid")


__all__ = [
    "PreparedAiModelSuitabilityReport",
    "prepare_ai_model_suitability_report",
]
