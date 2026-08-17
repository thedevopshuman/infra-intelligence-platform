"""Thread-safe operational document adapters for the local runtime."""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
from datetime import datetime, timedelta
from threading import RLock
from typing import Mapping, Optional

from iip.application.ports import (
    ActionExecutionTransition,
    ActionWorkflowRecord,
    ActorContext,
    InvestigationCompletionSloState,
    InvestigationJobClaim,
    PersistenceError,
)


class InMemoryOperationalStore:
    """Tenant-partitioned investigations, actions, sessions, and audit records."""

    def __init__(self) -> None:
        self._investigations: dict[
            tuple[str, str],
            tuple[dict[str, object], Optional[dict[str, object]], dict[str, object]],
        ] = {}
        self._investigation_jobs: dict[tuple[str, str], dict[str, object]] = {}
        self._proposals: dict[tuple[str, str], dict[str, object]] = {}
        self._proposal_keys: dict[tuple[str, str], str] = {}
        self._approvals: dict[tuple[str, str], dict[str, object]] = {}
        self._results: dict[tuple[str, str], dict[str, object]] = {}
        self._action_executions: dict[tuple[str, str], dict[str, object]] = {}
        self._sessions: dict[tuple[str, str], dict[str, object]] = {}
        self._audit: list[tuple[str, str, dict[str, object]]] = []
        self._lock = RLock()

    def start_investigation(
        self,
        actor: ActorContext,
        investigation_id: str,
        request: Mapping[str, object],
        status: Mapping[str, object],
    ) -> None:
        self._assert_tenant(actor, request)
        self._assert_tenant(actor, status)
        key = (actor.tenant_id, investigation_id)
        value = (
            copy.deepcopy(dict(request)),
            None,
            copy.deepcopy(dict(status)),
        )
        with self._lock:
            if key in self._investigations:
                raise PersistenceError("storage.conflict")
            self._investigations[key] = value

    def commit_investigation(
        self,
        actor: ActorContext,
        investigation_id: str,
        request: Mapping[str, object],
        report: Mapping[str, object],
        status: Mapping[str, object],
    ) -> None:
        self._assert_tenant(actor, request)
        self._assert_tenant(actor, report)
        self._assert_tenant(actor, status)
        key = (actor.tenant_id, investigation_id)
        value = (
            copy.deepcopy(dict(request)),
            copy.deepcopy(dict(report)),
            copy.deepcopy(dict(status)),
        )
        with self._lock:
            current = self._investigations.get(key)
            if current is not None and current[0] != value[0]:
                raise PersistenceError("storage.conflict")
            if current is not None and current[1] is not None and current != value:
                raise PersistenceError("storage.conflict")
            current_spec = current[2].get("spec") if current is not None else None
            report_spec = report.get("spec")
            if (
                isinstance(current_spec, Mapping)
                and current_spec.get("state") == "cancellation-requested"
                and (
                    not isinstance(report_spec, Mapping)
                    or report_spec.get("outcome") != "cancelled"
                )
            ):
                raise PersistenceError("storage.conflict")
            self._investigations[key] = value

    def get_investigation(
        self, actor: ActorContext, investigation_id: str
    ) -> Optional[Mapping[str, object]]:
        with self._lock:
            value = self._investigations.get((actor.tenant_id, investigation_id))
            return (
                copy.deepcopy(value[1])
                if value is not None and value[1] is not None
                else None
            )

    def get_investigation_request(
        self, actor: ActorContext, investigation_id: str
    ) -> Optional[Mapping[str, object]]:
        with self._lock:
            value = self._investigations.get((actor.tenant_id, investigation_id))
            return copy.deepcopy(value[0]) if value is not None else None

    def get_investigation_status(
        self, actor: ActorContext, investigation_id: str
    ) -> Optional[Mapping[str, object]]:
        with self._lock:
            value = self._investigations.get((actor.tenant_id, investigation_id))
            return copy.deepcopy(value[2]) if value is not None else None

    def request_investigation_cancellation(
        self,
        actor: ActorContext,
        investigation_id: str,
        status: Mapping[str, object],
    ) -> Mapping[str, object]:
        self._assert_tenant(actor, status)
        key = (actor.tenant_id, investigation_id)
        with self._lock:
            current = self._investigations.get(key)
            if current is None:
                raise PersistenceError("storage.not-found")
            current_spec = current[2].get("spec")
            state = current_spec.get("state") if isinstance(current_spec, Mapping) else None
            if state != "running":
                return copy.deepcopy(current[2])
            updated = copy.deepcopy(dict(status))
            self._investigations[key] = (current[0], current[1], updated)
            return copy.deepcopy(updated)

    def enqueue_investigation_job(
        self,
        actor: ActorContext,
        investigation_id: str,
        request: Mapping[str, object],
        status: Mapping[str, object],
        *,
        max_outstanding_jobs_per_tenant: int = 1000,
    ) -> Mapping[str, object]:
        self._assert_tenant(actor, request)
        self._assert_tenant(actor, status)
        if (
            isinstance(max_outstanding_jobs_per_tenant, bool)
            or not isinstance(max_outstanding_jobs_per_tenant, int)
            or not 1 <= max_outstanding_jobs_per_tenant <= 100_000
        ):
            raise PersistenceError("storage.input-invalid")
        key = (actor.tenant_id, investigation_id)
        entry: dict[str, object] = {
            "actor": actor,
            "request": copy.deepcopy(dict(request)),
            "status": copy.deepcopy(dict(status)),
            "attempts": 0,
            "availableAt": status["spec"]["availableAt"],
            "workerId": None,
            "claimToken": None,
            "leaseExpiresAt": None,
        }
        with self._lock:
            current = self._investigation_jobs.get(key)
            if current is not None:
                if current["request"] != entry["request"]:
                    raise PersistenceError("storage.conflict")
                return copy.deepcopy(current["status"])
            outstanding = sum(
                1
                for (candidate_tenant, _), candidate in (
                    self._investigation_jobs.items()
                )
                if candidate_tenant == actor.tenant_id
                and self._job_is_outstanding(candidate)
            )
            if outstanding >= max_outstanding_jobs_per_tenant:
                raise PersistenceError("storage.capacity-exceeded")
            self._investigation_jobs[key] = entry
            return copy.deepcopy(entry["status"])

    @staticmethod
    def _job_is_outstanding(entry: Mapping[str, object]) -> bool:
        status = entry.get("status")
        spec = status.get("spec") if isinstance(status, Mapping) else None
        return bool(
            isinstance(spec, Mapping)
            and spec.get("state")
            in {"queued", "running", "cancellation-requested"}
        )

    def get_investigation_job(
        self, actor: ActorContext, investigation_id: str
    ) -> Optional[Mapping[str, object]]:
        with self._lock:
            entry = self._investigation_jobs.get(
                (actor.tenant_id, investigation_id)
            )
            return copy.deepcopy(entry["status"]) if entry is not None else None

    def get_investigation_completion_slo_state(
        self,
        tenant_id: str,
        *,
        window_start: str,
        window_end: str,
        maturity_cutoff: str,
        completion_objective_seconds: int,
    ) -> InvestigationCompletionSloState:
        if (
            isinstance(completion_objective_seconds, bool)
            or not isinstance(completion_objective_seconds, int)
            or not 1 <= completion_objective_seconds <= 86_400
        ):
            raise ValueError("completion_objective_seconds is invalid")
        try:
            start = datetime.fromisoformat(window_start.replace("Z", "+00:00"))
            end = datetime.fromisoformat(window_end.replace("Z", "+00:00"))
            cutoff = datetime.fromisoformat(maturity_cutoff.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            raise ValueError("investigation completion SLO window is invalid") from None
        if (
            start.tzinfo is None
            or end.tzinfo is None
            or cutoff.tzinfo is None
            or not start < cutoff < end
            or int((end - cutoff).total_seconds()) != completion_objective_seconds
        ):
            raise ValueError("investigation completion SLO window is invalid")

        with self._lock:
            cohort: list[tuple[Mapping[str, object], datetime]] = []
            for (candidate_tenant, _), entry in self._investigation_jobs.items():
                if candidate_tenant != tenant_id:
                    continue
                status = entry.get("status")
                spec = status.get("spec") if isinstance(status, Mapping) else None
                if not isinstance(spec, Mapping):
                    raise PersistenceError("storage.corrupt")
                try:
                    queued_at = datetime.fromisoformat(
                        str(spec["queuedAt"]).replace("Z", "+00:00")
                    )
                except (KeyError, ValueError):
                    raise PersistenceError("storage.corrupt") from None
                if start <= queued_at <= end:
                    cohort.append((spec, queued_at))
            eligible = [item for item in cohort if item[1] <= cutoff]
            within = late = failed = cancelled = unfinished = 0
            for spec, queued_at in eligible:
                state = spec.get("state")
                if state in {"completed", "failed", "cancelled"}:
                    try:
                        completed_at = datetime.fromisoformat(
                            str(spec["completedAt"]).replace("Z", "+00:00")
                        )
                    except (KeyError, ValueError):
                        raise PersistenceError("storage.corrupt") from None
                    if completed_at < queued_at:
                        raise PersistenceError("storage.corrupt")
                    if completed_at > end:
                        unfinished += 1
                        continue
                if state == "completed":
                    if completed_at <= queued_at + timedelta(
                        seconds=completion_objective_seconds
                    ):
                        within += 1
                    else:
                        late += 1
                elif state == "failed":
                    failed += 1
                elif state == "cancelled":
                    cancelled += 1
                elif state in {"queued", "running", "cancellation-requested"}:
                    unfinished += 1
                else:
                    raise PersistenceError("storage.corrupt")

        return InvestigationCompletionSloState(
            tenant_id=tenant_id,
            window_start=window_start,
            window_end=window_end,
            maturity_cutoff=maturity_cutoff,
            accepted_jobs=len(cohort),
            immature_jobs=len(cohort) - len(eligible),
            eligible_jobs=len(eligible),
            within_objective_jobs=within,
            late_completed_jobs=late,
            failed_jobs=failed,
            cancelled_jobs=cancelled,
            unfinished_jobs=unfinished,
        )

    def claim_investigation_job(
        self,
        tenant_id: str,
        worker_id: str,
        now: str,
        lease_expires_at: str,
        *,
        max_tenant_concurrency: int = 1,
    ) -> Optional[InvestigationJobClaim]:
        if (
            isinstance(max_tenant_concurrency, bool)
            or not isinstance(max_tenant_concurrency, int)
            or not 1 <= max_tenant_concurrency <= 64
        ):
            raise PersistenceError("storage.input-invalid")
        with self._lock:
            active = sum(
                1
                for (candidate_tenant, _), entry in self._investigation_jobs.items()
                if candidate_tenant == tenant_id
                and self._job_has_live_lease(entry, now)
            )
            if active >= max_tenant_concurrency:
                return None
            candidates = []
            for (candidate_tenant, investigation_id), entry in self._investigation_jobs.items():
                if candidate_tenant != tenant_id:
                    continue
                status = entry["status"]
                spec = status.get("spec") if isinstance(status, Mapping) else None
                state = spec.get("state") if isinstance(spec, Mapping) else None
                ready = state == "queued" and str(entry["availableAt"]) <= now
                abandoned = state in {"running", "cancellation-requested"} and (
                    entry["leaseExpiresAt"] is not None
                    and str(entry["leaseExpiresAt"]) <= now
                )
                if ready or abandoned:
                    candidates.append((str(spec.get("queuedAt")), investigation_id, entry))
            if not candidates:
                return None
            _, investigation_id, entry = sorted(candidates, key=lambda item: item[:2])[0]
            attempts = int(entry["attempts"]) + 1
            claim_token = secrets.token_hex(32)
            status = copy.deepcopy(entry["status"])
            metadata = status["metadata"]
            spec = status["spec"]
            metadata["updatedAt"] = now
            if spec.get("state") == "queued":
                spec["state"] = "running"
                spec["startedAt"] = now
                spec.pop("availableAt", None)
            spec["attempts"] = attempts
            spec["heartbeatAt"] = now
            spec["leaseExpiresAt"] = lease_expires_at
            entry.update(
                {
                    "status": status,
                    "attempts": attempts,
                    "workerId": worker_id,
                    "claimToken": claim_token,
                    "leaseExpiresAt": lease_expires_at,
                }
            )
            actor = entry["actor"]
            assert isinstance(actor, ActorContext)
            return InvestigationJobClaim(
                actor=actor,
                investigation_id=investigation_id,
                request=copy.deepcopy(entry["request"]),
                claim_token=claim_token,
                attempts=attempts,
            )

    @staticmethod
    def _job_has_live_lease(entry: Mapping[str, object], now: str) -> bool:
        status = entry.get("status")
        spec = status.get("spec") if isinstance(status, Mapping) else None
        return bool(
            isinstance(spec, Mapping)
            and spec.get("state") in {"running", "cancellation-requested"}
            and entry.get("leaseExpiresAt") is not None
            and str(entry["leaseExpiresAt"]) > now
        )

    def heartbeat_investigation_job(
        self,
        tenant_id: str,
        investigation_id: str,
        worker_id: str,
        claim_token: str,
        status: Mapping[str, object],
    ) -> bool:
        with self._lock:
            entry = self._claimed_job(
                tenant_id, investigation_id, worker_id, claim_token
            )
            if entry is None:
                return False
            current_spec = entry["status"].get("spec")
            new_spec = status.get("spec")
            if not isinstance(current_spec, Mapping) or current_spec.get("state") not in {
                "running",
                "cancellation-requested",
            } or not isinstance(new_spec, Mapping) or new_spec.get("state") != current_spec.get("state"):
                return False
            entry["status"] = copy.deepcopy(dict(status))
            entry["leaseExpiresAt"] = (
                new_spec.get("leaseExpiresAt")
                if isinstance(new_spec, Mapping)
                else None
            )
            return True

    def release_investigation_job(
        self,
        tenant_id: str,
        investigation_id: str,
        worker_id: str,
        claim_token: str,
        status: Mapping[str, object],
        available_at: str,
        error_code: str,
    ) -> bool:
        del error_code
        with self._lock:
            entry = self._claimed_job(
                tenant_id, investigation_id, worker_id, claim_token
            )
            if entry is None:
                return False
            current_spec = entry["status"].get("spec")
            terminal_spec = status.get("spec")
            if (
                isinstance(current_spec, Mapping)
                and current_spec.get("state") == "cancellation-requested"
                and (
                    not isinstance(terminal_spec, Mapping)
                    or terminal_spec.get("state") != "cancelled"
                )
            ):
                return False
            entry.update(
                {
                    "status": copy.deepcopy(dict(status)),
                    "availableAt": available_at,
                    "workerId": None,
                    "claimToken": None,
                    "leaseExpiresAt": None,
                }
            )
            return True

    def finish_investigation_job(
        self,
        tenant_id: str,
        investigation_id: str,
        worker_id: str,
        claim_token: str,
        status: Mapping[str, object],
    ) -> bool:
        with self._lock:
            entry = self._claimed_job(
                tenant_id, investigation_id, worker_id, claim_token
            )
            if entry is None:
                return False
            current_spec = entry["status"].get("spec")
            terminal_spec = status.get("spec")
            if (
                isinstance(current_spec, Mapping)
                and current_spec.get("state") == "cancellation-requested"
                and (
                    not isinstance(terminal_spec, Mapping)
                    or terminal_spec.get("state") != "cancelled"
                )
            ):
                return False
            entry.update(
                {
                    "status": copy.deepcopy(dict(status)),
                    "workerId": None,
                    "claimToken": None,
                    "leaseExpiresAt": None,
                }
            )
            return True

    def request_investigation_job_cancellation(
        self,
        actor: ActorContext,
        investigation_id: str,
        status: Mapping[str, object],
    ) -> Mapping[str, object]:
        self._assert_tenant(actor, status)
        with self._lock:
            entry = self._investigation_jobs.get(
                (actor.tenant_id, investigation_id)
            )
            if entry is None:
                raise PersistenceError("storage.not-found")
            current_spec = entry["status"].get("spec")
            state = current_spec.get("state") if isinstance(current_spec, Mapping) else None
            if state in {"completed", "failed", "cancelled"}:
                return copy.deepcopy(entry["status"])
            entry["status"] = copy.deepcopy(dict(status))
            if status["spec"]["state"] == "cancelled":
                entry.update(
                    {"workerId": None, "claimToken": None, "leaseExpiresAt": None}
                )
            return copy.deepcopy(entry["status"])

    def _claimed_job(
        self,
        tenant_id: str,
        investigation_id: str,
        worker_id: str,
        claim_token: str,
    ) -> dict[str, object] | None:
        entry = self._investigation_jobs.get((tenant_id, investigation_id))
        if (
            entry is None
            or entry["workerId"] != worker_id
            or entry["claimToken"] != claim_token
        ):
            return None
        return entry

    def get_proposal_by_key(
        self, actor: ActorContext, idempotency_key: str
    ) -> Optional[Mapping[str, object]]:
        with self._lock:
            proposal_id = self._proposal_keys.get((actor.tenant_id, idempotency_key))
            value = self._proposals.get((actor.tenant_id, proposal_id or ""))
            return copy.deepcopy(value) if value is not None else None

    def get_proposal(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[Mapping[str, object]]:
        with self._lock:
            value = self._proposals.get((actor.tenant_id, proposal_id))
            return copy.deepcopy(value) if value is not None else None

    def get_action_workflow(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[ActionWorkflowRecord]:
        key = (actor.tenant_id, proposal_id)
        with self._lock:
            proposal = self._proposals.get(key)
            if proposal is None:
                return None
            return ActionWorkflowRecord(
                proposal=copy.deepcopy(proposal),
                approval=copy.deepcopy(self._approvals.get(key)),
                execution_status=copy.deepcopy(self._action_executions.get(key)),
                result=copy.deepcopy(self._results.get(key)),
            )

    def list_action_workflows(
        self,
        actor: ActorContext,
        *,
        before_created_at: Optional[str],
        before_proposal_id: Optional[str],
        limit: int,
    ) -> tuple[ActionWorkflowRecord, ...]:
        if limit < 1 or limit > 101:
            raise PersistenceError("storage.input-invalid")
        if (before_created_at is None) != (before_proposal_id is None):
            raise PersistenceError("storage.input-invalid")
        before = (
            self._action_position(before_created_at, before_proposal_id)
            if before_created_at is not None and before_proposal_id is not None
            else None
        )
        with self._lock:
            candidates = []
            for key, proposal in self._proposals.items():
                if key[0] != actor.tenant_id:
                    continue
                metadata = proposal.get("metadata")
                if not isinstance(metadata, Mapping):
                    raise PersistenceError("storage.corrupt")
                position = self._action_position(
                    metadata.get("createdAt"), metadata.get("id")
                )
                if before is None or position < before:
                    candidates.append((position, key, proposal))
            candidates.sort(key=lambda item: item[0], reverse=True)
            return tuple(
                ActionWorkflowRecord(
                    proposal=copy.deepcopy(proposal),
                    approval=copy.deepcopy(self._approvals.get(key)),
                    execution_status=copy.deepcopy(self._action_executions.get(key)),
                    result=copy.deepcopy(self._results.get(key)),
                )
                for _, key, proposal in candidates[:limit]
            )

    def commit_proposal(
        self, actor: ActorContext, document: Mapping[str, object]
    ) -> None:
        self._assert_tenant(actor, document)
        metadata = document["metadata"]
        spec = document["spec"]
        assert isinstance(metadata, Mapping) and isinstance(spec, Mapping)
        key = (actor.tenant_id, str(metadata["id"]))
        idempotency = (actor.tenant_id, str(spec["idempotencyKey"]))
        value = copy.deepcopy(dict(document))
        with self._lock:
            existing_id = self._proposal_keys.get(idempotency)
            if existing_id is not None and existing_id != key[1]:
                raise PersistenceError("storage.conflict")
            current = self._proposals.get(key)
            if current is not None and current != value:
                raise PersistenceError("storage.conflict")
            self._proposals[key] = value
            self._proposal_keys[idempotency] = key[1]

    def get_approval(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[Mapping[str, object]]:
        with self._lock:
            value = self._approvals.get((actor.tenant_id, proposal_id))
            return copy.deepcopy(value) if value is not None else None

    def commit_approval(
        self, actor: ActorContext, document: Mapping[str, object]
    ) -> None:
        self._assert_tenant(actor, document)
        spec = document["spec"]
        assert isinstance(spec, Mapping)
        key = (actor.tenant_id, str(spec["proposalId"]))
        value = copy.deepcopy(dict(document))
        with self._lock:
            current = self._approvals.get(key)
            if current is not None and current != value:
                raise PersistenceError("storage.conflict")
            self._approvals[key] = value

    def get_action_result(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[Mapping[str, object]]:
        with self._lock:
            value = self._results.get((actor.tenant_id, proposal_id))
            return copy.deepcopy(value) if value is not None else None

    def claim_action_execution(
        self, actor: ActorContext, document: Mapping[str, object]
    ) -> bool:
        self._assert_tenant(actor, document)
        metadata = document.get("metadata")
        spec = document.get("spec")
        if (
            not isinstance(metadata, Mapping)
            or not isinstance(spec, Mapping)
            or spec.get("state") != "executing"
        ):
            raise PersistenceError("storage.input-invalid")
        key = (actor.tenant_id, str(metadata.get("id")))
        value = copy.deepcopy(dict(document))
        with self._lock:
            if key in self._action_executions:
                return False
            self._action_executions[key] = value
            return True

    def get_action_execution_status(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[Mapping[str, object]]:
        with self._lock:
            value = self._action_executions.get((actor.tenant_id, proposal_id))
            return copy.deepcopy(value) if value is not None else None

    def list_expired_action_executions(
        self,
        actor: ActorContext,
        observed_at: str,
        *,
        limit: int,
    ) -> tuple[Mapping[str, object], ...]:
        if isinstance(limit, bool) or not 1 <= limit <= 500:
            raise PersistenceError("storage.input-invalid")
        observed = self._parse_time(observed_at)
        with self._lock:
            candidates: list[tuple[datetime, str, dict[str, object]]] = []
            for (tenant_id, proposal_id), document in self._action_executions.items():
                if tenant_id != actor.tenant_id:
                    continue
                spec = document.get("spec")
                if not isinstance(spec, Mapping):
                    raise PersistenceError("storage.corrupt")
                if spec.get("state") != "executing":
                    continue
                lease = spec.get("leaseExpiresAt")
                if not isinstance(lease, str):
                    raise PersistenceError("storage.corrupt")
                parsed_lease = self._parse_time(lease)
                if parsed_lease <= observed:
                    candidates.append((parsed_lease, proposal_id, document))
            candidates.sort(key=lambda item: (item[0], item[1]))
            return tuple(
                copy.deepcopy(document)
                for _, _, document in candidates[:limit]
            )

    def reconcile_expired_action_execution(
        self,
        actor: ActorContext,
        proposal_id: str,
        observed_at: str,
        document: Mapping[str, object],
        audit_document: Mapping[str, object],
    ) -> ActionExecutionTransition:
        self._assert_tenant(actor, document)
        key = (actor.tenant_id, proposal_id)
        with self._lock:
            current = self._action_executions.get(key)
            if current is None:
                raise PersistenceError("storage.not-found")
            current_spec = current.get("spec")
            if not isinstance(current_spec, Mapping):
                raise PersistenceError("storage.input-invalid")
            if (
                current_spec.get("state") == "executing"
                and self._parse_time(str(current_spec.get("leaseExpiresAt")))
                <= self._parse_time(observed_at)
            ):
                self._action_executions[key] = copy.deepcopy(dict(document))
                self._audit.append(
                    (
                        actor.tenant_id,
                        "action-execution-reconciliation-required",
                        copy.deepcopy(dict(audit_document)),
                    )
                )
                return ActionExecutionTransition(
                    status=copy.deepcopy(self._action_executions[key]),
                    transitioned=True,
                )
            return ActionExecutionTransition(
                status=copy.deepcopy(self._action_executions[key]),
                transitioned=False,
            )

    def commit_action_result(
        self,
        actor: ActorContext,
        document: Mapping[str, object],
        status: Mapping[str, object],
    ) -> None:
        self._assert_tenant(actor, document)
        self._assert_tenant(actor, status)
        metadata = document["metadata"]
        status_metadata = status.get("metadata")
        status_spec = status.get("spec")
        assert isinstance(metadata, Mapping) and isinstance(status_metadata, Mapping)
        key = (actor.tenant_id, str(metadata["id"]))
        if (
            status_metadata.get("id") != key[1]
            or not isinstance(status_spec, Mapping)
            or status_spec.get("state") == "executing"
        ):
            raise PersistenceError("storage.input-invalid")
        value = copy.deepcopy(dict(document))
        status_value = copy.deepcopy(dict(status))
        with self._lock:
            current = self._results.get(key)
            current_status = self._action_executions.get(key)
            if current is not None:
                if current != value or current_status != status_value:
                    raise PersistenceError("storage.conflict")
                return
            current_spec = (
                current_status.get("spec")
                if isinstance(current_status, Mapping)
                else None
            )
            if not isinstance(current_spec, Mapping) or current_spec.get("state") != "executing":
                raise PersistenceError("storage.conflict")
            self._results[key] = value
            self._action_executions[key] = status_value

    @staticmethod
    def _parse_time(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise PersistenceError("storage.input-invalid") from None
        if parsed.tzinfo is None:
            raise PersistenceError("storage.input-invalid")
        return parsed

    @staticmethod
    def _action_position(created_at: object, proposal_id: object) -> tuple[datetime, str]:
        if not isinstance(created_at, str) or not isinstance(proposal_id, str):
            raise PersistenceError("storage.corrupt")
        try:
            parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            raise PersistenceError("storage.corrupt") from None
        if parsed.tzinfo is None:
            raise PersistenceError("storage.corrupt")
        return parsed, proposal_id

    def commit_plugin_session(
        self, actor: ActorContext, document: Mapping[str, object]
    ) -> None:
        self._assert_tenant(actor, document)
        metadata = document["metadata"]
        assert isinstance(metadata, Mapping)
        key = (actor.tenant_id, str(metadata["id"]))
        with self._lock:
            if key in self._sessions:
                raise PersistenceError("storage.conflict")
            self._sessions[key] = copy.deepcopy(dict(document))

    def get_plugin_session(
        self, actor: ActorContext, session_id: str
    ) -> Optional[Mapping[str, object]]:
        with self._lock:
            value = self._sessions.get((actor.tenant_id, session_id))
            return copy.deepcopy(value) if value is not None else None

    def append_audit(
        self,
        actor: ActorContext,
        category: str,
        document: Mapping[str, object],
    ) -> str:
        encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(encoded).hexdigest()[:32]
        reference = f"audit://{actor.tenant_id}/{category}/{digest}"
        with self._lock:
            self._audit.append((actor.tenant_id, category, copy.deepcopy(dict(document))))
        return reference

    @staticmethod
    def _assert_tenant(actor: ActorContext, document: Mapping[str, object]) -> None:
        metadata = document.get("metadata")
        if not isinstance(metadata, Mapping) or metadata.get("tenantId") != actor.tenant_id:
            raise PersistenceError("storage.input-invalid")
