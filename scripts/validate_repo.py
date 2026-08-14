#!/usr/bin/env python3
"""Fast, dependency-free repository contract and architecture checks."""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path
from typing import Iterable, List, Mapping, Sequence


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

    for name, kind in (("agent-manifest.json", "Agent"), ("plugin-manifest.json", "Plugin")):
        manifest = documents.get(example_dir / name)
        if not isinstance(manifest, dict):
            fail(errors, f"{name} must be an object")
            continue
        if manifest.get("apiVersion") != "iip.platform/v1alpha1" or manifest.get("kind") != kind:
            fail(errors, f"{name} has the wrong version or kind")
        if not isinstance(manifest.get("metadata"), dict) or not isinstance(manifest.get("spec"), dict):
            fail(errors, f"{name} must contain metadata and spec objects")

    plugin_example = documents.get(example_dir / "plugin-manifest.json")
    plugin_package = documents.get(ROOT / "plugins/examples/kubernetes-observer/plugin.json")
    if plugin_example != plugin_package:
        fail(errors, "plugin package manifest has drifted from the canonical contract example")


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
