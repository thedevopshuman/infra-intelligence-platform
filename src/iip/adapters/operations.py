"""Thread-safe operational document adapters for the local runtime."""

from __future__ import annotations

import copy
import hashlib
import json
import secrets
from datetime import datetime, timedelta
from threading import RLock
from typing import Mapping, Optional

from iip.application.investigate import canonical_digest
from iip.application.ports import (
    ActionExecutionTransition,
    ActionWorkflowRecord,
    ActorContext,
    InvestigationCompletionSloState,
    InvestigationJobClaim,
    PersistenceError,
    PluginInvocationClaim,
    TelemetryExportInstanceState,
    TelemetryExportSignalState,
    TelemetryExportSloSignalState,
    TelemetryExportSloState,
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
        self._plugin_invocations: dict[tuple[str, str], dict[str, object]] = {}
        self._telemetry_export_health: dict[str, TelemetryExportInstanceState] = {}
        self._telemetry_export_health_samples: dict[
            tuple[str, str], TelemetryExportInstanceState
        ] = {}
        self._audit: list[tuple[str, str, dict[str, object]]] = []
        self._lock = RLock()

    def record_telemetry_export_health(
        self,
        state: TelemetryExportInstanceState,
        *,
        expire_before: str,
        sample_expire_before: str,
    ) -> None:
        with self._lock:
            self._telemetry_export_health = {
                instance_id: item
                for instance_id, item in self._telemetry_export_health.items()
                if item.last_reported_at >= expire_before
            }
            self._telemetry_export_health[state.instance_id] = copy.deepcopy(state)
            self._telemetry_export_health_samples = {
                key: item
                for key, item in self._telemetry_export_health_samples.items()
                if item.last_reported_at >= sample_expire_before
            }
            self._telemetry_export_health_samples.setdefault(
                (state.instance_id, state.last_reported_at), copy.deepcopy(state)
            )

    def list_telemetry_export_health(
        self,
        *,
        reported_since: str,
        limit: int,
    ) -> tuple[TelemetryExportInstanceState, ...]:
        if not 1 <= limit <= 1_001:
            raise PersistenceError("storage.input-invalid")
        with self._lock:
            items = tuple(
                sorted(
                    (
                        copy.deepcopy(item)
                        for item in self._telemetry_export_health.values()
                        if item.last_reported_at >= reported_since
                    ),
                    key=lambda item: (item.component, item.instance_id),
                )[:limit]
            )
        return items

    def retire_telemetry_export_health(self, instance_id: str) -> None:
        with self._lock:
            self._telemetry_export_health.pop(instance_id, None)

    def get_telemetry_export_slo_state(
        self,
        *,
        window_start: str,
        window_end: str,
    ) -> TelemetryExportSloState:
        try:
            start = datetime.fromisoformat(window_start.replace("Z", "+00:00"))
            end = datetime.fromisoformat(window_end.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            raise PersistenceError("storage.input-invalid") from None
        if start.tzinfo is None or end.tzinfo is None or start >= end:
            raise PersistenceError("storage.input-invalid")

        with self._lock:
            all_samples = tuple(self._telemetry_export_health_samples.values())
        grouped: dict[str, list[TelemetryExportInstanceState]] = {}
        for sample in all_samples:
            observed_at = datetime.fromisoformat(
                sample.last_reported_at.replace("Z", "+00:00")
            )
            if observed_at <= end:
                grouped.setdefault(sample.instance_id, []).append(sample)

        selected: list[TelemetryExportInstanceState] = []
        for samples in grouped.values():
            ordered = sorted(samples, key=lambda item: item.last_reported_at)
            before = [item for item in ordered if item.last_reported_at < window_start]
            if before:
                selected.append(before[-1])
            selected.extend(
                item
                for item in ordered
                if window_start <= item.last_reported_at <= window_end
            )
        return self._aggregate_telemetry_export_samples(
            tuple(selected), window_start=window_start, window_end=window_end
        )

    @staticmethod
    def _aggregate_telemetry_export_samples(
        samples: tuple[TelemetryExportInstanceState, ...],
        *,
        window_start: str,
        window_end: str,
    ) -> TelemetryExportSloState:
        counters = {
            signal: {
                "enabled": 0,
                "attempts": 0,
                "successes": 0,
                "failures": 0,
            }
            for signal in ("metrics", "traces")
        }
        in_window = tuple(
            item
            for item in samples
            if window_start <= item.last_reported_at <= window_end
        )
        by_instance: dict[str, list[TelemetryExportInstanceState]] = {}
        for item in samples:
            by_instance.setdefault(item.instance_id, []).append(item)
        for instance_samples in by_instance.values():
            previous: dict[str, TelemetryExportSignalState] = {}
            for item in sorted(
                instance_samples, key=lambda sample: sample.last_reported_at
            ):
                by_signal = {signal.signal: signal for signal in item.signals}
                if set(by_signal) != {"metrics", "traces"}:
                    raise PersistenceError("storage.corrupt")
                in_current_window = window_start <= item.last_reported_at <= window_end
                for signal_name, signal in by_signal.items():
                    prior = previous.get(signal_name)
                    if in_current_window:
                        counters[signal_name]["enabled"] += int(signal.enabled)
                        if prior is not None:
                            attempts = signal.attempts - prior.attempts
                            successes = signal.successes - prior.successes
                            failures = signal.failures - prior.failures
                        elif item.started_at >= window_start:
                            attempts = signal.attempts
                            successes = signal.successes
                            failures = signal.failures
                        else:
                            attempts = successes = failures = 0
                        if (
                            min(attempts, successes, failures) < 0
                            or attempts != successes + failures
                        ):
                            raise PersistenceError("storage.corrupt")
                        counters[signal_name]["attempts"] += attempts
                        counters[signal_name]["successes"] += successes
                        counters[signal_name]["failures"] += failures
                    previous[signal_name] = signal

        return TelemetryExportSloState(
            window_start=window_start,
            window_end=window_end,
            observed_instances=len({item.instance_id for item in in_window}),
            observed_samples=len(in_window),
            signals=tuple(
                TelemetryExportSloSignalState(
                    signal=signal,
                    enabled_observations=values["enabled"],
                    eligible_attempts=values["attempts"],
                    successful_attempts=values["successes"],
                    failed_attempts=values["failures"],
                )
                for signal, values in counters.items()
            ),
        )

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

    def claim_plugin_invocation(
        self,
        actor: ActorContext,
        session: Mapping[str, object],
        invocation: Mapping[str, object],
        request_digest: str,
        claimed_at: str,
    ) -> PluginInvocationClaim:
        self._assert_tenant(actor, session)
        self._assert_tenant(actor, invocation)
        session_metadata = session.get("metadata")
        session_spec = session.get("spec")
        invocation_metadata = invocation.get("metadata")
        limits = session_spec.get("limits") if isinstance(session_spec, Mapping) else None
        if (
            not isinstance(session_metadata, Mapping)
            or not isinstance(invocation_metadata, Mapping)
            or not isinstance(limits, Mapping)
            or invocation_metadata.get("sessionId") != session_metadata.get("id")
            or canonical_digest(invocation) != request_digest
            or isinstance(limits.get("maxRequests"), bool)
            or not isinstance(limits.get("maxRequests"), int)
        ):
            raise PersistenceError("storage.input-invalid")
        session_id = str(session_metadata["id"])
        request_id = str(invocation_metadata["id"])
        key = (actor.tenant_id, request_id)
        with self._lock:
            stored_session = self._sessions.get((actor.tenant_id, session_id))
            if stored_session is None:
                raise PersistenceError("storage.not-found")
            if stored_session != dict(session):
                raise PersistenceError("storage.conflict")
            existing = self._plugin_invocations.get(key)
            if existing is not None:
                if (
                    existing["sessionId"] != session_id
                    or existing["requestDigest"] != request_digest
                ):
                    raise PersistenceError("storage.conflict")
                result = existing.get("result")
                if isinstance(result, Mapping):
                    return PluginInvocationClaim("completed", copy.deepcopy(result))
                status = existing.get("status")
                status_spec = status.get("spec") if isinstance(status, Mapping) else None
                if (
                    isinstance(status_spec, Mapping)
                    and status_spec.get("state") == "cancellation-requested"
                ):
                    return PluginInvocationClaim("cancellation-requested")
                return PluginInvocationClaim("in-progress")
            created = self._parse_time(str(invocation_metadata.get("createdAt")))
            deadline = self._parse_time(str(invocation_metadata.get("deadline")))
            expires = self._parse_time(str(session_spec.get("expiresAt")))
            claim_time = self._parse_time(claimed_at)
            if not created <= claim_time <= deadline <= expires:
                raise PersistenceError("plugin.request.expired")
            count = sum(
                1
                for (tenant_id, _), record in self._plugin_invocations.items()
                if tenant_id == actor.tenant_id and record["sessionId"] == session_id
            )
            if count >= limits["maxRequests"]:
                raise PersistenceError("plugin.request.limit-exceeded")
            status = self._plugin_claim_status(
                actor, session_metadata, invocation_metadata, request_digest, claimed_at
            )
            self._plugin_invocations[key] = {
                "sessionId": session_id,
                "requestDigest": request_digest,
                "invocation": copy.deepcopy(dict(invocation)),
                "status": status,
                "result": None,
            }
            return PluginInvocationClaim("claimed")

    def commit_plugin_invocation_result(
        self,
        actor: ActorContext,
        request_digest: str,
        result: Mapping[str, object],
    ) -> None:
        self._assert_tenant(actor, result)
        metadata = result.get("metadata")
        spec = result.get("spec")
        if not isinstance(metadata, Mapping) or not isinstance(spec, Mapping):
            raise PersistenceError("storage.input-invalid")
        key = (actor.tenant_id, str(metadata.get("id")))
        with self._lock:
            record = self._plugin_invocations.get(key)
            if record is None:
                raise PersistenceError("storage.not-found")
            if (
                record["requestDigest"] != request_digest
                or record["sessionId"] != metadata.get("sessionId")
            ):
                raise PersistenceError("storage.conflict")
            current = record.get("result")
            value = copy.deepcopy(dict(result))
            if isinstance(current, Mapping):
                if dict(current) != value:
                    raise PersistenceError("storage.conflict")
                return
            status = record.get("status")
            status_spec = status.get("spec") if isinstance(status, Mapping) else None
            if (
                isinstance(status_spec, Mapping)
                and status_spec.get("state") == "cancellation-requested"
                and spec.get("status") != "cancelled"
            ):
                raise PersistenceError("plugin.request.cancellation-pending")
            record["result"] = value
            record["status"] = self._terminal_plugin_status(status, value)

    def get_plugin_invocation_status(
        self, actor: ActorContext, request_id: str
    ) -> Optional[Mapping[str, object]]:
        with self._lock:
            record = self._plugin_invocations.get((actor.tenant_id, request_id))
            status = record.get("status") if record is not None else None
            return copy.deepcopy(status) if isinstance(status, Mapping) else None

    def request_plugin_invocation_cancellation(
        self,
        actor: ActorContext,
        request_id: str,
        status: Mapping[str, object],
        audit_document: Mapping[str, object],
    ) -> Mapping[str, object]:
        self._assert_tenant(actor, status)
        self._assert_tenant(actor, audit_document)
        key = (actor.tenant_id, request_id)
        with self._lock:
            record = self._plugin_invocations.get(key)
            if record is None:
                raise PersistenceError("storage.not-found")
            current = record.get("status")
            current_spec = current.get("spec") if isinstance(current, Mapping) else None
            if not isinstance(current_spec, Mapping):
                raise PersistenceError("storage.corrupt")
            if current_spec.get("state") != "claimed":
                return copy.deepcopy(current)
            value = copy.deepcopy(dict(status))
            record["status"] = value
            self._audit.append(
                (actor.tenant_id, "plugin-invocation-cancellation", copy.deepcopy(dict(audit_document)))
            )
            return copy.deepcopy(value)

    def plugin_invocation_cancellation_requested(
        self, actor: ActorContext, request_id: str, request_digest: str
    ) -> bool:
        with self._lock:
            record = self._plugin_invocations.get((actor.tenant_id, request_id))
            if record is None or record.get("requestDigest") != request_digest:
                raise PersistenceError("storage.not-found")
            status = record.get("status")
            spec = status.get("spec") if isinstance(status, Mapping) else None
            if not isinstance(spec, Mapping):
                raise PersistenceError("storage.corrupt")
            return spec.get("state") == "cancellation-requested"

    def reconcile_plugin_invocation(
        self,
        actor: ActorContext,
        request_id: str,
        observed_at: str,
        result: Mapping[str, object],
        status: Mapping[str, object],
        audit_document: Mapping[str, object],
    ) -> Mapping[str, object]:
        self._assert_tenant(actor, result)
        self._assert_tenant(actor, status)
        self._assert_tenant(actor, audit_document)
        key = (actor.tenant_id, request_id)
        with self._lock:
            record = self._plugin_invocations.get(key)
            if record is None:
                raise PersistenceError("storage.not-found")
            current = record.get("status")
            current_spec = current.get("spec") if isinstance(current, Mapping) else None
            if not isinstance(current_spec, Mapping):
                raise PersistenceError("storage.corrupt")
            if current_spec.get("state") in {"succeeded", "failed", "cancelled"}:
                return copy.deepcopy(current)
            result_spec = result.get("spec")
            status_spec = status.get("spec")
            expected_state = (
                "cancelled"
                if current_spec.get("state") == "cancellation-requested"
                else "failed"
            )
            if (
                not isinstance(result_spec, Mapping)
                or not isinstance(status_spec, Mapping)
                or result_spec.get("status") != expected_state
                or status_spec.get("state") != expected_state
            ):
                raise PersistenceError("plugin.request.cancellation-pending")
            if self._parse_time(observed_at) < self._parse_time(str(current_spec["deadline"])):
                raise PersistenceError("plugin.reconciliation.deadline-live")
            record["result"] = copy.deepcopy(dict(result))
            record["status"] = copy.deepcopy(dict(status))
            self._audit.append(
                (actor.tenant_id, "plugin-invocation-reconciliation", copy.deepcopy(dict(audit_document)))
            )
            return copy.deepcopy(record["status"])

    @staticmethod
    def _plugin_claim_status(
        actor: ActorContext,
        session_metadata: Mapping[str, object],
        invocation_metadata: Mapping[str, object],
        request_digest: str,
        claimed_at: str,
    ) -> dict[str, object]:
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "PluginInvocationStatus",
            "metadata": {
                "id": invocation_metadata["id"],
                "sessionId": session_metadata["id"],
                "tenantId": actor.tenant_id,
                "pluginId": session_metadata["pluginId"],
                "pluginVersion": session_metadata["pluginVersion"],
                "updatedAt": claimed_at,
            },
            "spec": {
                "requestDigest": request_digest,
                "state": "claimed",
                "claimedAt": claimed_at,
                "deadline": invocation_metadata["deadline"],
            },
        }

    @staticmethod
    def _terminal_plugin_status(
        current: object, result: Mapping[str, object]
    ) -> dict[str, object]:
        if not isinstance(current, Mapping):
            raise PersistenceError("storage.corrupt")
        current_metadata = current.get("metadata")
        current_spec = current.get("spec")
        result_metadata = result.get("metadata")
        result_spec = result.get("spec")
        if not all(
            isinstance(value, Mapping)
            for value in (current_metadata, current_spec, result_metadata, result_spec)
        ):
            raise PersistenceError("storage.corrupt")
        status = str(result_spec["status"])
        terminal_spec = {
            **dict(current_spec),
            "state": status,
            "completedAt": result_metadata["completedAt"],
            "resultRef": (
                f"plugin-result://{current_metadata['tenantId']}/sessions/"
                f"{current_metadata['sessionId']}/invocations/{current_metadata['id']}"
            ),
        }
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "PluginInvocationStatus",
            "metadata": {
                **dict(current_metadata),
                "updatedAt": result_metadata["completedAt"],
            },
            "spec": terminal_spec,
        }

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
