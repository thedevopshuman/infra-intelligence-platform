#!/usr/bin/env python3
"""Fast, dependency-free repository contract and architecture checks."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, List, Mapping, Optional


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

REQUIRED_PATHS = (
    "README.md",
    "AGENTS.md",
    "docs/product/constitution.md",
    "docs/architecture/overview.md",
    "docs/architecture/authentication-boundary.md",
    "docs/architecture/authorization-policy.md",
    "docs/architecture/plugin-runtime.md",
    "docs/architecture/evidence-collection-pipeline.md",
    "docs/architecture/ingestion-freshness-telemetry.md",
    "docs/architecture/opentelemetry-portability.md",
    "docs/research/opensre-reference-analysis.md",
    "docs/research/brand/README.md",
    "docs/roadmap/initial-roadmap.md",
    "docs/specifications/policy-contract.md",
    "docs/specifications/plugin-invocation-contract.md",
    "docs/decisions/0005-credential-derived-request-identity.md",
    "docs/decisions/0006-deterministic-investigation-and-dry-run-actions.md",
    "docs/decisions/0007-reconciliation-membership-and-tombstones.md",
    "docs/decisions/0009-provider-cursor-sets-and-watch-recovery.md",
    "docs/decisions/0010-postgresql-backup-restore-verification.md",
    "docs/decisions/0011-ingestion-freshness-semantics.md",
    "docs/decisions/0012-opentelemetry-portability-boundary.md",
    "docs/decisions/0013-otlp-http-ingestion-metrics-export.md",
    "docs/decisions/0014-backend-neutral-telemetry-evidence-query.md",
    "docs/decisions/0015-prometheus-telemetry-evidence-adapter.md",
    "docs/decisions/0016-tenant-bound-otlp-metrics-receiver.md",
    "docs/decisions/0017-investigation-telemetry-selection.md",
    "docs/decisions/0018-evidence-aware-metric-assessment.md",
    "docs/decisions/0019-baseline-window-telemetry-assessment.md",
    "docs/decisions/0020-kubernetes-event-evidence-and-correlation.md",
    "docs/decisions/0021-read-only-kubernetes-event-api-adapter.md",
    "docs/decisions/0022-external-workload-identity-credential-broker.md",
    "docs/decisions/0023-backend-neutral-log-evidence-and-otlp-intake.md",
    "docs/decisions/0024-investigation-log-selection-and-assessment.md",
    "docs/decisions/0025-loki-historical-log-evidence-adapter.md",
    "docs/decisions/0026-resource-history-change-evidence.md",
    "docs/decisions/0029-investigation-context-correlation.md",
    "docs/decisions/0030-durable-investigation-lifecycle.md",
    "docs/decisions/0031-otlp-investigation-trace-export.md",
    "docs/decisions/0032-scope-end-rolling-baseline.md",
    "docs/decisions/0033-adversarial-evidence-release-gate.md",
    "docs/decisions/0034-auditable-cross-signal-planning.md",
    "docs/decisions/0035-one-shot-action-execution.md",
    "docs/decisions/0036-request-scoped-kubernetes-restart.md",
    "docs/decisions/0037-oidc-and-external-policy-boundaries.md",
    "docs/decisions/0038-signed-no-network-plugin-runner.md",
    "docs/decisions/0039-tenant-scoped-investigation-dispatch.md",
    "docs/decisions/0040-action-timers-and-fail-closed-reconciliation.md",
    "docs/decisions/0041-isolated-otlp-receiver-process.md",
    "docs/decisions/0042-dependency-aware-readiness.md",
    "docs/decisions/0043-explicit-ingestion-freshness-sampling.md",
    "docs/decisions/0044-tenant-scoped-outbox-delivery.md",
    "docs/decisions/0045-controlled-helm-schema-migrations.md",
    "docs/decisions/0046-strict-helm-values-contract.md",
    "docs/decisions/0047-attested-release-bundle.md",
    "docs/decisions/0048-explicit-tls-ingress-and-upgrade-conformance.md",
    "docs/decisions/0049-least-authority-scheduled-logical-backups.md",
    "docs/decisions/0050-immutable-application-image-identity.md",
    "docs/decisions/0051-immutable-ci-execution-dependencies.md",
    "docs/decisions/0052-process-local-telemetry-export-health.md",
    "docs/decisions/0053-authenticated-runtime-version-identity.md",
    "docs/decisions/0054-protected-investigation-signal-catalog.md",
    "docs/decisions/0055-bounded-outbox-quarantine.md",
    "docs/decisions/0056-governed-event-delivery-replay.md",
    "docs/decisions/0057-event-delivery-slo-semantics.md",
    "docs/specifications/event-delivery-slo-contract.md",
    "docs/decisions/0058-investigation-completion-slo-semantics.md",
    "docs/specifications/investigation-completion-slo-contract.md",
    "docs/decisions/0059-backend-neutral-query-availability-telemetry.md",
    "docs/specifications/query-availability-telemetry-contract.md",
    "docs/decisions/0062-audited-evidence-artifact-retention.md",
    "docs/specifications/evidence-retention-contract.md",
    "docs/operations/evidence-retention.md",
    "docs/operations/event-delivery.md",
    "docs/operations/helm-deployment.md",
    "docs/operations/investigation-signal-catalog.md",
    "docs/operations/release-artifacts.md",
    "docs/operations/opentelemetry-export.md",
    "docs/operations/prometheus-evidence.md",
    "docs/operations/kubernetes-event-evidence.md",
    "docs/operations/kubernetes-actions.md",
    "docs/operations/credential-broker.md",
    "docs/operations/otlp-metrics-receiver.md",
    "docs/operations/log-evidence.md",
    "docs/operations/resource-change-evidence.md",
    "docs/operations/postgresql-backup-restore.md",
    "docs/operations/plugin-runner.md",
    "docs/operations/measurements/postgresql-backup-restore.json",
    "contracts/schemas/resource.schema.json",
    "contracts/schemas/release-manifest.schema.json",
    "contracts/schemas/credential-lease-request.schema.json",
    "contracts/schemas/credential-lease.schema.json",
    "contracts/schemas/integration-config.schema.json",
    "contracts/schemas/action-proposal.schema.json",
    "contracts/schemas/action-approval.schema.json",
    "contracts/schemas/action-execution-status.schema.json",
    "contracts/schemas/action-result.schema.json",
    "contracts/schemas/action-workflow.schema.json",
    "contracts/schemas/action-workflow-page.schema.json",
    "contracts/schemas/plugin-session.schema.json",
    "contracts/schemas/policy-decision-request.schema.json",
    "contracts/schemas/policy-decision.schema.json",
    "contracts/schemas/resource-collection-request.schema.json",
    "contracts/schemas/resource-collection-result.schema.json",
    "contracts/schemas/resource-neighborhood.schema.json",
    "contracts/schemas/resource-change-evidence-request.schema.json",
    "contracts/schemas/resource-change-evidence-result.schema.json",
    "contracts/schemas/resource-timeline.schema.json",
    "contracts/schemas/page-info.schema.json",
    "contracts/schemas/error.schema.json",
    "contracts/schemas/event.schema.json",
    "contracts/schemas/event-delivery-health-report.schema.json",
    "contracts/schemas/agent-manifest.schema.json",
    "contracts/schemas/plugin-manifest.schema.json",
    "contracts/schemas/plugin-invocation.schema.json",
    "contracts/schemas/plugin-invocation-result.schema.json",
    "contracts/schemas/evidence.schema.json",
    "contracts/schemas/evidence-retention-report.schema.json",
    "contracts/schemas/kubernetes-event-evidence-request.schema.json",
    "contracts/schemas/kubernetes-event-evidence-result.schema.json",
    "contracts/schemas/telemetry-evidence-request.schema.json",
    "contracts/schemas/telemetry-evidence-result.schema.json",
    "contracts/schemas/otlp-metrics-evidence.schema.json",
    "contracts/schemas/log-evidence-request.schema.json",
    "contracts/schemas/log-evidence-result.schema.json",
    "contracts/schemas/otlp-logs-evidence.schema.json",
    "contracts/schemas/investigation-request.schema.json",
    "contracts/schemas/investigation-signal-catalog.schema.json",
    "contracts/schemas/investigation-report.schema.json",
    "contracts/schemas/investigation-cancellation-request.schema.json",
    "contracts/schemas/investigation-status.schema.json",
    "contracts/schemas/ingestion-freshness-report.schema.json",
    "contracts/schemas/telemetry-export-health-report.schema.json",
    "contracts/schemas/runtime-version-report.schema.json",
    "contracts/schemas/session-context.schema.json",
    "contracts/schemas/evaluation-scenario.schema.json",
    "contracts/examples/evidence.json",
    "contracts/examples/evidence-retention-report.json",
    "contracts/examples/credential-lease-request.json",
    "contracts/examples/credential-lease.json",
    "contracts/examples/kubernetes-event-evidence-request.json",
    "contracts/examples/kubernetes-event-evidence-result.json",
    "contracts/examples/telemetry-evidence-request.json",
    "contracts/examples/telemetry-evidence-result.json",
    "contracts/examples/otlp-metrics-evidence.json",
    "contracts/examples/log-evidence-request.json",
    "contracts/examples/log-evidence-result.json",
    "contracts/examples/otlp-logs-evidence.json",
    "contracts/examples/integration-config.json",
    "contracts/examples/action-proposal.json",
    "contracts/examples/action-approval.json",
    "contracts/examples/action-execution-status.json",
    "contracts/examples/action-result.json",
    "contracts/examples/action-workflow.json",
    "contracts/examples/action-workflow-page.json",
    "contracts/examples/plugin-session.json",
    "contracts/examples/plugin-invocation.json",
    "contracts/examples/plugin-invocation-result.json",
    "contracts/examples/policy-decision-request.json",
    "contracts/examples/policy-decision.json",
    "contracts/examples/investigation-request.json",
    "contracts/examples/investigation-request-catalog-resolved.json",
    "contracts/examples/investigation-signal-catalog.json",
    "contracts/examples/investigation-request-kubernetes-events.json",
    "contracts/examples/investigation-request-logs.json",
    "contracts/examples/investigation-request-context.json",
    "contracts/examples/investigation-request-telemetry.json",
    "contracts/examples/investigation-request-telemetry-baseline.json",
    "contracts/examples/investigation-report.json",
    "contracts/examples/investigation-report-kubernetes-events.json",
    "contracts/examples/investigation-report-logs.json",
    "contracts/examples/investigation-report-context.json",
    "contracts/examples/investigation-cancellation-request.json",
    "contracts/examples/investigation-status.json",
    "contracts/examples/investigation-report-telemetry.json",
    "contracts/examples/investigation-report-telemetry-baseline.json",
    "contracts/examples/ingestion-freshness-report.json",
    "contracts/examples/telemetry-export-health-report.json",
    "contracts/examples/event-delivery-health-report.json",
    "contracts/examples/runtime-version-report.json",
    "contracts/examples/session-context.json",
    "contracts/examples/evaluation-scenario.json",
    "contracts/examples/resource-collection-request.json",
    "contracts/examples/resource-collection-result.json",
    "contracts/examples/resource-tombstone.json",
    "contracts/examples/resource-neighborhood.json",
    "contracts/examples/resource-change-evidence-request.json",
    "contracts/examples/resource-change-evidence-result.json",
    "contracts/examples/resource-timeline.json",
    "contracts/examples/release-manifest.json",
    "contracts/examples/page-info.json",
    "contracts/examples/error.json",
    "docs/specifications/evidence-contract.md",
    "docs/specifications/credential-lease-contract.md",
    "docs/specifications/kubernetes-event-evidence-contract.md",
    "docs/specifications/telemetry-evidence-contract.md",
    "docs/specifications/otlp-metrics-evidence-contract.md",
    "docs/specifications/log-evidence-contract.md",
    "docs/specifications/otlp-logs-evidence-contract.md",
    "docs/specifications/integration-config-contract.md",
    "docs/specifications/action-contract.md",
    "docs/specifications/plugin-session-contract.md",
    "docs/specifications/investigation-contract.md",
    "docs/specifications/investigation-signal-catalog-contract.md",
    "docs/specifications/investigation-lifecycle-contract.md",
    "docs/specifications/ingestion-freshness-contract.md",
    "docs/specifications/telemetry-export-health-contract.md",
    "docs/specifications/event-delivery-health-contract.md",
    "docs/specifications/runtime-version-contract.md",
    "docs/specifications/session-context-contract.md",
    "docs/specifications/evaluation-scenario-contract.md",
    "docs/specifications/resource-collection-contract.md",
    "docs/specifications/resource-query-contract.md",
    "docs/specifications/release-manifest-contract.md",
    "docs/specifications/resource-change-evidence-contract.md",
    "requirements/verify.in",
    "requirements/verify.txt",
    "scripts/validate_schemas.py",
    "scripts/test_otel_export_health.py",
    "src/iip/application/collect_evidence.py",
    "src/iip/application/kubernetes_event_evidence.py",
    "src/iip/application/telemetry_evidence.py",
    "src/iip/application/ingest_otlp_metrics.py",
    "src/iip/application/log_evidence.py",
    "src/iip/application/ingest_otlp_logs.py",
    "src/iip/application/observe_ingestion.py",
    "src/iip/application/query_telemetry_export_health.py",
    "src/iip/application/query_runtime_version.py",
    "src/iip/adapters/investigation_catalog.py",
    "src/iip/application/sample_ingestion.py",
    "src/iip/application/deliver_events.py",
    "src/iip/application/query_event_delivery_slo.py",
    "src/iip/application/query_investigation_completion_slo.py",
    "src/iip/application/observe_query_availability.py",
    "src/iip/application/evidence_retention.py",
    "src/iip/adapters/event_publisher.py",
    "src/iip/application/resource_change_evidence.py",
    "src/iip/adapters/auth.py",
    "src/iip/adapters/health.py",
    "src/iip/adapters/evidence.py",
    "src/iip/adapters/otel.py",
    "src/iip/adapters/prometheus.py",
    "src/iip/adapters/loki.py",
    "src/iip/adapters/credential_broker.py",
    "src/iip/adapters/kubernetes_events.py",
    "src/iip/adapters/kubernetes_actions.py",
    "src/iip/adapters/policy.py",
    "src/iip/adapters/plugin_runner.py",
    "src/iip/application/query_actions.py",
    "src/iip/adapters/otlp_receiver.py",
    "src/iip/adapters/otlp_logs_receiver.py",
    "src/iip/adapters/postgres/migrations/0006_source_checkpoint_provider_cursors.sql",
    "src/iip/adapters/postgres/migrations/0007_investigation_lifecycle.sql",
    "src/iip/adapters/postgres/migrations/0008_action_execution_lifecycle.sql",
    "src/iip/adapters/postgres/migrations/0011_event_outbox_slo_window.sql",
    "src/iip/adapters/postgres/migrations/0012_investigation_job_slo_window.sql",
    "src/iip/adapters/postgres/migrations/0013_evidence_artifact_retention.sql",
    "src/iip/adapters/postgres/health.py",
    "tests/test_evidence_collection.py",
    "tests/test_telemetry_export_health.py",
    "tests/test_kubernetes_event_evidence.py",
    "tests/test_telemetry_evidence.py",
    "tests/test_ingestion_freshness.py",
    "tests/test_ingestion_sampling.py",
    "tests/test_event_delivery.py",
    "tests/test_event_delivery_slo.py",
    "tests/test_investigation_completion_slo.py",
    "tests/test_query_availability.py",
    "tests/test_helm_deployment.py",
    "tests/test_helm_values.py",
    "tests/test_release_bundle.py",
    "tests/test_ci_supply_chain.py",
    "tests/test_investigation_signal_catalog.py",
    ".github/dependabot.yml",
    "scripts/test_helm_install.sh",
    "scripts/build_release_bundle.sh",
    "scripts/release_bundle.py",
    "sdks/typescript/package-lock.json",
    "tests/test_authentication.py",
    "tests/test_readiness.py",
    "tests/test_operational_workflows.py",
    "tests/test_otel_metrics.py",
    "tests/test_otel_traces.py",
    "tests/test_otel_collector.py",
    "tests/test_prometheus_backend.py",
    "tests/test_credential_broker.py",
    "tests/test_prometheus_integration.py",
    "tests/test_loki_backend.py",
    "tests/test_loki_integration.py",
    "tests/test_kubernetes_events_backend.py",
    "tests/test_kubernetes_actions.py",
    "tests/test_kubernetes_actions_integration.py",
    "tests/test_action_queries.py",
    "tests/test_policy_adapter.py",
    "tests/test_plugin_runner.py",
    "tests/test_kubernetes_events_integration.py",
    "tests/test_otlp_receiver.py",
    "tests/test_log_evidence.py",
    "tests/test_resource_change_evidence.py",
    "tests/test_investigation_logs.py",
    "tests/test_investigation_lifecycle.py",
    "tests/test_otlp_logs_receiver.py",
    "tests/test_otlp_receiver_integration.py",
    "scripts/test_otel.sh",
    "scripts/run_plugin_runner_conformance.py",
    "scripts/test_prometheus.sh",
    "scripts/test_loki.sh",
    "scripts/test_kubernetes_events.sh",
    "scripts/test_kubernetes_actions.sh",
    "scripts/test_otlp_receiver.sh",
    "deploy/docker-compose.otel.yml",
    "deploy/docker-compose.prometheus.yml",
    "deploy/docker-compose.loki.yml",
    "deploy/docker-compose.otlp-receiver.yml",
    "deploy/otel/collector-test.yaml",
    "deploy/prometheus/prometheus-test.yml",
    "deploy/prometheus/integrations.example.json",
    "deploy/loki/loki-test.yaml",
    "deploy/loki/integrations.example.json",
    "deploy/credential-broker/external-http.example.json",
    "deploy/kubernetes-events/integrations.example.json",
    "deploy/kubernetes-actions/integrations.example.json",
    "deploy/kubernetes-actions/rbac.yaml",
    "deploy/kubernetes/dev/action-executor-fixture.yaml",
    "deploy/otlp/receiver-channels.example.json",
    "deploy/otlp/log-receiver-channels.example.json",
    "scripts/test_kubernetes_live.sh",
    "scripts/run_reference_workflow.py",
    "scripts/backup_restore_experiment.py",
    "api/openapi/control-plane.openapi.json",
    "api/openapi/otlp-receiver.openapi.json",
    "deploy/helm/infra-intelligence/Chart.yaml",
    "deploy/helm/infra-intelligence/values.schema.json",
    "deploy/helm/infra-intelligence/templates/migration-job.yaml",
    "deploy/helm/infra-intelligence/templates/migration-networkpolicy.yaml",
    "deploy/helm/infra-intelligence/templates/ingress.yaml",
    "deploy/helm/infra-intelligence/templates/backup-configmap.yaml",
    "deploy/helm/infra-intelligence/templates/backup-cronjob.yaml",
    "deploy/helm/infra-intelligence/templates/backup-networkpolicy.yaml",
    "deploy/helm/infra-intelligence/templates/validation.yaml",
)

LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def fail(errors: List[str], message: str) -> None:
    errors.append(message)


def validate_required_paths(errors: List[str]) -> None:
    for relative in REQUIRED_PATHS:
        if not (ROOT / relative).is_file():
            fail(errors, f"missing required file: {relative}")


def load_json_documents(errors: List[str]) -> Mapping[Path, object]:
    documents = {}
    for path in sorted(ROOT.rglob("*.json")):
        if "node_modules" in path.parts:
            continue
        try:
            documents[path] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            fail(errors, f"invalid JSON {path.relative_to(ROOT)}: {exc}")
    return documents


def validate_schema_metadata(documents: Mapping[Path, object], errors: List[str]) -> None:
    schema_dir = ROOT / "contracts" / "schemas"
    identifiers = set()
    for path, document in documents.items():
        if path.parent != schema_dir or not isinstance(document, dict):
            continue
        if document.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            fail(errors, f"schema must use draft 2020-12: {path.relative_to(ROOT)}")
        identifier = document.get("$id")
        if not isinstance(identifier, str) or not identifier:
            fail(errors, f"schema is missing $id: {path.relative_to(ROOT)}")
        elif identifier in identifiers:
            fail(errors, f"duplicate schema $id: {identifier}")
        else:
            identifiers.add(identifier)


def validate_authentication_boundary(
    documents: Mapping[Path, object], errors: List[str]
) -> None:
    """Prevent caller-controlled identity from returning to the HTTP boundary."""

    openapi_path = ROOT / "api" / "openapi" / "control-plane.openapi.json"
    openapi = documents.get(openapi_path)
    if not isinstance(openapi, dict):
        fail(errors, "OpenAPI document must be an object")
        return
    if openapi.get("security") != [{"bearerAuth": []}]:
        fail(errors, "OpenAPI protected operations must inherit Bearer authentication")
    components = openapi.get("components")
    if not isinstance(components, dict):
        fail(errors, "OpenAPI components must be an object")
        return
    security_schemes = components.get("securitySchemes")
    bearer = (
        security_schemes.get("bearerAuth")
        if isinstance(security_schemes, dict)
        else None
    )
    if not isinstance(bearer, dict) or (
        bearer.get("type") != "http" or bearer.get("scheme") != "bearer"
    ):
        fail(errors, "OpenAPI bearerAuth security scheme is missing or invalid")

    paths = openapi.get("paths")
    if not isinstance(paths, dict):
        fail(errors, "OpenAPI paths must be an object")
        return
    for public_path in ("/healthz", "/readyz"):
        item = paths.get(public_path)
        operation = item.get("get") if isinstance(item, dict) else None
        if not isinstance(operation, dict) or operation.get("security") != []:
            fail(errors, f"{public_path} must explicitly remain unauthenticated")
    for receiver_path in ("/v1/metrics", "/v1/logs"):
        if receiver_path in paths:
            fail(errors, f"control-plane OpenAPI must not expose {receiver_path}")
    for path, item in paths.items():
        if not path.startswith("/v1") or not isinstance(item, dict):
            continue
        for method, operation in item.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            responses = operation.get("responses") if isinstance(operation, dict) else None
            if not isinstance(responses, dict) or "401" not in responses:
                fail(errors, f"OpenAPI {method.upper()} {path} must declare HTTP 401")

    encoded = json.dumps(openapi, sort_keys=True).lower()
    for header in ("x-iip-tenant-id", "x-iip-actor-id"):
        if header in encoded:
            fail(errors, f"OpenAPI must not accept legacy identity header {header}")
    for relative in (
        "src/iip/surfaces/http.py",
        "sdks/python/src/infra_intelligence_sdk/client.py",
        "sdks/typescript/src/client.ts",
    ):
        content = (ROOT / relative).read_text(encoding="utf-8").lower()
        for header in ("x-iip-tenant-id", "x-iip-actor-id"):
            if header in content:
                fail(errors, f"legacy identity header remains in {relative}: {header}")

    receiver_path = ROOT / "api" / "openapi" / "otlp-receiver.openapi.json"
    receiver = documents.get(receiver_path)
    if not isinstance(receiver, dict):
        fail(errors, "OTLP receiver OpenAPI document must be an object")
        return
    receiver_paths = receiver.get("paths")
    allowed_receiver_paths = {"/healthz", "/readyz", "/v1/metrics", "/v1/logs"}
    if not isinstance(receiver_paths, dict) or set(receiver_paths) != allowed_receiver_paths:
        fail(errors, "OTLP receiver OpenAPI must expose only health and OTLP routes")
    if receiver.get("security") != [{"otlpChannelBearerAuth": []}]:
        fail(errors, "OTLP receiver OpenAPI must require channel Bearer authentication")


def canonical_digest(document: object) -> str:
    """Return the digest used by the repository's canonical ASCII examples."""

    encoded = json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def parse_timestamp(value: object) -> Optional[datetime]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def validate_versioned_envelope(
    document: object,
    *,
    filename: str,
    kind: str,
    errors: List[str],
) -> None:
    if not isinstance(document, dict):
        fail(errors, f"{filename} must be an object")
        return
    if document.get("apiVersion") != "iip.platform/v1alpha1" or document.get("kind") != kind:
        fail(errors, f"{filename} has the wrong version or kind")
    if not isinstance(document.get("metadata"), dict) or not isinstance(
        document.get("spec"), dict
    ):
        fail(errors, f"{filename} must contain metadata and spec objects")


def validate_collection_examples(
    documents: Mapping[Path, object], errors: List[str]
) -> None:
    """Check collection semantics that cannot be expressed in JSON Schema."""

    example_dir = ROOT / "contracts" / "examples"
    request = documents.get(example_dir / "resource-collection-request.json")
    result = documents.get(example_dir / "resource-collection-result.json")
    if not isinstance(request, dict) or not isinstance(result, dict):
        return
    request_metadata = request.get("metadata")
    request_spec = request.get("spec")
    result_metadata = result.get("metadata")
    result_spec = result.get("spec")
    if not all(
        isinstance(item, dict)
        for item in (request_metadata, request_spec, result_metadata, result_spec)
    ):
        return

    for field in ("requestId", "tenantId"):
        if result_metadata.get(field) != request_metadata.get(field):
            fail(errors, f"resource collection result {field} must match its request")
    if result_metadata.get("sourceId") != request_spec.get("sourceId"):
        fail(errors, "resource collection result sourceId must match its request")

    requested_at = parse_timestamp(request_metadata.get("requestedAt"))
    deadline = parse_timestamp(request_spec.get("deadline"))
    created_at = parse_timestamp(result_metadata.get("createdAt"))
    if (
        requested_at is None
        or deadline is None
        or created_at is None
        or not requested_at <= created_at <= deadline
    ):
        fail(errors, "resource collection timestamps must be requested <= created <= deadline")

    scope = request_spec.get("scope")
    completion = result_spec.get("completion")
    observations = result_spec.get("observations")
    limits = request_spec.get("limits")
    if not isinstance(scope, dict) or not isinstance(completion, dict):
        return
    if not isinstance(observations, list) or not isinstance(limits, dict):
        return
    if completion.get("scopeDigest") != canonical_digest(scope):
        fail(errors, "resource collection result scopeDigest must match its request scope")
    if completion.get("resourceCount") != len(observations):
        fail(errors, "resource collection resourceCount must equal its observation count")
    start_sequence = request_spec.get("startSequence")
    if isinstance(start_sequence, int):
        if completion.get("nextSequence") != start_sequence + len(observations):
            fail(errors, "resource collection nextSequence must follow its observation count")

    if len(observations) > limits.get("maxResources", -1):
        fail(errors, "resource collection result exceeds maxResources")
    encoded_result = json.dumps(
        result, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if len(encoded_result) > limits.get("maxOutputBytes", -1):
        fail(errors, "resource collection result exceeds maxOutputBytes")

    for index, observation in enumerate(observations):
        if not isinstance(observation, dict):
            continue
        metadata = observation.get("metadata")
        if not isinstance(metadata, dict):
            continue
        cursor = metadata.get("observation")
        if metadata.get("tenantId") != request_metadata.get("tenantId"):
            fail(errors, "resource collection observation tenant must match its request")
        if not isinstance(cursor, dict):
            fail(errors, "resource collection observation must contain a cursor")
            continue
        expected = start_sequence + index if isinstance(start_sequence, int) else None
        if cursor.get("sequence") != expected:
            fail(errors, "resource collection observation sequences must be contiguous")
        for request_field, cursor_field in (
            ("sourceId", "sourceId"),
            ("streamId", "streamId"),
            ("mode", "mode"),
        ):
            if cursor.get(cursor_field) != request_spec.get(request_field):
                fail(errors, f"resource collection observation {cursor_field} must match its request")
        if cursor.get("snapshotId") != request_spec.get("snapshotId"):
            fail(errors, "resource collection observation snapshotId must match its request")
        if "checkpoint" in cursor:
            fail(errors, "resource collection observations cannot advance a batch checkpoint")

    if completion.get("snapshotId") != request_spec.get("snapshotId"):
        fail(errors, "resource collection completion snapshotId must match its request")


def validate_resource_query_examples(
    documents: Mapping[Path, object], errors: List[str]
) -> None:
    """Check graph and timeline semantics spanning their referenced resources."""

    from iip.domain.models import PlatformEvent, Resource, index_resource_relationships

    example_dir = ROOT / "contracts" / "examples"
    neighborhood = documents.get(example_dir / "resource-neighborhood.json")
    timeline = documents.get(example_dir / "resource-timeline.json")
    if not isinstance(neighborhood, dict) or not isinstance(timeline, dict):
        return
    neighborhood_metadata = neighborhood.get("metadata")
    neighborhood_spec = neighborhood.get("spec")
    timeline_metadata = timeline.get("metadata")
    timeline_spec = timeline.get("spec")
    if not all(
        isinstance(item, dict)
        for item in (
            neighborhood_metadata,
            neighborhood_spec,
            timeline_metadata,
            timeline_spec,
        )
    ):
        return

    root_uid = neighborhood_metadata.get("rootResourceUid")
    tenant_id = neighborhood_metadata.get("tenantId")
    nodes = neighborhood_spec.get("nodes", [])
    edges = neighborhood_spec.get("edges", [])
    page = neighborhood_spec.get("page")
    parsed_nodes = {}
    if isinstance(nodes, list):
        for payload in nodes:
            if not isinstance(payload, dict):
                continue
            resource = Resource.from_dict(payload)
            parsed_nodes[resource.identity.uid] = resource
            if resource.identity.tenant_id != tenant_id:
                fail(errors, "resource neighborhood nodes must share its tenant")
    if root_uid not in parsed_nodes:
        fail(errors, "resource neighborhood must contain its root node")
    if isinstance(page, dict) and isinstance(edges, list):
        if len(edges) > page.get("limit", -1):
            fail(errors, "resource neighborhood edges exceed its page limit")
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            if root_uid not in (edge.get("source"), edge.get("target")):
                fail(errors, "resource neighborhood edge must touch its root")
            observed_uid = edge.get("observedResourceUid")
            observed = parsed_nodes.get(observed_uid)
            if observed is None:
                fail(errors, "resource neighborhood edge must include its observed resource")
                continue
            indexed = {item.edge_id: item for item in index_resource_relationships(observed)}
            expected = indexed.get(edge.get("id"))
            if expected is None or (
                expected.relationship_type != edge.get("type")
                or expected.source_ref != edge.get("source")
                or expected.target_ref != edge.get("target")
                or dict(expected.attributes) != edge.get("attributes")
            ):
                fail(errors, "resource neighborhood edge must match its observed projection")

    timeline_uid = timeline_metadata.get("resourceUid")
    timeline_tenant = timeline_metadata.get("tenantId")
    items = timeline_spec.get("items", [])
    timeline_page = timeline_spec.get("page")
    offsets = []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("resource"), dict):
                continue
            resource = Resource.from_dict(item["resource"])
            offsets.append(item.get("offset"))
            if (
                resource.identity.uid != timeline_uid
                or resource.identity.tenant_id != timeline_tenant
            ):
                fail(errors, "resource timeline items must match its resource and tenant")
            if item.get("observationHash") != PlatformEvent.canonical_hash(
                resource.to_dict()
            ):
                fail(errors, "resource timeline observationHash must match its resource")
    if offsets != sorted(offsets):
        fail(errors, "resource timeline offsets must be ascending")
    if isinstance(timeline_page, dict) and isinstance(items, list):
        if len(items) > timeline_page.get("limit", -1):
            fail(errors, "resource timeline items exceed its page limit")


def validate_evaluation_scenario(
    documents: Mapping[Path, object], errors: List[str]
) -> None:
    """Check replay and scoring invariants spanning scenario fixture contracts."""

    from iip.domain.models import PlatformEvent, Resource

    path = ROOT / "contracts" / "examples" / "evaluation-scenario.json"
    scenario = documents.get(path)
    if not isinstance(scenario, dict):
        return
    metadata = scenario.get("metadata")
    spec = scenario.get("spec")
    if not isinstance(metadata, dict) or not isinstance(spec, dict):
        return
    tenant_id = metadata.get("tenantId")
    fixtures = spec.get("fixtures")
    request = spec.get("request")
    expectations = spec.get("expectations")
    scoring = spec.get("scoring")
    if not all(
        isinstance(item, dict)
        for item in (fixtures, request, expectations, scoring)
    ):
        return

    graph = fixtures.get("graph")
    if not isinstance(graph, dict):
        return
    parsed_resources = {}
    resources = graph.get("resources", [])
    if isinstance(resources, list):
        for payload in resources:
            if not isinstance(payload, dict):
                continue
            resource = Resource.from_dict(payload)
            uid = resource.identity.uid
            if uid in parsed_resources:
                fail(errors, "evaluation scenario graph resource UIDs must be unique")
            parsed_resources[uid] = resource
            if resource.identity.tenant_id != tenant_id:
                fail(errors, "evaluation scenario graph resources must share its tenant")
    graph_uids = set(parsed_resources)
    roots = set(graph.get("rootResourceUids", []))
    if not roots.issubset(graph_uids):
        fail(errors, "evaluation scenario graph roots must resolve in its resources")
    for resource in parsed_resources.values():
        for relationship in resource.relationships:
            target = relationship.get("target")
            if (
                isinstance(target, str)
                and re.fullmatch(r"res_[a-f0-9]{32}", target)
                and target not in graph_uids
            ):
                fail(errors, "evaluation scenario platform relationship targets must resolve")

    timelines = fixtures.get("timelines", [])
    if isinstance(timelines, list):
        for timeline in timelines:
            if not isinstance(timeline, dict):
                continue
            timeline_metadata = timeline.get("metadata")
            timeline_spec = timeline.get("spec")
            if not isinstance(timeline_metadata, dict) or not isinstance(
                timeline_spec, dict
            ):
                continue
            page = timeline_spec.get("page")
            if not isinstance(page, dict) or page.get("hasMore") is not False:
                fail(errors, "evaluation scenario timelines must be complete terminal pages")
            uid = timeline_metadata.get("resourceUid")
            if timeline_metadata.get("tenantId") != tenant_id or uid not in graph_uids:
                fail(errors, "evaluation scenario timelines must resolve in its tenant graph")
            offsets = []
            recorded_times = []
            latest_accepted = None
            items = timeline_spec.get("items", [])
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict) or not isinstance(
                    item.get("resource"), dict
                ):
                    continue
                resource = Resource.from_dict(item["resource"])
                offsets.append(item.get("offset"))
                recorded_times.append(parse_timestamp(item.get("recordedAt")))
                if (
                    resource.identity.tenant_id != tenant_id
                    or resource.identity.uid != uid
                ):
                    fail(errors, "evaluation scenario timeline items must match its resource")
                if item.get("observationHash") != PlatformEvent.canonical_hash(
                    resource.to_dict()
                ):
                    fail(errors, "evaluation scenario timeline observation hash is invalid")
                if item.get("disposition") == "accepted":
                    latest_accepted = resource
            if offsets != sorted(offsets):
                fail(errors, "evaluation scenario timeline offsets must be ascending")
            if any(value is None for value in recorded_times) or recorded_times != sorted(
                recorded_times
            ):
                fail(errors, "evaluation scenario recorded times must be ascending")
            current = parsed_resources.get(uid)
            if (
                current is not None
                and latest_accepted is not None
                and current.to_dict() != latest_accepted.to_dict()
            ):
                fail(errors, "evaluation scenario current graph must match latest accepted history")

    alert = fixtures.get("alert")
    alert_event = PlatformEvent.from_dict(alert) if isinstance(alert, dict) else None
    if alert_event is not None and (
        alert_event.tenant_id != tenant_id or alert_event.subject not in graph_uids
    ):
        fail(errors, "evaluation scenario alert must resolve in its tenant graph")

    request_metadata = request.get("metadata")
    request_spec = request.get("spec")
    if not isinstance(request_metadata, dict) or not isinstance(request_spec, dict):
        return
    if request_metadata.get("tenantId") != tenant_id:
        fail(errors, "evaluation scenario request must share its tenant")
    request_scope = request_spec.get("scope")
    request_uids = set()
    range_start = range_end = None
    if isinstance(request_scope, dict):
        request_uids = set(request_scope.get("resourceUids", []))
        time_range = request_scope.get("timeRange")
        if isinstance(time_range, dict):
            range_start = parse_timestamp(time_range.get("start"))
            range_end = parse_timestamp(time_range.get("end"))
    if not request_uids.issubset(graph_uids) or not roots.issubset(request_uids):
        fail(errors, "evaluation scenario request scope must resolve graph roots")
    requested_at = parse_timestamp(request_metadata.get("requestedAt"))
    alert_time = parse_timestamp(alert.get("time")) if isinstance(alert, dict) else None
    if (
        None in (range_start, range_end, alert_time, requested_at)
        or not range_start <= alert_time <= requested_at <= range_end
    ):
        fail(errors, "evaluation scenario alert and request must fall within scope time")
    trigger = request_spec.get("trigger")
    if isinstance(trigger, dict) and isinstance(alert, dict):
        expected_reference = f"urn:iip:event:{alert.get('id')}"
        if (
            trigger.get("type") != "alert"
            or trigger.get("source") != alert.get("source")
            or trigger.get("reference") != expected_reference
            or request_metadata.get("correlationId") != alert.get("correlationid")
        ):
            fail(errors, "evaluation scenario request trigger must identify its alert")

    evidence_by_id = {}
    evidence_types = set()
    evidence_items = fixtures.get("evidence", [])
    if isinstance(evidence_items, list):
        for evidence in evidence_items:
            if not isinstance(evidence, dict):
                continue
            evidence_metadata = evidence.get("metadata")
            evidence_spec = evidence.get("spec")
            if not isinstance(evidence_metadata, dict) or not isinstance(
                evidence_spec, dict
            ):
                continue
            evidence_id = evidence_metadata.get("id")
            if evidence_id in evidence_by_id:
                fail(errors, "evaluation scenario evidence IDs must be unique")
            evidence_by_id[evidence_id] = evidence
            evidence_types.add(evidence_spec.get("type"))
            if evidence_metadata.get("tenantId") != tenant_id:
                fail(errors, "evaluation scenario evidence must share its tenant")
            if not set(evidence_spec.get("resourceRefs", [])).issubset(graph_uids):
                fail(errors, "evaluation scenario evidence resources must resolve")
            times = (
                parse_timestamp(evidence_spec.get("observedAt")),
                parse_timestamp(evidence_spec.get("retrievedAt")),
                parse_timestamp(evidence_metadata.get("recordedAt")),
            )
            if None in times or not times[0] <= times[1] <= times[2]:
                fail(errors, "evaluation scenario evidence times must be monotonic")
            if (
                range_start is not None
                and range_end is not None
                and times[0] is not None
                and not range_start <= times[0] <= range_end
            ):
                fail(errors, "evaluation scenario evidence observations must fit scope time")

    required = set(expectations.get("requiredEvidenceIds", []))
    red_herrings = set(expectations.get("redHerringEvidenceIds", []))
    adversarial = set(expectations.get("adversarialEvidenceIds", []))
    fixture_evidence_ids = set(evidence_by_id)
    if not required.union(red_herrings, adversarial).issubset(fixture_evidence_ids):
        fail(errors, "evaluation scenario expected evidence IDs must resolve")
    if required.intersection(red_herrings):
        fail(errors, "evaluation scenario required evidence and red herrings must be disjoint")
    fragments = expectations.get("prohibitedOutputFragments", [])
    adversarial_text = " ".join(
        str(evidence_by_id[evidence_id].get("spec", {}).get("summary", ""))
        for evidence_id in adversarial
        if isinstance(evidence_by_id.get(evidence_id), dict)
    ).casefold()
    if not isinstance(fragments, list) or any(
        not isinstance(fragment, str)
        or fragment.casefold() not in adversarial_text
        for fragment in fragments
    ):
        fail(
            errors,
            "evaluation scenario prohibited output fragments must resolve in adversarial evidence",
        )
    affected = set(expectations.get("affectedResourceUids", []))
    if not affected.issubset(graph_uids):
        fail(errors, "evaluation scenario affected resources must resolve")
    forbidden_types = set(expectations.get("forbiddenEvidenceTypes", []))
    requested_evidence_types = set(request_spec.get("evidenceTypes", []))
    if forbidden_types.intersection(evidence_types) or forbidden_types.intersection(
        requested_evidence_types
    ):
        fail(errors, "evaluation scenario forbidden evidence must not be exposed")
    if not evidence_types.issubset(requested_evidence_types):
        fail(errors, "evaluation scenario fixture evidence must fit the request upper bound")

    weights = scoring.get("weights")
    if isinstance(weights, dict) and sum(weights.values()) != 100:
        fail(errors, "evaluation scenario scoring weights must sum to 100")


def validate_kubernetes_event_evidence_examples(
    documents: Mapping[Path, object], errors: List[str]
) -> None:
    """Check Kubernetes Event request/result links beyond JSON Schema."""

    example_dir = ROOT / "contracts" / "examples"
    request = documents.get(example_dir / "kubernetes-event-evidence-request.json")
    result = documents.get(example_dir / "kubernetes-event-evidence-result.json")
    if not isinstance(request, dict) or not isinstance(result, dict):
        return
    request_metadata = request.get("metadata")
    request_spec = request.get("spec")
    result_metadata = result.get("metadata")
    result_spec = result.get("spec")
    if not all(
        isinstance(item, dict)
        for item in (request_metadata, request_spec, result_metadata, result_spec)
    ):
        return
    for field in ("requestId", "tenantId"):
        if result_metadata.get(field) != request_metadata.get(field):
            fail(errors, f"Kubernetes Event result {field} must match its request")
    if result_metadata.get("integrationId") != request_spec.get("integrationId"):
        fail(errors, "Kubernetes Event result integrationId must match its request")
    if result_spec.get("requestDigest") != canonical_digest(request):
        fail(errors, "Kubernetes Event result digest must match its request")
    if result_spec.get("timeRange") != request_spec.get("timeRange"):
        fail(errors, "Kubernetes Event result timeRange must match its request")

    time_range = request_spec.get("timeRange")
    start = end = None
    if isinstance(time_range, dict):
        start = parse_timestamp(time_range.get("start"))
        end = parse_timestamp(time_range.get("end"))
    requested_at = parse_timestamp(request_metadata.get("requestedAt"))
    created_at = parse_timestamp(result_metadata.get("createdAt"))
    deadline = parse_timestamp(request_spec.get("deadline"))
    if (
        None in (start, end, requested_at, created_at, deadline)
        or not start < end <= requested_at <= created_at <= deadline
    ):
        fail(errors, "Kubernetes Event evidence example times must be monotonic")

    events = result_spec.get("events")
    summary = result_spec.get("summary")
    query = request_spec.get("query")
    limits = request_spec.get("limits")
    resource_refs = set(request_spec.get("resourceRefs", []))
    if not isinstance(events, list) or not isinstance(summary, dict):
        return
    event_ids = set()
    sort_keys = []
    warning_count = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        event_id = event.get("id")
        if event_id in event_ids:
            fail(errors, "Kubernetes Event result IDs must be unique")
        event_ids.add(event_id)
        if event.get("resourceRef") not in resource_refs:
            fail(errors, "Kubernetes Event result resource must fit its request")
        if isinstance(query, dict):
            severities = query.get("severities", [])
            reasons = query.get("reasons", [])
            if severities and event.get("severity") not in severities:
                fail(errors, "Kubernetes Event severity must fit its request")
            if reasons and event.get("reason") not in reasons:
                fail(errors, "Kubernetes Event reason must fit its request")
        first = parse_timestamp(event.get("firstObservedAt"))
        last = parse_timestamp(event.get("lastObservedAt"))
        if (
            None in (first, last, start, end)
            or not start <= first <= last <= end
        ):
            fail(errors, "Kubernetes Event occurrence must fit its request range")
        sort_keys.append((event.get("lastObservedAt"), event_id))
        warning_count += event.get("severity") == "warning"
    if sort_keys != sorted(sort_keys):
        fail(errors, "Kubernetes Event result must use deterministic order")
    if summary.get("eventCount") != len(events) or summary.get(
        "warningEventCount"
    ) != warning_count:
        fail(errors, "Kubernetes Event summary must match its events")
    if isinstance(limits, dict) and len(events) > limits.get("maxEvents", -1):
        fail(errors, "Kubernetes Event result must fit request limits")


def validate_investigation_kubernetes_event_examples(
    documents: Mapping[Path, object], errors: List[str]
) -> None:
    """Check event investigation request/report links beyond JSON Schema."""

    example_dir = ROOT / "contracts" / "examples"
    request = documents.get(
        example_dir / "investigation-request-kubernetes-events.json"
    )
    report = documents.get(
        example_dir / "investigation-report-kubernetes-events.json"
    )
    if not isinstance(request, dict) or not isinstance(report, dict):
        return
    request_metadata = request.get("metadata")
    request_spec = request.get("spec")
    report_metadata = report.get("metadata")
    report_spec = report.get("spec")
    if not all(
        isinstance(item, dict)
        for item in (request_metadata, request_spec, report_metadata, report_spec)
    ):
        return
    if report_metadata.get("id") != request_metadata.get("id"):
        fail(errors, "Kubernetes Event investigation examples must share an ID")
    if report_metadata.get("tenantId") != request_metadata.get("tenantId"):
        fail(errors, "Kubernetes Event investigation examples must share a tenant")
    if report_spec.get("requestDigest") != canonical_digest(request):
        fail(errors, "Kubernetes Event investigation report digest must match its request")
    if report_spec.get("scope") != request_spec.get("scope"):
        fail(errors, "Kubernetes Event investigation report scope must match its request")

    selections = request_spec.get("kubernetesEventSelections", [])
    selections_by_id = {
        item.get("id"): item for item in selections if isinstance(item, dict)
    }
    report_evidence = set(report_spec.get("evidenceIds", []))
    hypotheses = report_spec.get("hypotheses", [])
    hypothesis_by_class = {
        item.get("rootCauseClass"): item
        for item in hypotheses
        if isinstance(item, dict)
    }
    assessments = report_spec.get("kubernetesEventAssessments", [])
    for assessment in assessments if isinstance(assessments, list) else []:
        if not isinstance(assessment, dict):
            continue
        selection = selections_by_id.get(assessment.get("selectionId"))
        if not isinstance(selection, dict):
            fail(errors, "Kubernetes Event assessment selectionId must resolve")
            continue
        rule = selection.get("interpretation")
        if not isinstance(rule, dict):
            fail(errors, "Kubernetes Event assessment requires its declared rule")
            continue
        for field in ("conditions", "minMatches"):
            if assessment.get(field) != rule.get(field):
                fail(errors, f"Kubernetes Event assessment {field} must match its request")
        root_cause = assessment.get("rootCauseClass")
        if root_cause not in selection.get("rootCauseClasses", []):
            fail(errors, "Kubernetes Event assessment root cause must fit its selection")
        evidence_id = assessment.get("evidenceId")
        if evidence_id not in report_evidence:
            fail(errors, "Kubernetes Event assessment Evidence must belong to its report")
        matched_ids = assessment.get("matchedEventIds")
        if isinstance(matched_ids, list) and assessment.get("matchedEventCount") != len(
            matched_ids
        ):
            fail(errors, "Kubernetes Event matched count must match its IDs")
        hypothesis = hypothesis_by_class.get(root_cause)
        disposition = assessment.get("disposition")
        if isinstance(hypothesis, dict) and disposition in {
            "supporting",
            "contradicting",
        }:
            field = (
                "supportingEvidenceIds"
                if disposition == "supporting"
                else "contradictingEvidenceIds"
            )
            if evidence_id not in hypothesis.get(field, []):
                fail(
                    errors,
                    f"Kubernetes Event {disposition} Evidence must cite its hypothesis",
                )


def validate_investigation_change_examples(
    documents: Mapping[Path, object], errors: List[str]
) -> None:
    """Check resource-change investigation request/report links beyond Schema."""

    example_dir = ROOT / "contracts" / "examples"
    request = documents.get(example_dir / "investigation-request-changes.json")
    report = documents.get(example_dir / "investigation-report-changes.json")
    if not isinstance(request, dict) or not isinstance(report, dict):
        return
    request_metadata = request.get("metadata")
    request_spec = request.get("spec")
    report_metadata = report.get("metadata")
    report_spec = report.get("spec")
    if not all(
        isinstance(item, dict)
        for item in (request_metadata, request_spec, report_metadata, report_spec)
    ):
        return
    if report_metadata.get("id") != request_metadata.get("id"):
        fail(errors, "change investigation examples must share an ID")
    if report_metadata.get("tenantId") != request_metadata.get("tenantId"):
        fail(errors, "change investigation examples must share a tenant")
    if report_spec.get("requestDigest") != canonical_digest(request):
        fail(errors, "change investigation report digest must match its request")
    if report_spec.get("scope") != request_spec.get("scope"):
        fail(errors, "change investigation report scope must match its request")

    selections = request_spec.get("changeSelections", [])
    selections_by_id = {
        item.get("id"): item for item in selections if isinstance(item, dict)
    }
    report_evidence = set(report_spec.get("evidenceIds", []))
    hypotheses = report_spec.get("hypotheses", [])
    hypothesis_by_class = {
        item.get("rootCauseClass"): item
        for item in hypotheses
        if isinstance(item, dict)
    }
    assessments = report_spec.get("changeAssessments", [])
    disposition_map = {
        "supports": "supporting",
        "contradicts": "contradicting",
        "neutral": "neutral",
    }
    for assessment in assessments if isinstance(assessments, list) else []:
        if not isinstance(assessment, dict):
            continue
        selection = selections_by_id.get(assessment.get("selectionId"))
        if not isinstance(selection, dict):
            fail(errors, "change assessment selectionId must resolve")
            continue
        rule = selection.get("interpretation")
        query = selection.get("query")
        limits = selection.get("limits")
        if not isinstance(rule, dict):
            fail(errors, "change assessment requires its declared rule")
            continue
        if assessment.get("minChanges") != rule.get("minChanges"):
            fail(errors, "change assessment minChanges must match its request")
        expected_kinds = query.get("changeKinds") if isinstance(query, dict) else None
        if assessment.get("changeKinds") != expected_kinds:
            fail(errors, "change assessment kinds must match its request")
        minimum = rule.get("minChanges")
        maximum = limits.get("maxChanges") if isinstance(limits, dict) else None
        if isinstance(minimum, int) and isinstance(maximum, int) and minimum > maximum:
            fail(errors, "change assessment threshold must fit its result limit")
        root_cause = assessment.get("rootCauseClass")
        if root_cause not in selection.get("rootCauseClasses", []):
            fail(errors, "change assessment root cause must fit its selection")
        evidence_id = assessment.get("evidenceId")
        if evidence_id not in report_evidence:
            fail(errors, "change assessment Evidence must belong to its report")
        disposition = assessment.get("disposition")
        observed = assessment.get("observedChangeCount")
        change_ids = assessment.get("observedChangeIds")
        if disposition in {"supporting", "contradicting", "neutral"}:
            if (
                not isinstance(observed, int)
                or not isinstance(minimum, int)
                or not isinstance(change_ids, list)
                or observed != len(change_ids)
            ):
                fail(errors, "change assessment with data requires matching IDs and count")
            else:
                configured = rule.get(
                    "whenMatched" if observed >= minimum else "whenNotMatched"
                )
                if disposition != disposition_map.get(configured):
                    fail(errors, "change assessment disposition must match its rule")
        elif observed is not None or change_ids is not None:
            fail(errors, "change no-data or incomplete assessment must omit observations")
        hypothesis = hypothesis_by_class.get(root_cause)
        if isinstance(hypothesis, dict) and disposition in {
            "supporting",
            "contradicting",
        }:
            field = (
                "supportingEvidenceIds"
                if disposition == "supporting"
                else "contradictingEvidenceIds"
            )
            if evidence_id not in hypothesis.get(field, []):
                fail(errors, f"change {disposition} Evidence must cite its hypothesis")


def validate_investigation_lifecycle_examples(
    documents: Mapping[Path, object], errors: List[str]
) -> None:
    """Check cancellation and lifecycle status links beyond JSON Schema."""

    example_dir = ROOT / "contracts" / "examples"
    request = documents.get(example_dir / "investigation-request.json")
    cancellation = documents.get(
        example_dir / "investigation-cancellation-request.json"
    )
    status = documents.get(example_dir / "investigation-status.json")
    if not all(isinstance(item, dict) for item in (request, cancellation, status)):
        return
    request_metadata = request.get("metadata")
    cancellation_metadata = cancellation.get("metadata")
    cancellation_spec = cancellation.get("spec")
    status_metadata = status.get("metadata")
    status_spec = status.get("spec")
    if not all(
        isinstance(item, dict)
        for item in (
            request_metadata,
            cancellation_metadata,
            cancellation_spec,
            status_metadata,
            status_spec,
        )
    ):
        return
    investigation_id = request_metadata.get("id")
    if cancellation_spec.get("investigationId") != investigation_id:
        fail(errors, "cancellation request must target its investigation example")
    if status_metadata.get("id") != investigation_id:
        fail(errors, "investigation status must identify its request example")
    if any(
        metadata.get("tenantId") != request_metadata.get("tenantId")
        for metadata in (cancellation_metadata, status_metadata)
    ):
        fail(errors, "investigation lifecycle examples must share a tenant")
    if cancellation_metadata.get("actorId") != request_metadata.get("actorId"):
        fail(errors, "cancellation actor must match its authenticated request example")
    if status_spec.get("requestDigest") != canonical_digest(request):
        fail(errors, "investigation status digest must match its request")
    cancellation_status = status_spec.get("cancellation")
    if not isinstance(cancellation_status, dict):
        fail(errors, "cancellation-requested status requires cancellation metadata")
    else:
        if cancellation_status.get("requestedBy") != cancellation_metadata.get(
            "actorId"
        ):
            fail(errors, "investigation cancellation requester must match its actor")
        if cancellation_status.get("requestedAt") != cancellation_metadata.get(
            "requestedAt"
        ):
            fail(errors, "investigation cancellation times must match")
        if cancellation_status.get("reasonCode") != cancellation_spec.get(
            "reasonCode"
        ):
            fail(errors, "investigation cancellation reasons must match")
    started = parse_timestamp(status_spec.get("startedAt"))
    requested = parse_timestamp(cancellation_metadata.get("requestedAt"))
    updated = parse_timestamp(status_metadata.get("updatedAt"))
    lease = parse_timestamp(status_spec.get("leaseExpiresAt"))
    if any(value is None for value in (started, requested, updated, lease)):
        fail(errors, "investigation lifecycle examples require valid timestamps")
    elif not started <= requested == updated < lease:
        fail(errors, "investigation lifecycle timestamps must be ordered")


def validate_investigation_context_examples(
    documents: Mapping[Path, object], errors: List[str]
) -> None:
    """Check repository-context investigation links beyond JSON Schema."""

    example_dir = ROOT / "contracts" / "examples"
    request = documents.get(example_dir / "investigation-request-context.json")
    report = documents.get(example_dir / "investigation-report-context.json")
    if not isinstance(request, dict) or not isinstance(report, dict):
        return
    request_metadata = request.get("metadata")
    request_spec = request.get("spec")
    report_metadata = report.get("metadata")
    report_spec = report.get("spec")
    if not all(
        isinstance(item, dict)
        for item in (request_metadata, request_spec, report_metadata, report_spec)
    ):
        return
    if report_metadata.get("id") != request_metadata.get("id"):
        fail(errors, "context investigation examples must share an ID")
    if report_metadata.get("tenantId") != request_metadata.get("tenantId"):
        fail(errors, "context investigation examples must share a tenant")
    if report_spec.get("requestDigest") != canonical_digest(request):
        fail(errors, "context investigation report digest must match its request")
    if report_spec.get("scope") != request_spec.get("scope"):
        fail(errors, "context investigation report scope must match its request")

    selections = request_spec.get("contextSelections", [])
    selections_by_id = {
        item.get("id"): item for item in selections if isinstance(item, dict)
    }
    report_evidence = set(report_spec.get("evidenceIds", []))
    hypotheses = report_spec.get("hypotheses", [])
    hypothesis_by_class = {
        item.get("rootCauseClass"): item
        for item in hypotheses
        if isinstance(item, dict)
    }
    assessments = report_spec.get("contextAssessments", [])
    disposition_map = {
        "supports": "supporting",
        "contradicts": "contradicting",
        "neutral": "neutral",
    }
    for assessment in assessments if isinstance(assessments, list) else []:
        if not isinstance(assessment, dict):
            continue
        selection = selections_by_id.get(assessment.get("selectionId"))
        if not isinstance(selection, dict):
            fail(errors, "context assessment selectionId must resolve")
            continue
        rule = selection.get("interpretation")
        query = selection.get("query")
        limits = selection.get("limits")
        if not isinstance(rule, dict):
            fail(errors, "context assessment requires its declared rule")
            continue
        minimum = rule.get("minDocuments")
        if assessment.get("minDocuments") != minimum:
            fail(errors, "context assessment minDocuments must match its request")
        expected_kinds = query.get("kinds") if isinstance(query, dict) else None
        expected_refs = query.get("referenceIds") if isinstance(query, dict) else None
        if assessment.get("kinds") != expected_kinds:
            fail(errors, "context assessment kinds must match its request")
        if assessment.get("referenceIds") != expected_refs:
            fail(errors, "context assessment references must match its request")
        maximum = limits.get("maxDocuments") if isinstance(limits, dict) else None
        if isinstance(minimum, int) and isinstance(maximum, int) and minimum > maximum:
            fail(errors, "context assessment threshold must fit its result limit")
        root_cause = assessment.get("rootCauseClass")
        if root_cause not in selection.get("rootCauseClasses", []):
            fail(errors, "context assessment root cause must fit its selection")
        evidence_id = assessment.get("evidenceId")
        if evidence_id not in report_evidence:
            fail(errors, "context assessment Evidence must belong to its report")
        disposition = assessment.get("disposition")
        observed = assessment.get("observedDocumentCount")
        document_ids = assessment.get("observedDocumentIds")
        reference_ids = assessment.get("observedReferenceIds")
        if disposition in {"supporting", "contradicting", "neutral"}:
            if (
                not isinstance(observed, int)
                or not isinstance(minimum, int)
                or not isinstance(document_ids, list)
                or observed != len(document_ids)
                or not isinstance(reference_ids, list)
                or not reference_ids
            ):
                fail(
                    errors,
                    "context assessment with data requires matching document IDs, count, and references",
                )
            else:
                configured = rule.get(
                    "whenMatched" if observed >= minimum else "whenNotMatched"
                )
                if disposition != disposition_map.get(configured):
                    fail(errors, "context assessment disposition must match its rule")
                if isinstance(expected_refs, list) and not set(reference_ids).issubset(
                    expected_refs
                ):
                    fail(errors, "context assessment observed references must be selected")
        elif any(value is not None for value in (observed, document_ids, reference_ids)):
            fail(errors, "context no-data or incomplete assessment must omit observations")
        hypothesis = hypothesis_by_class.get(root_cause)
        if isinstance(hypothesis, dict) and disposition in {
            "supporting",
            "contradicting",
        }:
            field = (
                "supportingEvidenceIds"
                if disposition == "supporting"
                else "contradictingEvidenceIds"
            )
            if evidence_id not in hypothesis.get(field, []):
                fail(errors, f"context {disposition} Evidence must cite its hypothesis")


def validate_investigation_log_examples(
    documents: Mapping[Path, object], errors: List[str]
) -> None:
    """Check log investigation request/report links beyond JSON Schema."""

    example_dir = ROOT / "contracts" / "examples"
    request = documents.get(example_dir / "investigation-request-logs.json")
    report = documents.get(example_dir / "investigation-report-logs.json")
    if not isinstance(request, dict) or not isinstance(report, dict):
        return
    request_metadata = request.get("metadata")
    request_spec = request.get("spec")
    report_metadata = report.get("metadata")
    report_spec = report.get("spec")
    if not all(
        isinstance(item, dict)
        for item in (request_metadata, request_spec, report_metadata, report_spec)
    ):
        return
    if report_metadata.get("id") != request_metadata.get("id"):
        fail(errors, "log investigation examples must share an ID")
    if report_metadata.get("tenantId") != request_metadata.get("tenantId"):
        fail(errors, "log investigation examples must share a tenant")
    if report_spec.get("requestDigest") != canonical_digest(request):
        fail(errors, "log investigation report digest must match its request")
    if report_spec.get("scope") != request_spec.get("scope"):
        fail(errors, "log investigation report scope must match its request")

    selections = request_spec.get("logSelections", [])
    selections_by_id = {
        item.get("id"): item for item in selections if isinstance(item, dict)
    }
    report_evidence = set(report_spec.get("evidenceIds", []))
    hypotheses = report_spec.get("hypotheses", [])
    hypothesis_by_class = {
        item.get("rootCauseClass"): item
        for item in hypotheses
        if isinstance(item, dict)
    }
    assessments = report_spec.get("logAssessments", [])
    disposition_map = {
        "supports": "supporting",
        "contradicts": "contradicting",
        "neutral": "neutral",
    }
    for assessment in assessments if isinstance(assessments, list) else []:
        if not isinstance(assessment, dict):
            continue
        selection = selections_by_id.get(assessment.get("selectionId"))
        if not isinstance(selection, dict):
            fail(errors, "log assessment selectionId must resolve")
            continue
        rule = selection.get("interpretation")
        if not isinstance(rule, dict):
            fail(errors, "log assessment requires its declared rule")
            continue
        minimum = rule.get("minRecords")
        limits = selection.get("limits")
        if assessment.get("minRecords") != minimum:
            fail(errors, "log assessment minRecords must match its request")
        maximum = limits.get("maxRecords") if isinstance(limits, dict) else None
        if isinstance(minimum, int) and isinstance(maximum, int) and minimum > maximum:
            fail(errors, "log assessment threshold must fit its result limit")
        root_cause = assessment.get("rootCauseClass")
        if root_cause not in selection.get("rootCauseClasses", []):
            fail(errors, "log assessment root cause must fit its selection")
        evidence_id = assessment.get("evidenceId")
        if evidence_id not in report_evidence:
            fail(errors, "log assessment Evidence must belong to its report")
        disposition = assessment.get("disposition")
        observed = assessment.get("observedRecordCount")
        if disposition in {"supporting", "contradicting", "neutral"}:
            if not isinstance(observed, int) or not isinstance(minimum, int):
                fail(errors, "log assessment with data requires an observed count")
            else:
                configured = rule.get(
                    "whenMatched" if observed >= minimum else "whenNotMatched"
                )
                if disposition != disposition_map.get(configured):
                    fail(errors, "log assessment disposition must match its rule")
        elif observed is not None:
            fail(errors, "log no-data or incomplete assessment must omit its count")
        hypothesis = hypothesis_by_class.get(root_cause)
        if isinstance(hypothesis, dict) and disposition in {
            "supporting",
            "contradicting",
        }:
            field = (
                "supportingEvidenceIds"
                if disposition == "supporting"
                else "contradictingEvidenceIds"
            )
            if evidence_id not in hypothesis.get(field, []):
                fail(errors, f"log {disposition} Evidence must cite its hypothesis")


def validate_telemetry_evidence_examples(
    documents: Mapping[Path, object], errors: List[str]
) -> None:
    """Check request/result invariants that JSON Schema cannot express."""

    example_dir = ROOT / "contracts" / "examples"
    request = documents.get(example_dir / "telemetry-evidence-request.json")
    result = documents.get(example_dir / "telemetry-evidence-result.json")
    if not isinstance(request, dict) or not isinstance(result, dict):
        return
    request_metadata = request.get("metadata")
    request_spec = request.get("spec")
    result_metadata = result.get("metadata")
    result_spec = result.get("spec")
    if not all(
        isinstance(item, dict)
        for item in (request_metadata, request_spec, result_metadata, result_spec)
    ):
        return

    for field in ("requestId", "tenantId"):
        if result_metadata.get(field) != request_metadata.get(field):
            fail(errors, f"telemetry evidence result {field} must match its request")
    if result_metadata.get("integrationId") != request_spec.get("integrationId"):
        fail(errors, "telemetry evidence result integrationId must match its request")
    if result_spec.get("requestDigest") != canonical_digest(request):
        fail(errors, "telemetry evidence result digest must match its request")
    if result_spec.get("timeRange") != request_spec.get("timeRange"):
        fail(errors, "telemetry evidence result timeRange must match its request")

    time_range = request_spec.get("timeRange")
    start = end = None
    if isinstance(time_range, dict):
        start = parse_timestamp(time_range.get("start"))
        end = parse_timestamp(time_range.get("end"))
    requested_at = parse_timestamp(request_metadata.get("requestedAt"))
    created_at = parse_timestamp(result_metadata.get("createdAt"))
    deadline = parse_timestamp(request_spec.get("deadline"))
    if (
        None in (start, end, requested_at, created_at, deadline)
        or not start < end <= requested_at <= created_at <= deadline
    ):
        fail(errors, "telemetry evidence example times must be monotonic")

    series = result_spec.get("series")
    summary = result_spec.get("summary")
    request_query = request_spec.get("query")
    limits = request_spec.get("limits")
    if not isinstance(series, list) or not isinstance(summary, dict):
        return
    point_count = 0
    for item in series:
        if not isinstance(item, dict):
            continue
        if isinstance(request_query, dict) and item.get("metric") != request_query.get(
            "metric"
        ):
            fail(errors, "telemetry evidence series metric must match its request")
        timestamps = []
        points = item.get("points", [])
        if isinstance(points, list):
            point_count += len(points)
            for point in points:
                timestamp = (
                    parse_timestamp(point.get("timestamp"))
                    if isinstance(point, dict)
                    else None
                )
                timestamps.append(timestamp)
                if (
                    timestamp is None
                    or start is None
                    or end is None
                    or not start <= timestamp <= end
                ):
                    fail(errors, "telemetry evidence point must fit its request range")
            if any(value is None for value in timestamps) or timestamps != sorted(
                timestamps
            ) or len(set(timestamps)) != len(timestamps):
                fail(errors, "telemetry evidence points must be strictly ascending")
    if summary.get("seriesCount") != len(series) or summary.get(
        "dataPointCount"
    ) != point_count:
        fail(errors, "telemetry evidence summary must match its series")
    if isinstance(limits, dict) and (
        len(series) > limits.get("maxSeries", -1)
        or point_count > limits.get("maxDataPoints", -1)
    ):
        fail(errors, "telemetry evidence result must fit request limits")


def validate_log_evidence_examples(
    documents: Mapping[Path, object], errors: List[str]
) -> None:
    """Check log request/result correlation, scope, order, and summary."""

    example_dir = ROOT / "contracts" / "examples"
    request = documents.get(example_dir / "log-evidence-request.json")
    result = documents.get(example_dir / "log-evidence-result.json")
    if not isinstance(request, dict) or not isinstance(result, dict):
        return
    request_metadata = request.get("metadata")
    request_spec = request.get("spec")
    result_metadata = result.get("metadata")
    result_spec = result.get("spec")
    if not all(
        isinstance(item, dict)
        for item in (request_metadata, request_spec, result_metadata, result_spec)
    ):
        return
    for field in ("requestId", "tenantId"):
        if result_metadata.get(field) != request_metadata.get(field):
            fail(errors, f"log evidence result {field} must match its request")
    if result_metadata.get("integrationId") != request_spec.get("integrationId"):
        fail(errors, "log evidence result integrationId must match its request")
    if result_spec.get("requestDigest") != canonical_digest(request):
        fail(errors, "log evidence result digest must match its request")
    if result_spec.get("timeRange") != request_spec.get("timeRange"):
        fail(errors, "log evidence result timeRange must match its request")

    time_range = request_spec.get("timeRange")
    start = end = None
    if isinstance(time_range, dict):
        start = parse_timestamp(time_range.get("start"))
        end = parse_timestamp(time_range.get("end"))
    requested_at = parse_timestamp(request_metadata.get("requestedAt"))
    created_at = parse_timestamp(result_metadata.get("createdAt"))
    deadline = parse_timestamp(request_spec.get("deadline"))
    if (
        None in (start, end, requested_at, created_at, deadline)
        or not start < end <= requested_at <= created_at <= deadline
    ):
        fail(errors, "log evidence example times must be monotonic")

    records = result_spec.get("records")
    summary = result_spec.get("summary")
    query = request_spec.get("query")
    resources = request_spec.get("resourceRefs")
    limits = request_spec.get("limits")
    if not isinstance(records, list) or not isinstance(summary, dict):
        return
    identities = []
    error_count = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        instant = parse_timestamp(record.get("timestamp"))
        identity = (instant, record.get("id"))
        identities.append(identity)
        if instant is None or start is None or end is None or not start <= instant <= end:
            fail(errors, "log evidence record must fit its request range")
        if isinstance(resources, list) and record.get("resourceRef") not in resources:
            fail(errors, "log evidence record resource must fit its request")
        if isinstance(query, dict):
            if record.get("serviceName") not in query.get("serviceNames", []):
                fail(errors, "log evidence record service must fit its request")
            severities = query.get("severities", [])
            if severities and record.get("severity") not in severities:
                fail(errors, "log evidence record severity must fit its request")
        if record.get("severity") in ("error", "fatal"):
            error_count += 1
    if (
        any(identity[0] is None for identity in identities)
        or identities != sorted(identities)
        or len(identities) != len(set(identities))
    ):
        fail(errors, "log evidence records must be unique and ordered")
    if (
        summary.get("recordCount") != len(records)
        or summary.get("errorCount") != error_count
    ):
        fail(errors, "log evidence summary must match its records")
    if isinstance(limits, dict) and len(records) > limits.get("maxRecords", -1):
        fail(errors, "log evidence result must fit request limits")


def validate_resource_change_evidence_examples(
    documents: Mapping[Path, object], errors: List[str]
) -> None:
    """Check change request/result correlation, order, scope, and summaries."""

    example_dir = ROOT / "contracts" / "examples"
    request = documents.get(example_dir / "resource-change-evidence-request.json")
    result = documents.get(example_dir / "resource-change-evidence-result.json")
    if not isinstance(request, dict) or not isinstance(result, dict):
        return
    request_metadata = request.get("metadata")
    request_spec = request.get("spec")
    result_metadata = result.get("metadata")
    result_spec = result.get("spec")
    if not all(
        isinstance(item, dict)
        for item in (request_metadata, request_spec, result_metadata, result_spec)
    ):
        return
    for field in ("requestId", "tenantId"):
        if result_metadata.get(field) != request_metadata.get(field):
            fail(errors, f"resource change result {field} must match its request")
    if result_metadata.get("integrationId") != request_spec.get("integrationId"):
        fail(errors, "resource change result integrationId must match its request")
    if result_spec.get("requestDigest") != canonical_digest(request):
        fail(errors, "resource change result digest must match its request")
    if result_spec.get("timeRange") != request_spec.get("timeRange"):
        fail(errors, "resource change result timeRange must match its request")
    time_range = request_spec.get("timeRange")
    start = end = None
    if isinstance(time_range, dict):
        start = parse_timestamp(time_range.get("start"))
        end = parse_timestamp(time_range.get("end"))
    requested_at = parse_timestamp(request_metadata.get("requestedAt"))
    created_at = parse_timestamp(result_metadata.get("createdAt"))
    deadline = parse_timestamp(request_spec.get("deadline"))
    if (
        None in (start, end, requested_at, created_at, deadline)
        or not start < end <= requested_at <= created_at <= deadline
    ):
        fail(errors, "resource change example times must be monotonic")
    changes = result_spec.get("changes")
    summary = result_spec.get("summary")
    query = request_spec.get("query")
    limits = request_spec.get("limits")
    resources = set(request_spec.get("resourceRefs", []))
    if not isinstance(changes, list) or not isinstance(summary, dict):
        return
    allowed = set(query.get("changeKinds", [])) if isinstance(query, dict) else set()
    identities = []
    counts: dict[object, int] = {}
    affected = set()
    for change in changes:
        if not isinstance(change, dict):
            continue
        observed = parse_timestamp(change.get("observedAt"))
        identity = (
            observed,
            change.get("resourceRef"),
            change.get("kind"),
            change.get("id"),
        )
        identities.append(identity)
        if observed is None or start is None or end is None or not start <= observed <= end:
            fail(errors, "resource change must fit its request range")
        resource_ref = change.get("resourceRef")
        if resource_ref not in resources:
            fail(errors, "resource change must fit its request scope")
        affected.add(resource_ref)
        kind = change.get("kind")
        counts[kind] = counts.get(kind, 0) + 1
        if allowed and kind not in allowed:
            fail(errors, "resource change kind must fit its request")
        if change.get("beforeObservationHash") == change.get("afterObservationHash"):
            fail(errors, "resource change before and after hashes must differ")
    if (
        any(identity[0] is None for identity in identities)
        or identities != sorted(identities)
        or len({identity[3] for identity in identities}) != len(identities)
    ):
        fail(errors, "resource changes must be unique and ordered")
    if (
        summary.get("changeCount") != len(changes)
        or summary.get("affectedResourceCount") != len(affected)
        or summary.get("countsByKind") != counts
    ):
        fail(errors, "resource change summary must match its changes")
    if isinstance(limits, dict) and len(changes) > limits.get("maxChanges", -1):
        fail(errors, "resource change result must fit request limits")


def validate_context_evidence_examples(
    documents: Mapping[Path, object], errors: List[str]
) -> None:
    """Check context request/result correlation, scope, hashes, and summaries."""

    example_dir = ROOT / "contracts" / "examples"
    request = documents.get(example_dir / "context-evidence-request.json")
    result = documents.get(example_dir / "context-evidence-result.json")
    if not isinstance(request, dict) or not isinstance(result, dict):
        return
    request_metadata = request.get("metadata")
    request_spec = request.get("spec")
    result_metadata = result.get("metadata")
    result_spec = result.get("spec")
    if not all(
        isinstance(item, dict)
        for item in (request_metadata, request_spec, result_metadata, result_spec)
    ):
        return
    for field in ("requestId", "tenantId"):
        if result_metadata.get(field) != request_metadata.get(field):
            fail(errors, f"context result {field} must match its request")
    if result_metadata.get("integrationId") != request_spec.get("integrationId"):
        fail(errors, "context result integrationId must match its request")
    if result_spec.get("requestDigest") != canonical_digest(request):
        fail(errors, "context result digest must match its request")
    requested_at = parse_timestamp(request_metadata.get("requestedAt"))
    created_at = parse_timestamp(result_metadata.get("createdAt"))
    deadline = parse_timestamp(request_spec.get("deadline"))
    if (
        None in (requested_at, created_at, deadline)
        or not requested_at <= created_at <= deadline
    ):
        fail(errors, "context evidence example times must be monotonic")
    query = request_spec.get("query")
    limits = request_spec.get("limits")
    requested_resources = set(request_spec.get("resourceRefs", []))
    requested_kinds = set(query.get("kinds", [])) if isinstance(query, dict) else set()
    requested_refs = (
        set(query.get("referenceIds", [])) if isinstance(query, dict) else set()
    )
    context_documents = result_spec.get("documents")
    summary = result_spec.get("summary")
    if not isinstance(context_documents, list) or not isinstance(summary, dict):
        return
    ids = set()
    references = set()
    counts: dict[object, int] = {}
    sort_keys = []
    for document in context_documents:
        if not isinstance(document, dict):
            continue
        ids.add(document.get("id"))
        references.add(document.get("referenceId"))
        kind = document.get("kind")
        counts[kind] = counts.get(kind, 0) + 1
        sort_keys.append((kind, document.get("referenceId")))
        if not set(document.get("resourceRefs", [])).issubset(requested_resources):
            fail(errors, "context document resources must fit its request")
        if requested_kinds and kind not in requested_kinds:
            fail(errors, "context document kind must fit its request")
        if requested_refs and document.get("referenceId") not in requested_refs:
            fail(errors, "context document reference must fit its request")
        excerpt = document.get("excerpt")
        expected_hash = (
            "sha256:" + hashlib.sha256(excerpt.encode("utf-8")).hexdigest()
            if isinstance(excerpt, str)
            else None
        )
        if document.get("excerptHash") != expected_hash:
            fail(errors, "context excerpt hash must match its redacted text")
        if document.get("trust") != "untrusted" or document.get(
            "instructionPolicy"
        ) != "data-only":
            fail(errors, "context documents must remain untrusted data")
    if (
        len(ids) != len(context_documents)
        or len(references) != len(context_documents)
        or sort_keys != sorted(sort_keys)
    ):
        fail(errors, "context documents must be unique and ordered")
    if (
        summary.get("documentCount") != len(context_documents)
        or summary.get("countsByKind") != counts
    ):
        fail(errors, "context summary must match its documents")
    if isinstance(limits, dict):
        if len(context_documents) > limits.get("maxDocuments", -1):
            fail(errors, "context result must fit its document limit")
        if any(
            isinstance(document, dict)
            and isinstance(document.get("excerpt"), str)
            and len(document["excerpt"]) > limits.get("maxExcerptChars", -1)
            for document in context_documents
        ):
            fail(errors, "context result must fit its excerpt limit")


def validate_investigation_telemetry_examples(
    documents: Mapping[Path, object], errors: List[str]
) -> None:
    """Check telemetry request/report links that JSON Schema cannot express."""

    example_dir = ROOT / "contracts" / "examples"
    pairs = (
        (
            "investigation-request-telemetry.json",
            "investigation-report-telemetry.json",
        ),
        (
            "investigation-request-telemetry-baseline.json",
            "investigation-report-telemetry-baseline.json",
        ),
    )
    for request_name, report_name in pairs:
        request = documents.get(example_dir / request_name)
        report = documents.get(example_dir / report_name)
        if isinstance(request, dict) and isinstance(report, dict):
            validate_investigation_telemetry_pair(request, report, errors)


def expand_rolling_baseline(
    rule: Mapping[str, object],
    request_spec: Mapping[str, object],
) -> Optional[dict[str, object]]:
    """Expand a scope-end anchored example rule to its auditable windows."""

    scope = request_spec.get("scope")
    scope_range = scope.get("timeRange") if isinstance(scope, dict) else None
    scope_end = (
        parse_timestamp(scope_range.get("end"))
        if isinstance(scope_range, dict)
        else None
    )
    baseline_seconds = rule.get("baselineDurationSeconds")
    evaluation_seconds = rule.get("evaluationDurationSeconds")
    gap_seconds = rule.get("gapSeconds")
    if (
        scope_end is None
        or not isinstance(baseline_seconds, int)
        or isinstance(baseline_seconds, bool)
        or not isinstance(evaluation_seconds, int)
        or isinstance(evaluation_seconds, bool)
        or not isinstance(gap_seconds, int)
        or isinstance(gap_seconds, bool)
    ):
        return None
    evaluation_start = scope_end - timedelta(seconds=evaluation_seconds)
    baseline_end = evaluation_start - timedelta(seconds=gap_seconds)
    baseline_start = baseline_end - timedelta(seconds=baseline_seconds)

    def timestamp(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")

    expanded = {
        key: rule.get(key)
        for key in (
            "statistic",
            "unit",
            "calculation",
            "operator",
            "threshold",
            "whenMatched",
            "whenNotMatched",
        )
    }
    expanded["baselineTimeRange"] = {
        "start": timestamp(baseline_start),
        "end": timestamp(baseline_end),
    }
    expanded["evaluationTimeRange"] = {
        "start": timestamp(evaluation_start),
        "end": timestamp(scope_end),
    }
    return expanded


def validate_investigation_signal_plan(
    request_spec: Mapping[str, object],
    report_spec: Mapping[str, object],
    errors: List[str],
) -> None:
    """Check that an emitted plan accounts for request candidates exactly once."""

    plan = report_spec.get("signalPlan")
    if plan is None:
        return
    if not isinstance(plan, dict):
        return
    definitions = (
        ("kubernetes.event", "kubernetesEventSelections"),
        ("repository.context", "contextSelections"),
        ("resource.change", "changeSelections"),
        ("telemetry.metrics", "telemetrySelections"),
        ("telemetry.logs", "logSelections"),
    )
    snapshot = request_spec.get("catalogSnapshot")
    generated = set()
    if isinstance(snapshot, dict):
        references = snapshot.get("generatedSelections")
        if isinstance(references, list):
            generated = {
                (reference.get("signal"), reference.get("selectionId"))
                for reference in references
                if isinstance(reference, dict)
            }
    expected = []
    for signal, field in definitions:
        selections = request_spec.get(field, [])
        if isinstance(selections, list):
            expected.extend(
                (
                    signal,
                    selection.get("id"),
                    (
                        "protected-catalog"
                        if (signal, selection.get("id")) in generated
                        else "request"
                    ),
                )
                for selection in selections
                if isinstance(selection, dict)
            )
    steps = plan.get("steps")
    if not isinstance(steps, list):
        return
    observed = [
        (step.get("signal"), step.get("selectionId"), step.get("origin"))
        for step in steps
        if isinstance(step, dict)
    ]
    if observed != expected:
        fail(errors, "investigation signal plan must account for ordered request candidates")
    if isinstance(snapshot, dict):
        expected_catalog = {
            "profileId": snapshot.get("profileId"),
            "profileVersion": snapshot.get("profileVersion"),
            "profileDigest": snapshot.get("profileDigest"),
            "snapshotDigest": snapshot.get("snapshotDigest"),
        }
        if plan.get("catalog") != expected_catalog:
            fail(errors, "investigation signal plan catalog must match its request snapshot")
    if [step.get("position") for step in steps if isinstance(step, dict)] != list(
        range(1, len(steps) + 1)
    ):
        fail(errors, "investigation signal plan positions must be contiguous")
    scheduled = sum(
        isinstance(step, dict) and step.get("decision") == "scheduled"
        for step in steps
    )
    if (
        plan.get("candidateCount") != len(steps)
        or plan.get("scheduledCount") != scheduled
        or plan.get("deferredCount") != len(steps) - scheduled
    ):
        fail(errors, "investigation signal plan counts must match its steps")
    leading_classes = {
        hypothesis.get("rootCauseClass")
        for hypothesis in report_spec.get("hypotheses", [])
        if isinstance(hypothesis, dict)
        and hypothesis.get("disposition") == "leading"
    }
    if leading_classes and plan.get("rootCauseClass") not in leading_classes:
        fail(errors, "investigation signal plan root cause must match its report")


def validate_investigation_telemetry_pair(
    request: Mapping[str, object],
    report: Mapping[str, object],
    errors: List[str],
) -> None:
    """Validate one telemetry investigation request/report example pair."""

    if not isinstance(request, dict) or not isinstance(report, dict):
        return
    request_metadata = request.get("metadata")
    request_spec = request.get("spec")
    report_metadata = report.get("metadata")
    report_spec = report.get("spec")
    if not all(
        isinstance(item, dict)
        for item in (request_metadata, request_spec, report_metadata, report_spec)
    ):
        return
    validate_investigation_signal_plan(request_spec, report_spec, errors)
    if report_metadata.get("id") != request_metadata.get("id"):
        fail(errors, "telemetry investigation examples must share an ID")
    if report_metadata.get("tenantId") != request_metadata.get("tenantId"):
        fail(errors, "telemetry investigation examples must share a tenant")
    if report_spec.get("requestDigest") != canonical_digest(request):
        fail(errors, "telemetry investigation report digest must match its request")
    if report_spec.get("scope") != request_spec.get("scope"):
        fail(errors, "telemetry investigation report scope must match its request")

    selections = request_spec.get("telemetrySelections", [])
    selections_by_id = {
        item.get("id"): item for item in selections if isinstance(item, dict)
    }
    report_evidence = set(report_spec.get("evidenceIds", []))
    hypotheses = report_spec.get("hypotheses", [])
    hypothesis_by_class = {
        item.get("rootCauseClass"): item
        for item in hypotheses
        if isinstance(item, dict)
    }
    assessments = report_spec.get("telemetryAssessments", [])
    for assessment in assessments if isinstance(assessments, list) else []:
        if not isinstance(assessment, dict):
            continue
        selection = selections_by_id.get(assessment.get("selectionId"))
        if not isinstance(selection, dict):
            fail(errors, "telemetry assessment selectionId must resolve in its request")
            continue
        interpretation = selection.get("interpretation")
        baseline_comparison = selection.get("baselineComparison")
        rolling_baseline = selection.get("rollingBaselineComparison")
        if not isinstance(baseline_comparison, dict) and isinstance(
            rolling_baseline, dict
        ):
            baseline_comparison = expand_rolling_baseline(
                rolling_baseline, request_spec
            )
        rule = (
            interpretation
            if isinstance(interpretation, dict)
            else baseline_comparison
        )
        query = selection.get("query")
        if not isinstance(rule, dict) or not isinstance(query, dict):
            fail(errors, "telemetry assessment requires its declared request rule")
            continue
        expected_fields = {
            "metric": query.get("metric"),
            "statistic": rule.get("statistic"),
            "unit": rule.get("unit"),
            "operator": rule.get("operator"),
            "threshold": rule.get("threshold"),
        }
        if isinstance(baseline_comparison, dict):
            expected_fields.update(
                {
                    "assessmentType": "baseline-comparison",
                    "baselineTimeRange": baseline_comparison.get(
                        "baselineTimeRange"
                    ),
                    "evaluationTimeRange": baseline_comparison.get(
                        "evaluationTimeRange"
                    ),
                    "calculation": baseline_comparison.get("calculation"),
                    "comparisonUnit": (
                        "1"
                        if baseline_comparison.get("calculation") == "ratio"
                        else baseline_comparison.get("unit")
                    ),
                }
            )
            scope = request_spec.get("scope")
            scope_range = scope.get("timeRange") if isinstance(scope, dict) else None
            baseline_range = baseline_comparison.get("baselineTimeRange")
            evaluation_range = baseline_comparison.get("evaluationTimeRange")
            timestamps = (
                parse_timestamp(scope_range.get("start"))
                if isinstance(scope_range, dict)
                else None,
                parse_timestamp(baseline_range.get("start"))
                if isinstance(baseline_range, dict)
                else None,
                parse_timestamp(baseline_range.get("end"))
                if isinstance(baseline_range, dict)
                else None,
                parse_timestamp(evaluation_range.get("start"))
                if isinstance(evaluation_range, dict)
                else None,
                parse_timestamp(evaluation_range.get("end"))
                if isinstance(evaluation_range, dict)
                else None,
                parse_timestamp(scope_range.get("end"))
                if isinstance(scope_range, dict)
                else None,
            )
            if any(value is None for value in timestamps) or not (
                timestamps[0] <= timestamps[1]
                < timestamps[2]
                < timestamps[3]
                < timestamps[4]
                <= timestamps[5]
            ):
                fail(
                    errors,
                    "telemetry baseline windows must be ordered inside request scope",
                )
            baseline_value = assessment.get("baselineValue")
            evaluation_value = assessment.get("evaluationValue")
            comparison_value = assessment.get("comparisonValue")
            if all(
                isinstance(value, (int, float)) and not isinstance(value, bool)
                for value in (baseline_value, evaluation_value, comparison_value)
            ):
                expected_comparison = (
                    evaluation_value - baseline_value
                    if baseline_comparison.get("calculation") == "difference"
                    else evaluation_value / baseline_value
                    if baseline_value != 0
                    else None
                )
                if expected_comparison is None or not math.isclose(
                    comparison_value,
                    expected_comparison,
                    rel_tol=1e-12,
                    abs_tol=1e-15,
                ):
                    fail(
                        errors,
                        "telemetry baseline comparisonValue must match its values",
                    )
        for field, expected in expected_fields.items():
            if assessment.get(field) != expected:
                fail(errors, f"telemetry assessment {field} must match its request")
        root_cause = assessment.get("rootCauseClass")
        if root_cause not in selection.get("rootCauseClasses", []):
            fail(errors, "telemetry assessment root cause must fit its selection")
        evidence_id = assessment.get("evidenceId")
        if evidence_id not in report_evidence:
            fail(errors, "telemetry assessment Evidence must belong to its report")
        hypothesis = hypothesis_by_class.get(root_cause)
        disposition = assessment.get("disposition")
        if isinstance(hypothesis, dict) and disposition in {
            "supporting",
            "contradicting",
        }:
            field = (
                "supportingEvidenceIds"
                if disposition == "supporting"
                else "contradictingEvidenceIds"
            )
            if evidence_id not in hypothesis.get(field, []):
                fail(errors, f"telemetry {disposition} Evidence must cite its hypothesis")


def validate_otlp_metrics_evidence_example(
    documents: Mapping[Path, object], errors: List[str]
) -> None:
    """Check normalized receiver invariants that JSON Schema cannot express."""

    path = ROOT / "contracts" / "examples" / "otlp-metrics-evidence.json"
    document = documents.get(path)
    if not isinstance(document, dict):
        return
    metadata = document.get("metadata")
    spec = document.get("spec")
    if not isinstance(metadata, dict) or not isinstance(spec, dict):
        return
    time_range = spec.get("timeRange")
    series = spec.get("series")
    summary = spec.get("summary")
    if (
        not isinstance(time_range, dict)
        or not isinstance(series, list)
        or not isinstance(summary, dict)
    ):
        return
    start = parse_timestamp(time_range.get("start"))
    end = parse_timestamp(time_range.get("end"))
    received = parse_timestamp(metadata.get("receivedAt"))
    if None in (start, end, received) or not start <= end:
        fail(errors, "OTLP metrics evidence time range must be ordered")

    point_count = 0
    metric_names = set()
    identities = []
    all_timestamps = []
    for item in series:
        if not isinstance(item, dict):
            continue
        metric_names.add(item.get("metric"))
        attributes = item.get("attributes")
        identity = (
            item.get("metric"),
            item.get("kind"),
            tuple(sorted(attributes.items())) if isinstance(attributes, dict) else (),
        )
        identities.append(identity)
        points = item.get("points")
        timestamps = []
        if isinstance(points, list):
            point_count += len(points)
            for point in points:
                timestamp = (
                    parse_timestamp(point.get("timestamp"))
                    if isinstance(point, dict)
                    else None
                )
                timestamps.append(timestamp)
                if (
                    timestamp is None
                    or start is None
                    or end is None
                    or not start <= timestamp <= end
                ):
                    fail(errors, "OTLP metrics evidence point must fit its range")
                elif timestamp is not None:
                    all_timestamps.append(timestamp)
            if (
                any(value is None for value in timestamps)
                or timestamps != sorted(timestamps)
                or len(set(timestamps)) != len(timestamps)
            ):
                fail(errors, "OTLP metrics evidence points must be strictly ascending")
    if identities != sorted(identities) or len(identities) != len(set(identities)):
        fail(errors, "OTLP metrics evidence series must be unique and ordered")
    if all_timestamps and (
        start != min(all_timestamps) or end != max(all_timestamps)
    ):
        fail(errors, "OTLP metrics evidence range must span its points")
    if (
        summary.get("metricCount") != len(metric_names)
        or summary.get("seriesCount") != len(series)
        or summary.get("dataPointCount") != point_count
    ):
        fail(errors, "OTLP metrics evidence summary must match its series")


def validate_otlp_logs_evidence_example(
    documents: Mapping[Path, object], errors: List[str]
) -> None:
    """Check OTLP logs time, order, uniqueness, and summary invariants."""

    path = ROOT / "contracts" / "examples" / "otlp-logs-evidence.json"
    document = documents.get(path)
    if not isinstance(document, dict):
        return
    spec = document.get("spec")
    if not isinstance(spec, dict):
        return
    time_range = spec.get("timeRange")
    records = spec.get("records")
    summary = spec.get("summary")
    if (
        not isinstance(time_range, dict)
        or not isinstance(records, list)
        or not isinstance(summary, dict)
    ):
        return
    start = parse_timestamp(time_range.get("start"))
    end = parse_timestamp(time_range.get("end"))
    identities = []
    timestamps = []
    services = set()
    error_count = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        timestamp = parse_timestamp(record.get("timestamp"))
        identities.append((timestamp, record.get("id")))
        if timestamp is not None:
            timestamps.append(timestamp)
        services.add(record.get("serviceName"))
        if record.get("severity") in ("error", "fatal"):
            error_count += 1
        if timestamp is None or start is None or end is None or not start <= timestamp <= end:
            fail(errors, "OTLP logs evidence record must fit its range")
    if (
        any(identity[0] is None for identity in identities)
        or identities != sorted(identities)
        or len(identities) != len(set(identities))
    ):
        fail(errors, "OTLP logs evidence records must be unique and ordered")
    if timestamps and (start != min(timestamps) or end != max(timestamps)):
        fail(errors, "OTLP logs evidence range must span its records")
    if (
        summary.get("serviceCount") != len(services)
        or summary.get("recordCount") != len(records)
        or summary.get("errorCount") != error_count
    ):
        fail(errors, "OTLP logs evidence summary must match its records")


def validate_examples(documents: Mapping[Path, object], errors: List[str]) -> None:
    from iip.domain.models import ContractError, Resource

    example_dir = ROOT / "contracts" / "examples"
    resource_path = example_dir / "resource.json"
    resource_document = documents.get(resource_path)
    if not isinstance(resource_document, dict):
        fail(errors, "resource example must be an object")
    else:
        try:
            parsed = Resource.from_dict(resource_document)
            round_trip = parsed.to_dict()
            if round_trip["metadata"]["uid"] != parsed.identity.uid:
                fail(errors, "resource round trip lost deterministic identity")
        except ContractError as exc:
            fail(errors, f"resource example violates domain contract: {exc}")

    tombstone_document = documents.get(example_dir / "resource-tombstone.json")
    if not isinstance(tombstone_document, dict):
        fail(errors, "resource tombstone example must be an object")
    else:
        try:
            tombstone = Resource.from_dict(tombstone_document)
            if (
                tombstone.lifecycle != "deleted"
                or tombstone.attributes
                or tombstone.relationships
            ):
                fail(errors, "resource tombstone must omit mutable state")
        except ContractError as exc:
            fail(errors, f"resource tombstone violates domain contract: {exc}")

    event = documents.get(example_dir / "event.json")
    if not isinstance(event, dict):
        fail(errors, "event example must be an object")
    else:
        required = {"specversion", "id", "source", "type", "time", "subject", "tenantid", "data"}
        missing = sorted(required.difference(event))
        if missing:
            fail(errors, f"event example missing: {', '.join(missing)}")
        if event.get("specversion") != "1.0":
            fail(errors, "event example must use CloudEvents specversion 1.0")
        if isinstance(resource_document, dict):
            parsed = Resource.from_dict(resource_document)
            if event.get("subject") != parsed.identity.uid:
                fail(errors, "event example subject must match the resource example UID")
            event_data = event.get("data")
            if isinstance(event_data, dict):
                expected_hash = parsed.to_dict()
                from iip.domain.models import PlatformEvent

                if event_data.get("observationHash") != PlatformEvent.canonical_hash(expected_hash):
                    fail(errors, "event example observationHash must match the resource example")
                if event_data.get("observation") != expected_hash["metadata"].get(
                    "observation"
                ):
                    fail(
                        errors,
                        "event example observation cursor must match the resource example",
                    )

    versioned_examples = (
        ("action-approval.json", "ActionApproval"),
        ("action-execution-status.json", "ActionExecutionStatus"),
        ("action-proposal.json", "ActionProposal"),
        ("action-result.json", "ActionResult"),
        ("agent-manifest.json", "Agent"),
        ("integration-config.json", "IntegrationConfig"),
        ("plugin-manifest.json", "Plugin"),
        ("plugin-invocation.json", "PluginInvocation"),
        ("plugin-invocation-result.json", "PluginInvocationResult"),
        ("plugin-session.json", "PluginSession"),
        ("policy-decision-request.json", "PolicyDecisionRequest"),
        ("policy-decision.json", "PolicyDecision"),
        ("evidence.json", "Evidence"),
        ("context-evidence-request.json", "ContextEvidenceRequest"),
        ("context-evidence-result.json", "ContextEvidenceResult"),
        ("telemetry-evidence-request.json", "TelemetryEvidenceRequest"),
        ("telemetry-evidence-result.json", "TelemetryEvidenceResult"),
        ("log-evidence-request.json", "LogEvidenceRequest"),
        ("log-evidence-result.json", "LogEvidenceResult"),
        (
            "kubernetes-event-evidence-request.json",
            "KubernetesEventEvidenceRequest",
        ),
        (
            "kubernetes-event-evidence-result.json",
            "KubernetesEventEvidenceResult",
        ),
        ("otlp-metrics-evidence.json", "OtlpMetricsEvidence"),
        ("otlp-logs-evidence.json", "OtlpLogsEvidence"),
        ("investigation-request.json", "InvestigationRequest"),
        (
            "investigation-cancellation-request.json",
            "InvestigationCancellationRequest",
        ),
        ("investigation-status.json", "InvestigationStatus"),
        ("investigation-request-changes.json", "InvestigationRequest"),
        ("investigation-request-context.json", "InvestigationRequest"),
        ("investigation-request-kubernetes-events.json", "InvestigationRequest"),
        ("investigation-request-logs.json", "InvestigationRequest"),
        ("investigation-report.json", "InvestigationReport"),
        ("investigation-report-changes.json", "InvestigationReport"),
        ("investigation-report-context.json", "InvestigationReport"),
        ("investigation-report-telemetry.json", "InvestigationReport"),
        ("investigation-report-kubernetes-events.json", "InvestigationReport"),
        ("investigation-report-logs.json", "InvestigationReport"),
        ("investigation-report-telemetry-baseline.json", "InvestigationReport"),
        ("ingestion-freshness-report.json", "IngestionFreshnessReport"),
        (
            "telemetry-export-health-report.json",
            "TelemetryExportHealthReport",
        ),
        (
            "event-delivery-health-report.json",
            "EventDeliveryHealthReport",
        ),
        ("runtime-version-report.json", "RuntimeVersionReport"),
        ("evaluation-scenario.json", "EvaluationScenario"),
        ("resource-collection-request.json", "ResourceCollectionRequest"),
        ("resource-collection-result.json", "ResourceCollectionResult"),
        ("resource-neighborhood.json", "ResourceNeighborhood"),
        ("resource-timeline.json", "ResourceTimeline"),
        (
            "resource-change-evidence-request.json",
            "ResourceChangeEvidenceRequest",
        ),
        (
            "resource-change-evidence-result.json",
            "ResourceChangeEvidenceResult",
        ),
    )
    for name, kind in versioned_examples:
        manifest = documents.get(example_dir / name)
        validate_versioned_envelope(manifest, filename=name, kind=kind, errors=errors)

    validate_collection_examples(documents, errors)
    validate_resource_query_examples(documents, errors)
    validate_kubernetes_event_evidence_examples(documents, errors)
    validate_investigation_kubernetes_event_examples(documents, errors)
    validate_investigation_lifecycle_examples(documents, errors)
    validate_investigation_change_examples(documents, errors)
    validate_investigation_context_examples(documents, errors)
    validate_investigation_log_examples(documents, errors)
    validate_telemetry_evidence_examples(documents, errors)
    validate_log_evidence_examples(documents, errors)
    validate_resource_change_evidence_examples(documents, errors)
    validate_context_evidence_examples(documents, errors)
    validate_investigation_telemetry_examples(documents, errors)
    validate_otlp_metrics_evidence_example(documents, errors)
    validate_otlp_logs_evidence_example(documents, errors)
    validate_evaluation_scenario(documents, errors)

    policy_request = documents.get(example_dir / "policy-decision-request.json")
    policy_decision = documents.get(example_dir / "policy-decision.json")
    if isinstance(policy_request, dict) and isinstance(policy_decision, dict):
        request_metadata = policy_request.get("metadata", {})
        request_spec = policy_request.get("spec", {})
        decision_metadata = policy_decision.get("metadata", {})
        decision_spec = policy_decision.get("spec", {})
        resource = request_spec.get("resource", {})
        tenant_id = request_metadata.get("tenantId")
        if resource.get("tenantId") != tenant_id:
            fail(errors, "policy request resource tenant must match its metadata tenant")
        if decision_metadata.get("tenantId") != tenant_id:
            fail(errors, "policy decision tenant must match its request")
        if decision_spec.get("inputDigest") != canonical_digest(policy_request):
            fail(errors, "policy decision inputDigest must match its canonical request")
        if not str(decision_spec.get("policySnapshotRef", "")).startswith(
            f"policy://{tenant_id}/snapshots/"
        ):
            fail(errors, "policy decision snapshot must belong to its request tenant")

    plugin_example = documents.get(example_dir / "plugin-manifest.json")
    plugin_package = documents.get(ROOT / "plugins/examples/kubernetes-observer/plugin.json")
    if plugin_example != plugin_package:
        fail(errors, "plugin package manifest has drifted from the canonical contract example")
    if isinstance(plugin_example, dict):
        plugin_spec = plugin_example.get("spec")
        if isinstance(plugin_spec, dict):
            capabilities = set(plugin_spec.get("capabilities", []))
            interfaces = plugin_spec.get("interfaces", [])
            if isinstance(interfaces, list):
                for interface in interfaces:
                    if (
                        isinstance(interface, dict)
                        and interface.get("capability") not in capabilities
                    ):
                        fail(errors, "plugin interface must advertise its capability")
        try:
            from iip.adapters.plugin_runner import PluginTrustStore

            example_trust = PluginTrustStore.from_json(
                json.dumps(
                    {
                        "keys": [
                            {
                                "keyId": "local-development-2026",
                                "publisher": "local-development",
                                "publicKey": "iDSVA6ip-KrgSi71B7GGYnqy5F9yU-ubRVR5z9ZgMNA",
                            }
                        ]
                    }
                )
            )
            example_trust.verify(plugin_example)
        except Exception:
            fail(errors, "plugin manifest example signature must verify against its publisher key")

    action_proposal = documents.get(example_dir / "action-proposal.json")
    action_approval = documents.get(example_dir / "action-approval.json")
    action_execution = documents.get(example_dir / "action-execution-status.json")
    action_result = documents.get(example_dir / "action-result.json")
    action_workflow = documents.get(example_dir / "action-workflow.json")
    action_workflow_page = documents.get(example_dir / "action-workflow-page.json")
    plugin_session = documents.get(example_dir / "plugin-session.json")
    plugin_invocation = documents.get(example_dir / "plugin-invocation.json")
    plugin_invocation_result = documents.get(
        example_dir / "plugin-invocation-result.json"
    )
    if all(
        isinstance(item, dict)
        for item in (
            action_proposal,
            action_approval,
            action_execution,
            action_result,
        )
    ):
        proposal_metadata = action_proposal["metadata"]
        proposal_spec = action_proposal["spec"]
        approval_metadata = action_approval["metadata"]
        approval_spec = action_approval["spec"]
        result_metadata = action_result["metadata"]
        result_spec = action_result["spec"]
        execution_metadata = action_execution["metadata"]
        execution_spec = action_execution["spec"]
        if len(
            {
                proposal_metadata.get("tenantId"),
                approval_metadata.get("tenantId"),
                execution_metadata.get("tenantId"),
                result_metadata.get("tenantId"),
            }
        ) != 1:
            fail(errors, "action examples must share a tenant")
        if approval_spec.get("proposalId") != proposal_metadata.get("id"):
            fail(errors, "action approval must identify its proposal")
        if result_metadata.get("id") != proposal_metadata.get("id"):
            fail(errors, "action result must identify its proposal")
        if execution_metadata.get("id") != proposal_metadata.get("id"):
            fail(errors, "action execution status must identify its proposal")
        if result_spec.get("approvalId") != approval_metadata.get("id"):
            fail(errors, "action result must identify its approval")
        if execution_spec.get("approvalId") != approval_metadata.get("id"):
            fail(errors, "action execution status must identify its approval")
        if result_spec.get("idempotencyKey") != proposal_spec.get("idempotencyKey"):
            fail(errors, "action result must retain its proposal idempotency key")
        if result_spec.get("proposalDigest") != canonical_digest(action_proposal):
            fail(errors, "action result proposalDigest must match its proposal")
        if approval_spec.get("proposalDigest") != canonical_digest(action_proposal):
            fail(errors, "action approval proposalDigest must match its proposal")
        if execution_spec.get("proposalDigest") != canonical_digest(action_proposal):
            fail(errors, "action execution proposalDigest must match its proposal")
        if execution_spec.get("state") != result_spec.get("outcome"):
            fail(errors, "terminal action execution state must match result outcome")
        if proposal_metadata.get("actorId") == approval_metadata.get("approverId"):
            fail(errors, "action proposal and approval actors must be distinct")
        proposal_time = parse_timestamp(proposal_metadata.get("createdAt"))
        approval_time = parse_timestamp(approval_metadata.get("decidedAt"))
        result_time = parse_timestamp(result_metadata.get("completedAt"))
        expiry_time = parse_timestamp(proposal_spec.get("expiresAt"))
        if (
            None in (proposal_time, approval_time, result_time, expiry_time)
            or not proposal_time <= approval_time <= result_time <= expiry_time
        ):
            fail(errors, "action example timestamps must be created <= approved <= completed <= expiry")

    if isinstance(action_workflow, dict) and isinstance(action_proposal, dict):
        workflow_metadata = action_workflow.get("metadata", {})
        workflow_spec = action_workflow.get("spec", {})
        if workflow_metadata.get("id") != action_proposal.get("metadata", {}).get("id"):
            fail(errors, "action workflow must identify its embedded proposal")
        if workflow_spec.get("proposal") != action_proposal:
            fail(errors, "action workflow example must embed the canonical proposal example")
    if isinstance(action_workflow_page, dict) and isinstance(action_workflow, dict):
        page_items = action_workflow_page.get("spec", {}).get("items", [])
        if page_items != [action_workflow]:
            fail(errors, "action workflow page must contain the canonical workflow example")

    if isinstance(plugin_example, dict) and isinstance(plugin_session, dict):
        manifest_metadata = plugin_example.get("metadata", {})
        manifest_spec = plugin_example.get("spec", {})
        session_metadata = plugin_session.get("metadata", {})
        session_spec = plugin_session.get("spec", {})
        if (
            session_metadata.get("pluginId") != manifest_metadata.get("id")
            or session_metadata.get("pluginVersion") != manifest_metadata.get("version")
        ):
            fail(errors, "plugin session must identify its manifest")
        if session_spec.get("manifestDigest") != canonical_digest(plugin_example):
            fail(errors, "plugin session manifestDigest must match its manifest")
        if not set(session_spec.get("grantedCapabilities", [])).issubset(
            set(manifest_spec.get("capabilities", []))
        ):
            fail(errors, "plugin session cannot grant undeclared capabilities")

    if all(
        isinstance(item, dict)
        for item in (
            plugin_example,
            plugin_session,
            plugin_invocation,
            plugin_invocation_result,
        )
    ):
        manifest_metadata = plugin_example["metadata"]
        invocation_metadata = plugin_invocation["metadata"]
        invocation_spec = plugin_invocation["spec"]
        result_metadata = plugin_invocation_result["metadata"]
        result_spec = plugin_invocation_result["spec"]
        session_metadata = plugin_session["metadata"]
        session_spec = plugin_session["spec"]
        if invocation_spec.get("manifestDigest") != canonical_digest(plugin_example):
            fail(errors, "plugin invocation manifestDigest must match its manifest")
        if len(
            {
                invocation_metadata.get("tenantId"),
                result_metadata.get("tenantId"),
                session_metadata.get("tenantId"),
            }
        ) != 1:
            fail(errors, "plugin invocation, result, and session must share a tenant")
        if len(
            {
                invocation_metadata.get("sessionId"),
                result_metadata.get("sessionId"),
                session_metadata.get("id"),
            }
        ) != 1:
            fail(errors, "plugin invocation and result must identify their session")
        if result_metadata.get("id") != invocation_metadata.get("id"):
            fail(errors, "plugin result must identify its invocation")
        if (
            result_metadata.get("pluginId") != manifest_metadata.get("id")
            or result_metadata.get("pluginVersion") != manifest_metadata.get("version")
        ):
            fail(errors, "plugin result must identify its manifest")
        capability = invocation_spec.get("capability")
        method = invocation_spec.get("method")
        interfaces = plugin_example.get("spec", {}).get("interfaces", [])
        if capability not in session_spec.get("grantedCapabilities", []):
            fail(errors, "plugin invocation capability must be granted by its session")
        if not any(
            isinstance(interface, dict)
            and interface.get("capability") == capability
            and interface.get("method") == method
            for interface in interfaces
        ):
            fail(errors, "plugin invocation method must resolve in its manifest")
        created = parse_timestamp(invocation_metadata.get("createdAt"))
        deadline = parse_timestamp(invocation_metadata.get("deadline"))
        completed = parse_timestamp(result_metadata.get("completedAt"))
        expires = parse_timestamp(session_spec.get("expiresAt"))
        if None in (created, deadline, completed, expires) or not (
            created <= completed <= deadline <= expires
        ):
            fail(errors, "plugin invocation timestamps must fit its session and deadline")
        output = result_spec.get("output")
        if result_spec.get("status") == "succeeded" and isinstance(output, dict):
            collection_request = documents.get(
                example_dir / "resource-collection-request.json"
            )
            collection_result = documents.get(
                example_dir / "resource-collection-result.json"
            )
            if invocation_spec.get("input") != collection_request:
                fail(errors, "plugin invocation input must embed its declared contract example")
            if output != collection_result:
                fail(errors, "plugin result output must embed its declared contract example")
            if result_spec.get("outputDigest") != canonical_digest(output):
                fail(errors, "plugin result outputDigest must match its canonical output")
            output_bytes = len(
                json.dumps(
                    output,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            usage = result_spec.get("usage", {})
            if usage.get("outputBytes") != output_bytes:
                fail(errors, "plugin result outputBytes must match its canonical example output")

    evidence = documents.get(example_dir / "evidence.json")
    request = documents.get(example_dir / "investigation-request.json")
    report = documents.get(example_dir / "investigation-report.json")
    agent = documents.get(example_dir / "agent-manifest.json")
    if not all(
        isinstance(item, dict)
        for item in (resource_document, evidence, request, report, agent)
    ):
        return

    evidence_metadata = evidence["metadata"]
    evidence_spec = evidence["spec"]
    request_metadata = request["metadata"]
    request_spec = request["spec"]
    report_metadata = report["metadata"]
    report_spec = report["spec"]
    agent_metadata = agent["metadata"]
    agent_spec = agent["spec"]
    if not all(
        isinstance(item, dict)
        for item in (
            evidence_metadata,
            evidence_spec,
            request_metadata,
            request_spec,
            report_metadata,
            report_spec,
            agent_metadata,
            agent_spec,
        )
    ):
        return

    resource_uid = Resource.from_dict(resource_document).identity.uid
    tenants = {
        evidence_metadata.get("tenantId"),
        request_metadata.get("tenantId"),
        report_metadata.get("tenantId"),
    }
    if len(tenants) != 1:
        fail(errors, "evidence, investigation request, and report examples must share a tenant")

    evidence_resources = evidence_spec.get("resourceRefs", [])
    request_scope = request_spec.get("scope")
    request_resources = (
        request_scope.get("resourceUids", []) if isinstance(request_scope, dict) else []
    )
    if resource_uid not in evidence_resources:
        fail(errors, "evidence example must reference the canonical resource example")
    if resource_uid not in request_resources:
        fail(errors, "investigation request example must scope the canonical resource example")

    evidence_times = (
        parse_timestamp(evidence_spec.get("observedAt")),
        parse_timestamp(evidence_spec.get("retrievedAt")),
        parse_timestamp(evidence_metadata.get("recordedAt")),
    )
    if any(value is None for value in evidence_times):
        fail(errors, "evidence example timestamps must be valid date-times")
    elif not evidence_times[0] <= evidence_times[1] <= evidence_times[2]:
        fail(errors, "evidence example timestamps must be observed <= retrieved <= recorded")

    handling = evidence_spec.get("handling")
    redaction = handling.get("redaction") if isinstance(handling, dict) else None
    if isinstance(redaction, dict):
        redaction_status = redaction.get("status")
        redaction_methods = redaction.get("methods")
        if redaction_status == "applied" and not redaction_methods:
            fail(errors, "evidence example with applied redaction must name a method")
        if redaction_status == "not-required" and redaction_methods:
            fail(errors, "evidence example without redaction cannot name a method")

    if report_metadata.get("id") != request_metadata.get("id"):
        fail(errors, "investigation request and report examples must share an ID")
    if report_spec.get("requestDigest") != canonical_digest(request):
        fail(errors, "investigation report requestDigest must match the canonical request example")
    if report_spec.get("scope") != request_scope:
        fail(errors, "investigation report scope must match the request example")

    request_range = request_scope.get("timeRange") if isinstance(request_scope, dict) else None
    if isinstance(request_range, dict):
        scope_start = parse_timestamp(request_range.get("start"))
        scope_end = parse_timestamp(request_range.get("end"))
        requested_at = parse_timestamp(request_metadata.get("requestedAt"))
        if None in (scope_start, scope_end, requested_at):
            fail(errors, "investigation request example timestamps must be valid date-times")
        elif not scope_start <= scope_end <= requested_at:
            fail(errors, "investigation request time range must end no later than requestedAt")

    started_at = parse_timestamp(report_spec.get("startedAt"))
    completed_at = parse_timestamp(report_spec.get("completedAt"))
    report_created_at = parse_timestamp(report_metadata.get("createdAt"))
    request_created_at = parse_timestamp(request_metadata.get("requestedAt"))
    if (
        started_at is None
        or completed_at is None
        or report_created_at is None
        or request_created_at is None
        or not request_created_at <= started_at <= completed_at <= report_created_at
    ):
        fail(
            errors,
            "investigation report timestamps must be requested <= started <= completed <= created",
        )

    evidence_id = evidence_metadata.get("id")
    report_evidence = set(report_spec.get("evidenceIds", []))
    if evidence_id not in report_evidence:
        fail(errors, "investigation report must cite the canonical evidence example")

    citations = set()
    hypotheses = report_spec.get("hypotheses", [])
    if isinstance(hypotheses, list):
        for hypothesis in hypotheses:
            if not isinstance(hypothesis, dict):
                continue
            supporting = hypothesis.get("supportingEvidenceIds", [])
            contradicting = hypothesis.get("contradictingEvidenceIds", [])
            citations.update(supporting if isinstance(supporting, list) else [])
            citations.update(contradicting if isinstance(contradicting, list) else [])
            if hypothesis.get("disposition") != "rejected" and not supporting:
                fail(errors, "material investigation hypotheses must cite supporting evidence")
    recommendations = report_spec.get("recommendations", [])
    if isinstance(recommendations, list):
        for recommendation in recommendations:
            if isinstance(recommendation, dict):
                cited = recommendation.get("evidenceIds", [])
                citations.update(cited if isinstance(cited, list) else [])
    unknown_citations = citations.difference(report_evidence)
    if unknown_citations:
        fail(errors, "investigation report contains citations outside its evidenceIds set")

    outcome = report_spec.get("outcome")
    terminal_reason = report_spec.get("terminalReason")
    allowed_terminal_reasons = {
        "conclusive": {"sufficient-evidence"},
        "inconclusive": {
            "insufficient-evidence",
            "budget-exhausted",
            "deadline-exceeded",
        },
        "failed": {"policy-denied", "runtime-error", "no-applicable-agent"},
        "cancelled": {"cancelled"},
    }
    if terminal_reason not in allowed_terminal_reasons.get(outcome, set()):
        fail(errors, "investigation report outcome and terminalReason are inconsistent")
    if outcome == "conclusive" and not hypotheses:
        fail(errors, "conclusive investigation reports must contain a hypothesis")
    if outcome == "inconclusive" and not report_spec.get("unknowns"):
        fail(errors, "inconclusive investigation reports must contain an unknown")

    agent_selector = request_spec.get("agentSelector")
    selected_agent = report_spec.get("agent")
    if outcome in ("conclusive", "inconclusive") and not isinstance(
        selected_agent, dict
    ):
        fail(errors, "conclusive and inconclusive reports must identify the selected agent")
    if isinstance(agent_selector, dict) and isinstance(selected_agent, dict):
        for field in ("id", "version"):
            if selected_agent.get(field) != agent_selector.get(field):
                fail(errors, f"investigation report agent {field} must match the request selector")
        if (
            selected_agent.get("id") != agent_metadata.get("id")
            or selected_agent.get("version") != agent_metadata.get("version")
        ):
            fail(errors, "investigation examples must select the canonical agent manifest")
        if selected_agent.get("manifestDigest") != canonical_digest(agent):
            fail(
                errors,
                "investigation report manifestDigest must match the canonical agent manifest",
            )

    allowed_tools = request_spec.get("allowedTools", [])
    manifest_tools = agent_spec.get("tools", [])
    if not set(allowed_tools).issubset(set(manifest_tools)):
        fail(errors, "investigation request tools must be within the selected agent manifest")

    authority_order = {"read": 0, "propose": 1, "approve": 2, "execute": 3}
    manifest_authority = agent_spec.get("authority")
    manifest_level = (
        manifest_authority.get("level")
        if isinstance(manifest_authority, dict)
        else None
    )
    request_level = request_spec.get("maxAuthority")
    if authority_order.get(request_level, 99) > authority_order.get(manifest_level, -1):
        fail(errors, "investigation request authority exceeds the selected agent manifest")

    budgets = request_spec.get("budgets")
    usage = report_spec.get("usage")
    budget_usage_fields = {
        "maxToolCalls": "toolCalls",
        "maxWallTimeSeconds": "wallTimeSeconds",
        "maxModelTokens": "modelTokens",
        "maxCostUsd": "costUsd",
        "maxEvidenceItems": "evidenceItems",
        "maxIterations": "iterations",
    }
    if isinstance(budgets, dict) and isinstance(usage, dict):
        for budget_field, usage_field in budget_usage_fields.items():
            maximum = budgets.get(budget_field)
            consumed = usage.get(usage_field)
            if (
                isinstance(maximum, (int, float))
                and isinstance(consumed, (int, float))
                and consumed > maximum
            ):
                fail(errors, f"investigation report {usage_field} exceeds request {budget_field}")


def iter_imports(tree: ast.AST) -> Iterable[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module


def allowed_internal_import(path: Path, module: str) -> bool:
    relative = path.relative_to(SRC / "iip")
    area = relative.parts[0]
    if area.endswith(".py"):
        area = "root"
    allowed = {
        "domain": ("iip.domain",),
        "application": ("iip.domain", "iip.application"),
        "adapters": ("iip.domain", "iip.application", "iip.adapters"),
        "surfaces": ("iip.application", "iip.bootstrap", "iip.surfaces"),
        "root": ("iip.domain", "iip.application", "iip.adapters", "iip.bootstrap"),
    }
    return module.startswith(allowed[area])


def validate_python_boundaries(errors: List[str]) -> None:
    for path in sorted((SRC / "iip").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as exc:
            fail(errors, f"invalid Python {path.relative_to(ROOT)}: {exc}")
            continue
        for module in iter_imports(tree):
            if module == "iip" or module.startswith("iip."):
                if not allowed_internal_import(path, module):
                    fail(
                        errors,
                        f"forbidden import in {path.relative_to(ROOT)}: {module}",
                    )

    for path in sorted((ROOT / "sdks").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module in iter_imports(tree):
            if module == "iip" or module.startswith("iip."):
                fail(errors, f"SDK imports server internals in {path.relative_to(ROOT)}: {module}")

    for path in sorted((ROOT / "plugins").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module in iter_imports(tree):
            if module == "iip" or module.startswith("iip."):
                fail(
                    errors,
                    f"plugin imports server internals in {path.relative_to(ROOT)}: {module}",
                )


def validate_markdown_links(errors: List[str]) -> None:
    for path in sorted(ROOT.rglob("*.md")):
        for target in LINK.findall(path.read_text(encoding="utf-8")):
            target = target.strip()
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            clean = target.split("#", 1)[0]
            if not clean:
                continue
            resolved = (path.parent / clean).resolve()
            if not resolved.exists():
                fail(errors, f"broken link in {path.relative_to(ROOT)}: {target}")


def main() -> int:
    errors: List[str] = []
    validate_required_paths(errors)
    documents = load_json_documents(errors)
    validate_schema_metadata(documents, errors)
    validate_authentication_boundary(documents, errors)
    validate_examples(documents, errors)
    validate_python_boundaries(errors)
    validate_markdown_links(errors)

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"validation failed with {len(errors)} error(s)", file=sys.stderr)
        return 1
    print(f"validated {len(documents)} JSON documents, package boundaries, and documentation links")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
