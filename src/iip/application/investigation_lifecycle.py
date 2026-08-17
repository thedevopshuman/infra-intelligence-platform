"""Tenant-scoped investigation status and cooperative cancellation use cases."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from iip.application.ports import (
    ActorContext,
    Clock,
    InvestigationRepository,
    PersistenceError,
)


_INVESTIGATION_ID = re.compile(r"inv_[a-f0-9]{32}")
_CANCELLATION_ID = re.compile(r"can_[a-f0-9]{32}")
_REASON_CODES = {"operator-requested", "incident-resolved", "superseded"}


class InvalidInvestigationCancellationError(ValueError):
    """Raised when a cancellation envelope is malformed or widens identity."""


class InvestigationLifecycleNotFoundError(LookupError):
    """Raised without revealing whether another tenant owns the identifier."""


@dataclass(frozen=True)
class GetInvestigationStatusCommand:
    actor: ActorContext
    investigation_id: str


@dataclass(frozen=True)
class CancelInvestigationCommand:
    actor: ActorContext
    request: Mapping[str, object]


class InvestigationLifecycleService:
    """Read lifecycle state and record idempotent cooperative cancellation."""

    def __init__(self, repository: InvestigationRepository, clock: Clock) -> None:
        self._repository = repository
        self._clock = clock

    def get(self, command: GetInvestigationStatusCommand) -> Mapping[str, object]:
        if (
            not isinstance(command.investigation_id, str)
            or not _INVESTIGATION_ID.fullmatch(command.investigation_id)
        ):
            raise InvestigationLifecycleNotFoundError("investigation.not_found")
        status = self._repository.get_investigation_status(
            command.actor, command.investigation_id
        )
        if status is None:
            raise InvestigationLifecycleNotFoundError("investigation.not_found")
        return status

    def cancel(self, command: CancelInvestigationCommand) -> Mapping[str, object]:
        request = self.validate_cancellation(command)
        metadata = request["metadata"]
        spec = request["spec"]
        assert isinstance(metadata, Mapping) and isinstance(spec, Mapping)
        investigation_id = str(spec["investigationId"])
        current = self._repository.get_investigation_status(
            command.actor, investigation_id
        )
        if current is None:
            raise InvestigationLifecycleNotFoundError("investigation.not_found")
        current_metadata = current.get("metadata")
        current_spec = current.get("spec")
        if not isinstance(current_metadata, Mapping) or not isinstance(
            current_spec, Mapping
        ):
            raise PersistenceError("storage.unavailable")
        state = current_spec.get("state")
        if state != "running":
            return current
        requested_at = str(metadata["requestedAt"])
        requested_time = self._parse_time(requested_at)
        started_time = self._parse_time(current_spec.get("startedAt"))
        if (
            requested_time is None
            or started_time is None
            or requested_time < started_time
        ):
            raise self._invalid()
        updated: dict[str, object] = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "InvestigationStatus",
            "metadata": {
                "id": investigation_id,
                "tenantId": command.actor.tenant_id,
                "updatedAt": requested_at,
            },
            "spec": {
                "requestDigest": current_spec["requestDigest"],
                "state": "cancellation-requested",
                "startedAt": current_spec["startedAt"],
                "leaseExpiresAt": current_spec["leaseExpiresAt"],
                "cancellation": {
                    "requestedBy": command.actor.actor_id,
                    "requestedAt": requested_at,
                    "reasonCode": spec["reasonCode"],
                },
            },
        }
        return self._repository.request_investigation_cancellation(
            command.actor, investigation_id, updated
        )

    def validate_cancellation(
        self, command: CancelInvestigationCommand
    ) -> Mapping[str, object]:
        """Validate cancellation identity and time without requiring live state."""

        request = command.request
        if (
            not isinstance(request, Mapping)
            or set(request) != {"apiVersion", "kind", "metadata", "spec"}
            or request.get("apiVersion") != "iip.platform/v1alpha1"
            or request.get("kind") != "InvestigationCancellationRequest"
        ):
            raise self._invalid()
        metadata = request.get("metadata")
        spec = request.get("spec")
        if (
            not isinstance(metadata, Mapping)
            or set(metadata) != {"id", "tenantId", "actorId", "requestedAt"}
            or not isinstance(metadata.get("id"), str)
            or not _CANCELLATION_ID.fullmatch(str(metadata["id"]))
            or metadata.get("tenantId") != command.actor.tenant_id
            or metadata.get("actorId") != command.actor.actor_id
            or not isinstance(spec, Mapping)
            or set(spec) != {"investigationId", "reasonCode"}
            or not isinstance(spec.get("investigationId"), str)
            or not _INVESTIGATION_ID.fullmatch(str(spec["investigationId"]))
            or spec.get("reasonCode") not in _REASON_CODES
        ):
            raise self._invalid()
        requested_at = self._parse_time(metadata.get("requestedAt"))
        now = self._parse_time(self._clock.now())
        if (
            requested_at is None
            or now is None
            or requested_at > now + timedelta(minutes=5)
        ):
            raise self._invalid()
        return request

    @staticmethod
    def _parse_time(value: object) -> datetime | None:
        if not isinstance(value, str) or not value.endswith("Z"):
            return None
        try:
            parsed = datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _invalid() -> InvalidInvestigationCancellationError:
        return InvalidInvestigationCancellationError("investigation.cancellation.invalid")
