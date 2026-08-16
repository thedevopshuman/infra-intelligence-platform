"""Composition root for local and test runtime profiles."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from iip.adapters.actions import KubernetesRestartDryRunExecutor
from iip.adapters.auth import DenyAllAuthenticator, HashedBearerAuthenticator
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
from iip.adapters.memory import AllowTenantPolicy, InMemoryResourceStore
from iip.adapters.operations import InMemoryOperationalStore
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
    AuthenticationConfigurationError,
    Authenticator,
    EventLog,
    EventOutbox,
    CredentialBroker,
    ContextDocumentsBackend,
    IngestionTelemetrySink,
    KubernetesEventsBackend,
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
from iip.application.query_resources import ResourceQueryService
from iip.application.resource_change_evidence import (
    ResourceChangeEvidenceService,
    ResourceHistoryChangeEvidenceProvider,
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
    actions: GovernedActionService
    plugin_sessions: PluginSessionService
    operational_store: Any
    evidence_store: Any
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
    telemetry_metrics_backend: TelemetryMetricsBackend | None = None,
    telemetry_logs_backend: TelemetryLogsBackend | None = None,
    kubernetes_events_backend: KubernetesEventsBackend | None = None,
    context_documents_backend: ContextDocumentsBackend | None = None,
    otlp_metrics_receiver: OtlpMetricsReceiverAdapter | None = None,
    otlp_logs_receiver: OtlpLogsReceiverAdapter | None = None,
    telemetry_runtime: Any = None,
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
        telemetry_metrics_backend,
        telemetry_logs_backend,
        kubernetes_events_backend,
        context_documents_backend,
        otlp_metrics_receiver,
        otlp_logs_receiver,
        telemetry_runtime,
    )


def _compose_runtime(
    store: Any,
    operational: Any,
    evidence_store: Any,
    authenticator: Authenticator,
    ingestion_objectives: IngestionFreshnessObjectives | None = None,
    ingestion_telemetry_sink: IngestionTelemetrySink | None = None,
    telemetry_metrics_backend: TelemetryMetricsBackend | None = None,
    telemetry_logs_backend: TelemetryLogsBackend | None = None,
    kubernetes_events_backend: KubernetesEventsBackend | None = None,
    context_documents_backend: ContextDocumentsBackend | None = None,
    otlp_metrics_receiver: OtlpMetricsReceiverAdapter | None = None,
    otlp_logs_receiver: OtlpLogsReceiverAdapter | None = None,
    telemetry_runtime: Any = None,
) -> Runtime:
    """Compose use cases from ports without leaking adapters into their owners."""

    policy = AllowTenantPolicy()
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
        investigations=DeterministicInvestigationService(
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
        ),
        actions=GovernedActionService(
            store,
            policy,
            operational,
            KubernetesRestartDryRunExecutor(),
            operational,
            clock,
        ),
        plugin_sessions=PluginSessionService(policy, operational, clock),
        operational_store=operational,
        evidence_store=evidence_store,
        telemetry_runtime=telemetry_runtime,
    )


def build_postgres_runtime(
    database_url: str,
    *,
    authenticator: Authenticator | None = None,
    migrate: bool = False,
    ingestion_objectives: IngestionFreshnessObjectives | None = None,
    ingestion_telemetry_sink: IngestionTelemetrySink | None = None,
    telemetry_metrics_backend: TelemetryMetricsBackend | None = None,
    telemetry_logs_backend: TelemetryLogsBackend | None = None,
    kubernetes_events_backend: KubernetesEventsBackend | None = None,
    context_documents_backend: ContextDocumentsBackend | None = None,
    otlp_metrics_receiver: OtlpMetricsReceiverAdapter | None = None,
    otlp_logs_receiver: OtlpLogsReceiverAdapter | None = None,
    telemetry_runtime: Any = None,
) -> Runtime:
    """Build a PostgreSQL-backed runtime without leaking the adapter into use cases."""

    from iip.adapters.postgres import PostgresOperationalStore, PostgresResourceStore

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
        telemetry_metrics_backend,
        telemetry_logs_backend,
        kubernetes_events_backend,
        context_documents_backend,
        otlp_metrics_receiver,
        otlp_logs_receiver,
        telemetry_runtime,
    )


def build_projection_maintenance(database_url: str) -> ProjectionRebuildService:
    """Compose the privileged PostgreSQL projection-recovery use case."""

    from iip.adapters.postgres import PostgresResourceStore

    return ProjectionRebuildService(PostgresResourceStore(database_url), AllowTenantPolicy())


def build_runtime_from_env() -> Runtime:
    """Select a runtime profile from process configuration at the composition root."""

    identity_config = os.environ.get("IIP_AUTH_IDENTITIES_JSON")
    if identity_config is None:
        raise AuthenticationConfigurationError(
            "authentication.configuration.required"
        )
    authenticator = HashedBearerAuthenticator.from_json(identity_config)
    objectives = _ingestion_objectives_from_env()
    telemetry_runtime = _otel_metrics_runtime_from_env()
    database_url = os.environ.get("IIP_DATABASE_URL")
    try:
        otlp_metrics_receiver = _otlp_metrics_receiver_from_env()
        otlp_logs_receiver = _otlp_logs_receiver_from_env()
        credential_broker = _credential_broker_from_env()
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
                    telemetry_runtime.sink
                    if telemetry_runtime is not None
                    else None
                ),
                telemetry_metrics_backend=telemetry_metrics_backend,
                telemetry_logs_backend=telemetry_logs_backend,
                kubernetes_events_backend=kubernetes_events_backend,
                context_documents_backend=context_documents_backend,
                otlp_metrics_receiver=otlp_metrics_receiver,
                otlp_logs_receiver=otlp_logs_receiver,
                telemetry_runtime=telemetry_runtime,
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
                telemetry_runtime.sink if telemetry_runtime is not None else None
            ),
            telemetry_metrics_backend=telemetry_metrics_backend,
            telemetry_logs_backend=telemetry_logs_backend,
            kubernetes_events_backend=kubernetes_events_backend,
            context_documents_backend=context_documents_backend,
            otlp_metrics_receiver=otlp_metrics_receiver,
            otlp_logs_receiver=otlp_logs_receiver,
            telemetry_runtime=telemetry_runtime,
        )
    except Exception:
        if telemetry_runtime is not None:
            telemetry_runtime.shutdown()
        raise


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
