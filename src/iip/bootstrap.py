"""Composition root for local and test runtime profiles."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from iip.adapters.actions import KubernetesRestartDryRunExecutor
from iip.adapters.auth import (
    DenyAllAuthenticator,
    HashedBearerAuthenticator,
    OidcJwtAuthenticator,
)
from iip.adapters.context import NoDataContextDocumentsBackend
from iip.adapters.evidence import (
    InMemoryEvidenceStore,
    NoDataKubernetesEventsBackend,
    NoDataTelemetryLogsBackend,
    NoDataTelemetryMetricsBackend,
    ResourceStateEvidenceProvider,
    StructuredTextRedactor,
    SystemClock,
    UuidEvidenceIdGenerator,
)
from iip.adapters.health import AlwaysReadyProbe
from iip.adapters.memory import AllowTenantPolicy, InMemoryResourceStore
from iip.adapters.operations import InMemoryOperationalStore
from iip.adapters.policy import ExternalHttpPolicyDecisionPoint
from iip.application.action_reconciliation import ActionReconciliationService
from iip.application.actions import GovernedActionService
from iip.application.collect_evidence import EvidenceCollectionService
from iip.application.context_evidence import ContextEvidenceProvider, ContextEvidenceService
from iip.application.ingest_collection import ResourceCollectionIngestionService
from iip.application.ingest_otlp_metrics import (
    OtlpMetricsIngestionService,
    OtlpMetricsReceiverAdapter,
    OtlpReceiverConfigurationError,
)
from iip.application.ingest_otlp_logs import (
    OtlpLogsIngestionService,
    OtlpLogsReceiverAdapter,
)
from iip.application.ports import (
    ActionExecutor,
    AuthenticationConfigurationError,
    Authenticator,
    EventLog,
    EventOutbox,
    CredentialBroker,
    ContextDocumentsBackend,
    IngestionTelemetrySink,
    InvestigationTelemetrySink,
    KubernetesEventsBackend,
    PolicyConfigurationError,
    PolicyDecisionPoint,
    ReadinessProbe,
    ResourceRepository,
    SourceCheckpointRepository,
    TelemetryMetricsBackend,
    TelemetryLogsBackend,
)
from iip.application.log_evidence import (
    LogEvidenceService,
    TelemetryLogsEvidenceProvider,
)
from iip.application.investigate import DeterministicInvestigationService
from iip.application.investigation_dispatch import InvestigationDispatchService
from iip.application.investigation_lifecycle import InvestigationLifecycleService
from iip.application.investigation_worker import InvestigationWorker
from iip.application.kubernetes_event_evidence import (
    KubernetesEventEvidenceService,
    KubernetesEventsEvidenceProvider,
)
from iip.application.ingest_resource import ResourceIngestionService
from iip.application.observe_ingestion import (
    IngestionFreshnessObjectives,
    IngestionFreshnessService,
    IngestionTelemetryInputError,
)
from iip.application.plugin_sessions import PluginSessionService
from iip.application.query_actions import ActionWorkflowQueryService
from iip.application.query_resources import ResourceQueryService
from iip.application.resource_change_evidence import (
    ResourceChangeEvidenceService,
    ResourceHistoryChangeEvidenceProvider,
)
from iip.application.sample_ingestion import (
    IngestionFreshnessSampler,
    IngestionMonitorTarget,
)
from iip.application.rebuild_projections import ProjectionRebuildService
from iip.application.telemetry_evidence import (
    TelemetryEvidenceService,
    TelemetryMetricsEvidenceProvider,
)


@dataclass(frozen=True)
class Runtime:
    """Concrete services and adapters owned by one process."""

    authenticator: Authenticator
    resources: ResourceRepository
    event_log: EventLog
    outbox: EventOutbox
    checkpoints: SourceCheckpointRepository
    ingestion: ResourceIngestionService
    collection_ingestion: ResourceCollectionIngestionService
    ingestion_telemetry: IngestionFreshnessService
    queries: ResourceQueryService
    evidence: EvidenceCollectionService
    kubernetes_event_evidence: KubernetesEventEvidenceService
    resource_change_evidence: ResourceChangeEvidenceService
    context_evidence: ContextEvidenceService
    telemetry_evidence: TelemetryEvidenceService
    log_evidence: LogEvidenceService
    otlp_metrics_ingestion: OtlpMetricsIngestionService | None
    otlp_logs_ingestion: OtlpLogsIngestionService | None
    investigations: DeterministicInvestigationService
    investigation_lifecycle: InvestigationLifecycleService
    investigation_dispatch: InvestigationDispatchService
    actions: GovernedActionService
    action_queries: ActionWorkflowQueryService
    plugin_sessions: PluginSessionService
    operational_store: Any
    investigation_jobs: Any
    evidence_store: Any
    readiness: ReadinessProbe
    telemetry_runtime: Any = None

    def force_flush_telemetry(self, timeout_millis: int = 10_000) -> bool:
        if self.telemetry_runtime is None:
            return True
        return bool(self.telemetry_runtime.force_flush(timeout_millis))

    def close(self) -> None:
        if self.telemetry_runtime is not None:
            self.telemetry_runtime.shutdown()


def build_local_runtime(
    authenticator: Authenticator | None = None,
    *,
    ingestion_objectives: IngestionFreshnessObjectives | None = None,
    ingestion_telemetry_sink: IngestionTelemetrySink | None = None,
    investigation_telemetry_sink: InvestigationTelemetrySink | None = None,
    telemetry_metrics_backend: TelemetryMetricsBackend | None = None,
    telemetry_logs_backend: TelemetryLogsBackend | None = None,
    kubernetes_events_backend: KubernetesEventsBackend | None = None,
    context_documents_backend: ContextDocumentsBackend | None = None,
    otlp_metrics_receiver: OtlpMetricsReceiverAdapter | None = None,
    otlp_logs_receiver: OtlpLogsReceiverAdapter | None = None,
    telemetry_runtime: Any = None,
    action_executor: ActionExecutor | None = None,
    policy: PolicyDecisionPoint | None = None,
    readiness: ReadinessProbe | None = None,
) -> Runtime:
    """Build the dependency graph for local execution."""

    store = InMemoryResourceStore()
    operational = InMemoryOperationalStore()
    evidence_store = InMemoryEvidenceStore()
    return _compose_runtime(
        store,
        operational,
        evidence_store,
        authenticator or DenyAllAuthenticator(),
        ingestion_objectives,
        ingestion_telemetry_sink,
        investigation_telemetry_sink,
        telemetry_metrics_backend,
        telemetry_logs_backend,
        kubernetes_events_backend,
        context_documents_backend,
        otlp_metrics_receiver,
        otlp_logs_receiver,
        telemetry_runtime,
        action_executor,
        policy,
        readiness,
    )


def _compose_runtime(
    store: Any,
    operational: Any,
    evidence_store: Any,
    authenticator: Authenticator,
    ingestion_objectives: IngestionFreshnessObjectives | None = None,
    ingestion_telemetry_sink: IngestionTelemetrySink | None = None,
    investigation_telemetry_sink: InvestigationTelemetrySink | None = None,
    telemetry_metrics_backend: TelemetryMetricsBackend | None = None,
    telemetry_logs_backend: TelemetryLogsBackend | None = None,
    kubernetes_events_backend: KubernetesEventsBackend | None = None,
    context_documents_backend: ContextDocumentsBackend | None = None,
    otlp_metrics_receiver: OtlpMetricsReceiverAdapter | None = None,
    otlp_logs_receiver: OtlpLogsReceiverAdapter | None = None,
    telemetry_runtime: Any = None,
    action_executor: ActionExecutor | None = None,
    configured_policy: PolicyDecisionPoint | None = None,
    readiness: ReadinessProbe | None = None,
) -> Runtime:
    """Compose use cases from ports without leaking adapters into their owners."""

    policy = configured_policy or AllowTenantPolicy()
    clock = SystemClock()
    ingestion = ResourceIngestionService(store, policy)
    queries = ResourceQueryService(store, policy)
    metrics_backend = (
        telemetry_metrics_backend
        if telemetry_metrics_backend is not None
        else NoDataTelemetryMetricsBackend(clock)
    )
    telemetry_provider = TelemetryMetricsEvidenceProvider(metrics_backend)
    logs_backend = (
        telemetry_logs_backend
        if telemetry_logs_backend is not None
        else NoDataTelemetryLogsBackend(clock)
    )
    log_provider = TelemetryLogsEvidenceProvider(logs_backend)
    event_backend = (
        kubernetes_events_backend
        if kubernetes_events_backend is not None
        else NoDataKubernetesEventsBackend(clock)
    )
    kubernetes_event_provider = KubernetesEventsEvidenceProvider(event_backend, store)
    resource_change_provider = ResourceHistoryChangeEvidenceProvider(store, clock)
    redactor = StructuredTextRedactor()
    context_backend = (
        context_documents_backend
        if context_documents_backend is not None
        else NoDataContextDocumentsBackend(clock)
    )
    context_provider = ContextEvidenceProvider(context_backend, redactor, clock)
    evidence = EvidenceCollectionService(
        store,
        {
            "resource-state": ResourceStateEvidenceProvider(store),
            "kubernetes-events": kubernetes_event_provider,
            "telemetry-query": telemetry_provider,
            "log-query": log_provider,
            "resource-history": resource_change_provider,
            "context-query": context_provider,
        },
        evidence_store,
        redactor,
        policy,
        UuidEvidenceIdGenerator(),
        clock,
    )
    telemetry_evidence = TelemetryEvidenceService(evidence, clock)
    log_evidence = LogEvidenceService(evidence, clock)
    kubernetes_event_evidence = KubernetesEventEvidenceService(evidence, clock)
    resource_change_evidence = ResourceChangeEvidenceService(evidence, clock)
    context_evidence = ContextEvidenceService(evidence, clock)
    investigations = DeterministicInvestigationService(
        store,
        evidence,
        operational,
        clock,
        kubernetes_events=kubernetes_event_evidence,
        resource_changes=resource_change_evidence,
        context=context_evidence,
        telemetry=telemetry_evidence,
        logs=log_evidence,
        evidence_store=evidence_store,
        telemetry_sink=investigation_telemetry_sink,
    )
    investigation_lifecycle = InvestigationLifecycleService(operational, clock)
    return Runtime(
        authenticator=authenticator,
        resources=store,
        event_log=store,
        outbox=store,
        checkpoints=store,
        ingestion=ingestion,
        collection_ingestion=ResourceCollectionIngestionService(
            ingestion,
            store,
            store,
            store,
            clock,
        ),
        ingestion_telemetry=IngestionFreshnessService(
            store,
            policy,
            clock,
            ingestion_objectives,
            ingestion_telemetry_sink,
        ),
        queries=queries,
        evidence=evidence,
        kubernetes_event_evidence=kubernetes_event_evidence,
        resource_change_evidence=resource_change_evidence,
        context_evidence=context_evidence,
        telemetry_evidence=telemetry_evidence,
        log_evidence=log_evidence,
        otlp_metrics_ingestion=(
            OtlpMetricsIngestionService(otlp_metrics_receiver, evidence, clock)
            if otlp_metrics_receiver is not None
            else None
        ),
        otlp_logs_ingestion=(
            OtlpLogsIngestionService(otlp_logs_receiver, evidence, clock)
            if otlp_logs_receiver is not None
            else None
        ),
        investigations=investigations,
        investigation_lifecycle=investigation_lifecycle,
        investigation_dispatch=InvestigationDispatchService(
            operational,
            investigations,
            investigation_lifecycle,
            clock,
        ),
        actions=GovernedActionService(
            store,
            policy,
            operational,
            action_executor or KubernetesRestartDryRunExecutor(),
            operational,
            clock,
            operational,
        ),
        action_queries=ActionWorkflowQueryService(operational, policy, clock),
        plugin_sessions=PluginSessionService(policy, operational, clock),
        operational_store=operational,
        investigation_jobs=operational,
        evidence_store=evidence_store,
        readiness=readiness or AlwaysReadyProbe(),
        telemetry_runtime=telemetry_runtime,
    )


def build_postgres_runtime(
    database_url: str,
    *,
    authenticator: Authenticator | None = None,
    migrate: bool = False,
    ingestion_objectives: IngestionFreshnessObjectives | None = None,
    ingestion_telemetry_sink: IngestionTelemetrySink | None = None,
    investigation_telemetry_sink: InvestigationTelemetrySink | None = None,
    telemetry_metrics_backend: TelemetryMetricsBackend | None = None,
    telemetry_logs_backend: TelemetryLogsBackend | None = None,
    kubernetes_events_backend: KubernetesEventsBackend | None = None,
    context_documents_backend: ContextDocumentsBackend | None = None,
    otlp_metrics_receiver: OtlpMetricsReceiverAdapter | None = None,
    otlp_logs_receiver: OtlpLogsReceiverAdapter | None = None,
    telemetry_runtime: Any = None,
    action_executor: ActionExecutor | None = None,
    policy: PolicyDecisionPoint | None = None,
    readiness_timeout_seconds: int = 2,
) -> Runtime:
    """Build a PostgreSQL-backed runtime without leaking the adapter into use cases."""

    from iip.adapters.postgres import (
        PostgresOperationalStore,
        PostgresReadinessProbe,
        PostgresResourceStore,
    )

    store = PostgresResourceStore(database_url)
    if migrate:
        store.migrate()
    operational = PostgresOperationalStore(database_url)
    return _compose_runtime(
        store,
        operational,
        operational,
        authenticator or DenyAllAuthenticator(),
        ingestion_objectives,
        ingestion_telemetry_sink,
        investigation_telemetry_sink,
        telemetry_metrics_backend,
        telemetry_logs_backend,
        kubernetes_events_backend,
        context_documents_backend,
        otlp_metrics_receiver,
        otlp_logs_receiver,
        telemetry_runtime,
        action_executor,
        policy,
        PostgresReadinessProbe(database_url, readiness_timeout_seconds),
    )


def build_projection_maintenance(database_url: str) -> ProjectionRebuildService:
    """Compose the privileged PostgreSQL projection-recovery use case."""

    from iip.adapters.postgres import PostgresResourceStore

    return ProjectionRebuildService(PostgresResourceStore(database_url), AllowTenantPolicy())


def build_runtime_from_env(*, include_action_executor: bool = True) -> Runtime:
    """Select a runtime profile from process configuration at the composition root."""

    return _build_runtime_from_env(
        _authenticator_from_env(),
        include_action_executor=include_action_executor,
    )


def build_workflow_worker_runtime_from_env() -> Runtime:
    """Compose a worker without interactive identity credentials or action impact."""

    return _build_runtime_from_env(
        DenyAllAuthenticator(),
        include_action_executor=False,
    )


def _build_runtime_from_env(
    authenticator: Authenticator,
    *,
    include_action_executor: bool,
) -> Runtime:
    policy = _policy_from_env()
    objectives = _ingestion_objectives_from_env()
    metrics_runtime = _otel_metrics_runtime_from_env()
    try:
        traces_runtime = _otel_traces_runtime_from_env()
    except Exception:
        if metrics_runtime is not None:
            metrics_runtime.shutdown()
        raise

    telemetry_runtime = _combine_telemetry_runtimes(
        metrics_runtime, traces_runtime
    )
    database_url = os.environ.get("IIP_DATABASE_URL")
    try:
        receiver_mode = os.environ.get("IIP_OTLP_RECEIVER_MODE", "disabled")
        if receiver_mode not in ("disabled", "shared"):
            raise OtlpReceiverConfigurationError("otlp.configuration.invalid")
        otlp_metrics_receiver = (
            _otlp_metrics_receiver_from_env()
            if receiver_mode == "shared"
            else None
        )
        otlp_logs_receiver = (
            _otlp_logs_receiver_from_env()
            if receiver_mode == "shared"
            else None
        )
        credential_broker = _credential_broker_from_env()
        action_executor = (
            _kubernetes_action_executor_from_env(credential_broker)
            if include_action_executor
            else None
        )
        telemetry_metrics_backend = _telemetry_metrics_backend_from_env(
            credential_broker
        )
        telemetry_logs_backend = _telemetry_logs_backend_from_env(
            credential_broker
        )
        kubernetes_events_backend = _kubernetes_events_backend_from_env(
            credential_broker
        )
        context_documents_backend = _context_documents_backend_from_env()
        if not database_url:
            return build_local_runtime(
                authenticator,
                ingestion_objectives=objectives,
                ingestion_telemetry_sink=(
                    metrics_runtime.sink
                    if metrics_runtime is not None
                    else None
                ),
                investigation_telemetry_sink=(
                    traces_runtime.sink if traces_runtime is not None else None
                ),
                telemetry_metrics_backend=telemetry_metrics_backend,
                telemetry_logs_backend=telemetry_logs_backend,
                kubernetes_events_backend=kubernetes_events_backend,
                context_documents_backend=context_documents_backend,
                otlp_metrics_receiver=otlp_metrics_receiver,
                otlp_logs_receiver=otlp_logs_receiver,
                telemetry_runtime=telemetry_runtime,
                action_executor=action_executor,
                policy=policy,
            )
        auto_migrate = (
            os.environ.get("IIP_DATABASE_AUTO_MIGRATE", "false").lower()
            == "true"
        )
        return build_postgres_runtime(
            database_url,
            authenticator=authenticator,
            migrate=auto_migrate,
            ingestion_objectives=objectives,
            ingestion_telemetry_sink=(
                metrics_runtime.sink if metrics_runtime is not None else None
            ),
            investigation_telemetry_sink=(
                traces_runtime.sink if traces_runtime is not None else None
            ),
            telemetry_metrics_backend=telemetry_metrics_backend,
            telemetry_logs_backend=telemetry_logs_backend,
            kubernetes_events_backend=kubernetes_events_backend,
            context_documents_backend=context_documents_backend,
            otlp_metrics_receiver=otlp_metrics_receiver,
            otlp_logs_receiver=otlp_logs_receiver,
            telemetry_runtime=telemetry_runtime,
            action_executor=action_executor,
            policy=policy,
            readiness_timeout_seconds=_readiness_timeout_from_env(),
        )
    except Exception:
        if telemetry_runtime is not None:
            telemetry_runtime.shutdown()
        raise


def build_otlp_receiver_runtime_from_env() -> Runtime:
    """Compose the isolated OTLP intake process from protected configuration."""

    database_url = os.environ.get("IIP_DATABASE_URL")
    if not database_url:
        raise OtlpReceiverConfigurationError("otlp.database.configuration.required")
    metrics_receiver = _otlp_metrics_receiver_from_env()
    logs_receiver = _otlp_logs_receiver_from_env()
    if metrics_receiver is None and logs_receiver is None:
        raise OtlpReceiverConfigurationError("otlp.configuration.required")
    auto_migrate = (
        os.environ.get("IIP_DATABASE_AUTO_MIGRATE", "false").lower() == "true"
    )
    return build_postgres_runtime(
        database_url,
        authenticator=DenyAllAuthenticator(),
        migrate=auto_migrate,
        ingestion_objectives=_ingestion_objectives_from_env(),
        otlp_metrics_receiver=metrics_receiver,
        otlp_logs_receiver=logs_receiver,
        policy=_policy_from_env(),
        readiness_timeout_seconds=_readiness_timeout_from_env(),
    )


def _readiness_timeout_from_env() -> int:
    raw = os.environ.get("IIP_READINESS_DATABASE_TIMEOUT_SECONDS", "2")
    try:
        value = int(raw)
    except ValueError:
        raise ValueError("readiness.database.configuration.invalid") from None
    if value < 1 or value > 10:
        raise ValueError("readiness.database.configuration.invalid")
    return value


def build_investigation_worker_from_env(runtime: Runtime) -> InvestigationWorker:
    """Compose one background worker from bounded process configuration."""

    def integer(name: str, default: int) -> int:
        raw = os.environ.get(name, str(default))
        try:
            return int(raw)
        except ValueError:
            raise ValueError("investigation.worker.configuration.invalid") from None

    worker_id = os.environ.get("IIP_WORKER_ID") or os.environ.get("HOSTNAME")
    if worker_id is None:
        raise ValueError("investigation.worker.configuration.required")
    return InvestigationWorker(
        runtime.investigation_jobs,
        runtime.investigations,
        SystemClock(),
        worker_id=worker_id,
        lease_seconds=integer("IIP_WORKER_LEASE_SECONDS", 30),
        heartbeat_seconds=integer("IIP_WORKER_HEARTBEAT_SECONDS", 10),
        retry_seconds=integer("IIP_WORKER_RETRY_SECONDS", 5),
        max_attempts=integer("IIP_WORKER_MAX_ATTEMPTS", 8),
    )


def build_action_reconciler_from_env(runtime: Runtime) -> ActionReconciliationService:
    """Compose the non-executing governed-action timer for a workflow worker."""

    worker_id = os.environ.get("IIP_WORKER_ID") or os.environ.get("HOSTNAME")
    if worker_id is None:
        raise ValueError("action.reconciler.configuration.required")
    try:
        batch_size = int(os.environ.get("IIP_ACTION_RECONCILIATION_BATCH_SIZE", "100"))
    except ValueError:
        raise ValueError("action.reconciler.configuration.invalid") from None
    return ActionReconciliationService(
        runtime.operational_store,
        SystemClock(),
        worker_id=worker_id,
        batch_size=batch_size,
    )


def build_ingestion_freshness_sampler_from_env(
    runtime: Runtime,
    allowed_tenants: tuple[str, ...],
) -> IngestionFreshnessSampler | None:
    """Compose explicitly enrolled automatic freshness targets for one worker."""

    raw = os.environ.get("IIP_INGESTION_MONITOR_TARGETS_JSON")
    if raw is None or raw == "":
        return None
    try:
        payload = json.loads(raw)
        items = payload["targets"]
        if (
            not isinstance(payload, dict)
            or set(payload) != {"targets"}
            or not isinstance(items, list)
            or not 1 <= len(items) <= 1000
        ):
            raise ValueError
        targets = tuple(
            IngestionMonitorTarget(
                tenant_id=item["tenantId"],
                source_id=item["sourceId"],
            )
            for item in items
            if isinstance(item, dict)
            and set(item) == {"tenantId", "sourceId"}
        )
        if len(targets) != len(items) or any(
            target.tenant_id not in allowed_tenants for target in targets
        ):
            raise ValueError
        return IngestionFreshnessSampler(runtime.ingestion_telemetry, targets)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError("ingestion.monitor.configuration.invalid") from None


def _authenticator_from_env() -> Authenticator:
    mode = os.environ.get("IIP_AUTH_MODE", "local-hashed")
    if mode == "local-hashed":
        identity_config = os.environ.get("IIP_AUTH_IDENTITIES_JSON")
        if identity_config is None:
            raise AuthenticationConfigurationError(
                "authentication.configuration.required"
            )
        return HashedBearerAuthenticator.from_json(identity_config)
    if mode == "oidc":
        oidc_config = os.environ.get("IIP_AUTH_OIDC_CONFIG_JSON")
        if oidc_config is None:
            raise AuthenticationConfigurationError(
                "authentication.configuration.required"
            )
        return OidcJwtAuthenticator.from_json(oidc_config)
    raise AuthenticationConfigurationError("authentication.configuration.invalid")


def _policy_from_env() -> PolicyDecisionPoint:
    mode = os.environ.get("IIP_POLICY_MODE", "local")
    if mode == "local":
        return AllowTenantPolicy()
    if mode == "external-http":
        configuration = os.environ.get("IIP_POLICY_CONFIG_JSON")
        if configuration is None:
            raise PolicyConfigurationError("policy.configuration.required")
        return ExternalHttpPolicyDecisionPoint.from_json(configuration)
    raise PolicyConfigurationError("policy.configuration.invalid")


def _ingestion_objectives_from_env() -> IngestionFreshnessObjectives:
    def value(name: str, default: float) -> float:
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            return float(raw)
        except ValueError:
            raise IngestionTelemetryInputError(
                "ingestion.objective.invalid"
            ) from None

    defaults = IngestionFreshnessObjectives()
    objectives = IngestionFreshnessObjectives(
        maximum_checkpoint_age_seconds=value(
            "IIP_INGESTION_MAX_CHECKPOINT_AGE_SECONDS",
            defaults.maximum_checkpoint_age_seconds,
        ),
        maximum_observation_age_seconds=value(
            "IIP_INGESTION_MAX_OBSERVATION_AGE_SECONDS",
            defaults.maximum_observation_age_seconds,
        ),
        maximum_ingestion_delay_seconds=value(
            "IIP_INGESTION_MAX_DELAY_SECONDS",
            defaults.maximum_ingestion_delay_seconds,
        ),
        maximum_pending_event_age_seconds=value(
            "IIP_INGESTION_MAX_PENDING_EVENT_AGE_SECONDS",
            defaults.maximum_pending_event_age_seconds,
        ),
        maximum_clock_skew_seconds=value(
            "IIP_INGESTION_MAX_CLOCK_SKEW_SECONDS",
            defaults.maximum_clock_skew_seconds,
        ),
    )
    objectives.validate()
    return objectives


def _otel_metrics_runtime_from_env() -> Any:
    enabled = os.environ.get("IIP_OTEL_METRICS_ENABLED", "false").lower()
    if enabled not in ("false", "true"):
        from iip.adapters.otel import OpenTelemetryConfigurationError

        raise OpenTelemetryConfigurationError("telemetry.configuration.invalid")
    if enabled == "false":
        return None

    from iip.adapters.otel import (
        OtlpMetricsConfiguration,
        build_otlp_metrics_runtime,
    )

    return build_otlp_metrics_runtime(
        OtlpMetricsConfiguration.from_environment(os.environ)
    )


def _otel_traces_runtime_from_env() -> Any:
    enabled = os.environ.get("IIP_OTEL_TRACES_ENABLED", "false").lower()
    if enabled not in ("false", "true"):
        from iip.adapters.otel import OpenTelemetryConfigurationError

        raise OpenTelemetryConfigurationError("telemetry.configuration.invalid")
    if enabled == "false":
        return None

    from iip.adapters.otel import (
        OtlpTracesConfiguration,
        build_otlp_traces_runtime,
    )

    return build_otlp_traces_runtime(
        OtlpTracesConfiguration.from_environment(os.environ)
    )


def _combine_telemetry_runtimes(*runtimes: Any) -> Any:
    configured = tuple(runtime for runtime in runtimes if runtime is not None)
    if not configured:
        return None
    if len(configured) == 1:
        return configured[0]
    from iip.adapters.otel import CompositeTelemetryRuntime

    return CompositeTelemetryRuntime(configured)


def _telemetry_metrics_backend_from_env(
    credential_broker: CredentialBroker | None = None,
) -> TelemetryMetricsBackend | None:
    backend = os.environ.get("IIP_TELEMETRY_METRICS_BACKEND", "no-data")
    if backend == "no-data":
        return None
    if backend != "prometheus":
        from iip.adapters.prometheus import PrometheusConfigurationError

        raise PrometheusConfigurationError(
            "telemetry.backend.configuration.invalid"
        )

    from iip.adapters.prometheus import build_prometheus_backend_from_environment

    return build_prometheus_backend_from_environment(
        os.environ, SystemClock(), credential_broker
    )


def _kubernetes_events_backend_from_env(
    credential_broker: CredentialBroker | None = None,
) -> KubernetesEventsBackend | None:
    backend = os.environ.get("IIP_KUBERNETES_EVENTS_BACKEND", "no-data")
    if backend == "no-data":
        return None
    if backend != "kubernetes-api":
        from iip.adapters.kubernetes_events import KubernetesEventsConfigurationError

        raise KubernetesEventsConfigurationError(
            "kubernetes.events.configuration.invalid"
        )

    from iip.adapters.kubernetes_events import (
        build_kubernetes_events_backend_from_environment,
    )

    return build_kubernetes_events_backend_from_environment(
        os.environ, SystemClock(), credential_broker
    )


def _kubernetes_action_executor_from_env(
    credential_broker: CredentialBroker | None = None,
) -> ActionExecutor | None:
    executor = os.environ.get("IIP_KUBERNETES_ACTION_EXECUTOR", "dry-run")
    if executor == "dry-run":
        return None
    if executor != "kubernetes-api":
        from iip.adapters.kubernetes_actions import KubernetesActionsConfigurationError

        raise KubernetesActionsConfigurationError(
            "kubernetes.actions.configuration.invalid"
        )
    from iip.adapters.kubernetes_actions import (
        build_kubernetes_action_executor_from_environment,
    )

    return build_kubernetes_action_executor_from_environment(
        os.environ, SystemClock(), credential_broker
    )


def _telemetry_logs_backend_from_env(
    credential_broker: CredentialBroker | None = None,
) -> TelemetryLogsBackend | None:
    backend = os.environ.get("IIP_TELEMETRY_LOGS_BACKEND", "no-data")
    if backend == "no-data":
        return None
    if backend != "loki":
        from iip.adapters.loki import LokiConfigurationError

        raise LokiConfigurationError("logs.backend.configuration.invalid")

    from iip.adapters.loki import build_loki_backend_from_environment

    return build_loki_backend_from_environment(
        os.environ, SystemClock(), credential_broker
    )


def _context_documents_backend_from_env() -> ContextDocumentsBackend | None:
    backend = os.environ.get("IIP_CONTEXT_BACKEND", "no-data")
    if backend == "no-data":
        return None
    if backend != "files":
        from iip.adapters.context import ContextConfigurationError

        raise ContextConfigurationError("context.configuration.invalid")

    from iip.adapters.context import build_context_backend_from_environment

    configured = build_context_backend_from_environment(os.environ, SystemClock())
    if configured is None:
        from iip.adapters.context import ContextConfigurationError

        raise ContextConfigurationError("context.configuration.required")
    return configured


def _credential_broker_from_env() -> CredentialBroker | None:
    mode = os.environ.get("IIP_CREDENTIAL_BROKER_MODE", "static")
    if mode == "static":
        return None
    if mode != "external-http":
        from iip.adapters.credential_broker import (
            CredentialBrokerConfigurationError,
        )

        raise CredentialBrokerConfigurationError(
            "credential.broker.configuration.invalid"
        )

    from iip.adapters.credential_broker import (
        build_external_credential_broker_from_environment,
    )

    return build_external_credential_broker_from_environment(
        os.environ, SystemClock()
    )


def _otlp_metrics_receiver_from_env() -> OtlpMetricsReceiverAdapter | None:
    enabled = os.environ.get("IIP_OTLP_RECEIVER_ENABLED", "false").lower()
    if enabled not in ("false", "true"):
        raise OtlpReceiverConfigurationError("otlp.configuration.invalid")
    if enabled == "false":
        return None
    configuration = os.environ.get("IIP_OTLP_RECEIVER_CHANNELS_JSON")
    if configuration is None:
        raise OtlpReceiverConfigurationError("otlp.configuration.required")

    from iip.adapters.otlp_receiver import ConfiguredOtlpMetricsReceiver

    return ConfiguredOtlpMetricsReceiver.from_json(configuration)


def _otlp_logs_receiver_from_env() -> OtlpLogsReceiverAdapter | None:
    enabled = os.environ.get("IIP_OTLP_LOGS_RECEIVER_ENABLED", "false").lower()
    if enabled not in ("false", "true"):
        raise OtlpReceiverConfigurationError("otlp.configuration.invalid")
    if enabled == "false":
        return None
    configuration = os.environ.get("IIP_OTLP_LOGS_RECEIVER_CHANNELS_JSON")
    if configuration is None:
        raise OtlpReceiverConfigurationError("otlp.configuration.required")

    from iip.adapters.otlp_logs_receiver import ConfiguredOtlpLogsReceiver

    return ConfiguredOtlpLogsReceiver.from_json(configuration)
