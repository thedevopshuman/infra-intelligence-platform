"""Ports owned by the application layer and implemented by adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Protocol

from iip.domain.models import ObservationDisposition, PlatformEvent, Resource


@dataclass(frozen=True)
class ActorContext:
    """Authenticated actor and tenant scope supplied by a surface."""

    actor_id: str
    tenant_id: str
    roles: tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyDecision:
    """Auditable authorization result."""

    allowed: bool
    reason_code: str


@dataclass(frozen=True)
class ResourceWriteResult:
    """Outcome of applying one observation to the latest resource projection."""

    resource: Resource
    disposition: ObservationDisposition


class ResourceRepository(Protocol):
    def upsert(self, resource: Resource) -> ResourceWriteResult:
        """Apply ordering rules and return the current canonical projection."""

    def get(self, tenant_id: str, uid: str) -> Optional[Resource]:
        """Return a resource only from the requested tenant scope."""

    def list(self, tenant_id: str) -> Iterable[Resource]:
        """List resources visible in the requested tenant scope."""


class EventPublisher(Protocol):
    def publish(self, event: PlatformEvent) -> None:
        """Durably publish or record an event according to adapter guarantees."""


class PolicyDecisionPoint(Protocol):
    def decide(
        self,
        actor: ActorContext,
        action: str,
        resource: Mapping[str, str],
    ) -> PolicyDecision:
        """Decide one action without granting authority beyond this request."""


class AgentExecutor(Protocol):
    def run(self, agent_id: str, request: Mapping[str, object]) -> Mapping[str, object]:
        """Run an agent against an explicitly scoped request."""


class PluginCatalog(Protocol):
    def resolve(self, plugin_id: str, version: str) -> Mapping[str, object]:
        """Resolve an installed plugin manifest by immutable identity."""
