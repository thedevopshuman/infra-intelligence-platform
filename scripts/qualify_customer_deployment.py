#!/usr/bin/env python3
"""Aggregate exact customer-cluster install, health, ingress, and continuity evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker

import deployment_diagnostics as diagnostics
import deployment_preflight as preflight
import qualify_customer_continuity as continuity
import qualify_ingress_availability as ingress


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "contracts/schemas/customer-deployment-qualification-report.schema.json"
API_VERSION = "iip.platform/v1alpha1"
KIND = "CustomerDeploymentQualificationReport"
QUALIFICATION_LEVEL = "single-cluster-control-plane-v1"
REPORT_ID = re.compile(r"^cdq_[a-f0-9]{32}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024
MAX_VALUES_FILES = 8
EVIDENCE_DEFINITIONS = (
    (
        "live-install-preflight",
        "CustomerDeploymentPreflightReport",
        "install-ready",
        "customer-deployment-qualification.preflight.not-install-ready",
    ),
    (
        "post-install-health",
        "DeploymentDiagnosticReport",
        "healthy",
        "customer-deployment-qualification.diagnostic.not-healthy",
    ),
    (
        "customer-ingress",
        "IngressAvailabilityQualificationReport",
        "qualified",
        "customer-deployment-qualification.ingress.not-qualified",
    ),
    (
        "control-plane-continuity",
        "CustomerContinuityQualificationReport",
        "qualified",
        "customer-deployment-qualification.continuity.not-qualified",
    ),
)
CHECK_IDS = (
    "source-binding",
    "profile-binding",
    "exact-release-identity",
    "explicit-current-cluster",
    "live-install-preflight",
    "post-continuity-health",
    "customer-ingress",
    "control-plane-continuity",
    "continuity-ingress-chain",
    "evidence-order",
    "evidence-freshness",
    "minimized-output",
)
LIMITATIONS = (
    "single-customer-cluster",
    "planned-single-api-pod-disruption",
    "point-in-time-dependency-observation",
    "artifact-publication-signatures-vulnerabilities-not-qualified",
    "database-ha-dr-not-qualified",
    "worker-receiver-continuity-not-qualified",
    "customer-integrations-and-live-ai-not-qualified",
    "regional-slo-and-capacity-not-qualified",
    "design-partner-legal-brand-governance-not-qualified",
)
FORBIDDEN_RETAINED_KEYS = frozenset(
    {
        "baseUrl",
        "context",
        "credential",
        "deploymentName",
        "host",
        "namespace",
        "releaseName",
        "repository",
        "secret",
        "token",
        "url",
    }
)


class CustomerDeploymentQualificationError(RuntimeError):
    """A stable customer deployment qualification failure."""


def _fail(code: str) -> None:
    raise CustomerDeploymentQualificationError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest_value(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        _fail("customer-deployment-qualification.time.invalid")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: object, code: str) -> datetime:
    if not isinstance(value, str):
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None:
        _fail(code)
    return parsed.astimezone(timezone.utc)


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _load_document(path: Path, code: str) -> tuple[Mapping[str, Any], str]:
    candidate = path.expanduser()
    try:
        if candidate.is_symlink() or not candidate.is_file():
            _fail(code)
        payload = candidate.read_bytes()
    except OSError:
        _fail(code)
    if len(payload) > MAX_DOCUMENT_BYTES:
        _fail(code)
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail(code)
    if not isinstance(document, Mapping):
        _fail(code)
    return document, "sha256:" + hashlib.sha256(payload).hexdigest()


def _file_digest(path: Path, code: str) -> str:
    try:
        payload = path.expanduser().read_bytes()
    except OSError:
        _fail(code)
    if len(payload) > MAX_DOCUMENT_BYTES:
        _fail(code)
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _write_report(path: Path, report: Mapping[str, object]) -> None:
    candidate = path.expanduser()
    if candidate.is_symlink():
        _fail("customer-deployment-qualification.output.invalid")
    destination = candidate.absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        os.replace(temporary, destination)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        _fail("customer-deployment-qualification.output.invalid")


def _report_identifier(
    metadata_without_id: Mapping[str, object], spec: Mapping[str, object]
) -> str:
    return "cdq_" + hashlib.sha256(
        _canonical({"metadata": metadata_without_id, "spec": spec})
    ).hexdigest()[:32]


def _check(identifier: str, passed: bool, error_code: str) -> dict[str, str]:
    result = {"id": identifier, "status": "passed" if passed else "failed"}
    if not passed:
        result["errorCode"] = error_code
    return result


def _report_status(document: Mapping[str, Any]) -> str:
    spec = _mapping(
        document.get("spec"), "customer-deployment-qualification.input.invalid"
    )
    status = spec.get("status")
    if not isinstance(status, str):
        _fail("customer-deployment-qualification.input.invalid")
    return status


def _evidence(
    *,
    identifier: str,
    contract_kind: str,
    success_status: str,
    error_code: str,
    document: Mapping[str, Any],
    digest: str,
) -> dict[str, str]:
    metadata = _mapping(
        document.get("metadata"), "customer-deployment-qualification.input.invalid"
    )
    report_id = metadata.get("id")
    status = _report_status(document)
    if not isinstance(report_id, str) or DIGEST.fullmatch(digest) is None:
        _fail("customer-deployment-qualification.input.invalid")
    result = {
        "id": identifier,
        "contractKind": contract_kind,
        "reportId": report_id,
        "reportDigest": digest,
        "observedStatus": status,
        "status": "passed" if status == success_status else "rejected",
    }
    if status != success_status:
        result["errorCode"] = error_code
    return result


def _subject_and_bindings(
    *,
    preflight_report: Mapping[str, Any],
    diagnostic_report: Mapping[str, Any],
    ingress_report: Mapping[str, Any],
    continuity_report: Mapping[str, Any],
    context: str,
    namespace: str,
    release_name: str,
    deployment_name: str,
    image_digest: str,
    current_cluster_environment: Mapping[str, str] | None,
) -> tuple[dict[str, str], dict[str, str]]:
    preflight_metadata = _mapping(
        preflight_report.get("metadata"),
        "customer-deployment-qualification.preflight.invalid",
    )
    preflight_spec = _mapping(
        preflight_report.get("spec"),
        "customer-deployment-qualification.preflight.invalid",
    )
    preflight_profile = _mapping(
        preflight_spec.get("profile"),
        "customer-deployment-qualification.preflight.invalid",
    )
    preflight_environment = _mapping(
        preflight_spec.get("environment"),
        "customer-deployment-qualification.preflight.invalid",
    )
    diagnostic_metadata = _mapping(
        diagnostic_report.get("metadata"),
        "customer-deployment-qualification.diagnostic.invalid",
    )
    diagnostic_spec = _mapping(
        diagnostic_report.get("spec"),
        "customer-deployment-qualification.diagnostic.invalid",
    )
    diagnostic_identity = _mapping(
        diagnostic_spec.get("expectedIdentity"),
        "customer-deployment-qualification.diagnostic.invalid",
    )
    ingress_metadata = _mapping(
        ingress_report.get("metadata"),
        "customer-deployment-qualification.ingress.invalid",
    )
    ingress_spec = _mapping(
        ingress_report.get("spec"),
        "customer-deployment-qualification.ingress.invalid",
    )
    continuity_metadata = _mapping(
        continuity_report.get("metadata"),
        "customer-deployment-qualification.continuity.invalid",
    )
    continuity_spec = _mapping(
        continuity_report.get("spec"),
        "customer-deployment-qualification.continuity.invalid",
    )
    continuity_subject = _mapping(
        continuity_spec.get("subject"),
        "customer-deployment-qualification.continuity.invalid",
    )
    continuity_bindings = _mapping(
        continuity_spec.get("bindings"),
        "customer-deployment-qualification.continuity.invalid",
    )

    revisions = {
        preflight_metadata.get("sourceRevision"),
        diagnostic_metadata.get("sourceRevision"),
        ingress_metadata.get("sourceRevision"),
        continuity_metadata.get("sourceRevision"),
        continuity_subject.get("sourceRevision"),
    }
    if len(revisions) != 1 or None in revisions:
        _fail("customer-deployment-qualification.source.crossed")
    revision = str(next(iter(revisions)))
    expected_identity = {
        "applicationVersion": continuity_subject.get("applicationVersion"),
        "chartVersion": continuity_subject.get("chartVersion"),
        "imageDigest": image_digest,
    }
    if (
        preflight_profile.get("applicationVersion")
        != continuity_subject.get("applicationVersion")
        or preflight_profile.get("chartVersion")
        != continuity_subject.get("chartVersion")
        or diagnostic_identity != expected_identity
        or continuity_subject.get("imageDigest") != image_digest
        or continuity_subject.get("contractsApiVersion") != API_VERSION
        or ingress_spec.get("targetIdentity")
        != {
            **{key: continuity_subject.get(key) for key in (
                "applicationVersion",
                "contractsApiVersion",
                "requiredMigration",
                "sourceRevision",
                "chartVersion",
                "imageDigest",
            )},
            "buildMode": "release",
        }
    ):
        _fail("customer-deployment-qualification.release.crossed")
    if preflight_environment.get("mode") != "cluster":
        _fail("customer-deployment-qualification.preflight.not-live")
    expected_context = continuity._digest_value({"kubernetesContext": context})
    expected_namespace = continuity._digest_value({"namespace": namespace})
    expected_deployment = continuity._digest_value(
        {
            "context": context,
            "namespace": namespace,
            "deployment": deployment_name,
        }
    )
    if (
        continuity_bindings.get("kubernetesContextBindingDigest")
        != expected_context
        or continuity_bindings.get("namespaceBindingDigest")
        != expected_namespace
        or continuity_bindings.get("deploymentBindingDigest")
        != expected_deployment
        or preflight_environment.get("namespaceDigest")
        != preflight._digest(namespace)
    ):
        _fail("customer-deployment-qualification.target.crossed")
    diagnostic_binding, _ = diagnostics._target(
        context=context,
        namespace=namespace,
        release_name=release_name,
        image_digest=image_digest,
    )
    if diagnostic_spec.get("targetBindingDigest") != diagnostic_binding:
        _fail("customer-deployment-qualification.target.crossed")
    if current_cluster_environment is not None:
        for key in ("kubernetesVersion", "clusterBindingDigest", "namespaceDigest"):
            if preflight_environment.get(key) != current_cluster_environment.get(key):
                _fail("customer-deployment-qualification.cluster.changed")

    profile_name = preflight_profile.get("name")
    required_migration = continuity_subject.get("requiredMigration")
    if not isinstance(profile_name, str) or not isinstance(required_migration, str):
        _fail("customer-deployment-qualification.profile.invalid")
    subject = {
        "profile": profile_name,
        "applicationVersion": str(continuity_subject["applicationVersion"]),
        "chartVersion": str(continuity_subject["chartVersion"]),
        "contractsApiVersion": API_VERSION,
        "requiredMigration": required_migration,
        "sourceRevision": revision,
        "imageDigest": image_digest,
    }
    bindings = {
        "clusterBindingDigest": str(preflight_environment["clusterBindingDigest"]),
        "namespaceBindingDigest": str(preflight_environment["namespaceDigest"]),
        "releaseBindingDigest": _digest_value(
            {"cluster": preflight_environment["clusterBindingDigest"], "release": release_name}
        ),
        "deploymentBindingDigest": str(
            continuity_bindings["deploymentBindingDigest"]
        ),
        "continuityTargetBindingDigest": str(
            continuity_bindings["targetBindingDigest"]
        ),
    }
    return subject, bindings


def _input_times(
    *,
    preflight_report: Mapping[str, Any],
    diagnostic_report: Mapping[str, Any],
    continuity_report: Mapping[str, Any],
) -> tuple[datetime, datetime, datetime, datetime]:
    preflight_metadata = _mapping(
        preflight_report.get("metadata"),
        "customer-deployment-qualification.time.invalid",
    )
    diagnostic_spec = _mapping(
        diagnostic_report.get("spec"),
        "customer-deployment-qualification.time.invalid",
    )
    diagnostic_environment = _mapping(
        diagnostic_spec.get("environment"),
        "customer-deployment-qualification.time.invalid",
    )
    continuity_spec = _mapping(
        continuity_report.get("spec"),
        "customer-deployment-qualification.time.invalid",
    )
    continuity_measurements = _mapping(
        continuity_spec.get("measurements"),
        "customer-deployment-qualification.time.invalid",
    )
    return (
        _parse_timestamp(
            preflight_metadata.get("generatedAt"),
            "customer-deployment-qualification.time.invalid",
        ),
        _parse_timestamp(
            continuity_measurements.get("startedAt"),
            "customer-deployment-qualification.time.invalid",
        ),
        _parse_timestamp(
            continuity_measurements.get("completedAt"),
            "customer-deployment-qualification.time.invalid",
        ),
        _parse_timestamp(
            diagnostic_environment.get("observedAt"),
            "customer-deployment-qualification.time.invalid",
        ),
    )


def build_report(
    *,
    preflight_report: Mapping[str, Any],
    preflight_digest: str,
    diagnostic_report: Mapping[str, Any],
    diagnostic_digest: str,
    ingress_report: Mapping[str, Any],
    ingress_digest: str,
    continuity_report: Mapping[str, Any],
    continuity_digest: str,
    context: str,
    namespace: str,
    release_name: str,
    deployment_name: str,
    image_digest: str,
    current_cluster_environment: Mapping[str, str] | None,
    maximum_evidence_age_seconds: int = 86400,
    maximum_clock_skew_seconds: int = 300,
    now: datetime | None = None,
) -> dict[str, Any]:
    if (
        isinstance(maximum_evidence_age_seconds, bool)
        or not 300 <= maximum_evidence_age_seconds <= 604800
        or isinstance(maximum_clock_skew_seconds, bool)
        or not 0 <= maximum_clock_skew_seconds <= 900
    ):
        _fail("customer-deployment-qualification.objective.invalid")
    instant_input = now or datetime.now(timezone.utc)
    if instant_input.tzinfo is None:
        _fail("customer-deployment-qualification.time.invalid")
    instant = instant_input.astimezone(timezone.utc)
    subject, bindings = _subject_and_bindings(
        preflight_report=preflight_report,
        diagnostic_report=diagnostic_report,
        ingress_report=ingress_report,
        continuity_report=continuity_report,
        context=context,
        namespace=namespace,
        release_name=release_name,
        deployment_name=deployment_name,
        image_digest=image_digest,
        current_cluster_environment=current_cluster_environment,
    )
    preflight_at, continuity_started, continuity_completed, health_observed = (
        _input_times(
            preflight_report=preflight_report,
            diagnostic_report=diagnostic_report,
            continuity_report=continuity_report,
        )
    )
    skew = maximum_clock_skew_seconds
    ordered = (
        preflight_at <= continuity_started
        and continuity_started <= continuity_completed
        and continuity_completed <= health_observed
    )
    times = (preflight_at, continuity_started, continuity_completed, health_observed)
    fresh = all(
        instant.timestamp() - maximum_evidence_age_seconds
        <= value.timestamp()
        <= instant.timestamp() + skew
        for value in times
    )
    oldest_age = max(0, math.ceil((instant - min(times)).total_seconds()))

    evidence = [
        _evidence(
            identifier=definition[0],
            contract_kind=definition[1],
            success_status=definition[2],
            error_code=definition[3],
            document=document,
            digest=digest,
        )
        for definition, document, digest in zip(
            EVIDENCE_DEFINITIONS,
            (
                preflight_report,
                diagnostic_report,
                ingress_report,
                continuity_report,
            ),
            (
                preflight_digest,
                diagnostic_digest,
                ingress_digest,
                continuity_digest,
            ),
        )
    ]
    evidence_by_id = {item["id"]: item for item in evidence}
    checks = [
        _check("source-binding", True, "customer-deployment-qualification.source.crossed"),
        _check("profile-binding", True, "customer-deployment-qualification.profile.crossed"),
        _check("exact-release-identity", True, "customer-deployment-qualification.release.crossed"),
        _check("explicit-current-cluster", True, "customer-deployment-qualification.cluster.changed"),
        _check(
            "live-install-preflight",
            evidence_by_id["live-install-preflight"]["status"] == "passed",
            "customer-deployment-qualification.preflight.not-install-ready",
        ),
        _check(
            "post-continuity-health",
            evidence_by_id["post-install-health"]["status"] == "passed" and ordered,
            "customer-deployment-qualification.diagnostic.not-post-continuity-healthy",
        ),
        _check(
            "customer-ingress",
            evidence_by_id["customer-ingress"]["status"] == "passed",
            "customer-deployment-qualification.ingress.not-qualified",
        ),
        _check(
            "control-plane-continuity",
            evidence_by_id["control-plane-continuity"]["status"] == "passed",
            "customer-deployment-qualification.continuity.not-qualified",
        ),
        _check("continuity-ingress-chain", True, "customer-deployment-qualification.ingress.crossed"),
        _check("evidence-order", ordered, "customer-deployment-qualification.evidence.order-invalid"),
        _check("evidence-freshness", fresh, "customer-deployment-qualification.evidence.stale"),
        _check("minimized-output", True, "customer-deployment-qualification.output.not-minimized"),
    ]
    failed_checks = sum(item["status"] == "failed" for item in checks)
    rejected_evidence = sum(item["status"] == "rejected" for item in evidence)
    status = (
        "qualified"
        if failed_checks == 0 and rejected_evidence == 0
        else "not-qualified"
    )
    generated_at = _timestamp(instant)
    spec: dict[str, Any] = {
        "status": status,
        "qualificationLevel": QUALIFICATION_LEVEL,
        "subject": subject,
        "bindings": bindings,
        "objective": {
            "maximumEvidenceAgeSeconds": maximum_evidence_age_seconds,
            "maximumClockSkewSeconds": maximum_clock_skew_seconds,
            "requirePostContinuityHealth": True,
        },
        "measurements": {
            "preflightGeneratedAt": _timestamp(preflight_at),
            "continuityStartedAt": _timestamp(continuity_started),
            "continuityCompletedAt": _timestamp(continuity_completed),
            "postContinuityHealthObservedAt": _timestamp(health_observed),
            "qualifiedAt": generated_at,
            "oldestEvidenceAgeSeconds": oldest_age,
        },
        "evidence": evidence,
        "checks": checks,
        "limitations": list(LIMITATIONS),
        "summary": {
            "requiredEvidence": len(evidence),
            "passedEvidence": len(evidence) - rejected_evidence,
            "rejectedEvidence": rejected_evidence,
            "totalChecks": len(checks),
            "passedChecks": len(checks) - failed_checks,
            "failedChecks": failed_checks,
            "overallStatus": status,
        },
    }
    metadata_without_id: dict[str, object] = {
        "generatedAt": generated_at,
        "sourceRevision": subject["sourceRevision"],
        "sourceDirty": False,
    }
    report = {
        "apiVersion": API_VERSION,
        "kind": KIND,
        "metadata": {
            "id": _report_identifier(metadata_without_id, spec),
            **metadata_without_id,
        },
        "spec": spec,
    }
    validate_report_document(report)
    return report


def _has_forbidden_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in FORBIDDEN_RETAINED_KEYS or _has_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_has_forbidden_key(child) for child in value)
    return False


def _expected_checks(report: Mapping[str, Any]) -> list[dict[str, str]]:
    spec = _mapping(
        report.get("spec"), "customer-deployment-qualification.report.invalid"
    )
    evidence = spec.get("evidence")
    measurements = _mapping(
        spec.get("measurements"),
        "customer-deployment-qualification.report.invalid",
    )
    objective = _mapping(
        spec.get("objective"), "customer-deployment-qualification.report.invalid"
    )
    if not isinstance(evidence, list) or len(evidence) != 4:
        _fail("customer-deployment-qualification.report.invalid")
    evidence_by_id = {
        str(_mapping(item, "customer-deployment-qualification.report.invalid").get("id")): _mapping(
            item, "customer-deployment-qualification.report.invalid"
        )
        for item in evidence
    }
    preflight_at = _parse_timestamp(
        measurements.get("preflightGeneratedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    continuity_started = _parse_timestamp(
        measurements.get("continuityStartedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    continuity_completed = _parse_timestamp(
        measurements.get("continuityCompletedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    health_observed = _parse_timestamp(
        measurements.get("postContinuityHealthObservedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    qualified_at = _parse_timestamp(
        measurements.get("qualifiedAt"),
        "customer-deployment-qualification.report.time-invalid",
    )
    skew = objective.get("maximumClockSkewSeconds")
    maximum_age = objective.get("maximumEvidenceAgeSeconds")
    if (
        isinstance(skew, bool)
        or not isinstance(skew, int)
        or isinstance(maximum_age, bool)
        or not isinstance(maximum_age, int)
    ):
        _fail("customer-deployment-qualification.report.objective-invalid")
    ordered = (
        preflight_at <= continuity_started
        and continuity_started <= continuity_completed
        and continuity_completed <= health_observed
    )
    values = (preflight_at, continuity_started, continuity_completed, health_observed)
    fresh = all(
        qualified_at.timestamp() - maximum_age
        <= value.timestamp()
        <= qualified_at.timestamp() + skew
        for value in values
    )
    expected_age = max(0, math.ceil((qualified_at - min(values)).total_seconds()))
    if measurements.get("oldestEvidenceAgeSeconds") != expected_age:
        _fail("customer-deployment-qualification.report.age-invalid")
    return [
        _check("source-binding", True, "customer-deployment-qualification.source.crossed"),
        _check("profile-binding", True, "customer-deployment-qualification.profile.crossed"),
        _check("exact-release-identity", True, "customer-deployment-qualification.release.crossed"),
        _check("explicit-current-cluster", True, "customer-deployment-qualification.cluster.changed"),
        _check(
            "live-install-preflight",
            evidence_by_id.get("live-install-preflight", {}).get("status") == "passed",
            "customer-deployment-qualification.preflight.not-install-ready",
        ),
        _check(
            "post-continuity-health",
            evidence_by_id.get("post-install-health", {}).get("status") == "passed" and ordered,
            "customer-deployment-qualification.diagnostic.not-post-continuity-healthy",
        ),
        _check(
            "customer-ingress",
            evidence_by_id.get("customer-ingress", {}).get("status") == "passed",
            "customer-deployment-qualification.ingress.not-qualified",
        ),
        _check(
            "control-plane-continuity",
            evidence_by_id.get("control-plane-continuity", {}).get("status") == "passed",
            "customer-deployment-qualification.continuity.not-qualified",
        ),
        _check("continuity-ingress-chain", True, "customer-deployment-qualification.ingress.crossed"),
        _check("evidence-order", ordered, "customer-deployment-qualification.evidence.order-invalid"),
        _check("evidence-freshness", fresh, "customer-deployment-qualification.evidence.stale"),
        _check(
            "minimized-output",
            not _has_forbidden_key(report),
            "customer-deployment-qualification.output.not-minimized",
        ),
    ]


def validate_report_document(report: Mapping[str, Any]) -> None:
    try:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("customer-deployment-qualification.schema.unavailable")
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(report),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        _fail("customer-deployment-qualification.report.schema-invalid")
    metadata = _mapping(
        report.get("metadata"), "customer-deployment-qualification.report.invalid"
    )
    spec = _mapping(
        report.get("spec"), "customer-deployment-qualification.report.invalid"
    )
    if metadata.get("generatedAt") != _mapping(
        spec.get("measurements"), "customer-deployment-qualification.report.invalid"
    ).get("qualifiedAt"):
        _fail("customer-deployment-qualification.report.time-invalid")
    subject = _mapping(
        spec.get("subject"), "customer-deployment-qualification.report.invalid"
    )
    if metadata.get("sourceRevision") != subject.get("sourceRevision"):
        _fail("customer-deployment-qualification.report.source-invalid")
    evidence = spec.get("evidence")
    if not isinstance(evidence, list):
        _fail("customer-deployment-qualification.report.evidence-invalid")
    for definition, item in zip(EVIDENCE_DEFINITIONS, evidence):
        current = _mapping(
            item, "customer-deployment-qualification.report.evidence-invalid"
        )
        expected_passed = current.get("observedStatus") == definition[2]
        if (
            current.get("id") != definition[0]
            or current.get("contractKind") != definition[1]
            or (current.get("status") == "passed") != expected_passed
            or (
                not expected_passed
                and current.get("errorCode") != definition[3]
            )
        ):
            _fail("customer-deployment-qualification.report.evidence-invalid")
    expected_checks = _expected_checks(report)
    if spec.get("checks") != expected_checks or [item["id"] for item in expected_checks] != list(CHECK_IDS):
        _fail("customer-deployment-qualification.report.checks-invalid")
    if spec.get("limitations") != list(LIMITATIONS):
        _fail("customer-deployment-qualification.report.limitations-invalid")
    rejected = sum(item.get("status") == "rejected" for item in evidence if isinstance(item, Mapping))
    failed = sum(item["status"] == "failed" for item in expected_checks)
    status = "qualified" if rejected == 0 and failed == 0 else "not-qualified"
    expected_summary = {
        "requiredEvidence": 4,
        "passedEvidence": 4 - rejected,
        "rejectedEvidence": rejected,
        "totalChecks": 12,
        "passedChecks": 12 - failed,
        "failedChecks": failed,
        "overallStatus": status,
    }
    if spec.get("status") != status or spec.get("summary") != expected_summary:
        _fail("customer-deployment-qualification.report.summary-invalid")
    metadata_without_id = dict(metadata)
    report_id = metadata_without_id.pop("id", None)
    if (
        not isinstance(report_id, str)
        or REPORT_ID.fullmatch(report_id) is None
        or report_id != _report_identifier(metadata_without_id, spec)
    ):
        _fail("customer-deployment-qualification.report.id-invalid")


def _validate_inputs(
    *,
    preflight_path: Path,
    diagnostic_path: Path,
    ingress_path: Path,
    continuity_path: Path,
    values: Sequence[Path],
    context: str,
    namespace: str,
    release_name: str,
    deployment_name: str,
    image_digest: str,
    helm: str,
) -> tuple[
    Mapping[str, Any],
    str,
    Mapping[str, Any],
    str,
    Mapping[str, Any],
    str,
    Mapping[str, Any],
    str,
]:
    if not values or len(values) > MAX_VALUES_FILES:
        _fail("customer-deployment-qualification.values.invalid")
    preflight_report, preflight_digest = _load_document(
        preflight_path, "customer-deployment-qualification.preflight.unreadable"
    )
    diagnostic_report, diagnostic_digest = _load_document(
        diagnostic_path, "customer-deployment-qualification.diagnostic.unreadable"
    )
    ingress_report, ingress_digest = _load_document(
        ingress_path, "customer-deployment-qualification.ingress.unreadable"
    )
    continuity_report, continuity_digest = _load_document(
        continuity_path, "customer-deployment-qualification.continuity.unreadable"
    )
    try:
        preflight.verify_report(
            preflight_path,
            values=values,
            namespace=namespace,
            release_name=release_name,
            helm=helm,
            require_clean=True,
            require_install_ready=False,
        )
        diagnostics.verify_report(
            diagnostic_report,
            context=context,
            namespace=namespace,
            release_name=release_name,
            image_digest=image_digest,
            require_clean=True,
            require_healthy=False,
        )
        ingress.validate_report_document(ingress_report)
        continuity.verify_report(
            report_path=continuity_path,
            ingress_report_path=ingress_path,
            require_clean=True,
            require_qualified=False,
        )
    except (
        preflight.DeploymentPreflightError,
        diagnostics.DeploymentDiagnosticError,
        ingress.IngressQualificationError,
        continuity.CustomerContinuityQualificationError,
    ):
        _fail("customer-deployment-qualification.input.verification-failed")
    for path, expected, code in (
        (preflight_path, preflight_digest, "customer-deployment-qualification.preflight.changed"),
        (diagnostic_path, diagnostic_digest, "customer-deployment-qualification.diagnostic.changed"),
        (ingress_path, ingress_digest, "customer-deployment-qualification.ingress.changed"),
        (continuity_path, continuity_digest, "customer-deployment-qualification.continuity.changed"),
    ):
        if _file_digest(path, code) != expected:
            _fail(code)
    return (
        preflight_report,
        preflight_digest,
        diagnostic_report,
        diagnostic_digest,
        ingress_report,
        ingress_digest,
        continuity_report,
        continuity_digest,
    )


def qualify(
    *,
    preflight_path: Path,
    diagnostic_path: Path,
    ingress_path: Path,
    continuity_path: Path,
    values: Sequence[Path],
    context: str,
    namespace: str,
    release_name: str,
    deployment_name: str,
    image_digest: str,
    output: Path,
    helm: str = "helm",
    kubectl: str = "kubectl",
    maximum_evidence_age_seconds: int = 86400,
    maximum_clock_skew_seconds: int = 300,
    now: Callable[[], datetime] | None = None,
) -> Mapping[str, Any]:
    inputs = _validate_inputs(
        preflight_path=preflight_path,
        diagnostic_path=diagnostic_path,
        ingress_path=ingress_path,
        continuity_path=continuity_path,
        values=values,
        context=context,
        namespace=namespace,
        release_name=release_name,
        deployment_name=deployment_name,
        image_digest=image_digest,
        helm=helm,
    )
    cluster_environment, cluster_check = preflight._cluster_environment(
        kubectl, context=context, namespace=namespace
    )
    if cluster_check.get("status") != "passed":
        _fail("customer-deployment-qualification.cluster.unavailable")
    report = build_report(
        preflight_report=inputs[0],
        preflight_digest=inputs[1],
        diagnostic_report=inputs[2],
        diagnostic_digest=inputs[3],
        ingress_report=inputs[4],
        ingress_digest=inputs[5],
        continuity_report=inputs[6],
        continuity_digest=inputs[7],
        context=context,
        namespace=namespace,
        release_name=release_name,
        deployment_name=deployment_name,
        image_digest=image_digest,
        current_cluster_environment=cluster_environment,
        maximum_evidence_age_seconds=maximum_evidence_age_seconds,
        maximum_clock_skew_seconds=maximum_clock_skew_seconds,
        now=(now or (lambda: datetime.now(timezone.utc)))(),
    )
    _write_report(output, report)
    return report


def verify_report(
    *,
    report_path: Path,
    preflight_path: Path,
    diagnostic_path: Path,
    ingress_path: Path,
    continuity_path: Path,
    values: Sequence[Path],
    context: str,
    namespace: str,
    release_name: str,
    deployment_name: str,
    image_digest: str,
    helm: str = "helm",
    kubectl: str = "kubectl",
    require_current_cluster: bool = False,
    require_qualified: bool = False,
) -> Mapping[str, Any]:
    report, _ = _load_document(
        report_path, "customer-deployment-qualification.report.unreadable"
    )
    validate_report_document(report)
    inputs = _validate_inputs(
        preflight_path=preflight_path,
        diagnostic_path=diagnostic_path,
        ingress_path=ingress_path,
        continuity_path=continuity_path,
        values=values,
        context=context,
        namespace=namespace,
        release_name=release_name,
        deployment_name=deployment_name,
        image_digest=image_digest,
        helm=helm,
    )
    current_cluster_environment: Mapping[str, str] | None = None
    if require_current_cluster:
        current_cluster_environment, cluster_check = preflight._cluster_environment(
            kubectl, context=context, namespace=namespace
        )
        if cluster_check.get("status") != "passed":
            _fail("customer-deployment-qualification.cluster.unavailable")
    metadata = _mapping(
        report.get("metadata"), "customer-deployment-qualification.report.invalid"
    )
    spec = _mapping(
        report.get("spec"), "customer-deployment-qualification.report.invalid"
    )
    objective = _mapping(
        spec.get("objective"), "customer-deployment-qualification.report.invalid"
    )
    reconstructed = build_report(
        preflight_report=inputs[0],
        preflight_digest=inputs[1],
        diagnostic_report=inputs[2],
        diagnostic_digest=inputs[3],
        ingress_report=inputs[4],
        ingress_digest=inputs[5],
        continuity_report=inputs[6],
        continuity_digest=inputs[7],
        context=context,
        namespace=namespace,
        release_name=release_name,
        deployment_name=deployment_name,
        image_digest=image_digest,
        current_cluster_environment=current_cluster_environment,
        maximum_evidence_age_seconds=int(objective["maximumEvidenceAgeSeconds"]),
        maximum_clock_skew_seconds=int(objective["maximumClockSkewSeconds"]),
        now=_parse_timestamp(
            metadata.get("generatedAt"),
            "customer-deployment-qualification.report.time-invalid",
        ),
    )
    if report != reconstructed:
        _fail("customer-deployment-qualification.report.input-mismatch")
    if require_qualified and spec.get("status") != "qualified":
        _fail("customer-deployment-qualification.report.not-qualified")
    return report


def _common_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--preflight-report", type=Path, required=True)
    parser.add_argument("--diagnostic-report", type=Path, required=True)
    parser.add_argument("--ingress-report", type=Path, required=True)
    parser.add_argument("--continuity-report", type=Path, required=True)
    parser.add_argument("--values", type=Path, action="append", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--namespace", default="iip-system")
    parser.add_argument("--release-name", default="iip")
    parser.add_argument("--deployment", default="iip-infra-intelligence")
    parser.add_argument("--image-digest", required=True)
    parser.add_argument("--helm", default=os.environ.get("IIP_HELM_BIN", "helm"))
    parser.add_argument("--kubectl", default=os.environ.get("IIP_KUBECTL_BIN", "kubectl"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    _common_inputs(generate)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--maximum-evidence-age-seconds", type=int, default=86400)
    generate.add_argument("--maximum-clock-skew-seconds", type=int, default=300)
    verify = commands.add_parser("verify")
    _common_inputs(verify)
    verify.add_argument("--report", type=Path, required=True)
    verify.add_argument("--require-current-cluster", action="store_true")
    verify.add_argument("--require-qualified", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    common = {
        "preflight_path": arguments.preflight_report,
        "diagnostic_path": arguments.diagnostic_report,
        "ingress_path": arguments.ingress_report,
        "continuity_path": arguments.continuity_report,
        "values": arguments.values,
        "context": arguments.context,
        "namespace": arguments.namespace,
        "release_name": arguments.release_name,
        "deployment_name": arguments.deployment,
        "image_digest": arguments.image_digest,
        "helm": arguments.helm,
        "kubectl": arguments.kubectl,
    }
    try:
        if arguments.command == "generate":
            report = qualify(
                **common,
                output=arguments.output,
                maximum_evidence_age_seconds=arguments.maximum_evidence_age_seconds,
                maximum_clock_skew_seconds=arguments.maximum_clock_skew_seconds,
            )
            print(
                f"customer deployment qualification {report['spec']['status']}: "
                f"{arguments.output}"
            )
            return 0 if report["spec"]["status"] == "qualified" else 1
        report = verify_report(
            **common,
            report_path=arguments.report,
            require_current_cluster=arguments.require_current_cluster,
            require_qualified=arguments.require_qualified,
        )
        print(f"customer deployment qualification verified: {report['spec']['status']}")
        return 0
    except CustomerDeploymentQualificationError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
