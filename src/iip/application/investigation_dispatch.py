"""Tenant-scoped durable dispatch for asynchronous investigations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from iip.application.investigate import (
    DeterministicInvestigationService,
    InvestigationConflictError,
    RunInvestigationCommand,
    canonical_digest,
)
from iip.application.investigation_lifecycle import (
    CancelInvestigationCommand,
    InvestigationLifecycleNotFoundError,
    InvestigationLifecycleService,
)
from iip.application.ports import (
    ActorContext,
    Clock,
    InvestigationJobRepository,
    PersistenceError,
)


class InvestigationJobNotFoundError(LookupError):
    """The job does not exist in the authenticated tenant."""


@dataclass(frozen=True)
class SubmitInvestigationJobCommand:
    actor: ActorContext
    request: Mapping[str, object]


@dataclass(frozen=True)
class GetInvestigationJobCommand:
    actor: ActorContext
    investigation_id: str


@dataclass(frozen=True)
class CancelInvestigationJobCommand:
    actor: ActorContext
    request: Mapping[str, object]


class InvestigationDispatchService:
    """Accept immutable requests and expose bounded background job state."""

    def __init__(
        self,
        repository: InvestigationJobRepository,
        investigations: DeterministicInvestigationService,
        lifecycle: InvestigationLifecycleService,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._investigations = investigations
        self._lifecycle = lifecycle
        self._clock = clock

    def submit(
        self, command: SubmitInvestigationJobCommand
    ) -> Mapping[str, object]:
        request = self._investigations.validate_for_dispatch(
            RunInvestigationCommand(command.actor, command.request)
        )
        metadata = request.get("metadata")
        if not isinstance(metadata, Mapping):
            raise PersistenceError("storage.input-invalid")
        investigation_id = str(metadata["id"])
        queued_at = self._clock.now()
        status = self.queued_status(
            command.actor,
            investigation_id,
            request,
            queued_at,
            attempts=0,
        )
        try:
            return self._repository.enqueue_investigation_job(
                command.actor,
                investigation_id,
                request,
                status,
            )
        except PersistenceError as exc:
            if str(exc) == "storage.conflict":
                raise InvestigationConflictError(
                    "investigation.id.conflict"
                ) from None
            raise

    def get(self, command: GetInvestigationJobCommand) -> Mapping[str, object]:
        document = self._repository.get_investigation_job(
            command.actor, command.investigation_id
        )
        if document is None:
            raise InvestigationJobNotFoundError("investigation.job.not_found")
        return document

    def cancel(
        self, command: CancelInvestigationJobCommand
    ) -> Mapping[str, object]:
        cancellation = self._lifecycle.validate_cancellation(
            CancelInvestigationCommand(command.actor, command.request)
        )
        spec = cancellation.get("spec")
        metadata = cancellation.get("metadata")
        if not isinstance(spec, Mapping) or not isinstance(metadata, Mapping):
            raise PersistenceError("storage.input-invalid")
        investigation_id = str(spec["investigationId"])
        current = self._repository.get_investigation_job(
            command.actor, investigation_id
        )
        if current is None:
            raise InvestigationJobNotFoundError("investigation.job.not_found")
        current_spec = current.get("spec")
        if not isinstance(current_spec, Mapping):
            raise PersistenceError("storage.unavailable")
        state = current_spec.get("state")
        if state in {"completed", "failed", "cancelled"}:
            return current
        if state == "cancellation-requested":
            return current
        requested_at = str(metadata["requestedAt"])
        cancellation_state = {
            "requestedBy": command.actor.actor_id,
            "requestedAt": requested_at,
            "reasonCode": spec["reasonCode"],
        }
        if state == "queued":
            updated = self.terminal_status(
                current,
                state="cancelled",
                completed_at=requested_at,
                cancellation=cancellation_state,
            )
        else:
            updated = self.cancellation_requested_status(
                current, cancellation_state, requested_at
            )
        stored = self._repository.request_investigation_job_cancellation(
            command.actor, investigation_id, updated
        )
        if state in {"running", "cancellation-requested"}:
            try:
                self._lifecycle.cancel(
                    CancelInvestigationCommand(command.actor, cancellation)
                )
            except InvestigationLifecycleNotFoundError:
                # The worker may own the job lease but not yet have committed the
                # investigation lease. Its pre-execution check observes this job.
                pass
        return stored

    @staticmethod
    def queued_status(
        actor: ActorContext,
        investigation_id: str,
        request: Mapping[str, object],
        queued_at: str,
        *,
        attempts: int,
        error_code: str | None = None,
        available_at: str | None = None,
    ) -> dict[str, object]:
        spec: dict[str, object] = {
            "requestDigest": canonical_digest(request),
            "state": "queued",
            "queuedAt": queued_at,
            "availableAt": available_at or queued_at,
            "attempts": attempts,
        }
        if error_code is not None:
            spec["lastErrorCode"] = error_code
        else:
            spec.pop("lastErrorCode", None)
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "InvestigationJobStatus",
            "metadata": {
                "id": investigation_id,
                "tenantId": actor.tenant_id,
                "updatedAt": queued_at,
            },
            "spec": spec,
        }

    @staticmethod
    def cancellation_requested_status(
        current: Mapping[str, object],
        cancellation: Mapping[str, object],
        updated_at: str,
    ) -> dict[str, object]:
        document = InvestigationDispatchService._copy_status(current)
        metadata = document["metadata"]
        spec = document["spec"]
        assert isinstance(metadata, dict) and isinstance(spec, dict)
        metadata["updatedAt"] = updated_at
        spec["state"] = "cancellation-requested"
        spec["cancellation"] = dict(cancellation)
        return document

    @staticmethod
    def terminal_status(
        current: Mapping[str, object],
        *,
        state: str,
        completed_at: str,
        report_ref: str | None = None,
        error_code: str | None = None,
        cancellation: Mapping[str, object] | None = None,
    ) -> dict[str, object]:
        document = InvestigationDispatchService._copy_status(current)
        metadata = document["metadata"]
        spec = document["spec"]
        assert isinstance(metadata, dict) and isinstance(spec, dict)
        metadata["updatedAt"] = completed_at
        spec["state"] = state
        spec["completedAt"] = completed_at
        for field in ("availableAt", "leaseExpiresAt", "heartbeatAt"):
            spec.pop(field, None)
        if report_ref is not None:
            spec["reportRef"] = report_ref
        if error_code is not None:
            spec["lastErrorCode"] = error_code
        else:
            spec.pop("lastErrorCode", None)
        if cancellation is not None:
            spec["cancellation"] = dict(cancellation)
        return document

    @staticmethod
    def _copy_status(current: Mapping[str, object]) -> dict[str, object]:
        metadata = current.get("metadata")
        spec = current.get("spec")
        if not isinstance(metadata, Mapping) or not isinstance(spec, Mapping):
            raise PersistenceError("storage.unavailable")
        return {
            "apiVersion": current.get("apiVersion"),
            "kind": current.get("kind"),
            "metadata": dict(metadata),
            "spec": dict(spec),
        }
