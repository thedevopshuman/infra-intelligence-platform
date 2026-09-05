"""Dependency-free HTTP surface for the reference vertical slice."""

from __future__ import annotations

import json
import os
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from typing import Any, Callable, Dict, Mapping, Optional
from urllib.parse import parse_qs, urlparse

from google.rpc.status_pb2 import Status

from iip.application.actions import (
    ActionWorkflowError,
    DecideActionCommand,
    ExecuteActionCommand,
    ProposeActionCommand,
)
from iip.application.collect_evidence import (
    EvidenceAuthorizationError,
    EvidenceDeadlineExceededError,
    EvidenceProviderUnavailableError,
    EvidenceRedactionError,
    InvalidEvidenceRequestError,
)
from iip.application.context_evidence import (
    CollectContextEvidenceCommand,
    InvalidContextEvidenceRequestError,
)
from iip.application.evidence_retention import (
    EvidenceRetentionAuthorizationError,
    EvidenceRetentionStateError,
    GetEvidenceRetentionCommand,
)
from iip.application.ingest_collection import (
    CollectionConflictError,
    IngestCollectionCommand,
    InvalidCollectionError,
)
from iip.application.ingest_ai_usage import (
    AiUsagePayloadTooLargeError,
    InvalidAiUsageRequestError,
    validate_ai_usage_channel,
)
from iip.application.ingest_resource import (
    AuthorizationError,
    IngestResourceCommand,
    InvalidInputError,
    ObservationConflictError,
    StaleObservationError,
)
from iip.application.ingest_otlp_metrics import (
    InvalidOtlpMetricsRequestError,
    OtlpPayloadTooLargeError,
    OtlpReceiverAuthenticationError,
    OtlpReceiverConfigurationError,
    validate_channel_context,
)
from iip.application.ingest_otlp_logs import (
    InvalidOtlpLogsRequestError,
    OtlpLogsPayloadTooLargeError,
    validate_logs_channel_context,
)
from iip.application.investigate import (
    InvestigationConflictError,
    InvestigationInProgressError,
    InvalidInvestigationError,
    RunInvestigationCommand,
)
from iip.application.investigation_lifecycle import (
    CancelInvestigationCommand,
    GetInvestigationStatusCommand,
    InvalidInvestigationCancellationError,
    InvestigationLifecycleNotFoundError,
)
from iip.application.investigation_dispatch import (
    CancelInvestigationJobCommand,
    GetInvestigationJobCommand,
    InvestigationJobNotFoundError,
    InvestigationQueueCapacityError,
    SubmitInvestigationJobCommand,
)
from iip.application.kubernetes_event_evidence import (
    CollectKubernetesEventEvidenceCommand,
    InvalidKubernetesEventEvidenceRequestError,
)
from iip.application.log_evidence import (
    CollectLogEvidenceCommand,
    InvalidLogEvidenceRequestError,
)
from iip.application.observe_ingestion import (
    GetIngestionFreshnessCommand,
    IngestionSourceNotFoundError,
    IngestionTelemetryAuthorizationError,
    IngestionTelemetryInputError,
    IngestionTelemetryStateError,
)
from iip.application.plugin_sessions import (
    OpenPluginSessionCommand,
    PluginHandshakeError,
)
from iip.application.plugin_invocations import (
    CancelPluginInvocationCommand,
    GetPluginInvocationStatusCommand,
    PluginInvocationAuthorizationError,
    PluginInvocationConflictError,
    PluginInvocationInputError,
    PluginInvocationNotFoundError,
    ReconcilePluginInvocationCommand,
)
from iip.application.ports import (
    ActorContext,
    AuthenticationError,
    PersistenceError,
    ReadinessError,
)
from iip.application.observe_query_availability import (
    RecordQueryAvailabilityCommand,
)
from iip.application.observe_otlp_receiver import RecordOtlpReceiverCommand
from iip.application.query_actions import (
    ActionQueryAuthorizationError,
    ActionQueryError,
    ActionWorkflowNotFoundError,
)
from iip.application.query_ai_allocations import (
    AiAllocationAuthorizationError,
    AiAllocationQueryError,
)
from iip.application.query_event_delivery_health import (
    EventDeliveryHealthAuthorizationError,
    EventDeliveryHealthInputError,
    EventDeliveryHealthStateError,
    GetEventDeliveryHealthCommand,
)
from iip.application.query_event_delivery_slo import (
    EventDeliverySloAuthorizationError,
    EventDeliverySloStateError,
    GetEventDeliverySloCommand,
)
from iip.application.query_investigation_completion_slo import (
    GetInvestigationCompletionSloCommand,
    InvestigationCompletionSloAuthorizationError,
    InvestigationCompletionSloStateError,
)
from iip.application.query_resources import (
    InvalidCursorError,
    InvalidQueryError,
    PageInfo,
    QueryAuthorizationError,
    ResourceNeighborhoodResult,
    ResourceNotFoundError,
    ResourceTimelineResult,
)
from iip.application.query_runtime_version import GetRuntimeVersionCommand
from iip.application.query_telemetry_deployment_health import (
    GetTelemetryDeploymentHealthCommand,
)
from iip.application.query_telemetry_export_health import (
    GetTelemetryExportHealthCommand,
    TelemetryExportHealthAuthorizationError,
    TelemetryExportHealthStateError,
)
from iip.application.query_telemetry_export_slo import (
    GetTelemetryExportSloCommand,
    TelemetryExportSloAuthorizationError,
    TelemetryExportSloStateError,
)
from iip.application.query_telemetry_export_burn_rate import (
    GetTelemetryExportBurnRateCommand,
    TelemetryExportBurnRateAuthorizationError,
    TelemetryExportBurnRateStateError,
)
from iip.application.query_collector_queue_loss import (
    CollectorQueueLossAuthorizationError,
    CollectorQueueLossStateError,
    GetCollectorQueueLossCommand,
)
from iip.application.resource_change_evidence import (
    CollectResourceChangeEvidenceCommand,
    InvalidResourceChangeEvidenceRequestError,
)
from iip.application.telemetry_evidence import (
    CollectTelemetryEvidenceCommand,
    InvalidTelemetryEvidenceRequestError,
)
from iip.bootstrap import Runtime, build_runtime_from_env


class ApiHandler(BaseHTTPRequestHandler):
    """Small HTTP adapter with credential-derived request identity."""

    runtime: Runtime
    server_version = "IIPReference/0.66.0"

    _console_assets = {
        "/": ("index.html", "text/html; charset=utf-8"),
        "/console": ("index.html", "text/html; charset=utf-8"),
        "/console/": ("index.html", "text/html; charset=utf-8"),
        "/console/app.css": ("app.css", "text/css; charset=utf-8"),
        "/console/app.js": ("app.js", "text/javascript; charset=utf-8"),
    }

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        operation = self._query_operation(urlparse(self.path).path)
        self._query_availability_context = (
            (operation, time.monotonic()) if operation is not None else None
        )
        try:
            self._do_GET()
        except Exception:
            self._finish_query_availability(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                uncaught_failure=True,
            )
            raise

    def _do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path in self._console_assets:
            self._console_asset(path)
            return
        if path == "/healthz":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/readyz":
            self._readiness()
            return
        if path == "/v1/authentication/console":
            if parsed.query:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": {"code": "request.invalid"}},
                )
            else:
                self._json(
                    HTTPStatus.OK,
                    dict(self.runtime.console_authentication),
                )
            return
        segments = path.strip("/").split("/")
        is_resource_query = (
            len(segments) == 4
            and segments[:2] == ["v1", "resources"]
            and segments[3] in ("neighborhood", "timeline")
        )
        if path.startswith("/v1/"):
            try:
                actor = self._actor()
            except AuthenticationError as exc:
                self._authentication_failed(exc)
                return
        if path == "/v1/session":
            self._json(
                HTTPStatus.OK,
                {
                    "apiVersion": "iip.platform/v1alpha1",
                    "kind": "SessionContext",
                    "metadata": {
                        "tenantId": actor.tenant_id,
                        "actorId": actor.actor_id,
                    },
                    "spec": {"roles": sorted(actor.roles)},
                },
            )
            return
        if path == "/v1/system/version":
            if parsed.query:
                self._json(
                    HTTPStatus.BAD_REQUEST,
                    {"error": {"code": "request.invalid"}},
                )
                return
            report = self.runtime.runtime_version.get(
                GetRuntimeVersionCommand(actor)
            )
            self._json(HTTPStatus.OK, report.to_dict())
            return
        if path == "/v1/resources":
            try:
                if parsed.query:
                    raise InvalidQueryError("request.invalid")
                items = [
                    resource.to_dict()
                    for resource in self.runtime.queries.list_resources(actor)
                ]
                self._json(HTTPStatus.OK, {"items": items})
            except QueryAuthorizationError:
                self._json(HTTPStatus.FORBIDDEN, {"error": {"code": "policy.denied"}})
            except InvalidQueryError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": {"code": str(exc)}})
            except PersistenceError:
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": {"code": "storage.unavailable"}},
                )
            return
        if path == "/v1/telemetry/ingestion":
            self._query_ingestion_telemetry(actor, parsed.query)
            return
        if path == "/v1/operations/telemetry/export-health":
            self._query_telemetry_export_health(actor, parsed.query)
            return
        if path == "/v1/operations/telemetry/deployment-export-health":
            self._query_telemetry_deployment_health(actor, parsed.query)
            return
        if path == "/v1/operations/telemetry/export-slo":
            self._query_telemetry_export_slo(actor, parsed.query)
            return
        if path == "/v1/operations/telemetry/export-burn-rate":
            self._query_telemetry_export_burn_rate(actor, parsed.query)
            return
        if path == "/v1/operations/telemetry/collector-queue-loss":
            self._query_collector_queue_loss(actor, parsed.query)
            return
        if path == "/v1/operations/events/delivery-health":
            self._query_event_delivery_health(actor, parsed.query)
            return
        if path == "/v1/operations/events/delivery-slo":
            self._query_event_delivery_slo(actor, parsed.query)
            return
        if path == "/v1/operations/investigations/completion-slo":
            self._query_investigation_completion_slo(actor, parsed.query)
            return
        if path == "/v1/operations/evidence/retention":
            self._query_evidence_retention(actor, parsed.query)
            return
        if path == "/v1/ai/economics/allocation":
            self._query_ai_allocation(actor, parsed.query)
            return
        if path == "/v1/actions":
            self._query_actions(actor, parsed.query)
            return
        if is_resource_query:
            self._query_resource(actor, segments[2], segments[3], parsed.query)
            return
        if len(segments) == 3 and segments[:2] == ["v1", "evidence"]:
            self._stored_document(
                lambda: self.runtime.evidence_store.get(actor, segments[2]),
                "evidence.not_found",
            )
            return
        if len(segments) == 3 and segments[:2] == ["v1", "investigations"]:
            self._stored_document(
                lambda: self.runtime.operational_store.get_investigation(
                    actor, segments[2]
                ),
                "investigation.not_found",
            )
            return
        if len(segments) == 3 and segments[:2] == ["v1", "investigation-jobs"]:
            try:
                document = self.runtime.investigation_dispatch.get(
                    GetInvestigationJobCommand(actor, segments[2])
                )
                self._json(HTTPStatus.OK, dict(document))
            except InvestigationJobNotFoundError:
                self._json(
                    HTTPStatus.NOT_FOUND,
                    {"error": {"code": "investigation.job.not_found"}},
                )
            except PersistenceError:
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": {"code": "storage.unavailable"}},
                )
            return
        if (
            len(segments) == 4
            and segments[:2] == ["v1", "investigations"]
            and segments[3] == "status"
        ):
            try:
                document = self.runtime.investigation_lifecycle.get(
                    GetInvestigationStatusCommand(actor, segments[2])
                )
                self._json(HTTPStatus.OK, dict(document))
            except InvestigationLifecycleNotFoundError:
                self._json(
                    HTTPStatus.NOT_FOUND,
                    {"error": {"code": "investigation.not_found"}},
                )
            except PersistenceError:
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": {"code": "storage.unavailable"}},
                )
            return
        if len(segments) == 3 and segments[:2] == ["v1", "actions"]:
            self._stored_document(
                lambda: self._action_document(actor, segments[2]),
                "action.not_found",
            )
            return
        if (
            len(segments) == 4
            and segments[:2] == ["v1", "actions"]
            and segments[3] == "workflow"
        ):
            try:
                document = self.runtime.action_queries.get(actor, segments[2])
                self._json(HTTPStatus.OK, dict(document))
            except ActionQueryError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": {"code": str(exc)}})
            except ActionQueryAuthorizationError:
                self._json(HTTPStatus.FORBIDDEN, {"error": {"code": "policy.denied"}})
            except ActionWorkflowNotFoundError:
                self._json(HTTPStatus.NOT_FOUND, {"error": {"code": "action.not_found"}})
            except PersistenceError:
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": {"code": "storage.unavailable"}},
                )
            return
        if len(segments) == 3 and segments[:2] == ["v1", "plugin-sessions"]:
            self._stored_document(
                lambda: self.runtime.operational_store.get_plugin_session(
                    actor, segments[2]
                ),
                "plugin.session.not_found",
            )
            return
        if (
            len(segments) == 4
            and segments[:2] == ["v1", "plugin-invocations"]
            and segments[3] == "status"
        ):
            try:
                if parsed.query:
                    raise PluginInvocationInputError(
                        "plugin.lifecycle.request.invalid"
                    )
                document = self.runtime.plugin_invocations.get(
                    GetPluginInvocationStatusCommand(actor, segments[2])
                )
                self._json(HTTPStatus.OK, dict(document))
            except PluginInvocationNotFoundError as exc:
                self._json(HTTPStatus.NOT_FOUND, {"error": {"code": str(exc)}})
            except PluginInvocationAuthorizationError as exc:
                self._json(HTTPStatus.FORBIDDEN, {"error": {"code": str(exc)}})
            except PluginInvocationInputError as exc:
                self._json(HTTPStatus.BAD_REQUEST, {"error": {"code": str(exc)}})
            except PersistenceError:
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": {"code": "storage.unavailable"}},
                )
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": {"code": "route.not_found"}})

    def _query_ingestion_telemetry(
        self, actor: ActorContext, query: str
    ) -> None:
        try:
            parameters = parse_qs(query, keep_blank_values=True)
            if set(parameters) != {"sourceId"}:
                raise IngestionTelemetryInputError("ingestion.source_id.invalid")
            source_id = self._single(parameters, "sourceId")
            if source_id is None:
                raise IngestionTelemetryInputError("ingestion.source_id.invalid")
            report = self.runtime.ingestion_telemetry.get(
                GetIngestionFreshnessCommand(actor, source_id)
            )
            self._json(HTTPStatus.OK, report.to_dict())
        except (IngestionTelemetryInputError, InvalidQueryError):
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"code": "request.invalid"}},
            )
        except IngestionTelemetryAuthorizationError:
            self._json(
                HTTPStatus.FORBIDDEN,
                {"error": {"code": "policy.denied"}},
            )
        except IngestionSourceNotFoundError:
            self._json(
                HTTPStatus.NOT_FOUND,
                {"error": {"code": "ingestion.source_not_found"}},
            )
        except (IngestionTelemetryStateError, PersistenceError):
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": {"code": "storage.unavailable"}},
            )

    def _query_ai_allocation(self, actor: ActorContext, query: str) -> None:
        try:
            parameters = parse_qs(query, keep_blank_values=True)
            if set(parameters) != {"start", "end", "groupBy"}:
                raise AiAllocationQueryError("request.invalid")
            start = self._single(parameters, "start")
            end = self._single(parameters, "end")
            group_by = self._single(parameters, "groupBy")
            if start is None or end is None or group_by is None:
                raise AiAllocationQueryError("request.invalid")
            service = self.runtime.ai_allocation_reports
            if service is None:
                self._json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": {"code": "ai.allocation.not-configured"}},
                )
                return
            report = service.get(
                actor,
                start=start,
                end=end,
                group_by=group_by,
            )
            self._json(HTTPStatus.OK, dict(report))
        except AiAllocationAuthorizationError:
            self._json(
                HTTPStatus.FORBIDDEN,
                {"error": {"code": "policy.denied"}},
            )
        except AiAllocationQueryError as exc:
            code = str(exc)
            status = (
                HTTPStatus.UNPROCESSABLE_ENTITY
                if code == "ai.allocation.source-limit-exceeded"
                else HTTPStatus.SERVICE_UNAVAILABLE
                if code == "ai.allocation.not-configured"
                else HTTPStatus.BAD_REQUEST
            )
            self._json(status, {"error": {"code": code}})
        except PersistenceError:
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": {"code": "storage.unavailable"}},
            )

    def _query_telemetry_export_health(
        self, actor: ActorContext, query: str
    ) -> None:
        try:
            if query:
                raise ValueError
            report = self.runtime.telemetry_export_health.get(
                GetTelemetryExportHealthCommand(actor)
            )
            self._json(HTTPStatus.OK, report.to_dict())
        except ValueError:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"code": "request.invalid"}},
            )
        except TelemetryExportHealthAuthorizationError:
            self._json(
                HTTPStatus.FORBIDDEN,
                {"error": {"code": "policy.denied"}},
            )
        except TelemetryExportHealthStateError:
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": {"code": "telemetry.export-health.unavailable"}},
            )

    def _query_telemetry_deployment_health(
        self, actor: ActorContext, query: str
    ) -> None:
        try:
            if query:
                raise ValueError
            report = self.runtime.telemetry_deployment_health.get(
                GetTelemetryDeploymentHealthCommand(actor)
            )
            self._json(HTTPStatus.OK, report.to_dict())
        except ValueError:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"code": "request.invalid"}},
            )
        except TelemetryExportHealthAuthorizationError:
            self._json(
                HTTPStatus.FORBIDDEN,
                {"error": {"code": "policy.denied"}},
            )
        except (TelemetryExportHealthStateError, PersistenceError):
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": {"code": "telemetry.export-health.unavailable"}},
            )

    def _query_telemetry_export_slo(
        self, actor: ActorContext, query: str
    ) -> None:
        try:
            if query:
                raise ValueError
            report = self.runtime.telemetry_export_slo.get(
                GetTelemetryExportSloCommand(actor)
            )
            self._json(HTTPStatus.OK, report.to_dict())
        except ValueError:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"code": "request.invalid"}},
            )
        except TelemetryExportSloAuthorizationError:
            self._json(
                HTTPStatus.FORBIDDEN,
                {"error": {"code": "policy.denied"}},
            )
        except (TelemetryExportSloStateError, PersistenceError):
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": {"code": "telemetry.export-slo.unavailable"}},
            )

    def _query_telemetry_export_burn_rate(
        self, actor: ActorContext, query: str
    ) -> None:
        try:
            if query:
                raise ValueError
            report = self.runtime.telemetry_export_burn_rate.get(
                GetTelemetryExportBurnRateCommand(actor)
            )
            self._json(HTTPStatus.OK, report.to_dict())
        except ValueError:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"code": "request.invalid"}},
            )
        except TelemetryExportBurnRateAuthorizationError:
            self._json(
                HTTPStatus.FORBIDDEN,
                {"error": {"code": "policy.denied"}},
            )
        except (TelemetryExportBurnRateStateError, PersistenceError):
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": {"code": "telemetry.export-burn-rate.unavailable"}},
            )

    def _query_collector_queue_loss(
        self, actor: ActorContext, query: str
    ) -> None:
        try:
            if query:
                raise ValueError
            report = self.runtime.collector_queue_loss.get(
                GetCollectorQueueLossCommand(actor)
            )
            self._json(HTTPStatus.OK, report.to_dict())
        except ValueError:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"code": "request.invalid"}},
            )
        except CollectorQueueLossAuthorizationError:
            self._json(
                HTTPStatus.FORBIDDEN,
                {"error": {"code": "policy.denied"}},
            )
        except (CollectorQueueLossStateError, PersistenceError):
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": {"code": "telemetry.collector-queue-loss.unavailable"}},
            )

    def _query_event_delivery_health(
        self, actor: ActorContext, query: str
    ) -> None:
        try:
            try:
                parameters = parse_qs(
                    query,
                    keep_blank_values=True,
                    max_num_fields=2,
                )
            except ValueError:
                raise EventDeliveryHealthInputError("request.invalid") from None
            if set(parameters).difference({"limit"}):
                raise EventDeliveryHealthInputError("request.invalid")
            values = parameters.get("limit", ["50"])
            if len(values) != 1 or not values[0]:
                raise EventDeliveryHealthInputError("request.invalid")
            try:
                limit = int(values[0])
            except ValueError:
                raise EventDeliveryHealthInputError("request.invalid") from None
            report = self.runtime.event_delivery_health.get(
                GetEventDeliveryHealthCommand(actor, limit)
            )
            self._json(HTTPStatus.OK, report.to_dict())
        except EventDeliveryHealthInputError:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"code": "request.invalid"}},
            )
        except EventDeliveryHealthAuthorizationError:
            self._json(
                HTTPStatus.FORBIDDEN,
                {"error": {"code": "policy.denied"}},
            )
        except (EventDeliveryHealthStateError, PersistenceError):
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": {"code": "event.delivery-health.unavailable"}},
            )

    def _query_event_delivery_slo(
        self, actor: ActorContext, query: str
    ) -> None:
        try:
            if query:
                raise ValueError
            report = self.runtime.event_delivery_slo.get(
                GetEventDeliverySloCommand(actor)
            )
            self._json(HTTPStatus.OK, report.to_dict())
        except ValueError:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"code": "request.invalid"}},
            )
        except EventDeliverySloAuthorizationError:
            self._json(
                HTTPStatus.FORBIDDEN,
                {"error": {"code": "policy.denied"}},
            )
        except (EventDeliverySloStateError, PersistenceError):
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": {"code": "event.delivery-slo.unavailable"}},
            )

    def _query_investigation_completion_slo(
        self, actor: ActorContext, query: str
    ) -> None:
        try:
            if query:
                raise ValueError
            report = self.runtime.investigation_completion_slo.get(
                GetInvestigationCompletionSloCommand(actor)
            )
            self._json(HTTPStatus.OK, report.to_dict())
        except ValueError:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"code": "request.invalid"}},
            )
        except InvestigationCompletionSloAuthorizationError:
            self._json(
                HTTPStatus.FORBIDDEN,
                {"error": {"code": "policy.denied"}},
            )
        except (InvestigationCompletionSloStateError, PersistenceError):
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "error": {
                        "code": "investigation.completion-slo.unavailable"
                    }
                },
            )

    def _query_evidence_retention(
        self, actor: ActorContext, query: str
    ) -> None:
        try:
            if query:
                raise ValueError
            report = self.runtime.evidence_retention.get(
                GetEvidenceRetentionCommand(actor)
            )
            self._json(HTTPStatus.OK, report.to_dict())
        except ValueError:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"code": "request.invalid"}},
            )
        except EvidenceRetentionAuthorizationError:
            self._json(
                HTTPStatus.FORBIDDEN,
                {"error": {"code": "policy.denied"}},
            )
        except (EvidenceRetentionStateError, PersistenceError):
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": {"code": "evidence.retention.unavailable"}},
            )

    def _query_actions(self, actor: ActorContext, query: str) -> None:
        try:
            try:
                parameters = parse_qs(
                    query,
                    keep_blank_values=True,
                    max_num_fields=3,
                )
            except ValueError:
                raise ActionQueryError("request.invalid") from None
            if set(parameters).difference({"cursor", "limit"}):
                raise ActionQueryError("request.invalid")
            cursor = self._single(parameters, "cursor")
            document = self.runtime.action_queries.list(
                actor,
                limit=self._limit(parameters),
                cursor=cursor,
            )
            self._json(HTTPStatus.OK, dict(document))
        except (ActionQueryError, InvalidQueryError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": {"code": str(exc)}})
        except ActionQueryAuthorizationError:
            self._json(HTTPStatus.FORBIDDEN, {"error": {"code": "policy.denied"}})
        except PersistenceError:
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": {"code": "storage.unavailable"}},
            )

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler contract
        path = urlparse(self.path).path
        if path == "/v1/metrics":
            self._receive_otlp_metrics()
            return
        if path == "/v1/logs":
            self._receive_otlp_logs()
            return
        if path == "/v1/traces":
            self._receive_ai_usage_traces()
            return
        segments = path.strip("/").split("/")
        known = (
            path
            in (
                "/v1/resources",
                "/v1/collections/ingest",
                "/v1/evidence/kubernetes/events/queries",
                "/v1/evidence/changes/queries",
                "/v1/evidence/context/queries",
                "/v1/evidence/logs/queries",
                "/v1/evidence/telemetry/queries",
                "/v1/investigations",
                "/v1/investigation-jobs",
                "/v1/actions/proposals",
                "/v1/plugin-sessions",
            )
            or (len(segments) == 4 and segments[:2] == ["v1", "actions"] and segments[3] in ("decision", "execute"))
            or (
                len(segments) == 4
                and segments[:2] == ["v1", "investigations"]
                and segments[3] == "cancel"
            )
            or (
                len(segments) == 4
                and segments[:2] == ["v1", "investigation-jobs"]
                and segments[3] == "cancel"
            )
            or (
                len(segments) == 4
                and segments[:2] == ["v1", "plugin-invocations"]
                and segments[3] in ("cancel", "reconcile")
            )
        )
        if not known:
            self._json(HTTPStatus.NOT_FOUND, {"error": {"code": "route.not_found"}})
            return
        try:
            actor = self._actor()
        except AuthenticationError as exc:
            self._authentication_failed(exc)
            return
        try:
            payload = self._read_json()
            if path == "/v1/resources":
                document: Mapping[str, object] = self.runtime.ingestion.execute(
                    IngestResourceCommand(
                        actor=actor,
                        payload=payload,
                        correlation_id=self.headers.get("x-correlation-id"),
                    )
                ).to_dict()
                status = HTTPStatus.ACCEPTED
            elif path == "/v1/collections/ingest":
                resources = self.runtime.collection_ingestion.execute(
                    IngestCollectionCommand(
                        actor=actor,
                        request=payload["request"],
                        result=payload["result"],
                        correlation_id=self.headers.get("x-correlation-id"),
                    )
                )
                document = {"items": [resource.to_dict() for resource in resources]}
                status = HTTPStatus.ACCEPTED
            elif path == "/v1/evidence/telemetry/queries":
                document = self.runtime.telemetry_evidence.execute(
                    CollectTelemetryEvidenceCommand(actor, payload)
                )
                status = HTTPStatus.CREATED
            elif path == "/v1/evidence/logs/queries":
                document = self.runtime.log_evidence.execute(
                    CollectLogEvidenceCommand(actor, payload)
                )
                status = HTTPStatus.CREATED
            elif path == "/v1/evidence/kubernetes/events/queries":
                document = self.runtime.kubernetes_event_evidence.execute(
                    CollectKubernetesEventEvidenceCommand(actor, payload)
                )
                status = HTTPStatus.CREATED
            elif path == "/v1/evidence/changes/queries":
                document = self.runtime.resource_change_evidence.execute(
                    CollectResourceChangeEvidenceCommand(actor, payload)
                )
                status = HTTPStatus.CREATED
            elif path == "/v1/evidence/context/queries":
                document = self.runtime.context_evidence.execute(
                    CollectContextEvidenceCommand(actor, payload)
                )
                status = HTTPStatus.CREATED
            elif path == "/v1/investigations":
                document = self.runtime.investigations.execute(
                    RunInvestigationCommand(actor, payload)
                )
                status = HTTPStatus.CREATED
            elif path == "/v1/investigation-jobs":
                document = self.runtime.investigation_dispatch.submit(
                    SubmitInvestigationJobCommand(actor, payload)
                )
                status = HTTPStatus.ACCEPTED
            elif (
                len(segments) == 4
                and segments[:2] == ["v1", "investigation-jobs"]
                and segments[3] == "cancel"
            ):
                cancellation_spec = payload.get("spec")
                if (
                    not isinstance(cancellation_spec, Mapping)
                    or cancellation_spec.get("investigationId") != segments[2]
                ):
                    raise InvalidInvestigationCancellationError(
                        "investigation.cancellation.invalid"
                    )
                document = self.runtime.investigation_dispatch.cancel(
                    CancelInvestigationJobCommand(actor, payload)
                )
                status = HTTPStatus.ACCEPTED
            elif (
                len(segments) == 4
                and segments[:2] == ["v1", "investigations"]
                and segments[3] == "cancel"
            ):
                cancellation_spec = payload.get("spec")
                if (
                    not isinstance(cancellation_spec, Mapping)
                    or cancellation_spec.get("investigationId") != segments[2]
                ):
                    raise InvalidInvestigationCancellationError(
                        "investigation.cancellation.invalid"
                    )
                document = self.runtime.investigation_lifecycle.cancel(
                    CancelInvestigationCommand(actor, payload)
                )
                status = HTTPStatus.ACCEPTED
            elif path == "/v1/actions/proposals":
                document = self.runtime.actions.propose(
                    ProposeActionCommand(
                        actor=actor,
                        investigation_id=payload["investigationId"],
                        action_type=payload["actionType"],
                        target_resource_uid=payload["targetResourceUid"],
                        parameters=payload.get("parameters", {}),
                        idempotency_key=payload["idempotencyKey"],
                        expires_at=payload["expiresAt"],
                        dry_run=payload.get("dryRun", True),
                    )
                )
                status = HTTPStatus.CREATED
            elif len(segments) == 4 and segments[3] == "decision":
                document = self.runtime.actions.decide(
                    DecideActionCommand(
                        actor=actor,
                        proposal_id=segments[2],
                        decision=payload["decision"],
                        rationale=payload["rationale"],
                    )
                )
                status = HTTPStatus.CREATED
            elif len(segments) == 4 and segments[3] == "execute":
                document = self.runtime.actions.execute(
                    ExecuteActionCommand(actor=actor, proposal_id=segments[2])
                )
                status = HTTPStatus.OK
            elif (
                len(segments) == 4
                and segments[:2] == ["v1", "plugin-invocations"]
                and segments[3] in ("cancel", "reconcile")
            ):
                lifecycle_spec = payload.get("spec")
                if (
                    not isinstance(lifecycle_spec, Mapping)
                    or lifecycle_spec.get("invocationId") != segments[2]
                ):
                    raise PluginInvocationInputError(
                        "plugin.lifecycle.request.invalid"
                    )
                if segments[3] == "cancel":
                    document = self.runtime.plugin_invocations.cancel(
                        CancelPluginInvocationCommand(actor, payload)
                    )
                    status = HTTPStatus.ACCEPTED
                else:
                    document = self.runtime.plugin_invocations.reconcile(
                        ReconcilePluginInvocationCommand(actor, payload)
                    )
                    status = HTTPStatus.OK
            else:
                limits = payload.get("limits", {})
                document = self.runtime.plugin_sessions.open(
                    OpenPluginSessionCommand(
                        actor=actor,
                        manifest=payload["manifest"],
                        requested_capabilities=tuple(payload["requestedCapabilities"]),
                        capability_token=payload["capabilityToken"],
                        max_requests=limits.get("maxRequests", 1),
                        max_wall_time_seconds=limits.get("maxWallTimeSeconds", 60),
                        max_output_bytes=limits.get("maxOutputBytes", 16_777_216),
                    )
                )
                status = HTTPStatus.CREATED
            self._json(status, dict(document))
        except InvalidInputError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": {"code": "contract.invalid"}})
        except AuthorizationError:
            self._json(HTTPStatus.FORBIDDEN, {"error": {"code": "policy.denied"}})
        except StaleObservationError:
            self._json(
                HTTPStatus.CONFLICT,
                {"error": {"code": "resource.observation.stale"}},
            )
        except ObservationConflictError:
            self._json(
                HTTPStatus.CONFLICT,
                {"error": {"code": "resource.observation.conflict"}},
            )
        except (InvalidCollectionError, InvalidInvestigationError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": {"code": str(exc)}})
        except CollectionConflictError as exc:
            self._json(HTTPStatus.CONFLICT, {"error": {"code": str(exc)}})
        except InvalidInvestigationCancellationError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": {"code": str(exc)}})
        except InvestigationLifecycleNotFoundError:
            self._json(
                HTTPStatus.NOT_FOUND,
                {"error": {"code": "investigation.not_found"}},
            )
        except InvestigationJobNotFoundError:
            self._json(
                HTTPStatus.NOT_FOUND,
                {"error": {"code": "investigation.job.not_found"}},
            )
        except InvalidKubernetesEventEvidenceRequestError:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"code": "kubernetes.event.request.invalid"}},
            )
        except InvalidResourceChangeEvidenceRequestError:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"code": "change.request.invalid"}},
            )
        except InvalidContextEvidenceRequestError:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"code": "context.request.invalid"}},
            )
        except (InvalidLogEvidenceRequestError, InvalidTelemetryEvidenceRequestError):
            code = (
                "logs.request.invalid"
                if path == "/v1/evidence/logs/queries"
                else "telemetry.request.invalid"
            )
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"code": code}},
            )
        except InvalidEvidenceRequestError:
            if path == "/v1/evidence/kubernetes/events/queries":
                code = "kubernetes.event.request.invalid"
            elif path == "/v1/evidence/changes/queries":
                code = "change.request.invalid"
            elif path == "/v1/evidence/context/queries":
                code = "context.request.invalid"
            elif path == "/v1/evidence/logs/queries":
                code = "logs.request.invalid"
            else:
                code = "telemetry.request.invalid"
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"code": code}},
            )
        except EvidenceAuthorizationError:
            self._json(HTTPStatus.FORBIDDEN, {"error": {"code": "policy.denied"}})
        except EvidenceDeadlineExceededError:
            self._json(
                HTTPStatus.REQUEST_TIMEOUT,
                {"error": {"code": "evidence.deadline.exceeded"}},
            )
        except (EvidenceProviderUnavailableError, EvidenceRedactionError):
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": {"code": "evidence.provider.unavailable"}},
            )
        except (InvestigationConflictError, InvestigationInProgressError) as exc:
            self._json(HTTPStatus.CONFLICT, {"error": {"code": str(exc)}})
        except InvestigationQueueCapacityError as exc:
            self._json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"error": {"code": str(exc)}},
            )
        except ActionWorkflowError as exc:
            code = str(exc)
            status = (
                HTTPStatus.FORBIDDEN
                if any(word in code for word in ("denied", "role-required", "not-approved"))
                else HTTPStatus.SERVICE_UNAVAILABLE
                if code.endswith("state-unavailable")
                else HTTPStatus.CONFLICT
                if any(
                    word in code
                    for word in (
                        "conflict",
                        "expired",
                        "in-progress",
                        "reconciliation-required",
                        "proposal-mismatch",
                    )
                )
                else HTTPStatus.BAD_REQUEST
            )
            self._json(status, {"error": {"code": code}})
        except PluginHandshakeError as exc:
            code = str(exc)
            status = HTTPStatus.FORBIDDEN if code.endswith("policy-denied") else HTTPStatus.BAD_REQUEST
            self._json(status, {"error": {"code": code}})
        except PluginInvocationInputError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": {"code": str(exc)}})
        except PluginInvocationAuthorizationError as exc:
            self._json(HTTPStatus.FORBIDDEN, {"error": {"code": str(exc)}})
        except PluginInvocationNotFoundError as exc:
            self._json(HTTPStatus.NOT_FOUND, {"error": {"code": str(exc)}})
        except PluginInvocationConflictError as exc:
            self._json(HTTPStatus.CONFLICT, {"error": {"code": str(exc)}})
        except PersistenceError:
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": {"code": "storage.unavailable"}},
            )
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, ValueError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": {"code": "request.invalid_json"}})

    def _query_resource(
        self,
        actor: ActorContext,
        resource_uid: str,
        query_kind: str,
        query: str,
    ) -> None:
        try:
            try:
                parameters = parse_qs(
                    query,
                    keep_blank_values=True,
                    max_num_fields=40,
                )
            except ValueError:
                raise InvalidQueryError("request.invalid") from None
            if query_kind == "neighborhood":
                allowed = {"cursor", "depth", "direction", "limit", "relationshipType"}
                if set(parameters).difference(allowed):
                    raise InvalidQueryError("request.invalid")
                if "depth" in parameters and self._single(parameters, "depth") != "1":
                    raise InvalidQueryError("request.invalid")
                result = self.runtime.queries.neighborhood(
                    actor,
                    resource_uid,
                    direction=self._single(parameters, "direction", "both"),
                    relationship_types=parameters.get("relationshipType", []),
                    limit=self._limit(parameters),
                    cursor=self._single(parameters, "cursor", None),
                )
                self._json(HTTPStatus.OK, self._neighborhood_payload(result))
            else:
                allowed = {"cursor", "limit"}
                if set(parameters).difference(allowed):
                    raise InvalidQueryError("request.invalid")
                result = self.runtime.queries.timeline(
                    actor,
                    resource_uid,
                    limit=self._limit(parameters),
                    cursor=self._single(parameters, "cursor", None),
                )
                self._json(HTTPStatus.OK, self._timeline_payload(result))
        except InvalidCursorError:
            self._json(
                HTTPStatus.BAD_REQUEST,
                {"error": {"code": "pagination.cursor_invalid"}},
            )
        except InvalidQueryError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": {"code": "request.invalid"}})
        except QueryAuthorizationError:
            self._json(HTTPStatus.FORBIDDEN, {"error": {"code": "policy.denied"}})
        except ResourceNotFoundError:
            self._json(HTTPStatus.NOT_FOUND, {"error": {"code": "resource.not_found"}})
        except PersistenceError:
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": {"code": "storage.unavailable"}},
            )

    def _actor(self) -> ActorContext:
        return self.runtime.authenticator.authenticate_bearer(self._bearer_token())

    def _bearer_token(self) -> str:
        get_all = getattr(self.headers, "get_all", None)
        if callable(get_all):
            values = get_all("authorization") or []
        else:
            value = self.headers.get("authorization")
            values = [value] if value is not None else []
        if not values:
            raise AuthenticationError("authentication.required")
        if len(values) != 1 or not isinstance(values[0], str):
            raise AuthenticationError("authentication.invalid")
        scheme, separator, token = values[0].partition(" ")
        if (
            scheme.lower() != "bearer"
            or separator != " "
            or not token
            or any(character.isspace() for character in token)
        ):
            raise AuthenticationError("authentication.invalid")
        return token

    def _receive_otlp_metrics(self) -> None:
        self._otlp_receiver_context = ("metrics", time.monotonic())
        service = self.runtime.otlp_metrics_ingestion
        if service is None:
            self._otlp_failure(HTTPStatus.NOT_FOUND, "otlp.receiver.disabled")
            return
        try:
            try:
                token = self._bearer_token()
            except AuthenticationError as exc:
                code = (
                    "otlp.authentication.required"
                    if str(exc) == "authentication.required"
                    else "otlp.authentication.invalid"
                )
                raise OtlpReceiverAuthenticationError(code) from None
            channel = service.authenticate_bearer(token)
            validate_channel_context(channel)
            self._authorize_otlp_transport(channel.channel_id)
            if not self._admit_otlp_channel(channel.channel_id):
                self._otlp_failure(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    "otlp.rate-limit.exceeded",
                )
                return

            content_type = self.headers.get("content-type", "")
            media_type = content_type.partition(";")[0].strip().lower()
            if media_type != "application/x-protobuf":
                self._otlp_failure(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    "otlp.content-type.unsupported",
                )
                return
            encoding = self.headers.get("content-encoding", "identity").strip().lower()
            payload = self._read_binary(channel.limits.max_request_bytes)
            service.ingest(channel, payload, content_encoding=encoding)
            self._otlp_response(HTTPStatus.OK, b"")
        except OtlpReceiverAuthenticationError as exc:
            self._otlp_failure(HTTPStatus.UNAUTHORIZED, str(exc))
        except OtlpPayloadTooLargeError:
            self._otlp_failure(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "otlp.request.too-large",
            )
        except InvalidOtlpMetricsRequestError as exc:
            status = (
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE
                if str(exc) == "otlp.compression.unsupported"
                else HTTPStatus.BAD_REQUEST
            )
            self._otlp_failure(status, str(exc))
        except OtlpReceiverConfigurationError:
            self._otlp_failure(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "otlp.receiver.unavailable",
            )
        except EvidenceAuthorizationError:
            self._otlp_failure(HTTPStatus.FORBIDDEN, "policy.denied")
        except InvalidEvidenceRequestError:
            self._otlp_failure(HTTPStatus.BAD_REQUEST, "otlp.request.invalid")
        except EvidenceDeadlineExceededError:
            self._otlp_failure(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "otlp.receiver.unavailable",
            )
        except (EvidenceProviderUnavailableError, EvidenceRedactionError, PersistenceError):
            self._otlp_failure(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "otlp.receiver.unavailable",
            )
        except Exception:
            self._finish_otlp_receiver_telemetry(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                uncaught_failure=True,
            )
            raise

    def _receive_otlp_logs(self) -> None:
        self._otlp_receiver_context = ("logs", time.monotonic())
        service = self.runtime.otlp_logs_ingestion
        if service is None:
            self._otlp_failure(HTTPStatus.NOT_FOUND, "otlp.receiver.disabled")
            return
        try:
            try:
                token = self._bearer_token()
            except AuthenticationError as exc:
                code = (
                    "otlp.authentication.required"
                    if str(exc) == "authentication.required"
                    else "otlp.authentication.invalid"
                )
                raise OtlpReceiverAuthenticationError(code) from None
            channel = service.authenticate_bearer(token)
            validate_logs_channel_context(channel)
            self._authorize_otlp_transport(channel.channel_id)
            if not self._admit_otlp_channel(channel.channel_id):
                self._otlp_failure(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    "otlp.rate-limit.exceeded",
                )
                return
            content_type = self.headers.get("content-type", "")
            media_type = content_type.partition(";")[0].strip().lower()
            if media_type != "application/x-protobuf":
                self._otlp_failure(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    "otlp.content-type.unsupported",
                )
                return
            encoding = self.headers.get("content-encoding", "identity").strip().lower()
            payload = self._read_binary(channel.limits.max_request_bytes)
            service.ingest(channel, payload, content_encoding=encoding)
            self._otlp_response(HTTPStatus.OK, b"")
        except OtlpReceiverAuthenticationError as exc:
            self._otlp_failure(HTTPStatus.UNAUTHORIZED, str(exc))
        except (OtlpLogsPayloadTooLargeError, OtlpPayloadTooLargeError):
            self._otlp_failure(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "otlp.request.too-large",
            )
        except (InvalidOtlpLogsRequestError, InvalidOtlpMetricsRequestError) as exc:
            status = (
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE
                if str(exc) == "otlp.compression.unsupported"
                else HTTPStatus.BAD_REQUEST
            )
            self._otlp_failure(status, str(exc))
        except OtlpReceiverConfigurationError:
            self._otlp_failure(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "otlp.receiver.unavailable",
            )
        except EvidenceAuthorizationError:
            self._otlp_failure(HTTPStatus.FORBIDDEN, "policy.denied")
        except InvalidEvidenceRequestError:
            self._otlp_failure(HTTPStatus.BAD_REQUEST, "otlp.request.invalid")
        except EvidenceDeadlineExceededError:
            self._otlp_failure(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "otlp.receiver.unavailable",
            )
        except (EvidenceProviderUnavailableError, EvidenceRedactionError, PersistenceError):
            self._otlp_failure(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "otlp.receiver.unavailable",
            )
        except Exception:
            self._finish_otlp_receiver_telemetry(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                uncaught_failure=True,
            )
            raise

    def _receive_ai_usage_traces(self) -> None:
        self._otlp_receiver_context = ("traces", time.monotonic())
        service = self.runtime.ai_usage_ingestion
        if service is None:
            self._otlp_failure(HTTPStatus.NOT_FOUND, "otlp.receiver.disabled")
            return
        try:
            try:
                token = self._bearer_token()
            except AuthenticationError as exc:
                code = (
                    "otlp.authentication.required"
                    if str(exc) == "authentication.required"
                    else "otlp.authentication.invalid"
                )
                raise OtlpReceiverAuthenticationError(code) from None
            channel = service.authenticate_bearer(token)
            validate_ai_usage_channel(channel)
            self._authorize_otlp_transport(channel.channel_id)
            if not self._admit_otlp_channel(channel.channel_id):
                self._otlp_failure(
                    HTTPStatus.TOO_MANY_REQUESTS,
                    "otlp.rate-limit.exceeded",
                )
                return
            content_type = self.headers.get("content-type", "")
            media_type = content_type.partition(";")[0].strip().lower()
            if media_type != "application/x-protobuf":
                self._otlp_failure(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    "otlp.content-type.unsupported",
                )
                return
            encoding = self.headers.get("content-encoding", "identity").strip().lower()
            payload = self._read_binary(channel.limits.max_request_bytes)
            service.ingest(channel, payload, content_encoding=encoding)
            self._otlp_response(HTTPStatus.OK, b"")
        except OtlpReceiverAuthenticationError as exc:
            self._otlp_failure(HTTPStatus.UNAUTHORIZED, str(exc))
        except (AiUsagePayloadTooLargeError, OtlpPayloadTooLargeError):
            self._otlp_failure(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                "otlp.request.too-large",
            )
        except (InvalidAiUsageRequestError, InvalidOtlpMetricsRequestError) as exc:
            status = (
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE
                if str(exc) == "otlp.compression.unsupported"
                else HTTPStatus.BAD_REQUEST
            )
            self._otlp_failure(status, str(exc))
        except OtlpReceiverConfigurationError:
            self._otlp_failure(
                HTTPStatus.SERVICE_UNAVAILABLE,
                "otlp.receiver.unavailable",
            )
        except PersistenceError as exc:
            if str(exc) == "storage.conflict":
                self._otlp_failure(HTTPStatus.CONFLICT, "otlp.usage.conflict")
            else:
                self._otlp_failure(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "otlp.receiver.unavailable",
                )
        except Exception:
            self._finish_otlp_receiver_telemetry(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                uncaught_failure=True,
            )
            raise

    def _authentication_failed(self, error: AuthenticationError) -> None:
        code = str(error)
        if code not in ("authentication.required", "authentication.invalid"):
            code = "authentication.invalid"
        self._json(HTTPStatus.UNAUTHORIZED, {"error": {"code": code}})

    def _readiness(self) -> None:
        try:
            self.runtime.readiness.check()
        except ReadinessError:
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {
                    "status": "unavailable",
                    "error": {"code": "readiness.unavailable"},
                },
            )
            return
        self._json(HTTPStatus.OK, {"status": "ok"})

    def _admit_otlp_channel(self, channel_id: str) -> bool:
        """Allow the shared compatibility receiver without process-level throttling."""

        del channel_id
        return True

    def _authorize_otlp_transport(self, channel_id: str) -> None:
        """Keep transport identity optional only for shared development mode."""

        del channel_id

    def _stored_document(
        self,
        loader: Callable[[], Optional[Mapping[str, object]]],
        not_found_code: str,
    ) -> None:
        try:
            document = loader()
        except PersistenceError:
            self._json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"error": {"code": "storage.unavailable"}},
            )
            return
        self._json(
            HTTPStatus.OK if document is not None else HTTPStatus.NOT_FOUND,
            dict(document)
            if document is not None
            else {"error": {"code": not_found_code}},
        )

    def _action_document(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[Mapping[str, object]]:
        document = self.runtime.operational_store.get_action_result(actor, proposal_id)
        if document is not None:
            return document
        document = self.runtime.operational_store.get_action_execution_status(
            actor, proposal_id
        )
        if document is not None:
            return document
        return self.runtime.operational_store.get_proposal(actor, proposal_id)

    @staticmethod
    def _single(
        parameters: Mapping[str, list[str]],
        name: str,
        default: Optional[str] = None,
    ) -> Optional[str]:
        values = parameters.get(name)
        if values is None:
            return default
        if len(values) != 1 or not values[0]:
            raise InvalidQueryError("request.invalid")
        return values[0]

    @classmethod
    def _limit(cls, parameters: Mapping[str, list[str]]) -> int:
        value = cls._single(parameters, "limit", "50")
        try:
            return int(value) if value is not None else 50
        except ValueError:
            raise InvalidQueryError("request.invalid") from None

    @staticmethod
    def _page_payload(page: PageInfo) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"limit": page.limit, "hasMore": page.has_more}
        if page.next_cursor is not None:
            payload["nextCursor"] = page.next_cursor
        return payload

    @classmethod
    def _neighborhood_payload(
        cls, result: ResourceNeighborhoodResult
    ) -> Dict[str, Any]:
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "ResourceNeighborhood",
            "metadata": {
                "tenantId": result.tenant_id,
                "rootResourceUid": result.root_resource_uid,
            },
            "spec": {
                "depth": 1,
                "direction": result.direction,
                "nodes": [resource.to_dict() for resource in result.nodes],
                "edges": [
                    {
                        "id": edge.edge_id,
                        "observedResourceUid": edge.observed_resource_uid,
                        "type": edge.relationship_type,
                        "source": edge.source_ref,
                        "target": edge.target_ref,
                        "attributes": dict(edge.attributes),
                    }
                    for edge in result.edges
                ],
                "page": cls._page_payload(result.page),
            },
        }

    @classmethod
    def _timeline_payload(cls, result: ResourceTimelineResult) -> Dict[str, Any]:
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "ResourceTimeline",
            "metadata": {
                "tenantId": result.tenant_id,
                "resourceUid": result.resource_uid,
            },
            "spec": {
                "items": [
                    {
                        "offset": item.offset,
                        "recordedAt": item.recorded_at,
                        "disposition": item.disposition.value,
                        "observationHash": item.observation_hash,
                        "resource": item.resource.to_dict(),
                    }
                    for item in result.items
                ],
                "page": cls._page_payload(result.page),
            },
        }

    def log_message(self, format: str, *args: object) -> None:
        """Keep the reference surface quiet; production uses structured telemetry."""

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("content-length", "0"))
        if length <= 0 or length > 1_048_576:
            raise ValueError("invalid content length")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")
        return payload

    def _read_binary(self, maximum: int) -> bytes:
        if self.headers.get("transfer-encoding") is not None:
            raise InvalidOtlpMetricsRequestError("otlp.content-length.invalid")
        get_all = getattr(self.headers, "get_all", None)
        if callable(get_all):
            values = get_all("content-length") or []
        else:
            value = self.headers.get("content-length")
            values = [value] if value is not None else []
        if len(values) != 1 or not isinstance(values[0], str):
            raise InvalidOtlpMetricsRequestError("otlp.content-length.invalid")
        try:
            length = int(values[0])
        except (TypeError, ValueError):
            raise InvalidOtlpMetricsRequestError("otlp.content-length.invalid") from None
        if length < 0:
            raise InvalidOtlpMetricsRequestError("otlp.content-length.invalid")
        if length > maximum:
            raise OtlpPayloadTooLargeError("otlp.request.too-large")
        payload = self.rfile.read(length)
        if len(payload) != length:
            raise InvalidOtlpMetricsRequestError("otlp.content-length.invalid")
        return payload

    def _otlp_failure(self, status: HTTPStatus, code: str) -> None:
        safe_codes = {
            "otlp.receiver.disabled",
            "otlp.authentication.required",
            "otlp.authentication.invalid",
            "otlp.content-type.unsupported",
            "otlp.content-length.invalid",
            "otlp.compression.invalid",
            "otlp.compression.unsupported",
            "otlp.protobuf.invalid",
            "otlp.metric.not-allowlisted",
            "otlp.metric.invalid",
            "otlp.metric.kind.unsupported",
            "otlp.temporality.unsupported",
            "otlp.attributes.dropped",
            "otlp.attribute.limit",
            "otlp.attribute.invalid",
            "otlp.attribute.duplicate",
            "otlp.attribute.ambiguous",
            "otlp.attribute.type.unsupported",
            "otlp.data-point.invalid",
            "otlp.data-point.unsupported",
            "otlp.data-point.time.invalid",
            "otlp.data-point.limit",
            "otlp.data-point.order.invalid",
            "otlp.series.limit",
            "otlp.series.duplicate",
            "otlp.series.order.invalid",
            "otlp.service.not-allowlisted",
            "otlp.log-record.invalid",
            "otlp.log-record.unsupported",
            "otlp.log-record.time.invalid",
            "otlp.log-record.limit",
            "otlp.log-record.order.invalid",
            "otlp.log-body.invalid",
            "otlp.log-body.type.unsupported",
            "otlp.log-severity.invalid",
            "otlp.trace-context.invalid",
            "otlp.span.kind.unsupported",
            "otlp.span.content-prohibited",
            "otlp.span.not-allowlisted",
            "otlp.span.status.invalid",
            "otlp.span.time.invalid",
            "otlp.span.limit",
            "otlp.span.duplicate",
            "otlp.scope.not-allowlisted",
            "otlp.usage.missing",
            "otlp.usage.breakdown.invalid",
            "otlp.usage.completeness.invalid",
            "otlp.usage.conflict",
            "otlp.attribute.required",
            "otlp.artifact.too-large",
            "otlp.clock.invalid",
            "otlp.request.invalid",
            "otlp.request.too-large",
            "otlp.receiver.unavailable",
            "policy.denied",
        }
        message = code if code in safe_codes else "otlp.request.invalid"
        self._otlp_response(status, Status(message=message).SerializeToString())

    def _otlp_response(self, status: HTTPStatus, body: bytes) -> None:
        try:
            self.send_response(status.value)
            self.send_header("content-type", "application/x-protobuf")
            self._security_headers()
            if status == HTTPStatus.UNAUTHORIZED:
                self.send_header("WWW-Authenticate", "Bearer")
            if status == HTTPStatus.SERVICE_UNAVAILABLE:
                self.send_header("Retry-After", "1")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            self._finish_otlp_receiver_telemetry(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                uncaught_failure=True,
            )
            raise
        self._finish_otlp_receiver_telemetry(status)

    def _json(self, status: HTTPStatus, payload: Dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        try:
            self.send_response(status.value)
            self.send_header("content-type", "application/json")
            self._security_headers()
            self.send_header("cache-control", "no-store")
            if status == HTTPStatus.UNAUTHORIZED:
                self.send_header("WWW-Authenticate", "Bearer")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            self._finish_query_availability(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                uncaught_failure=True,
            )
            raise
        self._finish_query_availability(status)

    @staticmethod
    def _query_operation(path: str) -> Optional[str]:
        exact = {
            "/v1/authentication/console": "console-authentication",
            "/v1/session": "session",
            "/v1/system/version": "runtime-version",
            "/v1/resources": "resources-list",
            "/v1/telemetry/ingestion": "ingestion-freshness",
            "/v1/operations/telemetry/export-health": "telemetry-export-health",
            "/v1/operations/telemetry/deployment-export-health": (
                "telemetry-deployment-export-health"
            ),
            "/v1/operations/telemetry/export-slo": "telemetry-export-slo",
            "/v1/operations/telemetry/export-burn-rate": (
                "telemetry-export-burn-rate"
            ),
            "/v1/operations/telemetry/collector-queue-loss": (
                "collector-queue-loss"
            ),
            "/v1/operations/events/delivery-health": "event-delivery-health",
            "/v1/operations/events/delivery-slo": "event-delivery-slo",
            "/v1/operations/investigations/completion-slo": (
                "investigation-completion-slo"
            ),
            "/v1/operations/evidence/retention": "evidence-retention",
            "/v1/ai/economics/allocation": "ai-allocation-report",
            "/v1/actions": "actions-list",
        }
        operation = exact.get(path)
        if operation is not None:
            return operation
        segments = path.strip("/").split("/")
        if (
            len(segments) == 4
            and segments[:2] == ["v1", "resources"]
            and segments[3] in ("neighborhood", "timeline")
        ):
            return f"resource-{segments[3]}"
        if len(segments) == 3 and segments[:2] == ["v1", "evidence"]:
            return "evidence-get"
        if len(segments) == 3 and segments[:2] == ["v1", "investigations"]:
            return "investigation-get"
        if (
            len(segments) == 4
            and segments[:2] == ["v1", "investigations"]
            and segments[3] == "status"
        ):
            return "investigation-status"
        if len(segments) == 3 and segments[:2] == ["v1", "investigation-jobs"]:
            return "investigation-job-get"
        if len(segments) == 3 and segments[:2] == ["v1", "actions"]:
            return "action-get"
        if (
            len(segments) == 4
            and segments[:2] == ["v1", "actions"]
            and segments[3] == "workflow"
        ):
            return "action-workflow-get"
        if len(segments) == 3 and segments[:2] == ["v1", "plugin-sessions"]:
            return "plugin-session-get"
        if (
            len(segments) == 4
            and segments[:2] == ["v1", "plugin-invocations"]
            and segments[3] == "status"
        ):
            return "plugin-invocation-status"
        return None

    def _finish_query_availability(
        self,
        status: HTTPStatus,
        *,
        uncaught_failure: bool = False,
    ) -> None:
        context = getattr(self, "_query_availability_context", None)
        if context is None:
            return
        self._query_availability_context = None
        operation, started_at = context
        try:
            self.runtime.query_availability.record(
                RecordQueryAvailabilityCommand(
                    operation=operation,
                    status_code=int(status),
                    duration_seconds=max(0.0, time.monotonic() - started_at),
                    uncaught_failure=uncaught_failure,
                )
            )
        except Exception:
            # Optional telemetry never changes an HTTP query result.
            return

    def _finish_otlp_receiver_telemetry(
        self,
        status: HTTPStatus,
        *,
        uncaught_failure: bool = False,
    ) -> None:
        context = getattr(self, "_otlp_receiver_context", None)
        if context is None:
            return
        self._otlp_receiver_context = None
        signal, started_at = context
        try:
            self.runtime.otlp_receiver_telemetry.record(
                RecordOtlpReceiverCommand(
                    signal=signal,
                    status_code=int(status),
                    duration_seconds=max(0.0, time.monotonic() - started_at),
                    uncaught_failure=uncaught_failure,
                )
            )
        except Exception:
            # Optional telemetry never changes an OTLP intake result.
            return

    def _console_asset(self, path: str) -> None:
        filename, content_type = self._console_assets[path]
        try:
            body = files("iip.surfaces").joinpath("static", filename).read_bytes()
        except (FileNotFoundError, OSError):
            self._json(
                HTTPStatus.NOT_FOUND,
                {"error": {"code": "route.not_found"}},
            )
            return
        self.send_response(HTTPStatus.OK.value)
        self.send_header("content-type", content_type)
        runtime = getattr(self, "runtime", None)
        self._security_headers(
            console_connect_origin=getattr(runtime, "console_token_origin", None)
        )
        self.send_header("cache-control", "no-store" if filename == "index.html" else "no-cache")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _security_headers(
        self, *, console_connect_origin: str | None = None
    ) -> None:
        self.send_header("x-content-type-options", "nosniff")
        self.send_header("referrer-policy", "no-referrer")
        self.send_header("x-frame-options", "DENY")
        self.send_header("cross-origin-opener-policy", "same-origin")
        self.send_header(
            "permissions-policy",
            "camera=(), microphone=(), geolocation=()",
        )
        connect_sources = "'self'"
        if console_connect_origin is not None:
            connect_sources += f" {console_connect_origin}"
        self.send_header(
            "content-security-policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            f"img-src 'self' data:; connect-src {connect_sources}; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'",
        )


def main() -> None:
    """Start the local reference API."""

    host = os.environ.get("IIP_HTTP_HOST", "0.0.0.0")
    port = int(os.environ.get("IIP_HTTP_PORT", "8080"))
    runtime = build_runtime_from_env()
    try:
        ApiHandler.runtime = runtime
        server = ThreadingHTTPServer((host, port), ApiHandler)
        print(f"IIP reference API listening on http://{host}:{port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("IIP reference API stopped")
        finally:
            server.server_close()
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
