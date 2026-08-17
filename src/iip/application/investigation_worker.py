"""Lease-driven background execution for durable investigation jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Event, Thread
from typing import Mapping

from iip.application.investigate import (
    DeterministicInvestigationService,
    InvestigationConflictError,
    InvestigationInProgressError,
    InvalidInvestigationError,
    RunInvestigationCommand,
)
from iip.application.investigation_dispatch import InvestigationDispatchService
from iip.application.ports import Clock, InvestigationJobClaim, InvestigationJobRepository


@dataclass(frozen=True)
class InvestigationWorkerResult:
    investigation_id: str
    disposition: str


class InvestigationWorker:
    """Run at most one claimed job for one explicitly configured tenant."""

    def __init__(
        self,
        repository: InvestigationJobRepository,
        investigations: DeterministicInvestigationService,
        clock: Clock,
        *,
        worker_id: str,
        lease_seconds: int = 30,
        heartbeat_seconds: int = 10,
        retry_seconds: int = 5,
        max_attempts: int = 8,
        max_tenant_concurrency: int = 1,
    ) -> None:
        if not worker_id or len(worker_id) > 128:
            raise ValueError("investigation.worker.configuration.invalid")
        if not 10 <= lease_seconds <= 300:
            raise ValueError("investigation.worker.configuration.invalid")
        if not 0 <= heartbeat_seconds < lease_seconds:
            raise ValueError("investigation.worker.configuration.invalid")
        if not 1 <= retry_seconds <= 300 or not 1 <= max_attempts <= 20:
            raise ValueError("investigation.worker.configuration.invalid")
        if (
            isinstance(max_tenant_concurrency, bool)
            or not isinstance(max_tenant_concurrency, int)
            or not 1 <= max_tenant_concurrency <= 64
        ):
            raise ValueError("investigation.worker.configuration.invalid")
        self._repository = repository
        self._investigations = investigations
        self._clock = clock
        self._worker_id = worker_id
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = heartbeat_seconds
        self._retry_seconds = retry_seconds
        self._max_attempts = max_attempts
        self._max_tenant_concurrency = max_tenant_concurrency

    def run_once(self, tenant_id: str) -> InvestigationWorkerResult | None:
        if not tenant_id or len(tenant_id) > 128:
            raise ValueError("investigation.worker.tenant.invalid")
        now = self._clock.now()
        claim = self._repository.claim_investigation_job(
            tenant_id,
            self._worker_id,
            now,
            self._after(now, self._lease_seconds),
            max_tenant_concurrency=self._max_tenant_concurrency,
        )
        if claim is None:
            return None
        current = self._repository.get_investigation_job(
            claim.actor, claim.investigation_id
        )
        if self._state(current) == "cancellation-requested":
            self._finish_cancelled(claim, current)
            return InvestigationWorkerResult(claim.investigation_id, "cancelled")
        if claim.attempts > self._max_attempts:
            return self._fail(claim, "investigation.retry.exhausted")

        stopped = Event()
        heartbeat = self._heartbeat_thread(claim, stopped)
        if heartbeat is not None:
            heartbeat.start()
        try:
            report = self._investigations.execute_prepared(
                RunInvestigationCommand(claim.actor, claim.request)
            )
            report_spec = report.get("spec")
            outcome = (
                report_spec.get("outcome")
                if isinstance(report_spec, Mapping)
                else "failed"
            )
            state = (
                "cancelled"
                if outcome == "cancelled"
                else "failed"
                if outcome == "failed"
                else "completed"
            )
            completed_at = (
                str(report_spec.get("completedAt"))
                if isinstance(report_spec, Mapping)
                else self._clock.now()
            )
            status = self._repository.get_investigation_job(
                claim.actor, claim.investigation_id
            )
            if status is None:
                return InvestigationWorkerResult(claim.investigation_id, "claim-lost")
            cancellation = None
            investigation_status = self._repository_investigation_status(claim)
            investigation_spec = (
                investigation_status.get("spec")
                if isinstance(investigation_status, Mapping)
                else None
            )
            if isinstance(investigation_spec, Mapping):
                value = investigation_spec.get("cancellation")
                if isinstance(value, Mapping):
                    cancellation = value
            terminal = InvestigationDispatchService.terminal_status(
                status,
                state=state,
                completed_at=completed_at,
                report_ref=(
                    f"investigation://{claim.actor.tenant_id}/"
                    f"{claim.investigation_id}/report"
                ),
                error_code=(
                    str(report_spec.get("terminalReason"))
                    if state == "failed" and isinstance(report_spec, Mapping)
                    else None
                ),
                cancellation=cancellation,
            )
            committed = self._repository.finish_investigation_job(
                claim.actor.tenant_id,
                claim.investigation_id,
                self._worker_id,
                claim.claim_token,
                terminal,
            )
            return InvestigationWorkerResult(
                claim.investigation_id, state if committed else "claim-lost"
            )
        except InvestigationInProgressError:
            return self._retry(claim, "investigation.in_progress")
        except InvestigationConflictError:
            return self._fail(claim, "investigation.id.conflict")
        except InvalidInvestigationError as exc:
            return self._fail(claim, str(exc))
        except Exception:
            # Provider and storage details never cross the worker boundary.
            return self._retry(claim, "investigation.runtime.unavailable")
        finally:
            stopped.set()
            if heartbeat is not None:
                heartbeat.join(timeout=max(1, self._heartbeat_seconds + 1))

    def _retry(
        self, claim: InvestigationJobClaim, error_code: str
    ) -> InvestigationWorkerResult:
        current = self._repository.get_investigation_job(
            claim.actor, claim.investigation_id
        )
        if current is None:
            return InvestigationWorkerResult(claim.investigation_id, "claim-lost")
        if self._state(current) == "cancellation-requested":
            self._finish_cancelled(claim, current)
            return InvestigationWorkerResult(claim.investigation_id, "cancelled")
        now = self._clock.now()
        if claim.attempts >= self._max_attempts:
            return self._fail(claim, "investigation.retry.exhausted")
        queued_at = self._queued_at(current)
        available_at = self._after(now, self._retry_seconds)
        status = InvestigationDispatchService.queued_status(
            claim.actor,
            claim.investigation_id,
            claim.request,
            queued_at,
            attempts=claim.attempts,
            error_code=error_code,
            available_at=available_at,
        )
        status["metadata"]["updatedAt"] = now
        released = self._repository.release_investigation_job(
            claim.actor.tenant_id,
            claim.investigation_id,
            self._worker_id,
            claim.claim_token,
            status,
            available_at,
            error_code,
        )
        return InvestigationWorkerResult(
            claim.investigation_id, "retry" if released else "claim-lost"
        )

    def _fail(
        self, claim: InvestigationJobClaim, error_code: str
    ) -> InvestigationWorkerResult:
        current = self._repository.get_investigation_job(
            claim.actor, claim.investigation_id
        )
        if current is None:
            return InvestigationWorkerResult(claim.investigation_id, "claim-lost")
        if self._state(current) == "cancellation-requested":
            self._finish_cancelled(claim, current)
            return InvestigationWorkerResult(claim.investigation_id, "cancelled")
        terminal = InvestigationDispatchService.terminal_status(
            current,
            state="failed",
            completed_at=self._clock.now(),
            error_code=error_code,
        )
        finished = self._repository.finish_investigation_job(
            claim.actor.tenant_id,
            claim.investigation_id,
            self._worker_id,
            claim.claim_token,
            terminal,
        )
        return InvestigationWorkerResult(
            claim.investigation_id, "failed" if finished else "claim-lost"
        )

    def _finish_cancelled(
        self,
        claim: InvestigationJobClaim,
        current: Mapping[str, object] | None,
    ) -> None:
        if current is None:
            return
        spec = current.get("spec")
        cancellation = (
            spec.get("cancellation") if isinstance(spec, Mapping) else None
        )
        terminal = InvestigationDispatchService.terminal_status(
            current,
            state="cancelled",
            completed_at=self._clock.now(),
            cancellation=(cancellation if isinstance(cancellation, Mapping) else None),
        )
        self._repository.finish_investigation_job(
            claim.actor.tenant_id,
            claim.investigation_id,
            self._worker_id,
            claim.claim_token,
            terminal,
        )

    def _heartbeat_thread(
        self, claim: InvestigationJobClaim, stopped: Event
    ) -> Thread | None:
        if self._heartbeat_seconds == 0:
            return None

        def heartbeat() -> None:
            while not stopped.wait(self._heartbeat_seconds):
                try:
                    current = self._repository.get_investigation_job(
                        claim.actor, claim.investigation_id
                    )
                    if current is None or self._state(current) not in {
                        "running",
                        "cancellation-requested",
                    }:
                        return
                    now = self._clock.now()
                    document = InvestigationDispatchService._copy_status(current)
                    metadata = document["metadata"]
                    spec = document["spec"]
                    assert isinstance(metadata, dict) and isinstance(spec, dict)
                    metadata["updatedAt"] = now
                    spec["heartbeatAt"] = now
                    spec["leaseExpiresAt"] = self._after(now, self._lease_seconds)
                    if not self._repository.heartbeat_investigation_job(
                        claim.actor.tenant_id,
                        claim.investigation_id,
                        self._worker_id,
                        claim.claim_token,
                        document,
                    ):
                        return
                except Exception:
                    return

        return Thread(
            target=heartbeat,
            name=f"iip-heartbeat-{claim.investigation_id}",
            daemon=True,
        )

    def _repository_investigation_status(
        self, claim: InvestigationJobClaim
    ) -> Mapping[str, object] | None:
        getter = getattr(self._repository, "get_investigation_status", None)
        if not callable(getter):
            return None
        return getter(claim.actor, claim.investigation_id)

    @staticmethod
    def _state(document: Mapping[str, object] | None) -> object:
        spec = document.get("spec") if isinstance(document, Mapping) else None
        return spec.get("state") if isinstance(spec, Mapping) else None

    @staticmethod
    def _queued_at(document: Mapping[str, object]) -> str:
        spec = document.get("spec")
        value = spec.get("queuedAt") if isinstance(spec, Mapping) else None
        if not isinstance(value, str):
            raise ValueError("investigation.job.state.invalid")
        return value

    @staticmethod
    def _after(value: str, seconds: int) -> str:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (parsed + timedelta(seconds=seconds)).astimezone(
            timezone.utc
        ).isoformat().replace("+00:00", "Z")
