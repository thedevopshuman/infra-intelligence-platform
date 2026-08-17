"""Fail-closed timer processing for ambiguous governed action executions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from iip.application.ports import (
    ActionRepository,
    ActorContext,
    Clock,
    PersistenceError,
)


_ACTION_ID = re.compile(r"act_[a-f0-9]{32}")
_APPROVAL_ID = re.compile(r"apr_[a-f0-9]{32}")
_DIGEST = re.compile(r"sha256:[a-f0-9]{64}")
_WORKER_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


@dataclass(frozen=True)
class ActionReconciliationResult:
    """Bounded work completed for one explicitly enrolled tenant."""

    tenant_id: str
    scanned: int
    transitioned: int


class ActionReconciliationService:
    """Close expired execution leases without ever replaying provider impact."""

    def __init__(
        self,
        repository: ActionRepository,
        clock: Clock,
        *,
        worker_id: str,
        batch_size: int = 100,
    ) -> None:
        if not isinstance(worker_id, str) or _WORKER_ID.fullmatch(worker_id) is None:
            raise ValueError("action.reconciler.configuration.invalid")
        if isinstance(batch_size, bool) or not 1 <= batch_size <= 500:
            raise ValueError("action.reconciler.configuration.invalid")
        self._repository = repository
        self._clock = clock
        self._worker_id = worker_id
        self._batch_size = batch_size

    def run_once(self, tenant_id: str) -> ActionReconciliationResult:
        if (
            not isinstance(tenant_id, str)
            or not tenant_id
            or len(tenant_id) > 128
            or tenant_id == "*"
        ):
            raise ValueError("action.reconciler.tenant.invalid")
        actor = ActorContext(
            actor_id=f"workflow-worker:{self._worker_id}",
            tenant_id=tenant_id,
            roles=("system",),
        )
        observed_at = self._clock.now()
        candidates = tuple(
            self._repository.list_expired_action_executions(
                actor,
                observed_at,
                limit=self._batch_size,
            )
        )
        transitioned = 0
        for current in candidates:
            status = build_uncertain_execution_status(current, observed_at)
            metadata = status["metadata"]
            spec = status["spec"]
            assert isinstance(metadata, Mapping) and isinstance(spec, Mapping)
            proposal_id = str(metadata["id"])
            transition = self._repository.reconcile_expired_action_execution(
                actor,
                proposal_id,
                observed_at,
                status,
                {
                    "proposalId": proposal_id,
                    "proposalDigest": spec["proposalDigest"],
                    "observedAt": observed_at,
                    "workerActorId": actor.actor_id,
                    "reasonCode": "action.execution.lease-expired",
                },
            )
            transitioned += int(transition.transitioned)
        return ActionReconciliationResult(
            tenant_id=tenant_id,
            scanned=len(candidates),
            transitioned=transitioned,
        )


def build_uncertain_execution_status(
    current: Mapping[str, object], observed_at: str
) -> dict[str, object]:
    """Build the canonical terminal status for an expired execution lease."""

    metadata = current.get("metadata")
    spec = current.get("spec")
    policy = spec.get("policyDecision") if isinstance(spec, Mapping) else None
    if (
        current.get("apiVersion") != "iip.platform/v1alpha1"
        or current.get("kind") != "ActionExecutionStatus"
        or not isinstance(metadata, Mapping)
        or not isinstance(spec, Mapping)
        or not isinstance(metadata.get("id"), str)
        or _ACTION_ID.fullmatch(str(metadata["id"])) is None
        or not isinstance(metadata.get("tenantId"), str)
        or spec.get("state") != "executing"
        or spec.get("attempt") != 1
        or not isinstance(spec.get("proposalDigest"), str)
        or _DIGEST.fullmatch(str(spec["proposalDigest"])) is None
        or not isinstance(spec.get("approvalId"), str)
        or _APPROVAL_ID.fullmatch(str(spec["approvalId"])) is None
        or not isinstance(spec.get("executorActorId"), str)
        or not 1 <= len(str(spec["executorActorId"])) <= 256
        or not isinstance(spec.get("startedAt"), str)
        or not isinstance(policy, Mapping)
        or policy.get("allowed") is not True
        or not all(
            isinstance(policy.get(field), str)
            for field in ("reasonCode", "policySnapshotRef", "inputDigest")
        )
        or _DIGEST.fullmatch(str(policy.get("inputDigest"))) is None
    ):
        raise PersistenceError("storage.corrupt")
    lease_expires_at = spec.get("leaseExpiresAt")
    if not isinstance(lease_expires_at, str):
        raise PersistenceError("storage.corrupt")
    if _parse_time(lease_expires_at) > _parse_time(observed_at):
        raise PersistenceError("storage.conflict")
    _parse_time(str(spec["startedAt"]))
    return {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "ActionExecutionStatus",
        "metadata": {
            "id": metadata["id"],
            "tenantId": metadata["tenantId"],
            "updatedAt": observed_at,
        },
        "spec": {
            "proposalDigest": spec.get("proposalDigest"),
            "approvalId": spec.get("approvalId"),
            "state": "manual-reconciliation-required",
            "attempt": 1,
            "executorActorId": spec.get("executorActorId"),
            "startedAt": spec.get("startedAt"),
            "completedAt": observed_at,
            "summary": (
                "The execution lease expired without a terminal result. The "
                "operation will not be replayed automatically because impact is unknown."
            ),
            "policyDecision": spec.get("policyDecision"),
        },
    }


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise PersistenceError("storage.corrupt") from None
    if parsed.tzinfo is None:
        raise PersistenceError("storage.corrupt")
    return parsed
