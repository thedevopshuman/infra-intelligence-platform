"""Composition root for local and test runtime profiles."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from . import __version__

if TYPE_CHECKING:
    from iip.adapters.postgres import PostgresConnectionConfiguration

from iip.adapters.actions import (
    ActionExecutorRouter,
    EventDeliveryReplayExecutor,
    KubernetesRestartDryRunExecutor,
)
from iip.adapters.auth import (
    DenyAllAuthenticator,
    HashedBearerAuthenticator,
    OidcJwtAuthenticator,
)
from iip.adapters.context import NoDataContextDocumentsBackend
from iip.adapters.event_publisher import (
    HttpsCloudEventsPublisher,
    HttpsEventPublisherConfiguration,
    StructuredLogEventPublisher,
)
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
from iip.adapters.evidence_redaction import EvidenceRedactionPolicyRegistry
from iip.adapters.health import AlwaysReadyProbe
from iip.adapters.investigation_catalog import build_investigation_signal_catalog
from iip.adapters.memory import AllowTenantPolicy, InMemoryResourceStore
from iip.adapters.operations import InMemoryOperationalStore
from iip.adapters.otel import DisabledTelemetryExportHealthReader
from iip.adapters.policy import ExternalHttpPolicyDecisionPoint
from iip.adapters.telemetry_health import PeriodicTelemetryExportHealthReporter
from iip.application.action_reconciliation import ActionReconciliationService
from iip.application.actions import GovernedActionService
from iip.application.calculate_ai_cost import (
    AiCostCalculationService,
    AiCostConfigurationError,
)
from iip.application.attribute_ai_usage import (
    AiAttributionConfigurationError,
    AiAttributionService,
    validate_ai_attribution_policy,
)
from iip.application.evaluate_ai_savings import (
    AiSavingsConfigurationError,
    AiSavingsEvaluationService,
    validate_ai_savings_profile,
)
from iip.application.collect_evidence import EvidenceCollectionService
from iip.application.context_evidence import ContextEvidenceProvider, ContextEvidenceService
from iip.application.deliver_events import EventDeliveryService
from iip.application.ingest_collection import ResourceCollectionIngestionService
from iip.application.ingest_ai_usage import (
    AiUsageIngestionService,
    AiUsageReceiverAdapter,
)
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
    AiAllocationTelemetrySink,
    AiEconomicsTelemetrySink,
    AuthenticationConfigurationError,
    Authenticator,
    EventLog,
    EventOutbox,
    CredentialBroker,
    ContextDocumentsBackend,
    IngestionTelemetrySink,
    InvestigationSignalCatalog,
    InvestigationTelemetrySink,
    KubernetesEventsBackend,
    OtlpReceiverTelemetrySink,
    PolicyConfigurationError,
    PolicyDecisionPoint,
    QueryAvailabilitySink,
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
from iip.application.evidence_retention import (
    EvidenceRetentionPolicy,
    EvidenceRetentionService,
)
from iip.application.event_outbox_retention import (
    EventOutboxRetentionPolicy,
    EventOutboxRetentionService,
)
from iip.application.investigation_dispatch import (
    InvestigationDispatchLimits,
    InvestigationDispatchService,
)
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
from iip.application.observe_query_availability import (
    QueryAvailabilityObjectives,
    QueryAvailabilityService,
)
from iip.application.observe_otlp_receiver import (
    OtlpReceiverObjectives,
    OtlpReceiverTelemetryService,
)
from iip.application.plugin_sessions import PluginSessionService
from iip.application.plugin_invocations import PluginInvocationLifecycleService
from iip.application.query_actions import ActionWorkflowQueryService
from iip.application.query_ai_allocations import (
    AiAllocationConfigurationError,
    AiAllocationProjectionService,
    AiAllocationReportService,
)
from iip.application.query_ai_savings import AiSavingsFindingQueryService
from iip.application.query_ai_invocation import AiInvocationObservationService
from iip.application.query_ai_history import AiHistoryAvailabilityService
from iip.application.query_event_delivery_health import EventDeliveryHealthService
from iip.application.query_event_delivery_slo import (
    EventDeliverySloObjectives,
    EventDeliverySloService,
)
from iip.application.query_investigation_completion_slo import (
    InvestigationCompletionSloObjectives,
    InvestigationCompletionSloService,
)
from iip.application.query_resources import ResourceQueryService
from iip.application.query_telemetry_deployment_health import (
    TelemetryDeploymentHealthService,
)
from iip.application.query_runtime_version import (
    RuntimeVersionIdentity,
    RuntimeVersionService,
)
from iip.application.query_telemetry_export_health import (
    TelemetryExportHealthService,
)
from iip.application.query_telemetry_export_slo import (
    TelemetryExportSloObjectives,
    TelemetryExportSloService,
)
from iip.application.query_telemetry_export_burn_rate import (
    TelemetryExportBurnRateObjectives,
    TelemetryExportBurnRateService,
)
from iip.application.query_collector_queue_loss import (
    CollectorQueueLossBinding,
    CollectorQueueLossObjectives,
    CollectorQueueLossService,
)
from iip.application.report_telemetry_export_health import (
    TelemetryExportHealthReporter,
    TelemetryExportHealthReportingConfiguration,
)
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
    console_authentication: Mapping[str, object]
    console_token_origin: str | None
    resources: ResourceRepository
    event_log: EventLog
    outbox: EventOutbox
    checkpoints: SourceCheckpointRepository
    ingestion: ResourceIngestionService
    collection_ingestion: ResourceCollectionIngestionService
    ingestion_telemetry: IngestionFreshnessService
    event_delivery_health: EventDeliveryHealthService
    event_delivery_slo: EventDeliverySloService
    investigation_completion_slo: InvestigationCompletionSloService
    query_availability: QueryAvailabilityService
    otlp_receiver_telemetry: OtlpReceiverTelemetryService
    telemetry_export_health: TelemetryExportHealthService
    telemetry_deployment_health: TelemetryDeploymentHealthService
    telemetry_export_slo: TelemetryExportSloService
    telemetry_export_burn_rate: TelemetryExportBurnRateService
    collector_queue_loss: CollectorQueueLossService
    runtime_version: RuntimeVersionService
    queries: ResourceQueryService
    evidence: EvidenceCollectionService
    evidence_retention: EvidenceRetentionService
    event_outbox_retention: EventOutboxRetentionService
    kubernetes_event_evidence: KubernetesEventEvidenceService
    resource_change_evidence: ResourceChangeEvidenceService
    context_evidence: ContextEvidenceService
    telemetry_evidence: TelemetryEvidenceService
    log_evidence: LogEvidenceService
    otlp_metrics_ingestion: OtlpMetricsIngestionService | None
    otlp_logs_ingestion: OtlpLogsIngestionService | None
    ai_usage_ingestion: AiUsageIngestionService | None
    ai_attribution_resolution: AiAttributionService | None
    ai_cost_calculation: AiCostCalculationService | None
    ai_savings_evaluation: AiSavingsEvaluationService | None
    ai_savings_findings: AiSavingsFindingQueryService
    ai_history_availability: AiHistoryAvailabilityService
    ai_allocation_reports: AiAllocationReportService | None
    ai_invocation_observation: AiInvocationObservationService | None
    ai_allocation_projection: AiAllocationProjectionService | None
    investigations: DeterministicInvestigationService
    investigation_lifecycle: InvestigationLifecycleService
    investigation_dispatch: InvestigationDispatchService
    actions: GovernedActionService
    action_queries: ActionWorkflowQueryService
    plugin_sessions: PluginSessionService
    plugin_invocations: PluginInvocationLifecycleService
    operational_store: Any
    investigation_jobs: Any
    evidence_store: Any
    readiness: ReadinessProbe
    telemetry_runtime: Any = None
    telemetry_health_reporter: Any = None

    def force_flush_telemetry(self, timeout_millis: int = 10_000) -> bool:
        if self.telemetry_runtime is None:
            return True
        return bool(self.telemetry_runtime.force_flush(timeout_millis))

    def close(self) -> None:
        if self.telemetry_health_reporter is not None:
            self.telemetry_health_reporter.close()
        if self.telemetry_runtime is not None:
            self.telemetry_runtime.shutdown()


def build_local_runtime(
    authenticator: Authenticator | None = None,
    *,
    ingestion_objectives: IngestionFreshnessObjectives | None = None,
    event_delivery_slo_objectives: EventDeliverySloObjectives | None = None,
    investigation_completion_slo_objectives: InvestigationCompletionSloObjectives | None = None,
    query_availability_objectives: QueryAvailabilityObjectives | None = None,
    telemetry_export_slo_objectives: TelemetryExportSloObjectives | None = None,
    telemetry_export_burn_rate_objectives: (
        TelemetryExportBurnRateObjectives | None
    ) = None,
    collector_queue_loss_binding: CollectorQueueLossBinding | None = None,
    collector_queue_loss_objectives: CollectorQueueLossObjectives | None = None,
    ingestion_telemetry_sink: IngestionTelemetrySink | None = None,
    query_availability_sink: QueryAvailabilitySink | None = None,
    investigation_telemetry_sink: InvestigationTelemetrySink | None = None,
    telemetry_metrics_backend: TelemetryMetricsBackend | None = None,
    telemetry_logs_backend: TelemetryLogsBackend | None = None,
    kubernetes_events_backend: KubernetesEventsBackend | None = None,
    context_documents_backend: ContextDocumentsBackend | None = None,
    otlp_metrics_receiver: OtlpMetricsReceiverAdapter | None = None,
    otlp_logs_receiver: OtlpLogsReceiverAdapter | None = None,
    ai_usage_receiver: AiUsageReceiverAdapter | None = None,
    telemetry_runtime: Any = None,
    action_executor: ActionExecutor | None = None,
    policy: PolicyDecisionPoint | None = None,
    signal_catalog: InvestigationSignalCatalog | None = None,
    readiness: ReadinessProbe | None = None,
    investigation_dispatch_limits: InvestigationDispatchLimits | None = None,
    evidence_retention_policy: EvidenceRetentionPolicy | None = None,
    telemetry_health_reporting: (
        TelemetryExportHealthReportingConfiguration | None
    ) = None,
    otlp_receiver_objectives: OtlpReceiverObjectives | None = None,
    otlp_receiver_telemetry_sink: OtlpReceiverTelemetrySink | None = None,
    ai_economics_telemetry_sink: AiEconomicsTelemetrySink | None = None,
    ai_attribution_policies: tuple[Mapping[str, object], ...] | None = None,
    ai_attribution_allow_test_fixtures: bool = False,
    ai_attribution_batch_size: int = 100,
    ai_cost_catalogs: tuple[Mapping[str, object], ...] | None = None,
    ai_cost_allow_test_fixtures: bool = False,
    ai_cost_qualification_policies: tuple[Mapping[str, object], ...] | None = None,
    ai_cost_qualification_reports: tuple[Mapping[str, object], ...] | None = None,
    ai_cost_require_production_qualification: bool = False,
    ai_cost_batch_size: int = 100,
    ai_savings_profiles: tuple[Mapping[str, object], ...] | None = None,
    ai_savings_allow_test_fixtures: bool = False,
    ai_allocation_policies: tuple[Mapping[str, object], ...] | None = None,
    ai_allocation_catalogs: tuple[Mapping[str, object], ...] | None = None,
    ai_allocation_allow_test_fixtures: bool = False,
    ai_allocation_source_record_limit: int = 10_000,
    ai_allocation_telemetry_sink: AiAllocationTelemetrySink | None = None,
    ai_allocation_window_seconds: int = 86_400,
    evidence_redaction_policies: tuple[Mapping[str, object], ...] | None = None,
    event_outbox_retention_policy: EventOutboxRetentionPolicy | None = None,
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
        event_delivery_slo_objectives,
        investigation_completion_slo_objectives,
        query_availability_objectives,
        telemetry_export_slo_objectives,
        telemetry_export_burn_rate_objectives,
        ingestion_telemetry_sink,
        query_availability_sink,
        investigation_telemetry_sink,
        telemetry_metrics_backend,
        telemetry_logs_backend,
        kubernetes_events_backend,
        context_documents_backend,
        otlp_metrics_receiver,
        otlp_logs_receiver,
        ai_usage_receiver,
        telemetry_runtime,
        action_executor,
        policy,
        signal_catalog,
        readiness,
        investigation_dispatch_limits,
        evidence_retention_policy,
        telemetry_health_reporting,
        otlp_receiver_objectives,
        otlp_receiver_telemetry_sink,
        ai_economics_telemetry_sink,
        collector_queue_loss_binding,
        collector_queue_loss_objectives,
        ai_attribution_policies,
        ai_attribution_allow_test_fixtures,
        ai_attribution_batch_size,
        ai_cost_catalogs,
        ai_cost_allow_test_fixtures,
        ai_cost_qualification_policies,
        ai_cost_qualification_reports,
        ai_cost_require_production_qualification,
        ai_cost_batch_size,
        ai_savings_profiles,
        ai_savings_allow_test_fixtures,
        ai_allocation_policies,
        ai_allocation_catalogs,
        ai_allocation_allow_test_fixtures,
        ai_allocation_source_record_limit,
        ai_allocation_telemetry_sink,
        ai_allocation_window_seconds,
        evidence_redaction_policies,
        event_outbox_retention_policy,
    )


def _compose_runtime(
    store: Any,
    operational: Any,
    evidence_store: Any,
    authenticator: Authenticator,
    ingestion_objectives: IngestionFreshnessObjectives | None = None,
    event_delivery_slo_objectives: EventDeliverySloObjectives | None = None,
    investigation_completion_slo_objectives: InvestigationCompletionSloObjectives | None = None,
    query_availability_objectives: QueryAvailabilityObjectives | None = None,
    telemetry_export_slo_objectives: TelemetryExportSloObjectives | None = None,
    telemetry_export_burn_rate_objectives: (
        TelemetryExportBurnRateObjectives | None
    ) = None,
    ingestion_telemetry_sink: IngestionTelemetrySink | None = None,
    query_availability_sink: QueryAvailabilitySink | None = None,
    investigation_telemetry_sink: InvestigationTelemetrySink | None = None,
    telemetry_metrics_backend: TelemetryMetricsBackend | None = None,
    telemetry_logs_backend: TelemetryLogsBackend | None = None,
    kubernetes_events_backend: KubernetesEventsBackend | None = None,
    context_documents_backend: ContextDocumentsBackend | None = None,
    otlp_metrics_receiver: OtlpMetricsReceiverAdapter | None = None,
    otlp_logs_receiver: OtlpLogsReceiverAdapter | None = None,
    ai_usage_receiver: AiUsageReceiverAdapter | None = None,
    telemetry_runtime: Any = None,
    action_executor: ActionExecutor | None = None,
    configured_policy: PolicyDecisionPoint | None = None,
    signal_catalog: InvestigationSignalCatalog | None = None,
    readiness: ReadinessProbe | None = None,
    investigation_dispatch_limits: InvestigationDispatchLimits | None = None,
    evidence_retention_policy: EvidenceRetentionPolicy | None = None,
    telemetry_health_reporting: (
        TelemetryExportHealthReportingConfiguration | None
    ) = None,
    otlp_receiver_objectives: OtlpReceiverObjectives | None = None,
    otlp_receiver_telemetry_sink: OtlpReceiverTelemetrySink | None = None,
    ai_economics_telemetry_sink: AiEconomicsTelemetrySink | None = None,
    collector_queue_loss_binding: CollectorQueueLossBinding | None = None,
    collector_queue_loss_objectives: CollectorQueueLossObjectives | None = None,
    ai_attribution_policies: tuple[Mapping[str, object], ...] | None = None,
    ai_attribution_allow_test_fixtures: bool = False,
    ai_attribution_batch_size: int = 100,
    ai_cost_catalogs: tuple[Mapping[str, object], ...] | None = None,
    ai_cost_allow_test_fixtures: bool = False,
    ai_cost_qualification_policies: tuple[Mapping[str, object], ...] | None = None,
    ai_cost_qualification_reports: tuple[Mapping[str, object], ...] | None = None,
    ai_cost_require_production_qualification: bool = False,
    ai_cost_batch_size: int = 100,
    ai_savings_profiles: tuple[Mapping[str, object], ...] | None = None,
    ai_savings_allow_test_fixtures: bool = False,
    ai_allocation_policies: tuple[Mapping[str, object], ...] | None = None,
    ai_allocation_catalogs: tuple[Mapping[str, object], ...] | None = None,
    ai_allocation_allow_test_fixtures: bool = False,
    ai_allocation_source_record_limit: int = 10_000,
    ai_allocation_telemetry_sink: AiAllocationTelemetrySink | None = None,
    ai_allocation_window_seconds: int = 86_400,
    evidence_redaction_policies: tuple[Mapping[str, object], ...] | None = None,
    event_outbox_retention_policy: EventOutboxRetentionPolicy | None = None,
) -> Runtime:
    """Compose use cases from ports without leaking adapters into their owners."""

    if (
        telemetry_health_reporting is not None
        and telemetry_export_slo_objectives is not None
        and telemetry_health_reporting.sample_retention_seconds
        < telemetry_export_slo_objectives.window_seconds
    ):
        raise ValueError("telemetry.export-slo.configuration.invalid")
    if (
        telemetry_health_reporting is not None
        and telemetry_export_burn_rate_objectives is not None
        and telemetry_health_reporting.sample_retention_seconds
        < telemetry_export_burn_rate_objectives.long_window_seconds
    ):
        raise ValueError("telemetry.export-burn-rate.configuration.invalid")
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
    redactor = StructuredTextRedactor(
        EvidenceRedactionPolicyRegistry(evidence_redaction_policies or ())
    )
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
        signal_catalog=signal_catalog,
    )
    investigation_lifecycle = InvestigationLifecycleService(operational, clock)
    console_authentication, console_token_origin = _console_authentication_for(
        authenticator
    )
    health_reader = (
        telemetry_runtime
        if telemetry_runtime is not None
        and callable(getattr(telemetry_runtime, "read_export_health", None))
        else DisabledTelemetryExportHealthReader()
    )
    health_reporter = (
        PeriodicTelemetryExportHealthReporter(
            TelemetryExportHealthReporter(
                health_reader,
                operational,
                clock,
                telemetry_health_reporting,
                heartbeat_sink=(
                    telemetry_runtime
                    if callable(
                        getattr(
                            telemetry_runtime,
                            "record_component_heartbeat",
                            None,
                        )
                    )
                    else None
                ),
            )
        )
        if telemetry_runtime is not None and telemetry_health_reporting is not None
        else None
    )
    runtime = Runtime(
        authenticator=authenticator,
        console_authentication=console_authentication,
        console_token_origin=console_token_origin,
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
        event_delivery_health=EventDeliveryHealthService(store, policy, clock),
        event_delivery_slo=EventDeliverySloService(
            store,
            policy,
            clock,
            event_delivery_slo_objectives,
        ),
        investigation_completion_slo=InvestigationCompletionSloService(
            operational,
            policy,
            clock,
            investigation_completion_slo_objectives,
        ),
        query_availability=QueryAvailabilityService(
            query_availability_sink,
            query_availability_objectives,
        ),
        otlp_receiver_telemetry=OtlpReceiverTelemetryService(
            otlp_receiver_telemetry_sink,
            otlp_receiver_objectives,
        ),
        telemetry_export_health=TelemetryExportHealthService(
            health_reader,
            policy,
            clock,
        ),
        telemetry_deployment_health=TelemetryDeploymentHealthService(
            operational,
            policy,
            clock,
            stale_after_seconds=(
                telemetry_health_reporting.stale_after_seconds
                if telemetry_health_reporting is not None
                else 120
            ),
            retention_seconds=(
                telemetry_health_reporting.retention_seconds
                if telemetry_health_reporting is not None
                else 600
            ),
        ),
        telemetry_export_slo=TelemetryExportSloService(
            operational,
            policy,
            clock,
            telemetry_export_slo_objectives,
        ),
        telemetry_export_burn_rate=TelemetryExportBurnRateService(
            operational,
            policy,
            clock,
            telemetry_export_burn_rate_objectives,
        ),
        collector_queue_loss=CollectorQueueLossService(
            metrics_backend,
            policy,
            clock,
            collector_queue_loss_binding,
            collector_queue_loss_objectives,
        ),
        runtime_version=RuntimeVersionService(
            _runtime_version_identity_from_env(),
            clock,
        ),
        queries=queries,
        evidence=evidence,
        evidence_retention=EvidenceRetentionService(
            evidence_store,
            policy,
            clock,
            evidence_retention_policy,
        ),
        event_outbox_retention=EventOutboxRetentionService(
            store, policy, clock, event_outbox_retention_policy,
        ),
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
        ai_usage_ingestion=(
            AiUsageIngestionService(ai_usage_receiver, store, clock)
            if ai_usage_receiver is not None
            else None
        ),
        ai_attribution_resolution=(
            AiAttributionService(
                store,
                clock,
                ai_attribution_policies,
                allow_test_fixtures=ai_attribution_allow_test_fixtures,
                batch_size=ai_attribution_batch_size,
            )
            if ai_attribution_policies is not None
            else None
        ),
        ai_cost_calculation=(
            AiCostCalculationService(
                store,
                clock,
                ai_cost_catalogs,
                allow_test_fixtures=ai_cost_allow_test_fixtures,
                qualification_policies=ai_cost_qualification_policies,
                qualification_reports=ai_cost_qualification_reports,
                require_production_qualification=(
                    ai_cost_require_production_qualification
                ),
                batch_size=ai_cost_batch_size,
            )
            if ai_cost_catalogs is not None
            else None
        ),
        ai_savings_evaluation=(
            AiSavingsEvaluationService(
                store,
                clock,
                ai_savings_profiles,
                telemetry_sink=ai_economics_telemetry_sink,
                allow_test_fixtures=ai_savings_allow_test_fixtures,
            )
            if ai_savings_profiles is not None
            else None
        ),
        ai_savings_findings=AiSavingsFindingQueryService(store, policy, clock),
        ai_history_availability=AiHistoryAvailabilityService(store, policy, clock),
        ai_allocation_reports=(
            AiAllocationReportService(
                store,
                policy,
                clock,
                ai_allocation_policies,
                ai_allocation_catalogs,
                allow_test_fixtures=ai_allocation_allow_test_fixtures,
                source_record_limit=ai_allocation_source_record_limit,
            )
            if ai_allocation_policies is not None
            and ai_allocation_catalogs is not None
            else None
        ),
        ai_invocation_observation=(
            AiInvocationObservationService(
                store,
                policy,
                clock,
                ai_allocation_policies,
                ai_allocation_catalogs,
                allow_test_fixtures=ai_allocation_allow_test_fixtures,
            )
            if ai_allocation_policies is not None
            and ai_allocation_catalogs is not None
            else None
        ),
        ai_allocation_projection=(
            AiAllocationProjectionService(
                store,
                clock,
                ai_allocation_policies,
                ai_allocation_catalogs,
                ai_allocation_telemetry_sink,
                allow_test_fixtures=ai_allocation_allow_test_fixtures,
                source_record_limit=ai_allocation_source_record_limit,
                window_seconds=ai_allocation_window_seconds,
            )
            if ai_allocation_policies is not None
            and ai_allocation_catalogs is not None
            and ai_allocation_telemetry_sink is not None
            else None
        ),
        investigations=investigations,
        investigation_lifecycle=investigation_lifecycle,
        investigation_dispatch=InvestigationDispatchService(
            operational,
            investigations,
            investigation_lifecycle,
            clock,
            investigation_dispatch_limits,
        ),
        actions=GovernedActionService(
            store,
            policy,
            operational,
            ActionExecutorRouter(
                action_executor or KubernetesRestartDryRunExecutor(),
                EventDeliveryReplayExecutor(store),
            ),
            operational,
            clock,
            operational,
            store,
        ),
        action_queries=ActionWorkflowQueryService(operational, policy, clock),
        plugin_sessions=PluginSessionService(policy, operational, clock),
        plugin_invocations=PluginInvocationLifecycleService(
            operational, policy, clock
        ),
        operational_store=operational,
        investigation_jobs=operational,
        evidence_store=evidence_store,
        readiness=readiness or AlwaysReadyProbe(),
        telemetry_runtime=telemetry_runtime,
        telemetry_health_reporter=health_reporter,
    )
    if health_reporter is not None:
        health_reporter.start()
    return runtime


def _runtime_version_identity_from_env() -> RuntimeVersionIdentity:
    """Build strict non-secret runtime identity without guessing deployment facts."""

    from iip.adapters.postgres.store import SCHEMA_MIGRATIONS

    revision_value = os.environ.get("IIP_BUILD_REVISION", "development")
    revision = None if revision_value in ("", "development") else revision_value
    chart_version = os.environ.get("IIP_DEPLOYMENT_HELM_CHART_VERSION") or None
    image_digest = os.environ.get("IIP_DEPLOYMENT_IMAGE_DIGEST") or None
    return RuntimeVersionIdentity(
        application_version=__version__,
        contract_api_version="iip.platform/v1alpha1",
        required_storage_migration=SCHEMA_MIGRATIONS[-1],
        build_revision=revision,
        helm_chart_version=chart_version,
        image_digest=image_digest,
    )


def build_postgres_runtime(
    database_url: str | PostgresConnectionConfiguration,
    *,
    authenticator: Authenticator | None = None,
    migrate: bool = False,
    ingestion_objectives: IngestionFreshnessObjectives | None = None,
    event_delivery_slo_objectives: EventDeliverySloObjectives | None = None,
    investigation_completion_slo_objectives: InvestigationCompletionSloObjectives | None = None,
    query_availability_objectives: QueryAvailabilityObjectives | None = None,
    telemetry_export_slo_objectives: TelemetryExportSloObjectives | None = None,
    telemetry_export_burn_rate_objectives: (
        TelemetryExportBurnRateObjectives | None
    ) = None,
    collector_queue_loss_binding: CollectorQueueLossBinding | None = None,
    collector_queue_loss_objectives: CollectorQueueLossObjectives | None = None,
    ingestion_telemetry_sink: IngestionTelemetrySink | None = None,
    query_availability_sink: QueryAvailabilitySink | None = None,
    investigation_telemetry_sink: InvestigationTelemetrySink | None = None,
    telemetry_metrics_backend: TelemetryMetricsBackend | None = None,
    telemetry_logs_backend: TelemetryLogsBackend | None = None,
    kubernetes_events_backend: KubernetesEventsBackend | None = None,
    context_documents_backend: ContextDocumentsBackend | None = None,
    otlp_metrics_receiver: OtlpMetricsReceiverAdapter | None = None,
    otlp_logs_receiver: OtlpLogsReceiverAdapter | None = None,
    ai_usage_receiver: AiUsageReceiverAdapter | None = None,
    telemetry_runtime: Any = None,
    action_executor: ActionExecutor | None = None,
    policy: PolicyDecisionPoint | None = None,
    signal_catalog: InvestigationSignalCatalog | None = None,
    readiness_timeout_seconds: int = 2,
    investigation_dispatch_limits: InvestigationDispatchLimits | None = None,
    evidence_retention_policy: EvidenceRetentionPolicy | None = None,
    telemetry_health_reporting: (
        TelemetryExportHealthReportingConfiguration | None
    ) = None,
    otlp_receiver_objectives: OtlpReceiverObjectives | None = None,
    otlp_receiver_telemetry_sink: OtlpReceiverTelemetrySink | None = None,
    ai_economics_telemetry_sink: AiEconomicsTelemetrySink | None = None,
    ai_attribution_policies: tuple[Mapping[str, object], ...] | None = None,
    ai_attribution_allow_test_fixtures: bool = False,
    ai_attribution_batch_size: int = 100,
    ai_cost_catalogs: tuple[Mapping[str, object], ...] | None = None,
    ai_cost_allow_test_fixtures: bool = False,
    ai_cost_qualification_policies: tuple[Mapping[str, object], ...] | None = None,
    ai_cost_qualification_reports: tuple[Mapping[str, object], ...] | None = None,
    ai_cost_require_production_qualification: bool = False,
    ai_cost_batch_size: int = 100,
    ai_savings_profiles: tuple[Mapping[str, object], ...] | None = None,
    ai_savings_allow_test_fixtures: bool = False,
    ai_allocation_policies: tuple[Mapping[str, object], ...] | None = None,
    ai_allocation_catalogs: tuple[Mapping[str, object], ...] | None = None,
    ai_allocation_allow_test_fixtures: bool = False,
    ai_allocation_source_record_limit: int = 10_000,
    ai_allocation_telemetry_sink: AiAllocationTelemetrySink | None = None,
    ai_allocation_window_seconds: int = 86_400,
    evidence_redaction_policies: tuple[Mapping[str, object], ...] | None = None,
    event_outbox_retention_policy: EventOutboxRetentionPolicy | None = None,
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
        event_delivery_slo_objectives,
        investigation_completion_slo_objectives,
        query_availability_objectives,
        telemetry_export_slo_objectives,
        telemetry_export_burn_rate_objectives,
        ingestion_telemetry_sink,
        query_availability_sink,
        investigation_telemetry_sink,
        telemetry_metrics_backend,
        telemetry_logs_backend,
        kubernetes_events_backend,
        context_documents_backend,
        otlp_metrics_receiver,
        otlp_logs_receiver,
        ai_usage_receiver,
        telemetry_runtime,
        action_executor,
        policy,
        signal_catalog,
        PostgresReadinessProbe(database_url, readiness_timeout_seconds),
        investigation_dispatch_limits,
        evidence_retention_policy,
        telemetry_health_reporting,
        otlp_receiver_objectives,
        otlp_receiver_telemetry_sink,
        ai_economics_telemetry_sink,
        collector_queue_loss_binding,
        collector_queue_loss_objectives,
        ai_attribution_policies,
        ai_attribution_allow_test_fixtures,
        ai_attribution_batch_size,
        ai_cost_catalogs,
        ai_cost_allow_test_fixtures,
        ai_cost_qualification_policies,
        ai_cost_qualification_reports,
        ai_cost_require_production_qualification,
        ai_cost_batch_size,
        ai_savings_profiles,
        ai_savings_allow_test_fixtures,
        ai_allocation_policies,
        ai_allocation_catalogs,
        ai_allocation_allow_test_fixtures,
        ai_allocation_source_record_limit,
        ai_allocation_telemetry_sink,
        ai_allocation_window_seconds,
        evidence_redaction_policies,
        event_outbox_retention_policy,
    )


def build_projection_maintenance(
    database_url: str | PostgresConnectionConfiguration,
) -> ProjectionRebuildService:
    """Compose the privileged PostgreSQL projection-recovery use case."""

    from iip.adapters.postgres import PostgresResourceStore

    return ProjectionRebuildService(PostgresResourceStore(database_url), AllowTenantPolicy())


def build_projection_maintenance_from_env() -> ProjectionRebuildService:
    """Compose privileged maintenance through protected process configuration."""

    from iip.adapters.postgres import PostgresConnectionConfigurationError

    database_url = os.environ.get("IIP_DATABASE_URL")
    if not database_url:
        raise SystemExit("IIP_DATABASE_URL is required")
    try:
        connection = _postgres_connection_configuration_from_env(database_url)
    except PostgresConnectionConfigurationError as error:
        raise SystemExit(str(error)) from None
    return build_projection_maintenance(connection)


def _postgres_connection_configuration_from_env(
    database_url: str,
) -> PostgresConnectionConfiguration:
    """Resolve protected database transport once for every packaged consumer."""

    from iip.adapters.postgres import PostgresConnectionConfiguration

    return PostgresConnectionConfiguration.from_environment(database_url, os.environ)


def _console_authentication_for(
    authenticator: Authenticator,
) -> tuple[Mapping[str, object], str | None]:
    """Derive only the non-secret browser bootstrap view at composition time."""

    if isinstance(authenticator, OidcJwtAuthenticator):
        return (
            authenticator.console_authentication_document(),
            authenticator.console_token_origin,
        )
    mode = (
        "local-token"
        if isinstance(authenticator, HashedBearerAuthenticator)
        else "access-token"
    )
    return (
        {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "ConsoleAuthenticationConfiguration",
            "spec": {"mode": mode},
        },
        None,
    )


def build_runtime_from_env(*, include_action_executor: bool = True) -> Runtime:
    """Select a runtime profile from process configuration at the composition root."""

    return _build_runtime_from_env(
        _authenticator_from_env(),
        include_action_executor=include_action_executor,
        include_ai_economics_engine=False,
    )


def build_workflow_worker_runtime_from_env() -> Runtime:
    """Compose a worker without interactive identity credentials or action impact."""

    # Validate feature-specific enrollment before creating adapters or starting
    # any background work; older worker profiles allow a broader tenant grammar.
    if _event_outbox_retention_policy_from_env().enabled:
        tenants = tuple(
            item.strip() for item in os.environ.get("IIP_WORKER_TENANTS", "").split(",")
            if item.strip()
        )
        if (
            not tenants or len(tenants) > 1000 or len(set(tenants)) != len(tenants)
            or any(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", tenant) is None for tenant in tenants)
        ):
            raise ValueError("event.outbox-retention.configuration.invalid")
    return _build_runtime_from_env(
        DenyAllAuthenticator(),
        include_action_executor=False,
        include_ai_economics_engine=True,
    )


def _build_runtime_from_env(
    authenticator: Authenticator,
    *,
    include_action_executor: bool,
    include_ai_economics_engine: bool,
) -> Runtime:
    policy = _policy_from_env()
    objectives = _ingestion_objectives_from_env()
    event_delivery_slo_objectives = _event_delivery_slo_objectives_from_env()
    investigation_completion_slo_objectives = (
        _investigation_completion_slo_objectives_from_env()
    )
    query_availability_objectives = _query_availability_objectives_from_env()
    otlp_receiver_objectives = _otlp_receiver_objectives_from_env()
    telemetry_export_slo_objectives = _telemetry_export_slo_objectives_from_env()
    telemetry_export_burn_rate_objectives = (
        _telemetry_export_burn_rate_objectives_from_env()
    )
    collector_queue_loss_binding = _collector_queue_loss_binding_from_env()
    collector_queue_loss_objectives = _collector_queue_loss_objectives_from_env()
    investigation_dispatch_limits = _investigation_dispatch_limits_from_env()
    evidence_retention_policy = _evidence_retention_policy_from_env()
    event_outbox_retention_policy = _event_outbox_retention_policy_from_env()
    evidence_redaction_policies = _evidence_redaction_policies_from_env()
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
    telemetry_health_reporting = (
        _telemetry_health_reporting_from_env(
            "api" if include_action_executor else "workflow-worker"
        )
        if telemetry_runtime is not None
        else None
    )
    if (
        telemetry_health_reporting is not None
        and telemetry_health_reporting.sample_retention_seconds
        < telemetry_export_slo_objectives.window_seconds
    ):
        raise ValueError("telemetry.export-slo.configuration.invalid")
    if (
        telemetry_health_reporting is not None
        and telemetry_health_reporting.sample_retention_seconds
        < telemetry_export_burn_rate_objectives.long_window_seconds
    ):
        raise ValueError("telemetry.export-burn-rate.configuration.invalid")
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
        ai_usage_receiver = (
            _ai_usage_receiver_from_env()
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
        context_documents_backend = _context_documents_backend_from_env(
            credential_broker
        )
        signal_catalog = build_investigation_signal_catalog(os.environ)
        (
            ai_attribution_policies,
            ai_attribution_allow_test_fixtures,
            ai_attribution_batch_size,
        ) = (
            _ai_attribution_engine_configuration_from_env()
            if include_ai_economics_engine
            else (None, False, 100)
        )
        (
            ai_cost_catalogs,
            ai_cost_qualification_policies,
            ai_cost_qualification_reports,
            ai_cost_allow_test_fixtures,
            ai_cost_require_production_qualification,
            ai_cost_batch_size,
        ) = (
            _ai_cost_engine_configuration_from_env()
            if include_ai_economics_engine
            else (None, None, None, False, False, 100)
        )
        ai_savings_configuration = (
            _ai_savings_engine_configuration_from_env(ai_cost_catalogs)
            if include_ai_economics_engine
            else None
        )
        ai_savings_profiles, ai_savings_allow_test_fixtures = (
            ai_savings_configuration
            if ai_savings_configuration is not None
            else (None, False)
        )
        (
            ai_allocation_policies,
            ai_allocation_catalogs,
            ai_allocation_allow_test_fixtures,
            ai_allocation_source_record_limit,
            ai_allocation_window_seconds,
        ) = _ai_allocation_report_configuration_from_env()
        if not database_url:
            return build_local_runtime(
                authenticator,
                ingestion_objectives=objectives,
                event_delivery_slo_objectives=event_delivery_slo_objectives,
                investigation_completion_slo_objectives=(
                    investigation_completion_slo_objectives
                ),
                query_availability_objectives=query_availability_objectives,
                telemetry_export_slo_objectives=telemetry_export_slo_objectives,
                telemetry_export_burn_rate_objectives=(
                    telemetry_export_burn_rate_objectives
                ),
                collector_queue_loss_binding=collector_queue_loss_binding,
                collector_queue_loss_objectives=collector_queue_loss_objectives,
                ingestion_telemetry_sink=(
                    metrics_runtime.sink
                    if metrics_runtime is not None
                    else None
                ),
                query_availability_sink=(
                    metrics_runtime.query_sink
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
                ai_usage_receiver=ai_usage_receiver,
                telemetry_runtime=telemetry_runtime,
                action_executor=action_executor,
                policy=policy,
                signal_catalog=signal_catalog,
                investigation_dispatch_limits=investigation_dispatch_limits,
                evidence_retention_policy=evidence_retention_policy,
                event_outbox_retention_policy=event_outbox_retention_policy,
                telemetry_health_reporting=telemetry_health_reporting,
                otlp_receiver_objectives=otlp_receiver_objectives,
                otlp_receiver_telemetry_sink=(
                    metrics_runtime.receiver_sink
                    if metrics_runtime is not None
                    else None
                ),
                ai_economics_telemetry_sink=(
                    metrics_runtime.ai_economics_sink
                    if metrics_runtime is not None
                    else None
                ),
                ai_attribution_policies=ai_attribution_policies,
                ai_attribution_allow_test_fixtures=(
                    ai_attribution_allow_test_fixtures
                ),
                ai_attribution_batch_size=ai_attribution_batch_size,
                ai_cost_catalogs=ai_cost_catalogs,
                ai_cost_allow_test_fixtures=ai_cost_allow_test_fixtures,
                ai_cost_qualification_policies=ai_cost_qualification_policies,
                ai_cost_qualification_reports=ai_cost_qualification_reports,
                ai_cost_require_production_qualification=(
                    ai_cost_require_production_qualification
                ),
                ai_cost_batch_size=ai_cost_batch_size,
                ai_savings_profiles=ai_savings_profiles,
                ai_savings_allow_test_fixtures=(
                    ai_savings_allow_test_fixtures
                ),
                ai_allocation_policies=ai_allocation_policies,
                ai_allocation_catalogs=ai_allocation_catalogs,
                ai_allocation_allow_test_fixtures=(
                    ai_allocation_allow_test_fixtures
                ),
                ai_allocation_source_record_limit=(
                    ai_allocation_source_record_limit
                ),
                ai_allocation_telemetry_sink=(
                    metrics_runtime.ai_allocation_sink
                    if metrics_runtime is not None
                    else None
                ),
                ai_allocation_window_seconds=ai_allocation_window_seconds,
                evidence_redaction_policies=evidence_redaction_policies,
            )
        auto_migrate = (
            os.environ.get("IIP_DATABASE_AUTO_MIGRATE", "false").lower()
            == "true"
        )
        database_connection = _postgres_connection_configuration_from_env(database_url)
        return build_postgres_runtime(
            database_connection,
            authenticator=authenticator,
            migrate=auto_migrate,
            ingestion_objectives=objectives,
            event_delivery_slo_objectives=event_delivery_slo_objectives,
            investigation_completion_slo_objectives=(
                investigation_completion_slo_objectives
            ),
            query_availability_objectives=query_availability_objectives,
            telemetry_export_slo_objectives=telemetry_export_slo_objectives,
            telemetry_export_burn_rate_objectives=(
                telemetry_export_burn_rate_objectives
            ),
            collector_queue_loss_binding=collector_queue_loss_binding,
            collector_queue_loss_objectives=collector_queue_loss_objectives,
            ingestion_telemetry_sink=(
                metrics_runtime.sink if metrics_runtime is not None else None
            ),
            query_availability_sink=(
                metrics_runtime.query_sink
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
            ai_usage_receiver=ai_usage_receiver,
            telemetry_runtime=telemetry_runtime,
            action_executor=action_executor,
            policy=policy,
            signal_catalog=signal_catalog,
            investigation_dispatch_limits=investigation_dispatch_limits,
            evidence_retention_policy=evidence_retention_policy,
            event_outbox_retention_policy=event_outbox_retention_policy,
            telemetry_health_reporting=telemetry_health_reporting,
            otlp_receiver_objectives=otlp_receiver_objectives,
            otlp_receiver_telemetry_sink=(
                metrics_runtime.receiver_sink
                if metrics_runtime is not None
                else None
            ),
            ai_economics_telemetry_sink=(
                metrics_runtime.ai_economics_sink
                if metrics_runtime is not None
                else None
            ),
            readiness_timeout_seconds=_readiness_timeout_from_env(),
            ai_attribution_policies=ai_attribution_policies,
            ai_attribution_allow_test_fixtures=(
                ai_attribution_allow_test_fixtures
            ),
            ai_attribution_batch_size=ai_attribution_batch_size,
            ai_cost_catalogs=ai_cost_catalogs,
            ai_cost_allow_test_fixtures=ai_cost_allow_test_fixtures,
            ai_cost_qualification_policies=ai_cost_qualification_policies,
            ai_cost_qualification_reports=ai_cost_qualification_reports,
            ai_cost_require_production_qualification=(
                ai_cost_require_production_qualification
            ),
            ai_cost_batch_size=ai_cost_batch_size,
            ai_savings_profiles=ai_savings_profiles,
            ai_savings_allow_test_fixtures=ai_savings_allow_test_fixtures,
            ai_allocation_policies=ai_allocation_policies,
            ai_allocation_catalogs=ai_allocation_catalogs,
            ai_allocation_allow_test_fixtures=(
                ai_allocation_allow_test_fixtures
            ),
            ai_allocation_source_record_limit=ai_allocation_source_record_limit,
            ai_allocation_telemetry_sink=(
                metrics_runtime.ai_allocation_sink
                if metrics_runtime is not None
                else None
            ),
            ai_allocation_window_seconds=ai_allocation_window_seconds,
            evidence_redaction_policies=evidence_redaction_policies,
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
    ai_usage_receiver = _ai_usage_receiver_from_env()
    if (
        metrics_receiver is None
        and logs_receiver is None
        and ai_usage_receiver is None
    ):
        raise OtlpReceiverConfigurationError("otlp.configuration.required")
    auto_migrate = (
        os.environ.get("IIP_DATABASE_AUTO_MIGRATE", "false").lower() == "true"
    )
    metrics_runtime = _otel_metrics_runtime_from_env()
    try:
        database_connection = _postgres_connection_configuration_from_env(database_url)
        telemetry_export_slo_objectives = _telemetry_export_slo_objectives_from_env()
        telemetry_export_burn_rate_objectives = (
            _telemetry_export_burn_rate_objectives_from_env()
        )
        collector_queue_loss_binding = _collector_queue_loss_binding_from_env()
        collector_queue_loss_objectives = (
            _collector_queue_loss_objectives_from_env()
        )
        telemetry_health_reporting = (
            _telemetry_health_reporting_from_env("otlp-receiver")
            if metrics_runtime is not None
            else None
        )
        return build_postgres_runtime(
            database_connection,
            authenticator=DenyAllAuthenticator(),
            migrate=auto_migrate,
            ingestion_objectives=_ingestion_objectives_from_env(),
            event_delivery_slo_objectives=_event_delivery_slo_objectives_from_env(),
            investigation_completion_slo_objectives=(
                _investigation_completion_slo_objectives_from_env()
            ),
            query_availability_objectives=_query_availability_objectives_from_env(),
            telemetry_export_slo_objectives=telemetry_export_slo_objectives,
            telemetry_export_burn_rate_objectives=(
                telemetry_export_burn_rate_objectives
            ),
            collector_queue_loss_binding=collector_queue_loss_binding,
            collector_queue_loss_objectives=collector_queue_loss_objectives,
            otlp_metrics_receiver=metrics_receiver,
            otlp_logs_receiver=logs_receiver,
            ai_usage_receiver=ai_usage_receiver,
            telemetry_runtime=metrics_runtime,
            telemetry_health_reporting=telemetry_health_reporting,
            otlp_receiver_objectives=_otlp_receiver_objectives_from_env(),
            otlp_receiver_telemetry_sink=(
                metrics_runtime.receiver_sink
                if metrics_runtime is not None
                else None
            ),
            policy=_policy_from_env(),
            readiness_timeout_seconds=_readiness_timeout_from_env(),
            evidence_redaction_policies=_evidence_redaction_policies_from_env(),
        )
    except Exception:
        if metrics_runtime is not None:
            metrics_runtime.shutdown()
        raise


def _readiness_timeout_from_env() -> int:
    raw = os.environ.get("IIP_READINESS_DATABASE_TIMEOUT_SECONDS", "2")
    try:
        value = int(raw)
    except ValueError:
        raise ValueError("readiness.database.configuration.invalid") from None
    if value < 1 or value > 10:
        raise ValueError("readiness.database.configuration.invalid")
    return value


def _telemetry_health_reporting_from_env(
    component: str,
) -> TelemetryExportHealthReportingConfiguration:
    def integer(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, str(default)))
        except ValueError:
            raise ValueError(
                "telemetry.export-health.reporting.configuration.invalid"
            ) from None

    instance_basis = (
        os.environ.get("IIP_RUNTIME_INSTANCE_ID")
        or os.environ.get("HOSTNAME")
        or "local-runtime"
    )[:256]
    instance_id = "sha256:" + hashlib.sha256(
        f"{component}\x1f{instance_basis}\x1f{os.getpid()}\x1f{secrets.token_hex(16)}".encode(
            "utf-8"
        )
    ).hexdigest()
    configuration = TelemetryExportHealthReportingConfiguration(
        instance_id=instance_id,
        component=component,
        interval_seconds=integer("IIP_TELEMETRY_HEALTH_INTERVAL_SECONDS", 30),
        stale_after_seconds=integer(
            "IIP_TELEMETRY_HEALTH_STALE_AFTER_SECONDS", 120
        ),
        retention_seconds=integer(
            "IIP_TELEMETRY_HEALTH_RETENTION_SECONDS", 600
        ),
        sample_retention_seconds=integer(
            "IIP_TELEMETRY_EXPORT_SLO_RETENTION_SECONDS", 604_800
        ),
    )
    configuration.validate()
    return configuration


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
        max_tenant_concurrency=integer(
            "IIP_WORKER_MAX_TENANT_CONCURRENCY", 1
        ),
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


def build_event_delivery_from_env(
    runtime: Runtime,
    allowed_tenants: tuple[str, ...],
) -> EventDeliveryService | None:
    """Compose an optional exact-tenant transactional-outbox publisher."""

    mode = os.environ.get("IIP_EVENT_PUBLISHER_MODE", "disabled")
    if mode == "disabled":
        return None
    if mode == "stdout-json":
        publisher = StructuredLogEventPublisher(allowed_tenants)
    elif mode == "https-webhook":
        raw = os.environ.get("IIP_EVENT_PUBLISHER_CONFIG_JSON")
        if raw is None:
            raise ValueError("event.publisher.configuration.required")
        configuration = HttpsEventPublisherConfiguration.from_json(raw)
        if frozenset(configuration.tenant_ids) != frozenset(allowed_tenants):
            raise ValueError("event.publisher.configuration.invalid")
        publisher = HttpsCloudEventsPublisher(configuration)
    else:
        raise ValueError("event.publisher.mode.invalid")

    worker_id = os.environ.get("IIP_WORKER_ID") or os.environ.get("HOSTNAME")
    if worker_id is None:
        raise ValueError("event.delivery.configuration.required")

    def integer(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, str(default)))
        except ValueError:
            raise ValueError("event.delivery.configuration.invalid") from None

    return EventDeliveryService(
        runtime.outbox,
        publisher,
        worker_id=worker_id,
        batch_size=integer("IIP_OUTBOX_BATCH_SIZE", 100),
        lease_seconds=integer("IIP_OUTBOX_LEASE_SECONDS", 30),
        retry_base_seconds=integer("IIP_OUTBOX_RETRY_BASE_SECONDS", 5),
        retry_max_seconds=integer("IIP_OUTBOX_RETRY_MAX_SECONDS", 300),
        max_attempts=integer("IIP_OUTBOX_MAX_ATTEMPTS", 8),
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


def _event_delivery_slo_objectives_from_env() -> EventDeliverySloObjectives:
    def value(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            raise ValueError(
                "event.delivery-slo.configuration.invalid"
            ) from None

    defaults = EventDeliverySloObjectives()
    return EventDeliverySloObjectives(
        window_seconds=value(
            "IIP_EVENT_DELIVERY_SLO_WINDOW_SECONDS",
            defaults.window_seconds,
        ),
        maximum_delivery_latency_seconds=value(
            "IIP_EVENT_DELIVERY_SLO_MAXIMUM_LATENCY_SECONDS",
            defaults.maximum_delivery_latency_seconds,
        ),
        minimum_attainment_basis_points=value(
            "IIP_EVENT_DELIVERY_SLO_MINIMUM_ATTAINMENT_BASIS_POINTS",
            defaults.minimum_attainment_basis_points,
        ),
        minimum_eligible_events=value(
            "IIP_EVENT_DELIVERY_SLO_MINIMUM_ELIGIBLE_EVENTS",
            defaults.minimum_eligible_events,
        ),
    )


def _investigation_completion_slo_objectives_from_env() -> InvestigationCompletionSloObjectives:
    def value(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            raise ValueError(
                "investigation.completion-slo.configuration.invalid"
            ) from None

    defaults = InvestigationCompletionSloObjectives()
    return InvestigationCompletionSloObjectives(
        window_seconds=value(
            "IIP_INVESTIGATION_COMPLETION_SLO_WINDOW_SECONDS",
            defaults.window_seconds,
        ),
        maximum_completion_seconds=value(
            "IIP_INVESTIGATION_COMPLETION_SLO_MAXIMUM_SECONDS",
            defaults.maximum_completion_seconds,
        ),
        minimum_attainment_basis_points=value(
            "IIP_INVESTIGATION_COMPLETION_SLO_MINIMUM_ATTAINMENT_BASIS_POINTS",
            defaults.minimum_attainment_basis_points,
        ),
        minimum_eligible_jobs=value(
            "IIP_INVESTIGATION_COMPLETION_SLO_MINIMUM_ELIGIBLE_JOBS",
            defaults.minimum_eligible_jobs,
        ),
    )


def _telemetry_export_slo_objectives_from_env() -> TelemetryExportSloObjectives:
    def value(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            raise ValueError(
                "telemetry.export-slo.configuration.invalid"
            ) from None

    defaults = TelemetryExportSloObjectives()
    return TelemetryExportSloObjectives(
        window_seconds=value(
            "IIP_TELEMETRY_EXPORT_SLO_WINDOW_SECONDS",
            defaults.window_seconds,
        ),
        minimum_attainment_basis_points=value(
            "IIP_TELEMETRY_EXPORT_SLO_MINIMUM_ATTAINMENT_BASIS_POINTS",
            defaults.minimum_attainment_basis_points,
        ),
        minimum_eligible_attempts=value(
            "IIP_TELEMETRY_EXPORT_SLO_MINIMUM_ELIGIBLE_ATTEMPTS",
            defaults.minimum_eligible_attempts,
        ),
    )


def _telemetry_export_burn_rate_objectives_from_env() -> (
    TelemetryExportBurnRateObjectives
):
    def value(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            raise ValueError(
                "telemetry.export-burn-rate.configuration.invalid"
            ) from None

    defaults = TelemetryExportBurnRateObjectives()
    return TelemetryExportBurnRateObjectives(
        short_window_seconds=value(
            "IIP_TELEMETRY_EXPORT_BURN_RATE_SHORT_WINDOW_SECONDS",
            defaults.short_window_seconds,
        ),
        long_window_seconds=value(
            "IIP_TELEMETRY_EXPORT_BURN_RATE_LONG_WINDOW_SECONDS",
            defaults.long_window_seconds,
        ),
        minimum_attainment_basis_points=value(
            "IIP_TELEMETRY_EXPORT_BURN_RATE_MINIMUM_ATTAINMENT_BASIS_POINTS",
            defaults.minimum_attainment_basis_points,
        ),
        minimum_eligible_attempts=value(
            "IIP_TELEMETRY_EXPORT_BURN_RATE_MINIMUM_ELIGIBLE_ATTEMPTS",
            defaults.minimum_eligible_attempts,
        ),
        critical_burn_rate_hundredths=value(
            "IIP_TELEMETRY_EXPORT_BURN_RATE_CRITICAL_HUNDREDTHS",
            defaults.critical_burn_rate_hundredths,
        ),
    )


def _collector_queue_loss_binding_from_env() -> CollectorQueueLossBinding | None:
    raw = os.environ.get("IIP_COLLECTOR_QUEUE_LOSS_BINDING_JSON")
    if not raw:
        return None
    try:
        document = json.loads(raw)
        if not isinstance(document, dict) or not {
            "integrationId",
            "exporterName",
        }.issubset(document):
            raise KeyError
        extra = set(document) - {"integrationId", "exporterName", "signals"}
        if extra:
            raise KeyError
        signals = document.get("signals", ["metrics", "logs"])
        if not isinstance(signals, list) or any(
            not isinstance(item, str) for item in signals
        ):
            raise TypeError
        return CollectorQueueLossBinding(
            document["integrationId"],
            document["exporterName"],
            tuple(signals),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise ValueError(
            "telemetry.collector-queue-loss.configuration.invalid"
        ) from None


def _collector_queue_loss_objectives_from_env() -> CollectorQueueLossObjectives:
    def value(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            raise ValueError(
                "telemetry.collector-queue-loss.configuration.invalid"
            ) from None

    defaults = CollectorQueueLossObjectives()
    return CollectorQueueLossObjectives(
        window_seconds=value(
            "IIP_COLLECTOR_QUEUE_LOSS_WINDOW_SECONDS",
            defaults.window_seconds,
        ),
        max_loss_basis_points=value(
            "IIP_COLLECTOR_QUEUE_LOSS_MAX_LOSS_BASIS_POINTS",
            defaults.max_loss_basis_points,
        ),
        max_queue_utilization_basis_points=value(
            "IIP_COLLECTOR_QUEUE_LOSS_MAX_QUEUE_UTILIZATION_BASIS_POINTS",
            defaults.max_queue_utilization_basis_points,
        ),
        minimum_eligible_attempts=value(
            "IIP_COLLECTOR_QUEUE_LOSS_MINIMUM_ELIGIBLE_ATTEMPTS",
            defaults.minimum_eligible_attempts,
        ),
    )


def _query_availability_objectives_from_env() -> QueryAvailabilityObjectives:
    def value(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            raise ValueError("query.availability.configuration.invalid") from None

    defaults = QueryAvailabilityObjectives()
    return QueryAvailabilityObjectives(
        window_seconds=value(
            "IIP_QUERY_AVAILABILITY_SLO_WINDOW_SECONDS",
            defaults.window_seconds,
        ),
        minimum_availability_basis_points=value(
            "IIP_QUERY_AVAILABILITY_SLO_MINIMUM_BASIS_POINTS",
            defaults.minimum_availability_basis_points,
        ),
        minimum_eligible_requests=value(
            "IIP_QUERY_AVAILABILITY_SLO_MINIMUM_ELIGIBLE_REQUESTS",
            defaults.minimum_eligible_requests,
        ),
    )


def _otlp_receiver_objectives_from_env() -> OtlpReceiverObjectives:
    def value(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            raise ValueError(
                "otlp.receiver.availability.configuration.invalid"
            ) from None

    defaults = OtlpReceiverObjectives()
    return OtlpReceiverObjectives(
        window_seconds=value(
            "IIP_OTLP_RECEIVER_SLO_WINDOW_SECONDS",
            defaults.window_seconds,
        ),
        minimum_availability_basis_points=value(
            "IIP_OTLP_RECEIVER_SLO_MINIMUM_BASIS_POINTS",
            defaults.minimum_availability_basis_points,
        ),
        minimum_eligible_requests=value(
            "IIP_OTLP_RECEIVER_SLO_MINIMUM_ELIGIBLE_REQUESTS",
            defaults.minimum_eligible_requests,
        ),
    )


def _investigation_dispatch_limits_from_env() -> InvestigationDispatchLimits:
    raw = os.environ.get(
        "IIP_INVESTIGATION_MAX_OUTSTANDING_JOBS_PER_TENANT",
        "1000",
    )
    try:
        value = int(raw)
    except ValueError:
        raise ValueError("investigation.queue.configuration.invalid") from None
    return InvestigationDispatchLimits(max_outstanding_jobs_per_tenant=value)


def _event_outbox_retention_policy_from_env() -> EventOutboxRetentionPolicy:
    """No duration is silently adopted when destructive cleanup is enabled."""
    prefix = "IIP_EVENT_OUTBOX_RETENTION_"
    enabled = os.environ.get(prefix + "ENABLED", "false")
    duration = os.environ.get(prefix + "PUBLISHED_SECONDS")
    batch = os.environ.get(prefix + "BATCH_SIZE", "100")
    if (
        enabled not in ("true", "false")
        or (enabled == "true" and duration is None)
        or any(value is not None and re.fullmatch(r"[0-9]{1,10}", value) is None
               for value in (duration, batch))
    ):
        raise ValueError("event.outbox-retention.configuration.invalid")
    return EventOutboxRetentionPolicy(
        enabled=enabled == "true",
        published_seconds=int(duration) if duration is not None else 2_592_000,
        batch_size=int(batch),
    )


def _evidence_retention_policy_from_env() -> EvidenceRetentionPolicy:
    """Build a strict, deployment-owned artifact-retention policy."""

    raw_enabled = os.environ.get("IIP_EVIDENCE_RETENTION_ENABLED", "false").lower()
    if raw_enabled not in ("false", "true"):
        raise ValueError("evidence.retention.configuration.invalid")

    defaults = EvidenceRetentionPolicy()

    def value(name: str, default: int) -> int:
        raw = os.environ.get(name)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            raise ValueError(
                "evidence.retention.configuration.invalid"
            ) from None

    return EvidenceRetentionPolicy(
        enabled=raw_enabled == "true",
        ephemeral_seconds=value(
            "IIP_EVIDENCE_RETENTION_EPHEMERAL_SECONDS",
            defaults.ephemeral_seconds,
        ),
        standard_seconds=value(
            "IIP_EVIDENCE_RETENTION_STANDARD_SECONDS",
            defaults.standard_seconds,
        ),
        extended_seconds=value(
            "IIP_EVIDENCE_RETENTION_EXTENDED_SECONDS",
            defaults.extended_seconds,
        ),
        batch_size=value(
            "IIP_EVIDENCE_RETENTION_BATCH_SIZE",
            defaults.batch_size,
        ),
    )


def _evidence_redaction_policies_from_env() -> (
    tuple[Mapping[str, object], ...] | None
):
    """Load optional protected exact-tenant additive redaction policies."""

    raw = os.environ.get("IIP_EVIDENCE_REDACTION_POLICIES_JSON")
    if raw in (None, ""):
        return None
    return EvidenceRedactionPolicyRegistry.from_json(raw).documents()


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
    if backend == "loki":
        from iip.adapters.loki import build_loki_backend_from_environment

        return build_loki_backend_from_environment(
            os.environ, SystemClock(), credential_broker
        )
    if backend == "opensearch":
        from iip.adapters.opensearch import build_opensearch_backend_from_environment

        return build_opensearch_backend_from_environment(
            os.environ, SystemClock(), credential_broker
        )

    from iip.adapters.loki import LokiConfigurationError

    raise LokiConfigurationError("logs.backend.configuration.invalid")


def _context_documents_backend_from_env(
    credential_broker: CredentialBroker | None = None,
) -> ContextDocumentsBackend | None:
    backend = os.environ.get("IIP_CONTEXT_BACKEND", "no-data")
    if backend == "no-data":
        return None
    if backend == "github":
        from iip.adapters.github_context import (
            build_github_context_backend_from_environment,
        )

        return build_github_context_backend_from_environment(
            os.environ,
            SystemClock(),
            credential_broker,
        )
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


def _ai_usage_receiver_from_env() -> AiUsageReceiverAdapter | None:
    enabled = os.environ.get("IIP_AI_USAGE_RECEIVER_ENABLED", "false").lower()
    if enabled not in ("false", "true"):
        raise OtlpReceiverConfigurationError("otlp.configuration.invalid")
    if enabled == "false":
        return None
    configuration = os.environ.get("IIP_AI_USAGE_RECEIVER_CHANNELS_JSON")
    if configuration is None:
        raise OtlpReceiverConfigurationError("otlp.configuration.required")

    from iip.adapters.otlp_ai_usage_receiver import ConfiguredAiUsageReceiver

    return ConfiguredAiUsageReceiver.from_json(configuration)


def _ai_attribution_engine_configuration_from_env() -> tuple[
    tuple[Mapping[str, object], ...] | None,
    bool,
    int,
]:
    enabled = os.environ.get("IIP_AI_ATTRIBUTION_ENABLED", "false").lower()
    if enabled not in ("false", "true"):
        raise AiAttributionConfigurationError(
            "ai.attribution.configuration.invalid"
        )
    if enabled == "false":
        return None, False, 100
    raw_policies = os.environ.get("IIP_AI_ATTRIBUTION_POLICIES_JSON")
    if raw_policies is None:
        raise AiAttributionConfigurationError(
            "ai.attribution.configuration.required"
        )
    allow_raw = os.environ.get(
        "IIP_AI_ATTRIBUTION_ALLOW_TEST_FIXTURES",
        "false",
    ).lower()
    if allow_raw not in ("false", "true"):
        raise AiAttributionConfigurationError(
            "ai.attribution.configuration.invalid"
        )
    try:
        batch_size = int(os.environ.get("IIP_AI_ATTRIBUTION_BATCH_SIZE", "100"))
    except ValueError:
        raise AiAttributionConfigurationError(
            "ai.attribution.configuration.invalid"
        ) from None
    if not 1 <= batch_size <= 1000:
        raise AiAttributionConfigurationError(
            "ai.attribution.configuration.invalid"
        )

    from iip.adapters.ai_attribution_policies import (
        ai_attribution_policies_from_json,
    )

    policies = ai_attribution_policies_from_json(raw_policies)
    validated = tuple(validate_ai_attribution_policy(item) for item in policies)
    worker_tenants = {
        item.strip()
        for item in os.environ.get("IIP_WORKER_TENANTS", "").split(",")
        if item.strip()
    }
    policy_tenants = {item.tenant_id for item in validated}
    if not worker_tenants or policy_tenants != worker_tenants:
        raise AiAttributionConfigurationError("ai.attribution.tenants.invalid")
    return policies, allow_raw == "true", batch_size


def _ai_allocation_report_configuration_from_env() -> tuple[
    tuple[Mapping[str, object], ...] | None,
    tuple[Mapping[str, object], ...] | None,
    bool,
    int,
    int,
]:
    enabled = os.environ.get("IIP_AI_ALLOCATION_REPORTING_ENABLED", "false").lower()
    if enabled not in ("false", "true"):
        raise AiAllocationConfigurationError(
            "ai.allocation.configuration.invalid"
        )
    if enabled == "false":
        return None, None, False, 10_000, 86_400
    raw_policies = os.environ.get("IIP_AI_ATTRIBUTION_POLICIES_JSON")
    raw_catalogs = os.environ.get("IIP_AI_PRICE_CATALOGS_JSON")
    if raw_policies is None or raw_catalogs is None:
        raise AiAllocationConfigurationError(
            "ai.allocation.configuration.required"
        )
    attribution_fixtures = os.environ.get(
        "IIP_AI_ATTRIBUTION_ALLOW_TEST_FIXTURES",
        "false",
    ).lower()
    price_fixtures = os.environ.get(
        "IIP_AI_PRICE_CATALOG_ALLOW_TEST_FIXTURES",
        "false",
    ).lower()
    if attribution_fixtures not in ("false", "true") or price_fixtures not in (
        "false",
        "true",
    ):
        raise AiAllocationConfigurationError(
            "ai.allocation.configuration.invalid"
        )
    try:
        source_record_limit = int(
            os.environ.get("IIP_AI_ALLOCATION_SOURCE_RECORD_LIMIT", "10000")
        )
        window_seconds = int(
            os.environ.get("IIP_AI_ALLOCATION_WINDOW_SECONDS", "86400")
        )
    except ValueError:
        raise AiAllocationConfigurationError(
            "ai.allocation.configuration.invalid"
        ) from None
    if not 1 <= source_record_limit <= 10_000 or not 60 <= window_seconds <= 2_678_400:
        raise AiAllocationConfigurationError(
            "ai.allocation.configuration.invalid"
        )
    from iip.adapters.ai_attribution_policies import (
        ai_attribution_policies_from_json,
    )
    from iip.adapters.ai_price_catalogs import ai_price_catalogs_from_json

    policies = ai_attribution_policies_from_json(raw_policies)
    catalogs = ai_price_catalogs_from_json(raw_catalogs)
    allow_fixtures = (
        attribution_fixtures == "true" and price_fixtures == "true"
    )
    return policies, catalogs, allow_fixtures, source_record_limit, window_seconds


def _ai_cost_engine_configuration_from_env() -> tuple[
    tuple[Mapping[str, object], ...] | None,
    tuple[Mapping[str, object], ...] | None,
    tuple[Mapping[str, object], ...] | None,
    bool,
    bool,
    int,
]:
    enabled = os.environ.get("IIP_AI_COST_ENGINE_ENABLED", "false").lower()
    if enabled not in ("false", "true"):
        raise AiCostConfigurationError("ai.cost.configuration.invalid")
    if enabled == "false":
        return None, None, None, False, False, 100
    raw_catalogs = os.environ.get("IIP_AI_PRICE_CATALOGS_JSON")
    if raw_catalogs is None:
        raise AiCostConfigurationError("ai.cost.configuration.required")
    allow_raw = os.environ.get(
        "IIP_AI_PRICE_CATALOG_ALLOW_TEST_FIXTURES",
        "false",
    ).lower()
    if allow_raw not in ("false", "true"):
        raise AiCostConfigurationError("ai.cost.configuration.invalid")
    require_raw = os.environ.get(
        "IIP_AI_PRICE_CATALOG_REQUIRE_QUALIFICATION",
        "false",
    ).lower()
    if require_raw not in ("false", "true"):
        raise AiCostConfigurationError("ai.cost.configuration.invalid")
    raw_qualifications = os.environ.get(
        "IIP_AI_PRICE_CATALOG_QUALIFICATIONS_JSON"
    )
    if (require_raw == "true") != (raw_qualifications is not None):
        raise AiCostConfigurationError("ai.cost.configuration.required")
    try:
        batch_size = int(os.environ.get("IIP_AI_COST_BATCH_SIZE", "100"))
    except ValueError:
        raise AiCostConfigurationError("ai.cost.configuration.invalid") from None
    if not 1 <= batch_size <= 1000:
        raise AiCostConfigurationError("ai.cost.configuration.invalid")

    from iip.adapters.ai_price_catalogs import ai_price_catalogs_from_json
    from iip.adapters.ai_price_catalog_qualifications import (
        ai_price_catalog_qualifications_from_json,
    )

    catalogs = ai_price_catalogs_from_json(raw_catalogs)
    policies: tuple[Mapping[str, object], ...] | None = None
    reports: tuple[Mapping[str, object], ...] | None = None
    if raw_qualifications is not None:
        policies, reports = ai_price_catalog_qualifications_from_json(
            raw_qualifications
        )
    worker_tenants = {
        item.strip()
        for item in os.environ.get("IIP_WORKER_TENANTS", "").split(",")
        if item.strip()
    }
    try:
        catalog_tenants = {
            item["metadata"]["tenantId"]  # type: ignore[index]
            for item in catalogs
        }
    except (KeyError, TypeError):
        raise AiCostConfigurationError("ai.cost.configuration.invalid") from None
    if not worker_tenants or catalog_tenants != worker_tenants:
        raise AiCostConfigurationError("ai.cost.tenants.invalid")
    return (
        catalogs,
        policies,
        reports,
        allow_raw == "true",
        require_raw == "true",
        batch_size,
    )


def _ai_savings_engine_configuration_from_env(
    catalogs: tuple[Mapping[str, object], ...] | None,
) -> tuple[tuple[Mapping[str, object], ...], bool] | None:
    enabled = os.environ.get("IIP_AI_SAVINGS_ENGINE_ENABLED", "false").lower()
    if enabled not in ("false", "true"):
        raise AiSavingsConfigurationError("ai.savings.configuration.invalid")
    if enabled == "false":
        return None
    allow_raw = os.environ.get(
        "IIP_AI_SAVINGS_ALLOW_TEST_FIXTURES",
        "false",
    ).lower()
    if allow_raw not in ("false", "true"):
        raise AiSavingsConfigurationError("ai.savings.configuration.invalid")
    allow_test_fixtures = allow_raw == "true"
    raw_profiles = os.environ.get("IIP_AI_SAVINGS_PROFILES_JSON")
    if raw_profiles is None or catalogs is None:
        raise AiSavingsConfigurationError("ai.savings.configuration.required")

    from iip.adapters.ai_savings_profiles import ai_savings_profiles_from_json

    profiles = ai_savings_profiles_from_json(raw_profiles)
    validated = tuple(
        validate_ai_savings_profile(
            item,
            allow_test_fixtures=allow_test_fixtures,
        )
        for item in profiles
    )
    worker_tenants = {
        item.strip()
        for item in os.environ.get("IIP_WORKER_TENANTS", "").split(",")
        if item.strip()
    }
    profile_tenants = {item.tenant_id for item in validated}
    try:
        catalog_by_tenant = {
            item["metadata"]["tenantId"]: item["metadata"]["id"]  # type: ignore[index]
            for item in catalogs
        }
    except (KeyError, TypeError):
        raise AiSavingsConfigurationError(
            "ai.savings.configuration.invalid"
        ) from None
    if (
        not worker_tenants
        or profile_tenants != worker_tenants
        or any(
            catalog_by_tenant.get(profile.tenant_id) != profile.catalog_id
            for profile in validated
        )
    ):
        raise AiSavingsConfigurationError("ai.savings.tenants.invalid")
    return profiles, allow_test_fixtures
