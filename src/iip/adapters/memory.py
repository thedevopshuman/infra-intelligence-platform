"""In-memory adapters for local development and contract tests."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Dict, Iterable, Mapping, Optional

from iip.application.ports import (
    ActorContext,
    OutboxMessage,
    PolicyDecision,
    ResourceObservationRecord,
    ResourceWriteResult,
    SourceCheckpoint,
    StoredEvent,
)
from iip.domain.models import (
    ObservationDisposition,
    PlatformEvent,
    Resource,
    ResourceRelationshipEdge,
    classify_resource_observation,
    index_resource_relationships,
)


@dataclass
class _MemoryOutboxEntry:
    message_id: int
    event: PlatformEvent
    attempts: int = 0
    claimed_by: Optional[str] = None
    claim_expires_at: Optional[datetime] = None
    available_at: datetime = datetime.min.replace(tzinfo=timezone.utc)
    published: bool = False
    last_error_code: Optional[str] = None


class InMemoryResourceStore:
    """Thread-safe transactional store for the local runtime profile."""

    def __init__(self) -> None:
        self._items: Dict[tuple[str, str], Resource] = {}
        self._history: list[ResourceObservationRecord] = []
        self._event_log: list[StoredEvent] = []
        self._outbox: Dict[int, _MemoryOutboxEntry] = {}
        self._checkpoints: Dict[tuple[str, str], SourceCheckpoint] = {}
        self._lock = RLock()

    @property
    def events(self) -> tuple[PlatformEvent, ...]:
        """Expose accepted events for local diagnostics and focused tests."""

        with self._lock:
            return tuple(item.event for item in self._event_log)

    def apply(
        self,
        resource: Resource,
        event: PlatformEvent,
        *,
        checkpoint_ready: bool = False,
    ) -> ResourceWriteResult:
        self._validate_event(resource, event)
        self._validate_checkpoint_request(resource, checkpoint_ready)
        key = (resource.identity.tenant_id, resource.identity.uid)
        observation_hash = PlatformEvent.canonical_hash(resource.to_dict())

        with self._lock:
            current = self._items.get(key)
            disposition = (
                ObservationDisposition.ACCEPTED
                if current is None
                else classify_resource_observation(current, resource)
            )
            if disposition == ObservationDisposition.DUPLICATE:
                return ResourceWriteResult(current, disposition)

            checkpoint = None
            if disposition == ObservationDisposition.ACCEPTED and checkpoint_ready:
                checkpoint = self._next_checkpoint(resource)

            history_offset = len(self._history) + 1
            self._history.append(
                ResourceObservationRecord(
                    offset=history_offset,
                    resource=resource,
                    disposition=disposition,
                    observation_hash=observation_hash,
                    recorded_at=PlatformEvent.now(),
                )
            )
            if disposition != ObservationDisposition.ACCEPTED:
                return ResourceWriteResult(current, disposition)

            self._items[key] = resource
            event_offset = len(self._event_log) + 1
            self._event_log.append(StoredEvent(event_offset, event))
            self._outbox[event_offset] = _MemoryOutboxEntry(event_offset, event)
            if checkpoint is not None:
                self._checkpoints[(checkpoint.tenant_id, checkpoint.source_id)] = checkpoint
            return ResourceWriteResult(resource, disposition)

    def get(self, tenant_id: str, uid: str) -> Optional[Resource]:
        with self._lock:
            return self._items.get((tenant_id, uid))

    def list(self, tenant_id: str) -> Iterable[Resource]:
        with self._lock:
            return tuple(
                item
                for (item_tenant, _), item in sorted(self._items.items())
                if item_tenant == tenant_id
            )

    def get_many(self, tenant_id: str, uids: Iterable[str]) -> Iterable[Resource]:
        requested = set(uids)
        with self._lock:
            return tuple(
                resource
                for (item_tenant, uid), resource in sorted(self._items.items())
                if item_tenant == tenant_id and uid in requested
            )

    def history(
        self,
        tenant_id: str,
        uid: str,
        *,
        after_offset: int = 0,
        limit: int = 1000,
    ) -> Iterable[ResourceObservationRecord]:
        self._validate_page(after_offset, limit)
        with self._lock:
            return tuple(
                item
                for item in self._history
                if item.resource.identity.tenant_id == tenant_id
                and item.resource.identity.uid == uid
                and item.offset > after_offset
            )[:limit]

    def relationships(
        self,
        tenant_id: str,
        uid: str,
        *,
        direction: str = "both",
        relationship_types: tuple[str, ...] = (),
        after_edge_id: Optional[str] = None,
        limit: int = 100,
    ) -> Iterable[ResourceRelationshipEdge]:
        self._validate_page(0, limit)
        if direction not in ("incoming", "outgoing", "both"):
            raise ValueError("direction is invalid")
        edges = []
        allowed_types = set(relationship_types)
        with self._lock:
            resources = tuple(
                resource
                for (item_tenant, _), resource in self._items.items()
                if item_tenant == tenant_id
            )
        for resource in resources:
            for edge in index_resource_relationships(resource):
                if direction == "incoming" and edge.target_ref != uid:
                    continue
                if direction == "outgoing" and edge.source_ref != uid:
                    continue
                if direction == "both" and uid not in (edge.source_ref, edge.target_ref):
                    continue
                if allowed_types and edge.relationship_type not in allowed_types:
                    continue
                if after_edge_id is not None and edge.edge_id <= after_edge_id:
                    continue
                edges.append(edge)
        return tuple(sorted(edges, key=lambda item: item.edge_id))[:limit]

    def list_events(
        self,
        tenant_id: str,
        *,
        after_offset: int = 0,
        limit: int = 100,
    ) -> Iterable[StoredEvent]:
        self._validate_page(after_offset, limit)
        with self._lock:
            return tuple(
                item
                for item in self._event_log
                if item.offset > after_offset and item.event.tenant_id == tenant_id
            )[:limit]

    def claim_outbox(
        self,
        tenant_id: str,
        worker_id: str,
        *,
        limit: int = 100,
        lease_seconds: int = 30,
    ) -> Iterable[OutboxMessage]:
        self._validate_worker(worker_id)
        self._validate_page(0, limit)
        if lease_seconds < 1 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")

        now = datetime.now(timezone.utc)
        claimed: list[OutboxMessage] = []
        with self._lock:
            for entry in self._outbox.values():
                if len(claimed) == limit:
                    break
                if entry.event.tenant_id != tenant_id or entry.published:
                    continue
                if entry.available_at > now:
                    continue
                if entry.claim_expires_at is not None and entry.claim_expires_at > now:
                    continue
                entry.attempts += 1
                entry.claimed_by = worker_id
                entry.claim_expires_at = now + timedelta(seconds=lease_seconds)
                claimed.append(OutboxMessage(entry.message_id, entry.event, entry.attempts))
        return tuple(claimed)

    def acknowledge_outbox(
        self,
        tenant_id: str,
        worker_id: str,
        message_id: int,
    ) -> bool:
        with self._lock:
            entry = self._outbox.get(message_id)
            if (
                entry is None
                or entry.event.tenant_id != tenant_id
                or entry.claimed_by != worker_id
                or entry.claim_expires_at is None
                or entry.claim_expires_at <= datetime.now(timezone.utc)
                or entry.published
            ):
                return False
            entry.published = True
            entry.claimed_by = None
            entry.claim_expires_at = None
            return True

    def release_outbox(
        self,
        tenant_id: str,
        worker_id: str,
        message_id: int,
        error_code: str,
        *,
        retry_after_seconds: int = 0,
    ) -> bool:
        self._validate_error_code(error_code)
        if retry_after_seconds < 0 or retry_after_seconds > 86_400:
            raise ValueError("retry_after_seconds must be between 0 and 86400")
        with self._lock:
            entry = self._outbox.get(message_id)
            if (
                entry is None
                or entry.event.tenant_id != tenant_id
                or entry.claimed_by != worker_id
                or entry.claim_expires_at is None
                or entry.claim_expires_at <= datetime.now(timezone.utc)
                or entry.published
            ):
                return False
            entry.claimed_by = None
            entry.claim_expires_at = None
            entry.available_at = datetime.now(timezone.utc) + timedelta(
                seconds=retry_after_seconds
            )
            entry.last_error_code = error_code
            return True

    def get_checkpoint(self, tenant_id: str, source_id: str) -> Optional[SourceCheckpoint]:
        with self._lock:
            return self._checkpoints.get((tenant_id, source_id))

    def _next_checkpoint(self, resource: Resource) -> SourceCheckpoint:
        cursor = resource.observation
        assert cursor is not None and cursor.checkpoint is not None
        key = (resource.identity.tenant_id, cursor.source_id)
        current = self._checkpoints.get(key)
        if current is not None:
            if current.stream_id == cursor.stream_id and cursor.sequence < current.sequence:
                raise ValueError("checkpoint sequence cannot move backwards")
            if (
                current.stream_id == cursor.stream_id
                and cursor.sequence == current.sequence
                and cursor.checkpoint != current.checkpoint
            ):
                raise ValueError("checkpoint content conflicts at the same sequence")
            if current.stream_id != cursor.stream_id and cursor.mode != "reconciliation":
                raise ValueError("checkpoint stream reset requires reconciliation")
        return SourceCheckpoint(
            tenant_id=resource.identity.tenant_id,
            source_id=cursor.source_id,
            stream_id=cursor.stream_id,
            sequence=cursor.sequence,
            checkpoint=cursor.checkpoint,
            committed_at=PlatformEvent.now(),
        )

    @staticmethod
    def _validate_event(resource: Resource, event: PlatformEvent) -> None:
        if event.tenant_id != resource.identity.tenant_id:
            raise ValueError("event tenant does not match resource tenant")
        if event.subject != resource.identity.uid:
            raise ValueError("event subject does not match resource identity")

    @staticmethod
    def _validate_checkpoint_request(resource: Resource, checkpoint_ready: bool) -> None:
        if checkpoint_ready and (
            resource.observation is None or resource.observation.checkpoint is None
        ):
            raise ValueError("checkpoint-ready write requires an observation checkpoint")

    @staticmethod
    def _validate_page(after_offset: int, limit: int) -> None:
        if after_offset < 0:
            raise ValueError("after_offset must not be negative")
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")

    @staticmethod
    def _validate_worker(worker_id: str) -> None:
        if not isinstance(worker_id, str) or not re.fullmatch(r"[a-zA-Z0-9._:-]{1,128}", worker_id):
            raise ValueError("worker_id is invalid")

    @staticmethod
    def _validate_error_code(error_code: str) -> None:
        if not isinstance(error_code, str) or not re.fullmatch(
            r"[a-z][a-z0-9_.-]{2,127}", error_code
        ):
            raise ValueError("error_code must be stable and non-sensitive")


class InMemoryEventPublisher:
    """Append-only downstream publisher useful for dispatcher tests."""

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
        if action not in ("resource:ingest", "resource:read"):
            return PolicyDecision(False, "action.unsupported")
        return PolicyDecision(True, "development.allow")


# Transitional alias for callers that used the foundation repository name.
InMemoryResourceRepository = InMemoryResourceStore
