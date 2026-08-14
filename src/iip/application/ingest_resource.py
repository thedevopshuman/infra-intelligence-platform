"""Resource ingestion use case."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from iip.application.ports import (
    ActorContext,
    PolicyDecisionPoint,
    ResourceObservationStore,
)
from iip.domain.models import (
    ContractError,
    ObservationDisposition,
    PlatformEvent,
    Resource,
)


class AuthorizationError(PermissionError):
    """Raised when policy denies a scoped application action."""


class InvalidInputError(ValueError):
    """Raised when a use-case input violates the public contract or request scope."""


class StaleObservationError(RuntimeError):
    """Raised when an older observation cannot replace the latest projection."""


class ObservationConflictError(RuntimeError):
    """Raised when ordering metadata conflicts with the latest projection."""


@dataclass(frozen=True)
class IngestResourceCommand:
    """Input to one resource observation transaction."""

    actor: ActorContext
    payload: Mapping[str, Any]
    correlation_id: Optional[str] = None
    checkpoint_ready: bool = False


class ResourceIngestionService:
    """Authorize, normalize, store, and announce a resource observation."""

    def __init__(
        self,
        store: ResourceObservationStore,
        policy: PolicyDecisionPoint,
    ) -> None:
        self._store = store
        self._policy = policy

    def execute(self, command: IngestResourceCommand) -> Resource:
        """Execute the ingestion transaction within the actor's tenant scope."""

        try:
            resource = Resource.from_dict(command.payload)
            if resource.identity.tenant_id != command.actor.tenant_id:
                raise ContractError("actor tenant does not match resource tenant")
        except ContractError as exc:
            raise InvalidInputError("resource input is invalid") from exc

        policy_resource = {
            "tenantId": resource.identity.tenant_id,
            "uid": resource.identity.uid,
        }
        if resource.observation is not None:
            policy_resource.update(
                {
                    "sourceId": resource.observation.source_id,
                    "streamId": resource.observation.stream_id,
                    "mode": resource.observation.mode,
                }
            )

        decision = self._policy.decide(
            actor=command.actor,
            action="resource:ingest",
            resource=policy_resource,
        )
        if not decision.allowed:
            raise AuthorizationError(decision.reason_code)

        if command.checkpoint_ready and (
            resource.observation is None or resource.observation.checkpoint is None
        ):
            raise InvalidInputError("checkpoint-ready ingestion requires a checkpoint cursor")

        observation_hash = PlatformEvent.canonical_hash(resource.to_dict())
        event_data: dict[str, Any] = {
            "resourceUid": resource.identity.uid,
            "observationHash": observation_hash,
        }
        if resource.observation is not None:
            event_data["observation"] = resource.observation.to_dict()
        event = PlatformEvent(
            event_id=str(uuid.uuid4()),
            event_type="io.iip.resource.observed.v1",
            source=f"urn:iip:ingestion:{resource.identity.provider}",
            time=PlatformEvent.now(),
            subject=resource.identity.uid,
            tenant_id=resource.identity.tenant_id,
            correlation_id=command.correlation_id,
            data=event_data,
        )
        write = self._store.apply(
            resource,
            event,
            checkpoint_ready=command.checkpoint_ready,
        )
        if write.disposition == ObservationDisposition.STALE:
            raise StaleObservationError("resource.observation.stale")
        if write.disposition == ObservationDisposition.CONFLICT:
            raise ObservationConflictError("resource.observation.conflict")
        return write.resource
