"""Dependency-free synchronous HTTP client."""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Mapping, Optional
from urllib.error import HTTPError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from .errors import ApiError
from .models import (
    ActionApproval,
    ActionExecutionStatus,
    ActionProposal,
    ActionResult,
    ActionWorkflow,
    ActionWorkflowPage,
    AiAllocationReport,
    CollectorQueueLossReport,
    ConsoleAuthenticationConfiguration,
    ContextEvidenceRequest,
    Evidence,
    EvidenceRetentionReport,
    EventDeliveryReplayCommand,
    EventDeliveryHealthReport,
    EventDeliverySloReport,
    IngestionFreshnessReport,
    InvestigationCompletionSloReport,
    InvestigationReport,
    InvestigationJobStatus,
    InvestigationRequest,
    InvestigationCancellationRequest,
    InvestigationStatus,
    KubernetesEventEvidenceRequest,
    LogEvidenceRequest,
    PluginSession,
    PluginInvocationCancellationRequest,
    PluginInvocationReconciliationRequest,
    PluginInvocationStatus,
    ResourceCollectionRequest,
    ResourceCollectionResult,
    ResourceChangeEvidenceRequest,
    ResourceNeighborhood,
    ResourceObservation,
    ResourceTimeline,
    RuntimeVersionReport,
    SessionContext,
    TelemetryEvidenceRequest,
    TelemetryDeploymentExportHealthReport,
    TelemetryExportHealthReport,
    TelemetryExportSloReport,
    TelemetryExportBurnRateReport,
)


def discover_console_authentication(
    base_url: str,
    timeout_seconds: float = 10.0,
) -> ConsoleAuthenticationConfiguration:
    """Read the public browser authentication profile without a credential."""

    if not isinstance(base_url, str) or not base_url:
        raise ValueError("base_url must be a non-empty string")
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
    ):
        raise ValueError("timeout_seconds must be positive")
    request = Request(
        f"{base_url.rstrip('/')}/v1/authentication/console",
        headers={"accept": "application/json"},
        method="GET",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            body = response.read(65_537)
            if len(body) > 65_536:
                raise ApiError(200, "response.invalid")
            payload = json.loads(body.decode("utf-8"))
    except HTTPError as exc:
        try:
            body = json.loads(exc.read(65_537).decode("utf-8"))
            code = str(body.get("error", {}).get("code", "request.failed"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            code = "request.failed"
        raise ApiError(exc.code, code) from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ApiError(200, "response.invalid") from exc
    if not isinstance(payload, dict):
        raise ApiError(200, "response.invalid")
    return ConsoleAuthenticationConfiguration.from_dict(payload)


class Client:
    """Bearer-authenticated control-plane client."""

    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        if not isinstance(bearer_token, str) or not bearer_token:
            raise ValueError("bearer_token must be a non-empty string")
        self._bearer_token = bearer_token
        self._timeout = timeout_seconds

    def ingest_resource(
        self,
        resource: ResourceObservation,
        correlation_id: Optional[str] = None,
    ) -> ResourceObservation:
        """Submit one resource observation."""

        headers = self._headers(correlation_id)
        request = Request(
            f"{self._base_url}/v1/resources",
            data=json.dumps(resource.to_dict()).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        payload = self._send(request)
        return ResourceObservation.from_dict(payload)

    def list_resources(self) -> list[ResourceObservation]:
        """List resources within this client's tenant scope."""

        request = Request(
            f"{self._base_url}/v1/resources",
            headers=self._headers(None),
            method="GET",
        )
        payload = self._send(request)
        items = payload.get("items", [])
        if not isinstance(items, list):
            raise ApiError(200, "response.invalid")
        return [ResourceObservation.from_dict(item) for item in items]

    def get_session(self) -> SessionContext:
        """Return the actor, tenant, and roles derived from this credential."""

        return SessionContext.from_dict(self._get("/v1/session"))

    def get_runtime_version(self) -> RuntimeVersionReport:
        """Return verified non-secret identity for the answering API process."""

        return RuntimeVersionReport.from_dict(self._get("/v1/system/version"))

    def get_resource_neighborhood(
        self,
        resource_uid: str,
        *,
        direction: str = "both",
        relationship_types: Iterable[str] = (),
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> ResourceNeighborhood:
        """Read one tenant-scoped page of current graph relationships."""

        query: list[tuple[str, str]] = [
            ("depth", "1"),
            ("direction", direction),
            ("limit", str(limit)),
        ]
        query.extend(("relationshipType", value) for value in relationship_types)
        if cursor is not None:
            query.append(("cursor", cursor))
        request = Request(
            f"{self._base_url}/v1/resources/{quote(resource_uid, safe='')}/neighborhood?{urlencode(query)}",
            headers=self._headers(None),
            method="GET",
        )
        return ResourceNeighborhood.from_dict(self._send(request))

    def get_resource_timeline(
        self,
        resource_uid: str,
        *,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> ResourceTimeline:
        """Read one tenant-scoped page of immutable resource observations."""

        query = [("limit", str(limit))]
        if cursor is not None:
            query.append(("cursor", cursor))
        request = Request(
            f"{self._base_url}/v1/resources/{quote(resource_uid, safe='')}/timeline?{urlencode(query)}",
            headers=self._headers(None),
            method="GET",
        )
        return ResourceTimeline.from_dict(self._send(request))

    def get_ingestion_freshness(self, source_id: str) -> IngestionFreshnessReport:
        """Evaluate current freshness and delivery health for one source."""

        query = urlencode({"sourceId": source_id})
        return IngestionFreshnessReport.from_dict(
            self._get(f"/v1/telemetry/ingestion?{query}")
        )

    def get_telemetry_export_health(self) -> TelemetryExportHealthReport:
        """Read process-local delivery outcomes as a platform administrator."""

        return TelemetryExportHealthReport.from_dict(
            self._get("/v1/operations/telemetry/export-health")
        )

    def get_telemetry_deployment_export_health(
        self,
    ) -> TelemetryDeploymentExportHealthReport:
        """Read recent API and worker delivery state as an administrator."""

        return TelemetryDeploymentExportHealthReport.from_dict(
            self._get("/v1/operations/telemetry/deployment-export-health")
        )

    def get_telemetry_export_slo(self) -> TelemetryExportSloReport:
        """Read rolling deployment export reliability as an administrator."""

        return TelemetryExportSloReport.from_dict(
            self._get("/v1/operations/telemetry/export-slo")
        )

    def get_telemetry_export_burn_rate(self) -> TelemetryExportBurnRateReport:
        """Read multi-window deployment export burn rate as an administrator."""

        return TelemetryExportBurnRateReport.from_dict(
            self._get("/v1/operations/telemetry/export-burn-rate")
        )

    def get_collector_queue_loss(self) -> CollectorQueueLossReport:
        """Read the Collector-observed queue/loss objective as an administrator.

        Distinct from get_telemetry_export_burn_rate, which measures IIP's
        own outbound exporter attempts rather than the customer Collector's
        internal queue for its pipeline to IIP.
        """

        return CollectorQueueLossReport.from_dict(
            self._get("/v1/operations/telemetry/collector-queue-loss")
        )

    def get_event_delivery_health(
        self, *, limit: int = 50
    ) -> EventDeliveryHealthReport:
        """Read tenant outbox backlog and bounded quarantine as an administrator."""

        query = urlencode({"limit": str(limit)})
        return EventDeliveryHealthReport.from_dict(
            self._get(f"/v1/operations/events/delivery-health?{query}")
        )

    def get_event_delivery_slo(self) -> EventDeliverySloReport:
        """Read the configured rolling publication objective as an administrator."""

        return EventDeliverySloReport.from_dict(
            self._get("/v1/operations/events/delivery-slo")
        )

    def get_investigation_completion_slo(
        self,
    ) -> InvestigationCompletionSloReport:
        """Read the rolling useful-completion objective as an administrator."""

        return InvestigationCompletionSloReport.from_dict(
            self._get("/v1/operations/investigations/completion-slo")
        )

    def get_evidence_retention(self) -> EvidenceRetentionReport:
        """Observe tenant Evidence artifact retention as an administrator."""

        return EvidenceRetentionReport.from_dict(
            self._get("/v1/operations/evidence/retention")
        )

    def ingest_resource_collection(
        self,
        request: ResourceCollectionRequest,
        result: ResourceCollectionResult,
        correlation_id: Optional[str] = None,
    ) -> list[ResourceObservation]:
        """Ingest a pair and return observations plus host-generated tombstones."""

        payload = self._post(
            "/v1/collections/ingest",
            {"request": request.to_dict(), "result": result.to_dict()},
            correlation_id,
        )
        items = payload.get("items")
        if not isinstance(items, list):
            raise ApiError(200, "response.invalid")
        return [ResourceObservation.from_dict(item) for item in items]

    def run_investigation(
        self, request: InvestigationRequest
    ) -> InvestigationReport:
        """Run the bounded investigation and return its terminal report."""

        return InvestigationReport.from_dict(
            self._post("/v1/investigations", request.to_dict())
        )

    def submit_investigation(
        self, request: InvestigationRequest
    ) -> InvestigationJobStatus:
        """Durably queue an investigation and return immediately."""

        return InvestigationJobStatus.from_dict(
            self._post("/v1/investigation-jobs", request.to_dict())
        )

    def get_investigation_job(
        self, investigation_id: str
    ) -> InvestigationJobStatus:
        return InvestigationJobStatus.from_dict(
            self._get(
                f"/v1/investigation-jobs/{quote(investigation_id, safe='')}"
            )
        )

    def cancel_investigation_job(
        self, request: InvestigationCancellationRequest
    ) -> InvestigationJobStatus:
        investigation_id = request.to_dict().get("spec", {}).get("investigationId")
        if not isinstance(investigation_id, str):
            raise ValueError("investigation cancellation request is invalid")
        return InvestigationJobStatus.from_dict(
            self._post(
                f"/v1/investigation-jobs/{quote(investigation_id, safe='')}/cancel",
                request.to_dict(),
            )
        )

    def get_investigation(self, investigation_id: str) -> InvestigationReport:
        return InvestigationReport.from_dict(
            self._get(f"/v1/investigations/{quote(investigation_id, safe='')}")
        )

    def get_investigation_status(self, investigation_id: str) -> InvestigationStatus:
        """Read durable running, cancellation, or terminal state."""

        return InvestigationStatus.from_dict(
            self._get(
                f"/v1/investigations/{quote(investigation_id, safe='')}/status"
            )
        )

    def cancel_investigation(
        self, request: InvestigationCancellationRequest
    ) -> InvestigationStatus:
        """Request cooperative cancellation without deleting audit history."""

        investigation_id = request.to_dict().get("spec", {}).get("investigationId")
        if not isinstance(investigation_id, str):
            raise ValueError("investigation cancellation request id is invalid")
        return InvestigationStatus.from_dict(
            self._post(
                f"/v1/investigations/{quote(investigation_id, safe='')}/cancel",
                request.to_dict(),
            )
        )

    def get_evidence(self, evidence_id: str) -> Evidence:
        return Evidence.from_dict(
            self._get(f"/v1/evidence/{quote(evidence_id, safe='')}")
        )

    def collect_telemetry_evidence(
        self, request: TelemetryEvidenceRequest
    ) -> Evidence:
        """Collect normalized metric evidence through the configured backend."""

        return Evidence.from_dict(
            self._post("/v1/evidence/telemetry/queries", request.to_dict())
        )

    def collect_log_evidence(self, request: LogEvidenceRequest) -> Evidence:
        """Collect normalized log evidence through the configured backend."""

        return Evidence.from_dict(
            self._post("/v1/evidence/logs/queries", request.to_dict())
        )

    def collect_kubernetes_event_evidence(
        self, request: KubernetesEventEvidenceRequest
    ) -> Evidence:
        """Collect normalized Kubernetes Event evidence through its adapter."""

        return Evidence.from_dict(
            self._post(
                "/v1/evidence/kubernetes/events/queries",
                request.to_dict(),
            )
        )

    def collect_resource_change_evidence(
        self, request: ResourceChangeEvidenceRequest
    ) -> Evidence:
        """Collect normalized changes from immutable resource history."""

        return Evidence.from_dict(
            self._post("/v1/evidence/changes/queries", request.to_dict())
        )

    def collect_context_evidence(self, request: ContextEvidenceRequest) -> Evidence:
        """Collect redacted allowlisted repository and runbook context."""

        return Evidence.from_dict(
            self._post("/v1/evidence/context/queries", request.to_dict())
        )

    def propose_action(self, command: Mapping[str, Any]) -> ActionProposal:
        return ActionProposal.from_dict(self._post("/v1/actions/proposals", command))

    def propose_event_delivery_replay(
        self,
        command: EventDeliveryReplayCommand,
    ) -> ActionProposal:
        """Propose a dry-run or live replay of one exact quarantine generation."""

        return self.propose_action(command.to_dict())

    def decide_action(
        self, proposal_id: str, decision: str, rationale: str
    ) -> ActionApproval:
        return ActionApproval.from_dict(
            self._post(
                f"/v1/actions/{quote(proposal_id, safe='')}/decision",
                {"decision": decision, "rationale": rationale},
            )
        )

    def execute_action(self, proposal_id: str) -> ActionResult:
        return ActionResult.from_dict(
            self._post(f"/v1/actions/{quote(proposal_id, safe='')}/execute", {})
        )

    def get_action(
        self, proposal_id: str
    ) -> ActionProposal | ActionExecutionStatus | ActionResult:
        payload = self._get(f"/v1/actions/{quote(proposal_id, safe='')}")
        kind = payload.get("kind")
        if kind == "ActionProposal":
            return ActionProposal.from_dict(payload)
        if kind == "ActionExecutionStatus":
            return ActionExecutionStatus.from_dict(payload)
        if kind == "ActionResult":
            return ActionResult.from_dict(payload)
        raise ValueError("action response kind is invalid")

    def get_action_workflow(self, proposal_id: str) -> ActionWorkflow:
        """Get the consistent proposal, approval, execution, and result view."""

        return ActionWorkflow.from_dict(
            self._get(f"/v1/actions/{quote(proposal_id, safe='')}/workflow")
        )

    def list_action_workflows(
        self, *, limit: int = 50, cursor: Optional[str] = None
    ) -> ActionWorkflowPage:
        """List newest governed actions in this client's tenant scope."""

        query = {"limit": str(limit)}
        if cursor is not None:
            query["cursor"] = cursor
        return ActionWorkflowPage.from_dict(
            self._get(f"/v1/actions?{urlencode(query)}")
        )

    def get_ai_allocation_report(
        self,
        *,
        start: str,
        end: str,
        group_by: str,
    ) -> AiAllocationReport:
        """Read one generation-bound application or team allocation report."""

        query = urlencode({"start": start, "end": end, "groupBy": group_by})
        return AiAllocationReport.from_dict(
            self._get(f"/v1/ai/economics/allocation?{query}")
        )

    def open_plugin_session(self, command: Mapping[str, Any]) -> PluginSession:
        return PluginSession.from_dict(self._post("/v1/plugin-sessions", command))

    def get_plugin_invocation_status(
        self, invocation_id: str
    ) -> PluginInvocationStatus:
        return PluginInvocationStatus.from_dict(
            self._get(
                f"/v1/plugin-invocations/{quote(invocation_id, safe='')}/status"
            )
        )

    def cancel_plugin_invocation(
        self, request: PluginInvocationCancellationRequest
    ) -> PluginInvocationStatus:
        payload = request.to_dict()
        spec = payload.get("spec")
        if not isinstance(spec, Mapping) or not isinstance(
            spec.get("invocationId"), str
        ):
            raise ValueError("plugin invocation cancellation request is invalid")
        invocation_id = str(spec["invocationId"])
        return PluginInvocationStatus.from_dict(
            self._post(
                f"/v1/plugin-invocations/{quote(invocation_id, safe='')}/cancel",
                payload,
            )
        )

    def reconcile_plugin_invocation(
        self, request: PluginInvocationReconciliationRequest
    ) -> PluginInvocationStatus:
        payload = request.to_dict()
        spec = payload.get("spec")
        if not isinstance(spec, Mapping) or not isinstance(
            spec.get("invocationId"), str
        ):
            raise ValueError("plugin invocation reconciliation request is invalid")
        invocation_id = str(spec["invocationId"])
        return PluginInvocationStatus.from_dict(
            self._post(
                f"/v1/plugin-invocations/{quote(invocation_id, safe='')}/reconcile",
                payload,
            )
        )

    def _get(self, path: str) -> Dict[str, Any]:
        return self._send(
            Request(
                f"{self._base_url}{path}",
                headers=self._headers(None),
                method="GET",
            )
        )

    def _post(
        self,
        path: str,
        payload: Mapping[str, Any],
        correlation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self._send(
            Request(
                f"{self._base_url}{path}",
                data=json.dumps(payload).encode("utf-8"),
                headers=self._headers(correlation_id),
                method="POST",
            )
        )

    def _headers(self, correlation_id: Optional[str]) -> Dict[str, str]:
        headers = {
            "content-type": "application/json",
            "authorization": f"Bearer {self._bearer_token}",
        }
        if correlation_id:
            headers["x-correlation-id"] = correlation_id
        return headers

    def _send(self, request: Request) -> Dict[str, Any]:
        try:
            with urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            try:
                body = json.loads(exc.read().decode("utf-8"))
                code = str(body.get("error", {}).get("code", "request.failed"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                code = "request.failed"
            raise ApiError(exc.code, code) from exc
        if not isinstance(payload, dict):
            raise ApiError(200, "response.invalid")
        return payload
