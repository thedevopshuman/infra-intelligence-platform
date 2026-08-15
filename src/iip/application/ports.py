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


class AuthenticationError(PermissionError):
    """Credential authentication failure with a stable external code."""


class AuthenticationConfigurationError(RuntimeError):
    """Fail-closed authentication configuration error."""


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
    provider_cursors: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ReconciliationSnapshot:
    """Last complete resource membership for one tenant-scoped source."""

    tenant_id: str
    source_id: str
    stream_id: str
    snapshot_id: str
    scope_digest: str
    sequence: int
    checkpoint: str
    result_digest: str
    resource_uids: tuple[str, ...]
    tombstoned_uids: tuple[str, ...]
    committed_at: str


@dataclass(frozen=True)
class ProjectionRebuildResult:
    """Tenant-scoped projection recovery result with verifiable state digests."""

    tenant_id: str
    dry_run: bool
    drift_detected: bool
    rebuild_performed: bool
    resource_count: int
    relationship_count: int
    latest_observation_offset: int
    before_digest: str
    expected_digest: str
    after_digest: str


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

    def commit_checkpoint(self, checkpoint: SourceCheckpoint, *, mode: str) -> None:
        """Advance a source cursor only after its full batch is durable."""


class ReconciliationRepository(Protocol):
    def get_reconciliation(
        self, tenant_id: str, source_id: str
    ) -> Optional[ReconciliationSnapshot]:
        """Return the last complete membership for exactly one source."""

    def commit_reconciliation(
        self,
        snapshot: ReconciliationSnapshot,
        checkpoint: SourceCheckpoint,
    ) -> None:
        """Atomically commit complete membership and its source checkpoint."""


class ResourceProjectionMaintenance(Protocol):
    def rebuild_projections(
        self,
        tenant_id: str,
        *,
        dry_run: bool,
        max_resources: int,
    ) -> ProjectionRebuildResult:
        """Verify or rebuild one tenant from immutable accepted observations."""


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


class Authenticator(Protocol):
    def authenticate_bearer(self, token: str) -> ActorContext:
        """Derive actor and tenant context from one presented Bearer credential."""


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

    def list(
        self,
        actor: ActorContext,
        *,
        resource_uids: tuple[str, ...] = (),
        evidence_types: tuple[str, ...] = (),
        limit: int = 100,
    ) -> Iterable[Mapping[str, object]]:
        """List bounded evidence metadata within one explicit tenant scope."""


class InvestigationRepository(Protocol):
    def commit_investigation(
        self,
        actor: ActorContext,
        investigation_id: str,
        request: Mapping[str, object],
        report: Mapping[str, object],
    ) -> None:
        """Atomically persist an immutable request and its terminal report."""

    def get_investigation(
        self, actor: ActorContext, investigation_id: str
    ) -> Optional[Mapping[str, object]]:
        """Return one report only from the actor's tenant scope."""

    def get_investigation_request(
        self, actor: ActorContext, investigation_id: str
    ) -> Optional[Mapping[str, object]]:
        """Return the immutable input paired with a stored report."""


class ActionRepository(Protocol):
    def get_proposal_by_key(
        self, actor: ActorContext, idempotency_key: str
    ) -> Optional[Mapping[str, object]]:
        """Resolve a proposal by tenant-scoped idempotency key."""

    def get_proposal(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[Mapping[str, object]]:
        """Resolve one tenant-scoped proposal."""

    def commit_proposal(
        self, actor: ActorContext, document: Mapping[str, object]
    ) -> None:
        """Persist one immutable proposal."""

    def get_approval(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[Mapping[str, object]]:
        """Resolve the decision for one proposal."""

    def commit_approval(
        self, actor: ActorContext, document: Mapping[str, object]
    ) -> None:
        """Persist one immutable approval decision."""

    def get_action_result(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[Mapping[str, object]]:
        """Resolve an idempotent terminal action result."""

    def commit_action_result(
        self, actor: ActorContext, document: Mapping[str, object]
    ) -> None:
        """Persist a terminal action result once."""


@dataclass(frozen=True)
class ActionExecutionOutcome:
    """Provider-neutral result returned by a request-scoped executor."""

    outcome: str
    provider: str
    operation_ref: str
    verification_status: str
    verification_summary: str


class ActionExecutor(Protocol):
    def execute(
        self,
        actor: ActorContext,
        proposal: Mapping[str, object],
        *,
        approval_id: str,
    ) -> ActionExecutionOutcome:
        """Execute exactly one approved, idempotent, request-scoped operation."""


class PluginSessionRepository(Protocol):
    def commit_plugin_session(
        self, actor: ActorContext, document: Mapping[str, object]
    ) -> None:
        """Persist a bounded session document without raw token material."""

    def get_plugin_session(
        self, actor: ActorContext, session_id: str
    ) -> Optional[Mapping[str, object]]:
        """Resolve one tenant-scoped plugin session."""


class AuditSink(Protocol):
    def append_audit(
        self,
        actor: ActorContext,
        category: str,
        document: Mapping[str, object],
    ) -> str:
        """Append an immutable record and return its provider-neutral reference."""


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
