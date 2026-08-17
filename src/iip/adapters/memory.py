"""In-memory adapters for local development and contract tests."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Dict, Iterable, Mapping, Optional

from iip.application.ports import (
    ActorContext,
    EventDeliverySloState,
    EventDeliveryState,
    OutboxMessage,
    PolicyDecision,
    QuarantinedOutboxMessage,
    ReconciliationSnapshot,
    ResourceObservationRecord,
    ResourceWriteResult,
    SourceCheckpoint,
    SourceIngestionState,
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
    created_at: str
    attempts: int = 0
    claimed_by: Optional[str] = None
    claim_expires_at: Optional[datetime] = None
    available_at: datetime = datetime.min.replace(tzinfo=timezone.utc)
    published_at: Optional[datetime] = None
    last_error_code: Optional[str] = None
    quarantined_at: Optional[datetime] = None


class InMemoryResourceStore:
    """Thread-safe transactional store for the local runtime profile."""

    def __init__(self) -> None:
        self._items: Dict[tuple[str, str], Resource] = {}
        self._history: list[ResourceObservationRecord] = []
        self._event_log: list[StoredEvent] = []
        self._outbox: Dict[int, _MemoryOutboxEntry] = {}
        self._checkpoints: Dict[tuple[str, str], SourceCheckpoint] = {}
        self._reconciliations: Dict[tuple[str, str], ReconciliationSnapshot] = {}
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
            recorded_at = PlatformEvent.now()
            self._history.append(
                ResourceObservationRecord(
                    offset=history_offset,
                    resource=resource,
                    disposition=disposition,
                    observation_hash=observation_hash,
                    recorded_at=recorded_at,
                )
            )
            if disposition != ObservationDisposition.ACCEPTED:
                return ResourceWriteResult(current, disposition)

            self._items[key] = resource
            event_offset = len(self._event_log) + 1
            self._event_log.append(StoredEvent(event_offset, event))
            self._outbox[event_offset] = _MemoryOutboxEntry(
                event_offset, event, recorded_at
            )
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
                if (
                    entry.event.tenant_id != tenant_id
                    or entry.published_at is not None
                    or entry.quarantined_at is not None
                ):
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
                or entry.published_at is not None
                or entry.quarantined_at is not None
            ):
                return False
            entry.published_at = datetime.now(timezone.utc)
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
                or entry.published_at is not None
                or entry.quarantined_at is not None
            ):
                return False
            entry.claimed_by = None
            entry.claim_expires_at = None
            entry.available_at = datetime.now(timezone.utc) + timedelta(
                seconds=retry_after_seconds
            )
            entry.last_error_code = error_code
            return True

    def quarantine_outbox(
        self,
        tenant_id: str,
        worker_id: str,
        message_id: int,
        error_code: str,
    ) -> bool:
        self._validate_error_code(error_code)
        now = datetime.now(timezone.utc)
        with self._lock:
            entry = self._outbox.get(message_id)
            if (
                entry is None
                or entry.event.tenant_id != tenant_id
                or entry.claimed_by != worker_id
                or entry.claim_expires_at is None
                or entry.claim_expires_at <= now
                or entry.published_at is not None
                or entry.quarantined_at is not None
            ):
                return False
            entry.claimed_by = None
            entry.claim_expires_at = None
            entry.last_error_code = error_code
            entry.quarantined_at = now
            return True

    def get_event_delivery_state(
        self,
        tenant_id: str,
        *,
        quarantine_limit: int = 50,
    ) -> EventDeliveryState:
        self._validate_page(0, quarantine_limit)
        if quarantine_limit > 50:
            raise ValueError("quarantine_limit must be between 1 and 50")
        now = datetime.now(timezone.utc)
        with self._lock:
            tenant_entries = tuple(
                entry
                for entry in self._outbox.values()
                if entry.event.tenant_id == tenant_id
            )
            pending = tuple(
                entry
                for entry in tenant_entries
                if entry.published_at is None and entry.quarantined_at is None
            )
            quarantined = sorted(
                (
                    entry
                    for entry in tenant_entries
                    if entry.quarantined_at is not None
                ),
                key=lambda entry: (entry.quarantined_at, entry.message_id),
                reverse=True,
            )
            oldest = min((entry.created_at for entry in pending), default=None)
            return EventDeliveryState(
                tenant_id=tenant_id,
                pending_events=len(pending),
                in_flight_events=sum(
                    entry.claim_expires_at is not None
                    and entry.claim_expires_at > now
                    for entry in pending
                ),
                retrying_events=sum(
                    entry.last_error_code is not None for entry in pending
                ),
                quarantined_events=len(quarantined),
                oldest_pending_event_recorded_at=oldest,
                quarantined=tuple(
                    self._quarantined_message(entry)
                    for entry in quarantined[:quarantine_limit]
                    if entry.quarantined_at is not None
                ),
            )

    def get_event_delivery_slo_state(
        self,
        tenant_id: str,
        *,
        window_start: str,
        window_end: str,
        maturity_cutoff: str,
        latency_objective_seconds: int,
    ) -> EventDeliverySloState:
        if (
            isinstance(latency_objective_seconds, bool)
            or not isinstance(latency_objective_seconds, int)
            or not 1 <= latency_objective_seconds <= 86_400
        ):
            raise ValueError("latency_objective_seconds is invalid")
        try:
            start = datetime.fromisoformat(window_start.replace("Z", "+00:00"))
            end = datetime.fromisoformat(window_end.replace("Z", "+00:00"))
            cutoff = datetime.fromisoformat(
                maturity_cutoff.replace("Z", "+00:00")
            )
        except (AttributeError, TypeError, ValueError):
            raise ValueError("event delivery SLO window is invalid") from None
        if (
            start.tzinfo is None
            or end.tzinfo is None
            or cutoff.tzinfo is None
            or not start < cutoff < end
            or int((end - cutoff).total_seconds()) != latency_objective_seconds
        ):
            raise ValueError("event delivery SLO window is invalid")

        with self._lock:
            cohort = []
            for entry in self._outbox.values():
                if entry.event.tenant_id != tenant_id:
                    continue
                created_at = datetime.fromisoformat(
                    entry.created_at.replace("Z", "+00:00")
                )
                if start <= created_at <= end:
                    cohort.append((entry, created_at))
            eligible = [item for item in cohort if item[1] <= cutoff]
            within = []
            late = []
            undelivered = []
            for entry, created_at in eligible:
                if entry.published_at is None:
                    undelivered.append(entry)
                elif entry.published_at <= created_at + timedelta(
                    seconds=latency_objective_seconds
                ):
                    within.append(entry)
                else:
                    late.append(entry)

        return EventDeliverySloState(
            tenant_id=tenant_id,
            window_start=window_start,
            window_end=window_end,
            maturity_cutoff=maturity_cutoff,
            created_events=len(cohort),
            immature_events=len(cohort) - len(eligible),
            eligible_events=len(eligible),
            within_objective_events=len(within),
            late_delivered_events=len(late),
            undelivered_events=len(undelivered),
            quarantined_events=sum(
                entry.quarantined_at is not None for entry in undelivered
            ),
        )

    def get_quarantined_outbox(
        self,
        tenant_id: str,
        message_id: int,
    ) -> Optional[QuarantinedOutboxMessage]:
        if (
            isinstance(message_id, bool)
            or not isinstance(message_id, int)
            or message_id < 1
        ):
            raise ValueError("message_id must be a positive integer")
        with self._lock:
            entry = self._outbox.get(message_id)
            if (
                entry is None
                or entry.event.tenant_id != tenant_id
                or entry.quarantined_at is None
            ):
                return None
            return self._quarantined_message(entry)

    def requeue_quarantined_outbox(
        self,
        tenant_id: str,
        message_id: int,
        *,
        expected_event_id: str,
        expected_quarantined_at: str,
        expected_attempts: int,
    ) -> bool:
        if (
            isinstance(message_id, bool)
            or not isinstance(message_id, int)
            or message_id < 1
            or not isinstance(expected_event_id, str)
            or not isinstance(expected_quarantined_at, str)
            or isinstance(expected_attempts, bool)
            or not isinstance(expected_attempts, int)
        ):
            raise ValueError("event delivery replay preconditions are invalid")
        now = datetime.now(timezone.utc)
        with self._lock:
            entry = self._outbox.get(message_id)
            quarantined_at = (
                entry.quarantined_at.isoformat().replace("+00:00", "Z")
                if entry is not None and entry.quarantined_at is not None
                else None
            )
            if (
                entry is None
                or entry.event.tenant_id != tenant_id
                or entry.event.event_id != expected_event_id
                or quarantined_at != expected_quarantined_at
                or entry.attempts != expected_attempts
                or entry.published_at is not None
            ):
                return False
            entry.attempts = 0
            entry.claimed_by = None
            entry.claim_expires_at = None
            entry.available_at = now
            entry.last_error_code = None
            entry.quarantined_at = None
            return True

    @staticmethod
    def _quarantined_message(
        entry: _MemoryOutboxEntry,
    ) -> QuarantinedOutboxMessage:
        assert entry.quarantined_at is not None
        return QuarantinedOutboxMessage(
            message_id=entry.message_id,
            tenant_id=entry.event.tenant_id,
            event_id=entry.event.event_id,
            event_source=entry.event.source,
            event_type=entry.event.event_type,
            subject=entry.event.subject,
            attempts=entry.attempts,
            quarantined_at=entry.quarantined_at.isoformat().replace(
                "+00:00", "Z"
            ),
            last_error_code=str(entry.last_error_code),
        )

    def get_checkpoint(self, tenant_id: str, source_id: str) -> Optional[SourceCheckpoint]:
        with self._lock:
            return self._checkpoints.get((tenant_id, source_id))

    def get_source_ingestion_state(
        self, tenant_id: str, source_id: str
    ) -> Optional[SourceIngestionState]:
        with self._lock:
            checkpoint = self._checkpoints.get((tenant_id, source_id))
            if checkpoint is None:
                return None
            observations = [
                item
                for item in self._history
                if item.disposition == ObservationDisposition.ACCEPTED
                and item.resource.identity.tenant_id == tenant_id
                and item.resource.observation is not None
                and item.resource.observation.source_id == source_id
            ]
            latest = max(observations, key=lambda item: item.offset, default=None)
            pending = []
            for entry in self._outbox.values():
                observation = entry.event.data.get("observation")
                if (
                    entry.event.tenant_id == tenant_id
                    and entry.published_at is None
                    and entry.quarantined_at is None
                    and isinstance(observation, Mapping)
                    and observation.get("sourceId") == source_id
                ):
                    pending.append(entry)
            return SourceIngestionState(
                tenant_id=tenant_id,
                source_id=source_id,
                stream_id=checkpoint.stream_id,
                checkpoint_sequence=checkpoint.sequence,
                checkpoint_committed_at=checkpoint.committed_at,
                latest_observed_at=(
                    latest.resource.observed_at if latest is not None else None
                ),
                latest_recorded_at=latest.recorded_at if latest is not None else None,
                accepted_observation_count=len(observations),
                pending_event_count=len(pending),
                oldest_pending_event_recorded_at=(
                    min(item.created_at for item in pending) if pending else None
                ),
            )

    def commit_checkpoint(self, checkpoint: SourceCheckpoint, *, mode: str) -> None:
        if mode not in ("incremental", "reconciliation"):
            raise ValueError("checkpoint mode is invalid")
        key = (checkpoint.tenant_id, checkpoint.source_id)
        with self._lock:
            current = self._checkpoints.get(key)
            self._validate_checkpoint_advance(current, checkpoint, mode)
            if current is not None and self._same_checkpoint(current, checkpoint):
                return
            self._checkpoints[key] = checkpoint

    def get_reconciliation(
        self, tenant_id: str, source_id: str
    ) -> Optional[ReconciliationSnapshot]:
        with self._lock:
            return self._reconciliations.get((tenant_id, source_id))

    def commit_reconciliation(
        self,
        snapshot: ReconciliationSnapshot,
        checkpoint: SourceCheckpoint,
    ) -> None:
        self._validate_reconciliation(snapshot, checkpoint)
        key = (snapshot.tenant_id, snapshot.source_id)
        with self._lock:
            current_snapshot = self._reconciliations.get(key)
            if current_snapshot is not None:
                if current_snapshot.snapshot_id == snapshot.snapshot_id:
                    if not self._same_reconciliation(current_snapshot, snapshot):
                        raise ValueError("reconciliation snapshot content conflicts")
                elif current_snapshot.scope_digest != snapshot.scope_digest:
                    raise ValueError("reconciliation source scope cannot change")
            self._validate_checkpoint_advance(
                self._checkpoints.get(key), checkpoint, "reconciliation"
            )
            self._reconciliations[key] = snapshot
            self._checkpoints[key] = checkpoint

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
    def _same_checkpoint(
        current: SourceCheckpoint, candidate: SourceCheckpoint
    ) -> bool:
        return (
            current.stream_id == candidate.stream_id
            and current.sequence == candidate.sequence
            and current.checkpoint == candidate.checkpoint
            and current.provider_cursors == candidate.provider_cursors
        )

    @staticmethod
    def _validate_checkpoint_advance(
        current: Optional[SourceCheckpoint],
        checkpoint: SourceCheckpoint,
        mode: str,
    ) -> None:
        if current is not None and current.stream_id == checkpoint.stream_id:
            if checkpoint.sequence < current.sequence:
                raise ValueError("checkpoint sequence cannot move backwards")
            if (
                checkpoint.sequence == current.sequence
                and (
                    checkpoint.checkpoint != current.checkpoint
                    or checkpoint.provider_cursors != current.provider_cursors
                )
            ):
                raise ValueError("checkpoint content conflicts at the same sequence")
        elif current is not None and mode != "reconciliation":
            raise ValueError("checkpoint stream reset requires reconciliation")

    @staticmethod
    def _validate_reconciliation(
        snapshot: ReconciliationSnapshot,
        checkpoint: SourceCheckpoint,
    ) -> None:
        if (
            snapshot.tenant_id != checkpoint.tenant_id
            or snapshot.source_id != checkpoint.source_id
            or snapshot.stream_id != checkpoint.stream_id
            or snapshot.sequence != checkpoint.sequence
            or snapshot.checkpoint != checkpoint.checkpoint
            or snapshot.committed_at != checkpoint.committed_at
            or snapshot.resource_uids != tuple(sorted(set(snapshot.resource_uids)))
            or snapshot.tombstoned_uids
            != tuple(sorted(set(snapshot.tombstoned_uids)))
            or set(snapshot.resource_uids).intersection(snapshot.tombstoned_uids)
            or len(snapshot.resource_uids) > 10_000
            or len(snapshot.tombstoned_uids) > 10_000
            or snapshot.sequence < 0
            or snapshot.sequence > 9_007_199_254_740_991
            or not re.fullmatch(r"snap_[a-f0-9]{32}", snapshot.snapshot_id)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", snapshot.scope_digest)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", snapshot.result_digest)
            or any(
                not re.fullmatch(r"res_[a-f0-9]{32}", uid)
                for uid in snapshot.resource_uids + snapshot.tombstoned_uids
            )
        ):
            raise ValueError("reconciliation state is invalid")

    @staticmethod
    def _same_reconciliation(
        current: ReconciliationSnapshot,
        incoming: ReconciliationSnapshot,
    ) -> bool:
        return (
            current.tenant_id,
            current.source_id,
            current.stream_id,
            current.snapshot_id,
            current.scope_digest,
            current.sequence,
            current.checkpoint,
            current.result_digest,
            current.resource_uids,
            current.tombstoned_uids,
        ) == (
            incoming.tenant_id,
            incoming.source_id,
            incoming.stream_id,
            incoming.snapshot_id,
            incoming.scope_digest,
            incoming.sequence,
            incoming.checkpoint,
            incoming.result_digest,
            incoming.resource_uids,
            incoming.tombstoned_uids,
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
        resource: Mapping[str, object],
    ) -> PolicyDecision:
        if not actor.actor_id or actor.actor_id == "anonymous":
            return PolicyDecision(False, "actor.anonymous")
        if resource.get("tenantId") != actor.tenant_id:
            return PolicyDecision(False, "tenant.scope_mismatch")
        if action not in (
            "action:approve",
            "action:execute",
            "action:propose",
            "action:read",
            "evidence:collect",
            "event-delivery-health:read",
            "event-delivery-slo:read",
            "ingestion-telemetry:read",
            "investigation-completion-slo:read",
            "plugin:open-session",
            "resource-projection:rebuild",
            "resource:ingest",
            "resource:read",
            "telemetry-export-health:read",
        ):
            return PolicyDecision(False, "action.unsupported")
        return PolicyDecision(True, "development.allow")


# Transitional alias for callers that used the foundation repository name.
InMemoryResourceRepository = InMemoryResourceStore
