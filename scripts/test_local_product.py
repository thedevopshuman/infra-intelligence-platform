#!/usr/bin/env python3
"""Exercise the customer workflow against the running durable local stack."""

from __future__ import annotations

import hashlib
import json
import secrets
import sys
import time
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CREDENTIALS_PATH = ROOT / ".iip" / "local-credentials.json"
BASE_URL = "http://127.0.0.1:8080"
APPLICATION_VERSION = tomllib.loads(
    (ROOT / "pyproject.toml").read_text(encoding="utf-8")
)["project"]["version"]
REQUIRED_MIGRATION = sorted(
    (ROOT / "src/iip/adapters/postgres/migrations").glob("*.sql")
)[-1].name


class ProductWorkflowError(RuntimeError):
    """A stable local end-to-end gate failure."""


def _credentials() -> dict[str, str]:
    try:
        document = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
        identities = document["identities"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ProductWorkflowError(
            "local credentials are unavailable; run make dev-up first"
        ) from exc
    tokens = {
        str(identity.get("actorId")): str(identity.get("bearerToken"))
        for identity in identities
        if isinstance(identity, Mapping)
    }
    required = {"local-operator", "local-approver", "local-executor"}
    if set(tokens).intersection(required) != required or any(
        not tokens[actor] for actor in required
    ):
        raise ProductWorkflowError("local workflow identities are invalid")
    return tokens


def _request(
    path: str,
    token: str | None,
    *,
    method: str = "GET",
    body: Mapping[str, object] | None = None,
) -> Mapping[str, object]:
    encoded = None
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        encoded = json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(BASE_URL + path, data=encoded, headers=headers, method=method)
    try:
        with urlopen(request, timeout=15) as response:
            result = json.load(response)
    except HTTPError as exc:
        try:
            response = json.load(exc)
            code = response.get("error", {}).get("code", "http.error")
        except (TypeError, ValueError, json.JSONDecodeError):
            code = "http.error"
        raise ProductWorkflowError(f"{method} {path} failed: {code}") from None
    except (OSError, URLError) as exc:
        raise ProductWorkflowError(f"{method} {path} could not reach the API") from exc
    if not isinstance(result, Mapping):
        raise ProductWorkflowError(f"{method} {path} returned an invalid document")
    return result


def _identifier(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(16)}"


def _wait_for_investigation(
    investigation_id: str, token: str, timeout_seconds: float = 30.0
) -> Mapping[str, object]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        status = _request(f"/v1/investigation-jobs/{investigation_id}", token)
        spec = status.get("spec")
        if not isinstance(spec, Mapping):
            raise ProductWorkflowError("investigation job returned invalid state")
        state = spec.get("state")
        if state in {"completed", "failed", "cancelled"}:
            if not spec.get("reportRef"):
                raise ProductWorkflowError(
                    f"investigation job ended without a report: {state}"
                )
            return _request(f"/v1/investigations/{investigation_id}", token)
        time.sleep(0.2)
    raise ProductWorkflowError("investigation job did not complete before timeout")


def _wait_for_event_delivery(token: str, timeout_seconds: float = 15.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        report = _request(
            "/v1/telemetry/ingestion?sourceId=kubernetes-local",
            token,
        )
        spec = report.get("spec")
        delivery = spec.get("delivery") if isinstance(spec, Mapping) else None
        if isinstance(delivery, Mapping) and delivery.get("pendingEvents") == 0:
            return
        time.sleep(0.2)
    raise ProductWorkflowError("transactional outbox did not drain before timeout")


def main() -> int:
    try:
        console_authentication = _request(
            "/v1/authentication/console",
            None,
        )
        if console_authentication != {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "ConsoleAuthenticationConfiguration",
            "spec": {"mode": "local-token"},
        }:
            raise ProductWorkflowError(
                "local console authentication discovery is invalid"
            )
        tokens = _credentials()
        operator = tokens["local-operator"]
        approver = tokens["local-approver"]
        executor = tokens["local-executor"]
        runtime_version = _request("/v1/system/version", operator)
        runtime_spec = runtime_version.get("spec")
        if (
            runtime_version.get("kind") != "RuntimeVersionReport"
            or runtime_version.get("metadata", {}).get("tenantId") != "local"
            or not isinstance(runtime_spec, Mapping)
            or runtime_spec.get("application", {}).get("version")
            != APPLICATION_VERSION
            or runtime_spec.get("contracts", {}).get("apiVersion")
            != "iip.platform/v1alpha1"
            or runtime_spec.get("storage", {}).get("requiredMigration")
            != REQUIRED_MIGRATION
            or runtime_spec.get("build") != {"mode": "development"}
            or runtime_spec.get("deployment") != {}
        ):
            raise ProductWorkflowError("local runtime identity is invalid")

        telemetry_health = _request(
            "/v1/operations/telemetry/deployment-export-health", operator
        )
        telemetry_spec = telemetry_health.get("spec")
        telemetry_summary = (
            telemetry_spec.get("summary")
            if isinstance(telemetry_spec, Mapping)
            else None
        )
        if (
            telemetry_health.get("kind")
            != "TelemetryDeploymentExportHealthReport"
            or "tenantId" in telemetry_health.get("metadata", {})
            or not isinstance(telemetry_spec, Mapping)
            or telemetry_spec.get("status")
            not in ("disabled", "awaiting-first-attempt", "healthy", "degraded")
            or not isinstance(telemetry_summary, Mapping)
            or not isinstance(telemetry_spec.get("instances"), list)
        ):
            raise ProductWorkflowError(
                "local deployment telemetry health is invalid"
            )
        retention = _request("/v1/operations/evidence/retention", operator)
        retention_spec = retention.get("spec")
        retention_artifacts = (
            retention_spec.get("artifacts")
            if isinstance(retention_spec, Mapping)
            else None
        )
        if (
            retention.get("kind") != "EvidenceRetentionReport"
            or retention.get("metadata", {}).get("tenantId") != "local"
            or not isinstance(retention_spec, Mapping)
            or retention_spec.get("mode") != "observe"
            or retention_spec.get("status")
            not in {"disabled", "current", "cleanup-required"}
            or not isinstance(retention_artifacts, Mapping)
            or retention_artifacts.get("expired") != 0
        ):
            raise ProductWorkflowError("local evidence retention report is invalid")
        now = datetime.now(timezone.utc)
        suffix = secrets.token_hex(6)
        workload_name = f"product-gate-{suffix}"
        sequence = int(now.timestamp() * 1000)
        stream_id = "obs_0f4e8c2a6b1d4975a3c9e7f102d468ab"
        request_id = _identifier("col")
        scope = {
            "provider": "kubernetes",
            "integrationId": "kubernetes-local",
            "rootExternalId": "cluster-local",
            "parameters": {"namespaces": ["default"]},
        }
        scope_digest = "sha256:" + hashlib.sha256(
            json.dumps(scope, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).hexdigest()
        resource_document = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "Resource",
            "metadata": {
                "tenantId": "local",
                "observedAt": now.isoformat().replace("+00:00", "Z"),
                "observation": {
                    "sourceId": "kubernetes-local",
                    "streamId": stream_id,
                    "sequence": sequence,
                    "mode": "incremental",
                    "resourceVersion": str(sequence),
                },
                "labels": {"environment": "local-product-gate"},
            },
            "spec": {
                "provider": "kubernetes",
                "type": "apps/deployment",
                "externalId": f"cluster-local/default/{workload_name}",
                "displayName": workload_name,
                "attributes": {
                    "namespace": "default",
                    "providerUid": secrets.token_hex(16),
                    "replicas": 2,
                    "availableReplicas": 1,
                },
                "relationships": [],
            },
            "status": {"health": "degraded", "lifecycle": "active"},
        }
        collection = _request(
            "/v1/collections/ingest",
            operator,
            method="POST",
            body={
                "request": {
                    "apiVersion": "iip.platform/v1alpha1",
                    "kind": "ResourceCollectionRequest",
                    "metadata": {
                        "requestId": request_id,
                        "tenantId": "local",
                        "actorId": "local-operator",
                        "requestedAt": now.isoformat().replace("+00:00", "Z"),
                    },
                    "spec": {
                        "sourceId": "kubernetes-local",
                        "streamId": stream_id,
                        "mode": "incremental",
                        "startSequence": sequence,
                        "scope": scope,
                        "limits": {
                            "maxResources": 10,
                            "maxOutputBytes": 1048576,
                        },
                        "deadline": (now + timedelta(minutes=5))
                        .isoformat()
                        .replace("+00:00", "Z"),
                    },
                },
                "result": {
                    "apiVersion": "iip.platform/v1alpha1",
                    "kind": "ResourceCollectionResult",
                    "metadata": {
                        "requestId": request_id,
                        "tenantId": "local",
                        "sourceId": "kubernetes-local",
                        "createdAt": now.isoformat().replace("+00:00", "Z"),
                    },
                    "spec": {
                        "observations": [resource_document],
                        "completion": {
                            "status": "complete",
                            "resourceCount": 1,
                            "nextSequence": sequence + 1,
                            "checkpoint": f"local-product-gate:{sequence}",
                            "scopeDigest": scope_digest,
                        },
                    },
                },
            },
        )
        items = collection.get("items")
        if not isinstance(items, list) or len(items) != 1:
            raise ProductWorkflowError("collection ingestion returned invalid resources")
        resource = items[0]
        if not isinstance(resource, Mapping):
            raise ProductWorkflowError("collection ingestion returned invalid resource")
        resource_uid = str(resource.get("metadata", {}).get("uid", ""))
        if not resource_uid.startswith("res_"):
            raise ProductWorkflowError("resource ingestion did not return a canonical UID")

        investigation_id = _identifier("inv")
        question = f"Why is {workload_name} degraded?"
        job = _request(
            "/v1/investigation-jobs",
            operator,
            method="POST",
            body={
                "apiVersion": "iip.platform/v1alpha1",
                "kind": "InvestigationRequest",
                "metadata": {
                    "id": investigation_id,
                    "tenantId": "local",
                    "actorId": "local-operator",
                    "requestedAt": now.isoformat().replace("+00:00", "Z"),
                    "correlationId": f"local-product-gate-{suffix}",
                },
                "spec": {
                    "question": question,
                    "trigger": {
                        "type": "user",
                        "source": "urn:iip:local-product-gate",
                        "summary": question,
                    },
                    "scope": {
                        "resourceUids": [resource_uid],
                        "timeRange": {
                            "start": (now - timedelta(hours=1))
                            .isoformat()
                            .replace("+00:00", "Z"),
                            "end": now.isoformat().replace("+00:00", "Z"),
                        },
                    },
                    "agentSelector": {
                        "id": "incident-investigator",
                        "version": "0.1.0",
                    },
                    "evidenceTypes": [
                        "kubernetes.resource-status",
                        "resource.change",
                    ],
                    "allowedTools": ["resources/query", "evidence/fetch"],
                    "budgets": {
                        "maxToolCalls": 8,
                        "maxWallTimeSeconds": 120,
                        "maxModelTokens": 0,
                        "maxCostUsd": 0,
                        "maxEvidenceItems": 16,
                        "maxIterations": 8,
                    },
                    "maxAuthority": "propose",
                    "priority": "normal",
                },
            },
        )
        if job.get("kind") != "InvestigationJobStatus":
            raise ProductWorkflowError("investigation was not durably queued")
        report = _wait_for_investigation(investigation_id, operator)
        if report.get("kind") != "InvestigationReport":
            raise ProductWorkflowError("investigation did not return a terminal report")
        report_spec = report.get("spec")
        signal_plan = (
            report_spec.get("signalPlan")
            if isinstance(report_spec, Mapping)
            else None
        )
        steps = signal_plan.get("steps") if isinstance(signal_plan, Mapping) else None
        if not isinstance(steps, list) or not any(
            isinstance(step, Mapping)
            and step.get("origin") == "protected-catalog"
            and step.get("signal") == "resource.change"
            for step in steps
        ):
            raise ProductWorkflowError(
                "investigation did not use the reviewed tenant signal catalog"
            )

        proposal = _request(
            "/v1/actions/proposals",
            operator,
            method="POST",
            body={
                "investigationId": investigation_id,
                "actionType": "kubernetes.restart-workload",
                "targetResourceUid": resource_uid,
                "parameters": {
                    "namespace": "default",
                    "workloadKind": "deployment",
                    "workloadName": workload_name,
                },
                "idempotencyKey": f"local-product-gate-{suffix}",
                "expiresAt": (now + timedelta(minutes=30))
                .isoformat()
                .replace("+00:00", "Z"),
                "dryRun": True,
            },
        )
        proposal_id = str(proposal.get("metadata", {}).get("id", ""))
        if not proposal_id.startswith("act_"):
            raise ProductWorkflowError("proposal did not return an action ID")

        _request(
            f"/v1/actions/{proposal_id}/decision",
            approver,
            method="POST",
            body={
                "decision": "approved",
                "rationale": "The product gate is scoped, reversible, and non-mutating.",
            },
        )
        result = _request(
            f"/v1/actions/{proposal_id}/execute",
            executor,
            method="POST",
            body={},
        )
        replay = _request(
            f"/v1/actions/{proposal_id}/execute",
            executor,
            method="POST",
            body={},
        )
        workflow = _request(
            f"/v1/actions/{proposal_id}/workflow",
            operator,
        )
        page = _request("/v1/actions?limit=100", operator)
        item_ids = {
            str(item.get("metadata", {}).get("id", ""))
            for item in page.get("spec", {}).get("items", [])
            if isinstance(item, Mapping)
        }
        if result != replay:
            raise ProductWorkflowError("duplicate execution did not return the same result")
        if workflow.get("spec", {}).get("state") != "dry-run":
            raise ProductWorkflowError("workflow did not reach the dry-run terminal state")
        if proposal_id not in item_ids:
            raise ProductWorkflowError("terminal workflow is missing from the action queue")
        _wait_for_event_delivery(operator)
        delivery_health = _request(
            "/v1/operations/events/delivery-health?limit=20", operator
        )
        delivery_spec = delivery_health.get("spec")
        if (
            delivery_health.get("kind") != "EventDeliveryHealthReport"
            or delivery_health.get("metadata", {}).get("tenantId") != "local"
            or not isinstance(delivery_spec, Mapping)
            or delivery_spec.get("status") != "healthy"
            or delivery_spec.get("delivery", {}).get("pendingEvents") != 0
            or delivery_spec.get("delivery", {}).get("quarantinedEvents") != 0
        ):
            raise ProductWorkflowError("local event delivery health is invalid")
        delivery_slo = _request(
            "/v1/operations/events/delivery-slo", operator
        )
        slo_spec = delivery_slo.get("spec")
        measurement = (
            slo_spec.get("measurement") if isinstance(slo_spec, Mapping) else None
        )
        objective = (
            slo_spec.get("objective") if isinstance(slo_spec, Mapping) else None
        )
        if (
            delivery_slo.get("kind") != "EventDeliverySloReport"
            or delivery_slo.get("metadata", {}).get("tenantId") != "local"
            or not isinstance(measurement, Mapping)
            or not isinstance(objective, Mapping)
            or objective.get("maximumDeliveryLatencySeconds") != 60
            or objective.get("minimumAttainmentBasisPoints") != 9900
            or measurement.get("createdEvents", 0) < 1
            or measurement.get("createdEvents")
            != measurement.get("eligibleEvents", 0)
            + measurement.get("immatureEvents", 0)
        ):
            raise ProductWorkflowError("local event delivery SLO is invalid")
        investigation_slo = _request(
            "/v1/operations/investigations/completion-slo", operator
        )
        investigation_slo_spec = investigation_slo.get("spec")
        investigation_measurement = (
            investigation_slo_spec.get("measurement")
            if isinstance(investigation_slo_spec, Mapping)
            else None
        )
        investigation_objective = (
            investigation_slo_spec.get("objective")
            if isinstance(investigation_slo_spec, Mapping)
            else None
        )
        if (
            investigation_slo.get("kind")
            != "InvestigationCompletionSloReport"
            or investigation_slo.get("metadata", {}).get("tenantId") != "local"
            or not isinstance(investigation_measurement, Mapping)
            or not isinstance(investigation_objective, Mapping)
            or investigation_objective.get("maximumCompletionSeconds") != 300
            or investigation_objective.get("minimumAttainmentBasisPoints") != 9900
            or investigation_measurement.get("acceptedJobs", 0) < 1
            or investigation_measurement.get("acceptedJobs")
            != investigation_measurement.get("eligibleJobs", 0)
            + investigation_measurement.get("immatureJobs", 0)
        ):
            raise ProductWorkflowError(
                "local investigation completion SLO is invalid"
            )
        print(
            "local product workflow passed: runtime identity/telemetry delivery → "
            "collection → event delivery health/SLO → queued worker investigation and "
            "completion SLO → proposal → independent approval → one-shot dry-run → queue"
        )
        print(f"action: {proposal_id}")
        return 0
    except ProductWorkflowError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
