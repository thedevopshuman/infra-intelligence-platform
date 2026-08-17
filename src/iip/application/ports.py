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


class ReadinessError(RuntimeError):
    """A required runtime dependency cannot safely serve requests."""


class AuthenticationError(PermissionError):
    """Credential authentication failure with a stable external code."""


class AuthenticationConfigurationError(RuntimeError):
    """Fail-closed authentication configuration error."""


class PolicyConfigurationError(RuntimeError):
    """Fail-closed policy-adapter configuration error."""


class EventPublisherConfigurationError(RuntimeError):
    """Fail-closed event-publisher configuration error."""


class EventPublicationError(RuntimeError):
    """A provider-neutral event delivery failure with a stable code."""


class ReadinessProbe(Protocol):
    def check(self) -> None:
        """Return only when required dependencies are ready for traffic."""


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
    policy_snapshot_ref: Optional[str] = None


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
class QuarantinedOutboxMessage:
    """Value-minimized terminal delivery record for one exact tenant."""

    message_id: int
    tenant_id: str
    event_id: str
    event_source: str
    event_type: str
    subject: str
    attempts: int
    quarantined_at: str
    last_error_code: str


@dataclass(frozen=True)
class EventDeliveryState:
    """Bounded tenant-scoped outbox health facts returned by storage."""

    tenant_id: str
    pending_events: int
    in_flight_events: int
    retrying_events: int
    quarantined_events: int
    oldest_pending_event_recorded_at: Optional[str]
    quarantined: tuple[QuarantinedOutboxMessage, ...]


@dataclass(frozen=True)
class EventDeliverySloState:
    """Aggregate durable publication outcomes for one exact tenant window."""

    tenant_id: str
    window_start: str
    window_end: str
    maturity_cutoff: str
    created_events: int
    immature_events: int
    eligible_events: int
    within_objective_events: int
    late_delivered_events: int
    undelivered_events: int
    quarantined_events: int


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
class SourceIngestionState:
    """Tenant/source telemetry facts returned without policy interpretation."""

    tenant_id: str
    source_id: str
    stream_id: str
    checkpoint_sequence: int
    checkpoint_committed_at: str
    latest_observed_at: Optional[str]
    latest_recorded_at: Optional[str]
    accepted_observation_count: int
    pending_event_count: int
    oldest_pending_event_recorded_at: Optional[str]


@dataclass(frozen=True)
class IngestionFreshnessMeasurement:
    """Provider-neutral freshness values offered to an observational sink."""

    tenant_id: str
    source_id: str
    within_objective: bool
    checkpoint_age_seconds: float
    observation_age_seconds: Optional[float]
    ingestion_delay_seconds: Optional[float]
    accepted_observation_count: int
    pending_event_count: int
    oldest_pending_event_age_seconds: Optional[float]
    violations: tuple[str, ...]


@dataclass(frozen=True)
class QueryAvailabilityMeasurement:
    """Bounded HTTP query outcome offered to an observational telemetry sink."""

    operation: str
    outcome: str
    availability: str
    duration_seconds: float
    objective_window_seconds: int
    objective_minimum_availability_basis_points: int
    objective_minimum_eligible_requests: int


@dataclass(frozen=True)
class InvestigationExecutionMeasurement:
    """Bounded terminal facts offered to an observational telemetry sink."""

    tenant_id: str
    investigation_id: str
    outcome: str
    terminal_reason: str
    started_at: str
    completed_at: str
    wall_time_seconds: float
    tool_calls: int
    evidence_items: int


@dataclass(frozen=True)
class TelemetryExportSignalState:
    """Bounded, provider-neutral delivery state for one telemetry signal."""

    signal: str
    enabled: bool
    status: str
    attempts: int
    successes: int
    failures: int
    consecutive_failures: int
    last_attempt_at: Optional[str] = None
    last_success_at: Optional[str] = None
    last_failure_at: Optional[str] = None
    last_failure_code: Optional[str] = None


@dataclass(frozen=True)
class TelemetryExportInstanceState:
    """Pseudonymous latest exporter state reported by one runtime instance."""

    instance_id: str
    component: str
    started_at: str
    last_reported_at: str
    signals: tuple[TelemetryExportSignalState, ...]


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
class ContextDocumentQuery:
    """Credential-free bounded repository/runbook query for one tenant scope."""

    tenant_id: str
    actor_id: str
    request_id: str
    integration_id: str
    resource_uids: tuple[str, ...]
    kinds: tuple[str, ...]
    reference_ids: tuple[str, ...]
    max_documents: int
    max_excerpt_chars: int
    max_bytes: int
    deadline: str


@dataclass(frozen=True)
class ContextDocument:
    """One untrusted repository/runbook document returned by a backend."""

    reference_id: str
    resource_uids: tuple[str, ...]
    kind: str
    title: str
    locator: str
    revision: str
    content: str


@dataclass(frozen=True)
class ContextDocumentsResult:
    """Untrusted bounded result returned by a repository/runbook backend."""

    executed_at: str
    status: str
    documents: tuple[ContextDocument, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceRedactionResult:
    """Decoded artifact bytes after mandatory redaction inspection."""

    content: bytes
    methods: tuple[str, ...]


@dataclass(frozen=True)
class TelemetryMetricsQuery:
    """Credential-free normalized metric query passed to a backend adapter."""

    tenant_id: str
    actor_id: str
    request_id: str
    integration_id: str
    resource_uids: tuple[str, ...]
    start: str
    end: str
    metric: str
    filters: tuple[tuple[str, str, str], ...]
    aggregation: str
    step_seconds: int
    group_by: tuple[str, ...]
    max_series: int
    max_data_points: int
    max_bytes: int
    deadline: str


@dataclass(frozen=True)
class TelemetryMetricPoint:
    """One untrusted backend metric value at an explicit instant."""

    timestamp: str
    value: float


@dataclass(frozen=True)
class TelemetryMetricSeries:
    """One untrusted metric series returned by a backend adapter."""

    metric: str
    unit: str
    attributes: tuple[tuple[str, str], ...]
    points: tuple[TelemetryMetricPoint, ...]


@dataclass(frozen=True)
class TelemetryMetricsResult:
    """Untrusted bounded result returned by a telemetry backend adapter."""

    executed_at: str
    status: str
    series: tuple[TelemetryMetricSeries, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class TelemetryLogsQuery:
    """Credential-free normalized log query passed to a backend adapter."""

    tenant_id: str
    actor_id: str
    request_id: str
    integration_id: str
    resource_uids: tuple[str, ...]
    start: str
    end: str
    service_names: tuple[str, ...]
    severities: tuple[str, ...]
    filters: tuple[tuple[str, str, str], ...]
    max_records: int
    max_bytes: int
    deadline: str


@dataclass(frozen=True)
class TelemetryLogRecord:
    """One untrusted normalized log record returned by a backend adapter."""

    record_id: str
    resource_uid: str
    timestamp: str
    severity: str
    service_name: str
    body: str
    attributes: tuple[tuple[str, str], ...]
    observed_timestamp: Optional[str] = None
    trace_id: Optional[str] = None
    span_id: Optional[str] = None


@dataclass(frozen=True)
class TelemetryLogsResult:
    """Untrusted bounded result returned by a log backend adapter."""

    executed_at: str
    status: str
    records: tuple[TelemetryLogRecord, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class KubernetesEventResourceRef:
    """Scoped platform identity plus the external identity an adapter may query."""

    platform_uid: str
    provider: str
    resource_type: str
    external_id: str


@dataclass(frozen=True)
class KubernetesEventQuery:
    """Credential-free normalized Kubernetes Event query for one integration."""

    tenant_id: str
    actor_id: str
    request_id: str
    integration_id: str
    resources: tuple[KubernetesEventResourceRef, ...]
    start: str
    end: str
    severities: tuple[str, ...]
    reasons: tuple[str, ...]
    max_events: int
    max_bytes: int
    deadline: str


@dataclass(frozen=True)
class KubernetesEventRecord:
    """One untrusted normalized event returned by a Kubernetes adapter."""

    event_id: str
    resource_uid: str
    severity: str
    reason: str
    condition: str
    first_observed_at: str
    last_observed_at: str
    occurrence_count: int
    reporting_controller: Optional[str] = None
    message: Optional[str] = None


@dataclass(frozen=True)
class KubernetesEventsResult:
    """Untrusted bounded event result returned by a Kubernetes adapter."""

    executed_at: str
    status: str
    events: tuple[KubernetesEventRecord, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CredentialLeaseRequest:
    """Exact request scope supplied to a credential broker by an adapter."""

    tenant_id: str
    actor_id: str
    integration_id: str
    credential_ref: str
    provider: str
    scopes: tuple[str, ...]
    deadline: str


@dataclass(frozen=True, repr=False)
class CredentialLease:
    """Secret-bearing lease that must remain inside the requesting adapter."""

    scheme: str
    secret: str
    expires_at: str | None = None

    def __repr__(self) -> str:
        return (
            "CredentialLease(scheme="
            f"{self.scheme!r}, secret=<redacted>, expires_at={self.expires_at!r})"
        )


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

    def quarantine_outbox(
        self,
        tenant_id: str,
        worker_id: str,
        message_id: int,
        error_code: str,
    ) -> bool:
        """Terminally quarantine a message only while the worker owns its lease."""

    def get_event_delivery_state(
        self,
        tenant_id: str,
        *,
        quarantine_limit: int = 50,
    ) -> EventDeliveryState:
        """Return exact-tenant backlog and bounded quarantine facts."""

    def get_event_delivery_slo_state(
        self,
        tenant_id: str,
        *,
        window_start: str,
        window_end: str,
        maturity_cutoff: str,
        latency_objective_seconds: int,
    ) -> EventDeliverySloState:
        """Return aggregate outcomes for one exact tenant and mature cohort."""

    def get_quarantined_outbox(
        self,
        tenant_id: str,
        message_id: int,
    ) -> Optional[QuarantinedOutboxMessage]:
        """Return one value-minimized quarantined row from an exact tenant."""

    def requeue_quarantined_outbox(
        self,
        tenant_id: str,
        message_id: int,
        *,
        expected_event_id: str,
        expected_quarantined_at: str,
        expected_attempts: int,
    ) -> bool:
        """Atomically requeue one exact quarantine generation when all facts match."""


class SourceCheckpointRepository(Protocol):
    def get_checkpoint(self, tenant_id: str, source_id: str) -> Optional[SourceCheckpoint]:
        """Return the committed cursor for exactly one tenant and source."""

    def commit_checkpoint(self, checkpoint: SourceCheckpoint, *, mode: str) -> None:
        """Advance a source cursor only after its full batch is durable."""


class SourceIngestionTelemetryRepository(Protocol):
    def get_source_ingestion_state(
        self, tenant_id: str, source_id: str
    ) -> Optional[SourceIngestionState]:
        """Return point-in-time telemetry facts for exactly one tenant/source."""


class IngestionTelemetrySink(Protocol):
    def record_ingestion_freshness(
        self, measurement: IngestionFreshnessMeasurement
    ) -> None:
        """Record without network I/O or changing the owning use-case result."""


class QueryAvailabilitySink(Protocol):
    def record_query_availability(
        self, measurement: QueryAvailabilityMeasurement
    ) -> None:
        """Record one bounded query outcome without becoming serving authority."""


class InvestigationTelemetrySink(Protocol):
    def record_investigation_execution(
        self, measurement: InvestigationExecutionMeasurement
    ) -> None:
        """Record terminal execution facts without becoming workflow authority."""


class InvestigationSignalCatalog(Protocol):
    def profiles(self) -> Iterable[Mapping[str, object]]:
        """Return bounded protected profiles for composition-time validation."""

    def get_profile(self, tenant_id: str) -> Optional[Mapping[str, object]]:
        """Return the one profile bound to exactly this tenant, when configured."""


class TelemetryExportHealthReader(Protocol):
    def read_export_health(self) -> Iterable[TelemetryExportSignalState]:
        """Return bounded process-local delivery state without provider details."""


class TelemetryExportHealthRepository(Protocol):
    def record_telemetry_export_health(
        self,
        state: TelemetryExportInstanceState,
        *,
        expire_before: str,
    ) -> None:
        """Upsert one internal instance heartbeat and expire older observations."""

    def list_telemetry_export_health(
        self,
        *,
        reported_since: str,
        limit: int,
    ) -> Iterable[TelemetryExportInstanceState]:
        """Return recent instance observations without tenant or provider details."""

    def retire_telemetry_export_health(self, instance_id: str) -> None:
        """Remove one gracefully stopped runtime instance from the live view."""


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


class TelemetryMetricsBackend(Protocol):
    def query_metrics(self, request: TelemetryMetricsQuery) -> TelemetryMetricsResult:
        """Query one configured backend without exposing credentials or vendor types."""


class TelemetryLogsBackend(Protocol):
    def query_logs(self, request: TelemetryLogsQuery) -> TelemetryLogsResult:
        """Query scoped logs without exposing credentials or vendor query types."""


class KubernetesEventsBackend(Protocol):
    def query_events(self, request: KubernetesEventQuery) -> KubernetesEventsResult:
        """Query scoped Kubernetes Events without exposing credentials or client types."""


class ContextDocumentsBackend(Protocol):
    def query_context(self, request: ContextDocumentQuery) -> ContextDocumentsResult:
        """Read allowlisted repository/runbook context without exposing storage details."""


class CredentialBroker(Protocol):
    def resolve(self, request: CredentialLeaseRequest) -> CredentialLease:
        """Issue one request-scoped credential lease to an adapter."""


@dataclass(frozen=True, repr=False)
class PluginMediationBinding:
    """Protected host binding for one public, invocation-scoped read grant."""

    tenant_id: str
    plugin_id: str
    plugin_version: str
    grant_id: str
    integration_id: str
    provider: str
    destination: str
    credential_name: str
    endpoint: str
    credential_ref: str
    ca_bundle_path: Optional[str]
    path_templates: tuple[str, ...]
    query_keys: tuple[str, ...]
    scopes: tuple[str, ...]
    max_requests: int
    max_response_bytes: int

    def __repr__(self) -> str:
        return (
            "PluginMediationBinding(tenant_id="
            f"{self.tenant_id!r}, plugin_id={self.plugin_id!r}, "
            f"plugin_version={self.plugin_version!r}, grant_id={self.grant_id!r}, "
            "endpoint=<protected>, credential_ref=<protected>)"
        )


class PluginMediationBindingRegistry(Protocol):
    def resolve_plugin_mediation_binding(
        self,
        actor: ActorContext,
        plugin_id: str,
        plugin_version: str,
        grant_id: str,
    ) -> Optional[PluginMediationBinding]:
        """Resolve protected authority only for one exact tenant and plugin grant."""


class PluginMediationGateway(Protocol):
    def fetch_plugin_json(
        self,
        actor: ActorContext,
        binding: PluginMediationBinding,
        *,
        path: str,
        query: Mapping[str, tuple[str, ...]],
        deadline: str,
        max_response_bytes: int,
    ) -> object:
        """Perform one exact, credential-mediated provider read and return JSON."""


class PluginActionProposalGateway(Protocol):
    def propose_plugin_action(
        self,
        actor: ActorContext,
        *,
        investigation_id: str,
        action_type: str,
        target_resource_uid: str,
        parameters: Mapping[str, object],
        idempotency_key: str,
        expires_at: str,
        dry_run: bool,
    ) -> Mapping[str, object]:
        """Create one governed proposal without approval or execution authority."""


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


@dataclass(frozen=True)
class EvidenceRetentionState:
    """Value-minimized artifact-retention facts for one exact tenant."""

    tenant_id: str
    evaluated_at: str
    stored_artifacts: int
    eligible_artifacts: int
    expired_artifacts: int
    remaining_eligible_artifacts: int
    legal_hold_artifacts: int
    audit_ref: Optional[str] = None


class EvidenceRetentionStore(Protocol):
    def evaluate_evidence_retention(
        self,
        tenant_id: str,
        evaluated_at: str,
        *,
        ephemeral_seconds: int,
        standard_seconds: int,
        extended_seconds: int,
        limit: int,
        expire: bool,
        policy_digest: str,
    ) -> EvidenceRetentionState:
        """Observe or atomically expire bounded artifact bytes and append audit."""


class InvestigationRepository(Protocol):
    def start_investigation(
        self,
        actor: ActorContext,
        investigation_id: str,
        request: Mapping[str, object],
        status: Mapping[str, object],
    ) -> None:
        """Persist an accepted request and its bounded running lease before tools run."""

    def commit_investigation(
        self,
        actor: ActorContext,
        investigation_id: str,
        request: Mapping[str, object],
        report: Mapping[str, object],
        status: Mapping[str, object],
    ) -> None:
        """Atomically close an accepted request with an immutable terminal report."""

    def get_investigation(
        self, actor: ActorContext, investigation_id: str
    ) -> Optional[Mapping[str, object]]:
        """Return one report only from the actor's tenant scope."""

    def get_investigation_request(
        self, actor: ActorContext, investigation_id: str
    ) -> Optional[Mapping[str, object]]:
        """Return the immutable accepted input, including while work is running."""

    def get_investigation_status(
        self, actor: ActorContext, investigation_id: str
    ) -> Optional[Mapping[str, object]]:
        """Return the current tenant-scoped lifecycle status."""

    def request_investigation_cancellation(
        self,
        actor: ActorContext,
        investigation_id: str,
        status: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Atomically record the first cancellation request or return terminal state."""


@dataclass(frozen=True)
class InvestigationJobClaim:
    """One explicitly tenant-scoped background investigation lease."""

    actor: ActorContext
    investigation_id: str
    request: Mapping[str, object]
    claim_token: str
    attempts: int


@dataclass(frozen=True)
class InvestigationCompletionSloState:
    """Aggregate durable job outcomes for one exact tenant window."""

    tenant_id: str
    window_start: str
    window_end: str
    maturity_cutoff: str
    accepted_jobs: int
    immature_jobs: int
    eligible_jobs: int
    within_objective_jobs: int
    late_completed_jobs: int
    failed_jobs: int
    cancelled_jobs: int
    unfinished_jobs: int


class InvestigationJobRepository(Protocol):
    def enqueue_investigation_job(
        self,
        actor: ActorContext,
        investigation_id: str,
        request: Mapping[str, object],
        status: Mapping[str, object],
        *,
        max_outstanding_jobs_per_tenant: int = 1000,
    ) -> Mapping[str, object]:
        """Idempotently enqueue within one exact tenant's outstanding-job cap."""

    def get_investigation_job(
        self, actor: ActorContext, investigation_id: str
    ) -> Optional[Mapping[str, object]]:
        """Return one job only from the actor's explicit tenant scope."""

    def get_investigation_completion_slo_state(
        self,
        tenant_id: str,
        *,
        window_start: str,
        window_end: str,
        maturity_cutoff: str,
        completion_objective_seconds: int,
    ) -> InvestigationCompletionSloState:
        """Return aggregate durable completion outcomes for one mature cohort."""

    def claim_investigation_job(
        self,
        tenant_id: str,
        worker_id: str,
        now: str,
        lease_expires_at: str,
        *,
        max_tenant_concurrency: int = 1,
    ) -> Optional[InvestigationJobClaim]:
        """Claim within one tenant and its deployment-wide live-lease cap."""

    def heartbeat_investigation_job(
        self,
        tenant_id: str,
        investigation_id: str,
        worker_id: str,
        claim_token: str,
        status: Mapping[str, object],
    ) -> bool:
        """Renew exactly one live claim without widening tenant scope."""

    def release_investigation_job(
        self,
        tenant_id: str,
        investigation_id: str,
        worker_id: str,
        claim_token: str,
        status: Mapping[str, object],
        available_at: str,
        error_code: str,
    ) -> bool:
        """Release a retryable claim with a stable redacted error code."""

    def finish_investigation_job(
        self,
        tenant_id: str,
        investigation_id: str,
        worker_id: str,
        claim_token: str,
        status: Mapping[str, object],
    ) -> bool:
        """Atomically terminalize a claim owned by the named worker."""

    def request_investigation_job_cancellation(
        self,
        actor: ActorContext,
        investigation_id: str,
        status: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Cancel queued work or persist cancellation intent for a live claim."""


@dataclass(frozen=True)
class ActionWorkflowRecord:
    """Consistent tenant-scoped action records returned by one repository read."""

    proposal: Mapping[str, object]
    approval: Optional[Mapping[str, object]] = None
    execution_status: Optional[Mapping[str, object]] = None
    result: Optional[Mapping[str, object]] = None


@dataclass(frozen=True)
class ActionExecutionTransition:
    """Result of one atomic fail-closed action reconciliation attempt."""

    status: Mapping[str, object]
    transitioned: bool


class ActionRepository(Protocol):
    def get_proposal_by_key(
        self, actor: ActorContext, idempotency_key: str
    ) -> Optional[Mapping[str, object]]:
        """Resolve a proposal by tenant-scoped idempotency key."""

    def get_proposal(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[Mapping[str, object]]:
        """Resolve one tenant-scoped proposal."""

    def get_action_workflow(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[ActionWorkflowRecord]:
        """Read one proposal and its related records from one consistent snapshot."""

    def list_action_workflows(
        self,
        actor: ActorContext,
        *,
        before_created_at: Optional[str],
        before_proposal_id: Optional[str],
        limit: int,
    ) -> Iterable[ActionWorkflowRecord]:
        """Return newest tenant workflows before an optional stable position."""

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

    def claim_action_execution(
        self, actor: ActorContext, document: Mapping[str, object]
    ) -> bool:
        """Atomically claim the one permitted execution attempt for a proposal."""

    def get_action_execution_status(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[Mapping[str, object]]:
        """Resolve the durable execution lifecycle for one proposal."""

    def list_expired_action_executions(
        self,
        actor: ActorContext,
        observed_at: str,
        *,
        limit: int,
    ) -> Iterable[Mapping[str, object]]:
        """List bounded exact-tenant execution leases eligible for reconciliation."""

    def reconcile_expired_action_execution(
        self,
        actor: ActorContext,
        proposal_id: str,
        observed_at: str,
        document: Mapping[str, object],
        audit_document: Mapping[str, object],
    ) -> ActionExecutionTransition:
        """Atomically fail an expired lease closed and append its audit record once."""

    def commit_action_result(
        self,
        actor: ActorContext,
        document: Mapping[str, object],
        status: Mapping[str, object],
    ) -> None:
        """Atomically persist the terminal action result and lifecycle state once."""


@dataclass(frozen=True)
class ActionExecutionOutcome:
    """Provider-neutral result returned by a request-scoped executor."""

    outcome: str
    provider: str
    operation_ref: str
    verification_status: str
    verification_summary: str
    error_code: Optional[str] = None
    rollback_status: Optional[str] = None
    rollback_summary: Optional[str] = None


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


@dataclass(frozen=True)
class PluginInvocationClaim:
    """Atomic ownership outcome for one canonical plugin invocation."""

    state: str
    result: Optional[Mapping[str, object]] = None


class PluginInvocationLedger(Protocol):
    def claim_plugin_invocation(
        self,
        actor: ActorContext,
        session: Mapping[str, object],
        invocation: Mapping[str, object],
        request_digest: str,
        claimed_at: str,
    ) -> PluginInvocationClaim:
        """Claim once, recover a terminal result, or report ambiguous ownership."""

    def commit_plugin_invocation_result(
        self,
        actor: ActorContext,
        request_digest: str,
        result: Mapping[str, object],
    ) -> None:
        """Persist one terminal host-created result for its exact request digest."""

    def get_plugin_invocation_status(
        self, actor: ActorContext, request_id: str
    ) -> Optional[Mapping[str, object]]:
        """Resolve one exact-tenant public invocation lifecycle document."""

    def request_plugin_invocation_cancellation(
        self,
        actor: ActorContext,
        request_id: str,
        status: Mapping[str, object],
        audit_document: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Atomically record first cancellation intent and its audit record."""

    def plugin_invocation_cancellation_requested(
        self, actor: ActorContext, request_id: str, request_digest: str
    ) -> bool:
        """Poll exact claim cancellation without exposing request content."""

    def reconcile_plugin_invocation(
        self,
        actor: ActorContext,
        request_id: str,
        observed_at: str,
        result: Mapping[str, object],
        status: Mapping[str, object],
        audit_document: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Close one post-deadline ambiguous claim without replaying it."""


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
