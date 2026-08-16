#!/usr/bin/env python3
"""Fast, dependency-free repository contract and architecture checks."""

from __future__ import annotations

import ast
import hashlib
import json
import math
import re
import sys
from datetime import datetime
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
    "docs/architecture/evidence-collection-pipeline.md",
    "docs/architecture/ingestion-freshness-telemetry.md",
    "docs/architecture/opentelemetry-portability.md",
    "docs/research/opensre-reference-analysis.md",
    "docs/research/brand/README.md",
    "docs/roadmap/initial-roadmap.md",
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
    "docs/operations/opentelemetry-export.md",
    "docs/operations/prometheus-evidence.md",
    "docs/operations/kubernetes-event-evidence.md",
    "docs/operations/otlp-metrics-receiver.md",
    "docs/operations/postgresql-backup-restore.md",
    "docs/operations/measurements/postgresql-backup-restore.json",
    "contracts/schemas/resource.schema.json",
    "contracts/schemas/integration-config.schema.json",
    "contracts/schemas/action-proposal.schema.json",
    "contracts/schemas/action-approval.schema.json",
    "contracts/schemas/action-result.schema.json",
    "contracts/schemas/plugin-session.schema.json",
    "contracts/schemas/resource-collection-request.schema.json",
    "contracts/schemas/resource-collection-result.schema.json",
    "contracts/schemas/resource-neighborhood.schema.json",
    "contracts/schemas/resource-timeline.schema.json",
    "contracts/schemas/page-info.schema.json",
    "contracts/schemas/error.schema.json",
    "contracts/schemas/event.schema.json",
    "contracts/schemas/agent-manifest.schema.json",
    "contracts/schemas/plugin-manifest.schema.json",
    "contracts/schemas/evidence.schema.json",
    "contracts/schemas/kubernetes-event-evidence-request.schema.json",
    "contracts/schemas/kubernetes-event-evidence-result.schema.json",
    "contracts/schemas/telemetry-evidence-request.schema.json",
    "contracts/schemas/telemetry-evidence-result.schema.json",
    "contracts/schemas/otlp-metrics-evidence.schema.json",
    "contracts/schemas/investigation-request.schema.json",
    "contracts/schemas/investigation-report.schema.json",
    "contracts/schemas/ingestion-freshness-report.schema.json",
    "contracts/schemas/evaluation-scenario.schema.json",
    "contracts/examples/evidence.json",
    "contracts/examples/kubernetes-event-evidence-request.json",
    "contracts/examples/kubernetes-event-evidence-result.json",
    "contracts/examples/telemetry-evidence-request.json",
    "contracts/examples/telemetry-evidence-result.json",
    "contracts/examples/otlp-metrics-evidence.json",
    "contracts/examples/integration-config.json",
    "contracts/examples/action-proposal.json",
    "contracts/examples/action-approval.json",
    "contracts/examples/action-result.json",
    "contracts/examples/plugin-session.json",
    "contracts/examples/investigation-request.json",
    "contracts/examples/investigation-request-kubernetes-events.json",
    "contracts/examples/investigation-request-telemetry.json",
    "contracts/examples/investigation-request-telemetry-baseline.json",
    "contracts/examples/investigation-report.json",
    "contracts/examples/investigation-report-kubernetes-events.json",
    "contracts/examples/investigation-report-telemetry.json",
    "contracts/examples/investigation-report-telemetry-baseline.json",
    "contracts/examples/ingestion-freshness-report.json",
    "contracts/examples/evaluation-scenario.json",
    "contracts/examples/resource-collection-request.json",
    "contracts/examples/resource-collection-result.json",
    "contracts/examples/resource-tombstone.json",
    "contracts/examples/resource-neighborhood.json",
    "contracts/examples/resource-timeline.json",
    "contracts/examples/page-info.json",
    "contracts/examples/error.json",
    "docs/specifications/evidence-contract.md",
    "docs/specifications/kubernetes-event-evidence-contract.md",
    "docs/specifications/telemetry-evidence-contract.md",
    "docs/specifications/otlp-metrics-evidence-contract.md",
    "docs/specifications/integration-config-contract.md",
    "docs/specifications/action-contract.md",
    "docs/specifications/plugin-session-contract.md",
    "docs/specifications/investigation-contract.md",
    "docs/specifications/ingestion-freshness-contract.md",
    "docs/specifications/evaluation-scenario-contract.md",
    "docs/specifications/resource-collection-contract.md",
    "docs/specifications/resource-query-contract.md",
    "requirements/verify.in",
    "requirements/verify.txt",
    "scripts/validate_schemas.py",
    "src/iip/application/collect_evidence.py",
    "src/iip/application/kubernetes_event_evidence.py",
    "src/iip/application/telemetry_evidence.py",
    "src/iip/application/ingest_otlp_metrics.py",
    "src/iip/application/observe_ingestion.py",
    "src/iip/adapters/auth.py",
    "src/iip/adapters/evidence.py",
    "src/iip/adapters/otel.py",
    "src/iip/adapters/prometheus.py",
    "src/iip/adapters/kubernetes_events.py",
    "src/iip/adapters/otlp_receiver.py",
    "src/iip/adapters/postgres/migrations/0006_source_checkpoint_provider_cursors.sql",
    "tests/test_evidence_collection.py",
    "tests/test_kubernetes_event_evidence.py",
    "tests/test_telemetry_evidence.py",
    "tests/test_ingestion_freshness.py",
    "tests/test_authentication.py",
    "tests/test_operational_workflows.py",
    "tests/test_otel_metrics.py",
    "tests/test_otel_collector.py",
    "tests/test_prometheus_backend.py",
    "tests/test_prometheus_integration.py",
    "tests/test_kubernetes_events_backend.py",
    "tests/test_kubernetes_events_integration.py",
    "tests/test_otlp_receiver.py",
    "tests/test_otlp_receiver_integration.py",
    "scripts/test_otel.sh",
    "scripts/test_prometheus.sh",
    "scripts/test_kubernetes_events.sh",
    "scripts/test_otlp_receiver.sh",
    "deploy/docker-compose.otel.yml",
    "deploy/docker-compose.prometheus.yml",
    "deploy/docker-compose.otlp-receiver.yml",
    "deploy/otel/collector-test.yaml",
    "deploy/prometheus/prometheus-test.yml",
    "deploy/prometheus/integrations.example.json",
    "deploy/kubernetes-events/integrations.example.json",
    "deploy/otlp/receiver-channels.example.json",
    "scripts/test_kubernetes_live.sh",
    "scripts/run_reference_workflow.py",
    "scripts/backup_restore_experiment.py",
    "api/openapi/control-plane.openapi.json",
    "deploy/helm/infra-intelligence/Chart.yaml",
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
    fixture_evidence_ids = set(evidence_by_id)
    if not required.union(red_herrings).issubset(fixture_evidence_ids):
        fail(errors, "evaluation scenario expected evidence IDs must resolve")
    if required.intersection(red_herrings):
        fail(errors, "evaluation scenario required evidence and red herrings must be disjoint")
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
        ("action-proposal.json", "ActionProposal"),
        ("action-result.json", "ActionResult"),
        ("agent-manifest.json", "Agent"),
        ("integration-config.json", "IntegrationConfig"),
        ("plugin-manifest.json", "Plugin"),
        ("plugin-session.json", "PluginSession"),
        ("evidence.json", "Evidence"),
        ("telemetry-evidence-request.json", "TelemetryEvidenceRequest"),
        ("telemetry-evidence-result.json", "TelemetryEvidenceResult"),
        (
            "kubernetes-event-evidence-request.json",
            "KubernetesEventEvidenceRequest",
        ),
        (
            "kubernetes-event-evidence-result.json",
            "KubernetesEventEvidenceResult",
        ),
        ("otlp-metrics-evidence.json", "OtlpMetricsEvidence"),
        ("investigation-request.json", "InvestigationRequest"),
        ("investigation-request-kubernetes-events.json", "InvestigationRequest"),
        ("investigation-report.json", "InvestigationReport"),
        ("investigation-report-telemetry.json", "InvestigationReport"),
        ("investigation-report-kubernetes-events.json", "InvestigationReport"),
        ("investigation-report-telemetry-baseline.json", "InvestigationReport"),
        ("ingestion-freshness-report.json", "IngestionFreshnessReport"),
        ("evaluation-scenario.json", "EvaluationScenario"),
        ("resource-collection-request.json", "ResourceCollectionRequest"),
        ("resource-collection-result.json", "ResourceCollectionResult"),
        ("resource-neighborhood.json", "ResourceNeighborhood"),
        ("resource-timeline.json", "ResourceTimeline"),
    )
    for name, kind in versioned_examples:
        manifest = documents.get(example_dir / name)
        validate_versioned_envelope(manifest, filename=name, kind=kind, errors=errors)

    validate_collection_examples(documents, errors)
    validate_resource_query_examples(documents, errors)
    validate_kubernetes_event_evidence_examples(documents, errors)
    validate_investigation_kubernetes_event_examples(documents, errors)
    validate_telemetry_evidence_examples(documents, errors)
    validate_investigation_telemetry_examples(documents, errors)
    validate_otlp_metrics_evidence_example(documents, errors)
    validate_evaluation_scenario(documents, errors)

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

    action_proposal = documents.get(example_dir / "action-proposal.json")
    action_approval = documents.get(example_dir / "action-approval.json")
    action_result = documents.get(example_dir / "action-result.json")
    plugin_session = documents.get(example_dir / "plugin-session.json")
    if all(
        isinstance(item, dict)
        for item in (action_proposal, action_approval, action_result)
    ):
        proposal_metadata = action_proposal["metadata"]
        proposal_spec = action_proposal["spec"]
        approval_metadata = action_approval["metadata"]
        approval_spec = action_approval["spec"]
        result_metadata = action_result["metadata"]
        result_spec = action_result["spec"]
        if len(
            {
                proposal_metadata.get("tenantId"),
                approval_metadata.get("tenantId"),
                result_metadata.get("tenantId"),
            }
        ) != 1:
            fail(errors, "action examples must share a tenant")
        if approval_spec.get("proposalId") != proposal_metadata.get("id"):
            fail(errors, "action approval must identify its proposal")
        if result_metadata.get("id") != proposal_metadata.get("id"):
            fail(errors, "action result must identify its proposal")
        if result_spec.get("approvalId") != approval_metadata.get("id"):
            fail(errors, "action result must identify its approval")
        if result_spec.get("idempotencyKey") != proposal_spec.get("idempotencyKey"):
            fail(errors, "action result must retain its proposal idempotency key")
        if result_spec.get("proposalDigest") != canonical_digest(action_proposal):
            fail(errors, "action result proposalDigest must match its proposal")
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
