"""Resource ingestion use case."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from iip.application.ports import (
    ActorContext,
    EventPublisher,
    PolicyDecisionPoint,
    ResourceRepository,
)
from iip.domain.models import ContractError, PlatformEvent, Resource


class AuthorizationError(PermissionError):
    """Raised when policy denies a scoped application action."""


class InvalidInputError(ValueError):
    """Raised when a use-case input violates the public contract or request scope."""


@dataclass(frozen=True)
class IngestResourceCommand:
    """Input to one resource observation transaction."""

    actor: ActorContext
    payload: Mapping[str, Any]
    correlation_id: Optional[str] = None


class ResourceIngestionService:
    """Authorize, normalize, store, and announce a resource observation."""

    def __init__(
        self,
        repository: ResourceRepository,
        events: EventPublisher,
        policy: PolicyDecisionPoint,
    ) -> None:
        self._repository = repository
        self._events = events
        self._policy = policy

    def execute(self, command: IngestResourceCommand) -> Resource:
        """Execute the ingestion transaction within the actor's tenant scope."""

        try:
            resource = Resource.from_dict(command.payload)
            if resource.identity.tenant_id != command.actor.tenant_id:
                raise ContractError("actor tenant does not match resource tenant")
        except ContractError as exc:
            raise InvalidInputError("resource input is invalid") from exc

        decision = self._policy.decide(
            actor=command.actor,
            action="resource:ingest",
            resource={"tenantId": resource.identity.tenant_id, "uid": resource.identity.uid},
        )
        if not decision.allowed:
            raise AuthorizationError(decision.reason_code)

        stored = self._repository.upsert(resource)
        event = PlatformEvent(
            event_id=str(uuid.uuid4()),
            event_type="io.iip.resource.observed.v1",
            source=f"urn:iip:ingestion:{resource.identity.provider}",
            time=PlatformEvent.now(),
            subject=resource.identity.uid,
            tenant_id=resource.identity.tenant_id,
            correlation_id=command.correlation_id,
            data={
                "resource": stored.to_dict(),
                "observationHash": PlatformEvent.canonical_hash(stored.to_dict()),
            },
        )
        self._events.publish(event)
        return stored
