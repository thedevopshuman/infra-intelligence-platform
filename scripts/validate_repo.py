#!/usr/bin/env python3
"""Fast, dependency-free repository contract and architecture checks."""

from __future__ import annotations

import ast
import hashlib
import json
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
    "docs/architecture/evidence-collection-pipeline.md",
    "docs/research/opensre-reference-analysis.md",
    "docs/research/brand/README.md",
    "docs/roadmap/initial-roadmap.md",
    "contracts/schemas/resource.schema.json",
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
    "contracts/schemas/investigation-request.schema.json",
    "contracts/schemas/investigation-report.schema.json",
    "contracts/schemas/evaluation-scenario.schema.json",
    "contracts/examples/evidence.json",
    "contracts/examples/investigation-request.json",
    "contracts/examples/investigation-report.json",
    "contracts/examples/evaluation-scenario.json",
    "contracts/examples/resource-collection-request.json",
    "contracts/examples/resource-collection-result.json",
    "contracts/examples/resource-neighborhood.json",
    "contracts/examples/resource-timeline.json",
    "contracts/examples/page-info.json",
    "contracts/examples/error.json",
    "docs/specifications/evidence-contract.md",
    "docs/specifications/investigation-contract.md",
    "docs/specifications/evaluation-scenario-contract.md",
    "docs/specifications/resource-collection-contract.md",
    "docs/specifications/resource-query-contract.md",
    "requirements/verify.in",
    "requirements/verify.txt",
    "scripts/validate_schemas.py",
    "src/iip/application/collect_evidence.py",
    "src/iip/adapters/evidence.py",
    "tests/test_evidence_collection.py",
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
        ("agent-manifest.json", "Agent"),
        ("plugin-manifest.json", "Plugin"),
        ("evidence.json", "Evidence"),
        ("investigation-request.json", "InvestigationRequest"),
        ("investigation-report.json", "InvestigationReport"),
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
