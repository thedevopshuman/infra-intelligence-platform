"""In-memory adapters for local development and contract tests."""

from __future__ import annotations

from threading import RLock
from typing import Dict, Iterable, Mapping, Optional

from iip.application.ports import ActorContext, PolicyDecision, ResourceWriteResult
from iip.domain.models import (
    ObservationDisposition,
    PlatformEvent,
    Resource,
    classify_resource_observation,
)


class InMemoryResourceRepository:
    """Thread-safe tenant-scoped resource repository."""

    def __init__(self) -> None:
        self._items: Dict[tuple[str, str], Resource] = {}
        self._lock = RLock()

    def upsert(self, resource: Resource) -> ResourceWriteResult:
        key = (resource.identity.tenant_id, resource.identity.uid)
        with self._lock:
            current = self._items.get(key)
            if current is None:
                self._items[key] = resource
                return ResourceWriteResult(resource, ObservationDisposition.ACCEPTED)

            disposition = classify_resource_observation(current, resource)
            if disposition == ObservationDisposition.ACCEPTED:
                self._items[key] = resource
                return ResourceWriteResult(resource, disposition)
            return ResourceWriteResult(current, disposition)

    def get(self, tenant_id: str, uid: str) -> Optional[Resource]:
        with self._lock:
            return self._items.get((tenant_id, uid))

    def list(self, tenant_id: str) -> Iterable[Resource]:
        with self._lock:
            return tuple(
                item for (item_tenant, _), item in self._items.items() if item_tenant == tenant_id
            )


class InMemoryEventPublisher:
    """Append-only event recorder for local execution."""

    def __init__(self) -> None:
        self.events: list[PlatformEvent] = []
        self._lock = RLock()

    def publish(self, event: PlatformEvent) -> None:
        with self._lock:
            self.events.append(event)


class AllowTenantPolicy:
    """Development policy that allows non-anonymous actors in their own tenant."""

    def decide(
        self,
        actor: ActorContext,
        action: str,
        resource: Mapping[str, str],
    ) -> PolicyDecision:
        if not actor.actor_id or actor.actor_id == "anonymous":
            return PolicyDecision(False, "actor.anonymous")
        if resource.get("tenantId") != actor.tenant_id:
            return PolicyDecision(False, "tenant.scope_mismatch")
        if action != "resource:ingest":
            return PolicyDecision(False, "action.unsupported")
        return PolicyDecision(True, "development.allow")
