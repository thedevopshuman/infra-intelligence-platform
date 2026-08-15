"""Composition root for local and test runtime profiles."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from iip.adapters.actions import KubernetesRestartDryRunExecutor
from iip.adapters.auth import DenyAllAuthenticator, HashedBearerAuthenticator
from iip.adapters.evidence import (
    InMemoryEvidenceStore,
    ResourceStateEvidenceProvider,
    StructuredTextRedactor,
    SystemClock,
    UuidEvidenceIdGenerator,
)
from iip.adapters.memory import AllowTenantPolicy, InMemoryResourceStore
from iip.adapters.operations import InMemoryOperationalStore
from iip.application.actions import GovernedActionService
from iip.application.collect_evidence import EvidenceCollectionService
from iip.application.ingest_collection import ResourceCollectionIngestionService
from iip.application.ports import (
    AuthenticationConfigurationError,
    Authenticator,
    EventLog,
    EventOutbox,
    IngestionTelemetrySink,
    ResourceRepository,
    SourceCheckpointRepository,
)
from iip.application.investigate import DeterministicInvestigationService
from iip.application.ingest_resource import ResourceIngestionService
from iip.application.observe_ingestion import (
    IngestionFreshnessObjectives,
    IngestionFreshnessService,
    IngestionTelemetryInputError,
)
from iip.application.plugin_sessions import PluginSessionService
from iip.application.query_resources import ResourceQueryService
from iip.application.rebuild_projections import ProjectionRebuildService


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
        telemetry_runtime,
    )


def _compose_runtime(
    store: Any,
    operational: Any,
    evidence_store: Any,
    authenticator: Authenticator,
    ingestion_objectives: IngestionFreshnessObjectives | None = None,
    ingestion_telemetry_sink: IngestionTelemetrySink | None = None,
    telemetry_runtime: Any = None,
) -> Runtime:
    """Compose use cases from ports without leaking adapters into their owners."""

    policy = AllowTenantPolicy()
    clock = SystemClock()
    ingestion = ResourceIngestionService(store, policy)
    queries = ResourceQueryService(store, policy)
    evidence = EvidenceCollectionService(
        store,
        {"resource-state": ResourceStateEvidenceProvider(store)},
        evidence_store,
        StructuredTextRedactor(),
        policy,
        UuidEvidenceIdGenerator(),
        clock,
    )
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
        investigations=DeterministicInvestigationService(
            store, evidence, operational, clock
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
        if not database_url:
            return build_local_runtime(
                authenticator,
                ingestion_objectives=objectives,
                ingestion_telemetry_sink=(
                    telemetry_runtime.sink
                    if telemetry_runtime is not None
                    else None
                ),
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
