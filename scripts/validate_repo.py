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
    "docs/research/opensre-reference-analysis.md",
    "docs/research/brand/README.md",
    "docs/roadmap/initial-roadmap.md",
    "contracts/schemas/resource.schema.json",
    "contracts/schemas/event.schema.json",
    "contracts/schemas/agent-manifest.schema.json",
    "contracts/schemas/plugin-manifest.schema.json",
    "contracts/schemas/evidence.schema.json",
    "contracts/schemas/investigation-request.schema.json",
    "contracts/schemas/investigation-report.schema.json",
    "contracts/examples/evidence.json",
    "contracts/examples/investigation-request.json",
    "contracts/examples/investigation-report.json",
    "docs/specifications/evidence-contract.md",
    "docs/specifications/investigation-contract.md",
    "requirements/verify.in",
    "requirements/verify.txt",
    "scripts/validate_schemas.py",
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

    versioned_examples = (
        ("agent-manifest.json", "Agent"),
        ("plugin-manifest.json", "Plugin"),
        ("evidence.json", "Evidence"),
        ("investigation-request.json", "InvestigationRequest"),
        ("investigation-report.json", "InvestigationReport"),
    )
    for name, kind in versioned_examples:
        manifest = documents.get(example_dir / name)
        validate_versioned_envelope(manifest, filename=name, kind=kind, errors=errors)

    plugin_example = documents.get(example_dir / "plugin-manifest.json")
    plugin_package = documents.get(ROOT / "plugins/examples/kubernetes-observer/plugin.json")
    if plugin_example != plugin_package:
        fail(errors, "plugin package manifest has drifted from the canonical contract example")

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
