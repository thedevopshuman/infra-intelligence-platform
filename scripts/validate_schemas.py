#!/usr/bin/env python3
"""Validate every contract example with the pinned Draft 2020-12 runtime."""

from __future__ import annotations

import json
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import List, Mapping, Optional, Sequence, Tuple

try:
    from jsonschema import Draft202012Validator, FormatChecker
    from jsonschema.exceptions import SchemaError
    from referencing import Registry, Resource as ReferencingResource
except ModuleNotFoundError:
    print(
        "ERROR: JSON Schema verification dependencies are missing; "
        "install requirements/verify.txt",
        file=sys.stderr,
    )
    raise SystemExit(2)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "contracts" / "schemas"
EXAMPLE_DIR = ROOT / "contracts" / "examples"
EXPECTED_JSONSCHEMA_VERSION = "4.25.1"
REQUIRED_FORMATS = frozenset({"date-time", "uri-reference"})

SCHEMA_EXAMPLES: Mapping[str, Tuple[str, ...]] = {
    "action-approval.schema.json": ("action-approval.json",),
    "action-execution-status.schema.json": ("action-execution-status.json",),
    "action-proposal.schema.json": ("action-proposal.json",),
    "action-result.schema.json": ("action-result.json",),
    "action-workflow.schema.json": ("action-workflow.json",),
    "action-workflow-page.schema.json": ("action-workflow-page.json",),
    "agent-manifest.schema.json": ("agent-manifest.json",),
    "credential-lease-request.schema.json": ("credential-lease-request.json",),
    "credential-lease.schema.json": ("credential-lease.json",),
    "context-evidence-request.schema.json": ("context-evidence-request.json",),
    "context-evidence-result.schema.json": ("context-evidence-result.json",),
    "evaluation-scenario.schema.json": ("evaluation-scenario.json",),
    "event.schema.json": ("event.json",),
    "error.schema.json": ("error.json",),
    "evidence.schema.json": ("evidence.json",),
    "investigation-report.schema.json": (
        "investigation-report.json",
        "investigation-report-changes.json",
        "investigation-report-context.json",
        "investigation-report-kubernetes-events.json",
        "investigation-report-logs.json",
        "investigation-report-telemetry.json",
        "investigation-report-telemetry-baseline.json",
    ),
    "investigation-cancellation-request.schema.json": (
        "investigation-cancellation-request.json",
    ),
    "investigation-status.schema.json": ("investigation-status.json",),
    "investigation-request.schema.json": (
        "investigation-request.json",
        "investigation-request-changes.json",
        "investigation-request-context.json",
        "investigation-request-kubernetes-events.json",
        "investigation-request-logs.json",
        "investigation-request-telemetry.json",
        "investigation-request-telemetry-baseline.json",
    ),
    "ingestion-freshness-report.schema.json": (
        "ingestion-freshness-report.json",
    ),
    "integration-config.schema.json": ("integration-config.json",),
    "log-evidence-request.schema.json": ("log-evidence-request.json",),
    "log-evidence-result.schema.json": ("log-evidence-result.json",),
    "kubernetes-event-evidence-request.schema.json": (
        "kubernetes-event-evidence-request.json",
    ),
    "kubernetes-event-evidence-result.schema.json": (
        "kubernetes-event-evidence-result.json",
    ),
    "otlp-metrics-evidence.schema.json": ("otlp-metrics-evidence.json",),
    "otlp-logs-evidence.schema.json": ("otlp-logs-evidence.json",),
    "telemetry-evidence-request.schema.json": ("telemetry-evidence-request.json",),
    "telemetry-evidence-result.schema.json": ("telemetry-evidence-result.json",),
    "plugin-manifest.schema.json": ("plugin-manifest.json",),
    "plugin-session.schema.json": ("plugin-session.json",),
    "page-info.schema.json": ("page-info.json",),
    "resource-collection-request.schema.json": ("resource-collection-request.json",),
    "resource-collection-result.schema.json": ("resource-collection-result.json",),
    "resource-neighborhood.schema.json": ("resource-neighborhood.json",),
    "resource-change-evidence-request.schema.json": (
        "resource-change-evidence-request.json",
    ),
    "resource-change-evidence-result.schema.json": (
        "resource-change-evidence-result.json",
    ),
    "resource.schema.json": ("resource.json", "resource-tombstone.json"),
    "session-context.schema.json": ("session-context.json",),
    "resource-timeline.schema.json": ("resource-timeline.json",),
}


def installed_runtime_errors() -> List[str]:
    errors: List[str] = []
    try:
        installed_version = version("jsonschema")
    except PackageNotFoundError:
        return ["jsonschema is not installed"]
    if installed_version != EXPECTED_JSONSCHEMA_VERSION:
        errors.append(
            "jsonschema version must be "
            f"{EXPECTED_JSONSCHEMA_VERSION}, found {installed_version}"
        )

    checker = FormatChecker()
    missing_formats = sorted(REQUIRED_FORMATS.difference(checker.checkers))
    if missing_formats:
        errors.append(
            "jsonschema format extras are missing checkers for: "
            + ", ".join(missing_formats)
        )
    return errors


def load_json(path: Path, errors: List[str]) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        errors.append(f"cannot read {path.relative_to(ROOT)}: {exc}")
    except json.JSONDecodeError as exc:
        errors.append(f"invalid JSON {path.relative_to(ROOT)}: {exc}")
    return None


def json_location(parts: Sequence[object]) -> str:
    location = "$"
    for part in parts:
        if isinstance(part, int):
            location += f"[{part}]"
        else:
            location += f".{part}"
    return location


def contract_registry() -> Registry[object]:
    """Build a registry so schemas can reference other public contracts by ID."""

    resources = []
    for path in sorted(SCHEMA_DIR.glob("*.schema.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        identifier = document.get("$id")
        if isinstance(identifier, str):
            resources.append((identifier, ReferencingResource.from_contents(document)))
    return Registry().with_resources(resources)


def instance_validation_errors(
    schema: Mapping[str, object],
    instance: object,
    *,
    label: str,
    registry: Optional[Registry[object]] = None,
) -> List[str]:
    validator = Draft202012Validator(
        schema,
        format_checker=FormatChecker(),
        registry=registry or contract_registry(),
    )
    return [
        f"{label} {json_location(error.absolute_path)}: {error.message}"
        for error in sorted(
            validator.iter_errors(instance),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
    ]


def validate_mapping_coverage(errors: List[str]) -> None:
    actual_schemas = {path.name for path in SCHEMA_DIR.glob("*.schema.json")}
    mapped_schemas = set(SCHEMA_EXAMPLES)
    for name in sorted(actual_schemas.difference(mapped_schemas)):
        errors.append(f"schema has no validation mapping: contracts/schemas/{name}")
    for name in sorted(mapped_schemas.difference(actual_schemas)):
        errors.append(f"validation mapping references missing schema: {name}")

    actual_examples = {path.name for path in EXAMPLE_DIR.glob("*.json")}
    mapped_examples = {
        example
        for examples in SCHEMA_EXAMPLES.values()
        for example in examples
    }
    for name in sorted(actual_examples.difference(mapped_examples)):
        errors.append(f"contract example has no schema mapping: contracts/examples/{name}")
    for name in sorted(mapped_examples.difference(actual_examples)):
        errors.append(f"schema mapping references missing example: {name}")


def validate_repository() -> List[str]:
    errors = installed_runtime_errors()
    validate_mapping_coverage(errors)
    if errors:
        return errors

    registry = contract_registry()
    for schema_name, example_names in SCHEMA_EXAMPLES.items():
        schema_path = SCHEMA_DIR / schema_name
        schema_document = load_json(schema_path, errors)
        if not isinstance(schema_document, dict):
            continue
        try:
            Draft202012Validator.check_schema(schema_document)
        except SchemaError as exc:
            errors.append(
                f"invalid Draft 2020-12 schema {schema_path.relative_to(ROOT)}: "
                f"{exc.message}"
            )
            continue

        for example_name in example_names:
            example_path = EXAMPLE_DIR / example_name
            example_document = load_json(example_path, errors)
            if example_document is None:
                continue
            errors.extend(
                instance_validation_errors(
                    schema_document,
                    example_document,
                    label=str(example_path.relative_to(ROOT)),
                    registry=registry,
                )
            )
    return errors


def main() -> int:
    errors = validate_repository()
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(
            f"schema validation failed with {len(errors)} error(s)",
            file=sys.stderr,
        )
        return 1

    example_count = sum(len(names) for names in SCHEMA_EXAMPLES.values())
    print(
        f"validated {len(SCHEMA_EXAMPLES)} Draft 2020-12 schemas against "
        f"{example_count} examples with jsonschema {EXPECTED_JSONSCHEMA_VERSION}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
