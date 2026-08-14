"""Ports owned by the application layer and implemented by adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional, Protocol

from iip.domain.models import (
    ObservationDisposition,
    PlatformEvent,
    Resource,
    ResourceRelationshipEdge,
)


class PersistenceError(RuntimeError):
    """Provider-neutral durable-store failure with a stable external code."""


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


@dataclass(frozen=True)
class ResourceObservationRecord:
    """Immutable resource observation retained for tenant-scoped history."""

    offset: int
    resource: Resource
    disposition: ObservationDisposition
    observation_hash: str
    recorded_at: str


@dataclass(frozen=True)
class StoredEvent:
    """One durable event and its monotonic storage offset."""

    offset: int
    event: PlatformEvent


@dataclass(frozen=True)
class OutboxMessage:
    """Leased event delivery returned to one explicitly named worker."""

    message_id: int
    event: PlatformEvent
    attempts: int


@dataclass(frozen=True)
class SourceCheckpoint:
    """Last explicitly committed cursor for a tenant-scoped source."""

    tenant_id: str
    source_id: str
    stream_id: str
    sequence: int
    checkpoint: str
    committed_at: str


@dataclass(frozen=True)
class EvidenceProviderRequest:
    """Credential-free, bounded request passed across a provider boundary."""

    tenant_id: str
    actor_id: str
    evidence_type: str
    integration_id: str
    resource_uids: tuple[str, ...]
    locator: str
    query: Optional[str]
    max_bytes: int
    deadline: str


@dataclass(frozen=True)
class RawEvidenceArtifact:
    """Untrusted artifact returned by an evidence provider."""

    content: bytes
    media_type: str
    observed_at: str
    summary: str


@dataclass(frozen=True)
class EvidenceRedactionResult:
    """Decoded artifact bytes after mandatory redaction inspection."""

    content: bytes
    methods: tuple[str, ...]


class ResourceRepository(Protocol):
    def get(self, tenant_id: str, uid: str) -> Optional[Resource]:
        """Return a resource only from the requested tenant scope."""

    def list(self, tenant_id: str) -> Iterable[Resource]:
        """List resources visible in the requested tenant scope."""

    def get_many(self, tenant_id: str, uids: Iterable[str]) -> Iterable[Resource]:
        """Resolve a bounded resource set only within the requested tenant scope."""

    def history(
        self,
        tenant_id: str,
        uid: str,
        *,
        after_offset: int = 0,
        limit: int = 1000,
    ) -> Iterable[ResourceObservationRecord]:
        """Page immutable observations only from the requested tenant scope."""

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
        """Page current graph edges touching one tenant-scoped resource."""


class ResourceObservationStore(ResourceRepository, Protocol):
    def apply(
        self,
        resource: Resource,
        event: PlatformEvent,
        *,
        checkpoint_ready: bool = False,
    ) -> ResourceWriteResult:
        """Atomically apply one observation and its accepted-event side effects.

        Implementations persist the projection, immutable observation, event, and
        outbox entry together. A checkpoint is included only when the trusted
        caller confirms that every mutation represented by the cursor is durable.
        """


class EventLog(Protocol):
    def list_events(
        self,
        tenant_id: str,
        *,
        after_offset: int = 0,
        limit: int = 100,
    ) -> Iterable[StoredEvent]:
        """Replay tenant-scoped events after an exclusive storage offset."""


class EventOutbox(Protocol):
    def claim_outbox(
        self,
        tenant_id: str,
        worker_id: str,
        *,
        limit: int = 100,
        lease_seconds: int = 30,
    ) -> Iterable[OutboxMessage]:
        """Lease available messages within one explicit tenant scope."""

    def acknowledge_outbox(
        self,
        tenant_id: str,
        worker_id: str,
        message_id: int,
    ) -> bool:
        """Mark a message delivered only when the named worker holds its lease."""

    def release_outbox(
        self,
        tenant_id: str,
        worker_id: str,
        message_id: int,
        error_code: str,
        *,
        retry_after_seconds: int = 0,
    ) -> bool:
        """Release a leased message using a stable, non-sensitive error code."""


class SourceCheckpointRepository(Protocol):
    def get_checkpoint(self, tenant_id: str, source_id: str) -> Optional[SourceCheckpoint]:
        """Return the committed cursor for exactly one tenant and source."""


class EventPublisher(Protocol):
    def publish(self, event: PlatformEvent) -> None:
        """Durably publish or record an event according to adapter guarantees."""


class PolicyDecisionPoint(Protocol):
    def decide(
        self,
        actor: ActorContext,
        action: str,
        resource: Mapping[str, object],
    ) -> PolicyDecision:
        """Decide one action without granting authority beyond this request."""


class EvidenceProvider(Protocol):
    def fetch(self, request: EvidenceProviderRequest) -> RawEvidenceArtifact:
        """Retrieve one bounded artifact without exposing adapter credentials."""


class EvidenceRedactor(Protocol):
    def redact(
        self,
        content: bytes,
        *,
        media_type: str,
        evidence_type: str,
    ) -> EvidenceRedactionResult:
        """Inspect and redact decoded provider output before persistence."""


class EvidenceStore(Protocol):
    def commit(
        self,
        actor: ActorContext,
        evidence_id: str,
        document: Mapping[str, object],
        decoded_content: bytes,
    ) -> None:
        """Atomically persist immutable evidence metadata and artifact bytes."""

    def get(
        self,
        actor: ActorContext,
        evidence_id: str,
    ) -> Optional[Mapping[str, object]]:
        """Return metadata only from the actor's explicit tenant scope."""

    def read_artifact(
        self,
        actor: ActorContext,
        evidence_id: str,
    ) -> Optional[bytes]:
        """Return bytes only from the actor's explicit tenant scope."""


class EvidenceIdGenerator(Protocol):
    def new_id(self) -> str:
        """Return one opaque platform Evidence identifier."""


class Clock(Protocol):
    def now(self) -> str:
        """Return the current time as an RFC 3339 timestamp."""


class AgentExecutor(Protocol):
    def run(self, agent_id: str, request: Mapping[str, object]) -> Mapping[str, object]:
        """Run an agent against an explicitly scoped request."""


class PluginCatalog(Protocol):
    def resolve(self, plugin_id: str, version: str) -> Mapping[str, object]:
        """Resolve an installed plugin manifest by immutable identity."""
