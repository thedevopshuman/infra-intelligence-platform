#!/usr/bin/env python3
"""Aggregate customer-approved sustained workload and planned failure evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "contracts" / "schemas"
sys.path.insert(0, str(ROOT / "scripts"))

import qualify_customer_continuity as control_plane_continuity  # noqa: E402
import qualify_customer_deployment as customer_deployment  # noqa: E402
import qualify_customer_postgresql_continuity as postgresql_continuity  # noqa: E402
import qualify_customer_processing_continuity as processing_continuity  # noqa: E402
import qualify_customer_sustained_workload as sustained_workload  # noqa: E402


API_VERSION = "iip.platform/v1alpha1"
PROFILE_KIND = "CustomerFailureOverlapProfile"
REPORT_KIND = "CustomerFailureOverlapQualificationReport"
QUALIFICATION_LEVEL = "customer-private-pilot-failure-overlap-v1"
QUALIFICATION_BOUNDARY = "customer-environment-planned-failure-overlap"
PROFILE_ID = re.compile(r"^cfop_[a-f0-9]{32}$")
REPORT_ID = re.compile(r"^cfoq_[a-f0-9]{32}$")
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024

CHECK_IDS = (
    "source-binding",
    "profile-review",
    "customer-approval",
    "evidence-freshness",
    "exact-release-identity",
    "customer-deployment",
    "sustained-core-workload",
    "control-plane-continuity",
    "worker-receiver-continuity",
    "postgresql-primary-promotion",
    "deployment-source-binding",
    "sustained-profile-binding",
    "api-target-chain",
    "otlp-target-chain",
    "kubernetes-environment-chain",
    "database-target-chain",
    "post-deployment-window",
    "sustained-failure-enclosure",
    "pre-failure-load-window",
    "post-failure-load-window",
    "minimized-output",
)

LIMITATIONS = (
    "customer-approved-private-pilot-core-proxy",
    "operator-coordinated-planned-failures",
    "single-cluster-and-selected-targets",
    "automatic-failover-fencing-and-split-brain-not-proven",
    "node-zone-region-and-disaster-recovery-not-qualified",
    "production-volume-long-window-slo-and-partner-acceptance-not-qualified",
)

TEMPORAL_ERRORS = frozenset(
    {
        "customer-failure-overlap.evidence.future",
        "customer-failure-overlap.evidence.stale",
        "customer-failure-overlap.evidence.expired",
    }
)

FORBIDDEN_RETAINED_KEYS = frozenset(
    {
        "accessToken",
        "apiBaseUrl",
        "authorization",
        "caBundle",
        "credential",
        "databaseHost",
        "endpoint",
        "environmentId",
        "hostname",
        "modelId",
        "namespace",
        "password",
        "prompt",
        "region",
        "repository",
        "resourceUid",
        "response",
        "secret",
        "spanId",
        "tenantId",
        "token",
        "traceId",
    }
)


class CustomerFailureOverlapError(RuntimeError):
    """Stable failure for unsafe, crossed, or irreproducible overlap evidence."""


@dataclass(frozen=True)
class Requirement:
    identifier: str
    kind: str
    boundary: str
    success_status: str
    validator: Callable[[Mapping[str, Any]], None]


@dataclass(frozen=True)
class EvidenceDocument:
    document: Mapping[str, Any]
    file_digest: str
    generated_at: datetime


def _fail(code: str) -> None:
    raise CustomerFailureOverlapError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _bytes_digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _sequence(value: object, code: str) -> Sequence[Any]:
    if not isinstance(value, list):
        _fail(code)
    return value


def _path(value: object, parts: Sequence[str]) -> object:
    current = value
    for part in parts:
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _parse_time(value: object, code: str) -> datetime:
    if not isinstance(value, str):
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(code)
    return parsed.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _elapsed_milliseconds(started: datetime, completed: datetime) -> int:
    elapsed = int((completed - started).total_seconds() * 1000)
    if elapsed < 0:
        _fail("customer-failure-overlap.window.invalid")
    return elapsed


def _validate_schema(document: object, schema_name: str, code: str) -> None:
    try:
        schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("customer-failure-overlap.schema.unavailable")
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
            document
        ),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        _fail(code)


def _read_document(
    path: Path,
    *,
    code: str,
    protected: bool = False,
) -> tuple[Mapping[str, Any], str]:
    candidate = path.expanduser()
    descriptor = -1
    raw = b""
    try:
        if candidate.is_symlink():
            _fail(code)
        descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or not 1 <= details.st_size <= MAX_DOCUMENT_BYTES
            or (protected and stat.S_IMODE(details.st_mode) != 0o600)
            or (
                protected
                and hasattr(os, "getuid")
                and details.st_uid != os.getuid()
            )
        ):
            _fail(code)
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            raw = handle.read(MAX_DOCUMENT_BYTES + 1)
        if len(raw) > MAX_DOCUMENT_BYTES:
            _fail(code)
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return _mapping(value, code), _bytes_digest(raw)


def _profile_id(metadata: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    without_id = dict(metadata)
    without_id.pop("id", None)
    return "cfop_" + hashlib.sha256(
        _canonical({"metadata": without_id, "spec": spec})
    ).hexdigest()[:32]


def _report_id(metadata: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    return "cfoq_" + hashlib.sha256(
        _canonical({"metadata": metadata, "spec": spec})
    ).hexdigest()[:32]


def validate_profile(profile: Mapping[str, Any]) -> None:
    code = "customer-failure-overlap.profile.invalid"
    _validate_schema(profile, "customer-failure-overlap-profile.schema.json", code)
    metadata = _mapping(profile.get("metadata"), code)
    spec = _mapping(profile.get("spec"), code)
    reviewed = _parse_time(metadata.get("reviewedAt"), code)
    valid_until = _parse_time(metadata.get("validUntil"), code)
    identifier = metadata.get("id")
    if (
        profile.get("apiVersion") != API_VERSION
        or profile.get("kind") != PROFILE_KIND
        or spec.get("qualificationLevel") != QUALIFICATION_LEVEL
        or valid_until <= reviewed
        or valid_until - reviewed > timedelta(days=90)
        or not isinstance(identifier, str)
        or PROFILE_ID.fullmatch(identifier) is None
        or identifier != _profile_id(metadata, spec)
    ):
        _fail(code)


def load_profile(path: Path) -> Mapping[str, Any]:
    profile, _ = _read_document(
        path,
        code="customer-failure-overlap.profile.unreadable",
        protected=True,
    )
    validate_profile(profile)
    return profile


def _wrap_validator(
    validator: Callable[[Mapping[str, Any]], None],
    error_type: type[Exception],
    code: str,
) -> Callable[[Mapping[str, Any]], None]:
    def wrapped(document: Mapping[str, Any]) -> None:
        try:
            validator(document)
        except error_type:
            _fail(code)

    return wrapped


REQUIREMENTS = (
    Requirement(
        "customer-deployment",
        "CustomerDeploymentQualificationReport",
        "customer-deployment",
        "qualified",
        _wrap_validator(
            customer_deployment.validate_report_document,
            customer_deployment.CustomerDeploymentQualificationError,
            "customer-failure-overlap.customer-deployment.invalid",
        ),
    ),
    Requirement(
        "sustained-core-workload",
        "CustomerSustainedWorkloadQualificationReport",
        "customer-environment-sustained-workload",
        "qualified",
        _wrap_validator(
            sustained_workload.validate_report_document,
            sustained_workload.CustomerSustainedWorkloadError,
            "customer-failure-overlap.sustained-workload.invalid",
        ),
    ),
    Requirement(
        "control-plane-continuity",
        "CustomerContinuityQualificationReport",
        "customer-control-plane-continuity",
        "qualified",
        _wrap_validator(
            control_plane_continuity.validate_report_document,
            control_plane_continuity.CustomerContinuityQualificationError,
            "customer-failure-overlap.control-plane-continuity.invalid",
        ),
    ),
    Requirement(
        "worker-receiver-continuity",
        "CustomerProcessingContinuityQualificationReport",
        "customer-worker-receiver-processing-continuity",
        "qualified",
        _wrap_validator(
            processing_continuity.validate_report_document,
            processing_continuity.CustomerProcessingContinuityError,
            "customer-failure-overlap.processing-continuity.invalid",
        ),
    ),
    Requirement(
        "postgresql-primary-promotion",
        "CustomerPostgreSQLContinuityQualificationReport",
        "customer-postgresql-primary-promotion",
        "qualified",
        _wrap_validator(
            postgresql_continuity.validate_report_document,
            postgresql_continuity.CustomerPostgreSQLContinuityError,
            "customer-failure-overlap.postgresql-continuity.invalid",
        ),
    ),
)


def _load_sustained_profile(path: Path) -> tuple[Mapping[str, Any], str]:
    profile, _ = _read_document(
        path,
        code="customer-failure-overlap.sustained-profile.unreadable",
        protected=True,
    )
    try:
        sustained_workload.validate_profile(profile)
    except sustained_workload.CustomerSustainedWorkloadError:
        _fail("customer-failure-overlap.sustained-profile.invalid")
    return profile, _digest(profile)


def _load_sources(paths: Mapping[str, Path]) -> Mapping[str, EvidenceDocument]:
    documents: dict[str, EvidenceDocument] = {}
    for requirement in REQUIREMENTS:
        document, digest = _read_document(
            paths[requirement.identifier],
            code=f"customer-failure-overlap.{requirement.identifier}.unreadable",
        )
        requirement.validator(document)
        documents[requirement.identifier] = EvidenceDocument(
            document=document,
            file_digest=digest,
            generated_at=_parse_time(
                _path(document, ("metadata", "generatedAt")),
                f"customer-failure-overlap.{requirement.identifier}.invalid",
            ),
        )
    return documents


def _status(document: Mapping[str, Any]) -> str:
    value = _path(document, ("spec", "status"))
    if not isinstance(value, str):
        _fail("customer-failure-overlap.evidence.invalid")
    return value


def _release_subject(document: Mapping[str, Any]) -> Mapping[str, Any]:
    subject = _mapping(
        _path(document, ("spec", "subject")),
        "customer-failure-overlap.evidence.crossed",
    )
    return {
        key: subject.get(key)
        for key in (
            "applicationVersion",
            "chartVersion",
            "contractsApiVersion",
            "requiredMigration",
            "sourceRevision",
            "imageDigest",
        )
    }


def _cross_check_sources(
    profile: Mapping[str, Any],
    sustained_profile: Mapping[str, Any],
    sustained_profile_digest: str,
    sources: Mapping[str, EvidenceDocument],
) -> Mapping[str, Any]:
    code = "customer-failure-overlap.evidence.crossed"
    spec = _mapping(profile.get("spec"), code)
    expected_release = _mapping(spec.get("release"), code)
    expected = _mapping(spec.get("bindings"), code)
    sustained_spec = _mapping(sustained_profile.get("spec"), code)
    if _mapping(sustained_spec.get("release"), code) != expected_release:
        _fail(code)
    if any(
        _release_subject(source.document) != expected_release
        for source in sources.values()
    ):
        _fail(code)
    if any(
        _path(source.document, ("metadata", "sourceRevision"))
        != expected_release.get("sourceRevision")
        for source in sources.values()
    ):
        _fail(code)

    deployment = sources["customer-deployment"]
    sustained = sources["sustained-core-workload"]
    control = sources["control-plane-continuity"]
    processing = sources["worker-receiver-continuity"]
    postgresql = sources["postgresql-primary-promotion"]
    deployment_bindings = _mapping(
        _path(deployment.document, ("spec", "bindings")), code
    )
    sustained_bindings = _mapping(
        _path(sustained.document, ("spec", "bindings")), code
    )
    control_bindings = _mapping(
        _path(control.document, ("spec", "bindings")), code
    )
    processing_bindings = _mapping(
        _path(processing.document, ("spec", "bindings")), code
    )
    postgresql_bindings = _mapping(
        _path(postgresql.document, ("spec", "bindings")), code
    )

    api_target = expected.get("apiTargetBindingDigest")
    otlp_target = expected.get("otlpTargetBindingDigest")
    context = expected.get("kubernetesContextBindingDigest")
    namespace = expected.get("namespaceBindingDigest")
    if (
        expected.get("deploymentReportDigest") != deployment.file_digest
        or expected.get("sustainedWorkloadProfileDigest")
        != sustained_profile_digest
        or sustained_bindings.get("profileDigest") != sustained_profile_digest
        or expected.get("clusterBindingDigest")
        != deployment_bindings.get("clusterBindingDigest")
        or namespace != deployment_bindings.get("namespaceBindingDigest")
        or api_target != deployment_bindings.get("continuityTargetBindingDigest")
        or otlp_target
        != deployment_bindings.get("processingOtlpTargetBindingDigest")
        or otlp_target
        != deployment_bindings.get("otlpReceiverEndpointBindingDigest")
        or expected.get("databaseTargetBindingDigest")
        != deployment_bindings.get("databaseTargetBindingDigest")
        or expected.get("processingProfileDigest")
        != deployment_bindings.get("processingProfileDigest")
        or expected.get("postgresqlProfileDigest")
        != deployment_bindings.get("databaseProfileDigest")
        or api_target != sustained_bindings.get("apiTargetBindingDigest")
        or otlp_target != sustained_bindings.get("otlpTargetBindingDigest")
        or api_target != control_bindings.get("targetBindingDigest")
        or context != control_bindings.get("kubernetesContextBindingDigest")
        or namespace != control_bindings.get("namespaceBindingDigest")
        or api_target != processing_bindings.get("apiTargetBindingDigest")
        or otlp_target != processing_bindings.get("otlpTargetBindingDigest")
        or context != processing_bindings.get("kubernetesContextBindingDigest")
        or namespace != processing_bindings.get("namespaceBindingDigest")
        or expected.get("processingProfileDigest")
        != processing_bindings.get("profileDigest")
        or api_target != postgresql_bindings.get("apiTargetBindingDigest")
        or otlp_target != postgresql_bindings.get("otlpTargetBindingDigest")
        or expected.get("databaseTargetBindingDigest")
        != postgresql_bindings.get("databaseTargetBindingDigest")
        or context != postgresql_bindings.get("kubernetesContextBindingDigest")
        or namespace != postgresql_bindings.get("namespaceBindingDigest")
        or expected.get("postgresqlProfileDigest")
        != postgresql_bindings.get("profileDigest")
    ):
        _fail(code)
    return expected_release


def _source_valid_until(requirement: Requirement, source: EvidenceDocument) -> datetime | None:
    if requirement.identifier != "sustained-core-workload":
        return None
    return _parse_time(
        _path(source.document, ("metadata", "validUntil")),
        "customer-failure-overlap.sustained-workload.invalid",
    )


def _evidence_item(
    requirement: Requirement,
    source: EvidenceDocument,
    *,
    current: datetime,
    maximum_age_seconds: int,
    maximum_clock_skew_seconds: int,
) -> dict[str, Any]:
    metadata = _mapping(
        source.document.get("metadata"), "customer-failure-overlap.evidence.invalid"
    )
    observed = _status(source.document)
    age_seconds = max(0, int((current - source.generated_at).total_seconds()))
    source_valid_until = _source_valid_until(requirement, source)
    error: str | None = None
    if source.generated_at > current + timedelta(seconds=maximum_clock_skew_seconds):
        error = "customer-failure-overlap.evidence.future"
    elif current - source.generated_at > timedelta(seconds=maximum_age_seconds):
        error = "customer-failure-overlap.evidence.stale"
    elif source_valid_until is not None and current >= source_valid_until:
        error = "customer-failure-overlap.evidence.expired"
    elif metadata.get("sourceDirty") is not False:
        error = "customer-failure-overlap.evidence.source-dirty"
    elif observed != requirement.success_status:
        error = "customer-failure-overlap.evidence.status-not-qualified"
    item: dict[str, Any] = {
        "id": requirement.identifier,
        "contractKind": requirement.kind,
        "qualificationBoundary": requirement.boundary,
        "reportId": metadata.get("id"),
        "reportDigest": source.file_digest,
        "generatedAt": _timestamp(source.generated_at),
        "validUntil": (
            _timestamp(source_valid_until) if source_valid_until is not None else None
        ),
        "ageSeconds": age_seconds,
        "observedStatus": observed,
        "status": "passed" if error is None else "failed",
    }
    if error is not None:
        item["errorCode"] = error
    return item


def _check(identifier: str, passed: bool, code: str) -> dict[str, str]:
    result = {"id": identifier, "status": "passed" if passed else "failed"}
    if not passed:
        result["errorCode"] = code
    return result


def _has_forbidden_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in FORBIDDEN_RETAINED_KEYS or _has_forbidden_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_forbidden_key(item) for item in value)
    return False


def _derived_checks(
    *,
    metadata: Mapping[str, Any],
    spec: Mapping[str, Any],
    profile_current: bool,
    customer_approved: bool,
    exact_release: bool,
    deployment_binding: bool,
    sustained_profile_binding: bool,
    api_chain: bool,
    otlp_chain: bool,
    kubernetes_chain: bool,
    database_chain: bool,
    post_deployment: bool,
    enclosed: bool,
) -> list[dict[str, str]]:
    evidence = [
        _mapping(item, "customer-failure-overlap.report.invalid")
        for item in _sequence(
            spec.get("evidence"), "customer-failure-overlap.report.invalid"
        )
    ]
    evidence_by_id = {str(item.get("id")): item for item in evidence}
    objective = _mapping(
        spec.get("objective"), "customer-failure-overlap.report.invalid"
    )
    measurements = _mapping(
        spec.get("measurements"), "customer-failure-overlap.report.invalid"
    )
    source_bound = metadata.get("sourceDirty") is False and all(
        item.get("errorCode") != "customer-failure-overlap.evidence.source-dirty"
        for item in evidence
    )
    evidence_fresh = all(
        item.get("errorCode") not in TEMPORAL_ERRORS for item in evidence
    )
    checks = [
        _check("source-binding", source_bound, "customer-failure-overlap.source.invalid"),
        _check("profile-review", profile_current, "customer-failure-overlap.profile.expired"),
        _check("customer-approval", customer_approved, "customer-failure-overlap.customer-approval.missing"),
        _check("evidence-freshness", evidence_fresh, "customer-failure-overlap.evidence.not-current"),
        _check("exact-release-identity", exact_release, "customer-failure-overlap.release.mismatch"),
    ]
    for requirement in REQUIREMENTS:
        item = evidence_by_id.get(requirement.identifier, {})
        checks.append(
            _check(
                requirement.identifier,
                item.get("status") == "passed",
                f"customer-failure-overlap.{requirement.identifier}.not-qualified",
            )
        )
    checks.extend(
        (
            _check("deployment-source-binding", deployment_binding, "customer-failure-overlap.deployment.binding-mismatch"),
            _check("sustained-profile-binding", sustained_profile_binding, "customer-failure-overlap.sustained-profile.binding-mismatch"),
            _check("api-target-chain", api_chain, "customer-failure-overlap.api-target.binding-mismatch"),
            _check("otlp-target-chain", otlp_chain, "customer-failure-overlap.otlp-target.binding-mismatch"),
            _check("kubernetes-environment-chain", kubernetes_chain, "customer-failure-overlap.kubernetes.binding-mismatch"),
            _check("database-target-chain", database_chain, "customer-failure-overlap.database.binding-mismatch"),
            _check("post-deployment-window", post_deployment, "customer-failure-overlap.window.before-deployment"),
            _check("sustained-failure-enclosure", enclosed, "customer-failure-overlap.window.not-enclosed"),
            _check(
                "pre-failure-load-window",
                int(measurements.get("preFailureLoadSeconds", -1))
                >= int(objective.get("minimumPreFailureLoadSeconds", 0)),
                "customer-failure-overlap.window.insufficient-pre-failure-load",
            ),
            _check(
                "post-failure-load-window",
                int(measurements.get("postFailureLoadSeconds", -1))
                >= int(objective.get("minimumPostFailureLoadSeconds", 0)),
                "customer-failure-overlap.window.insufficient-post-failure-load",
            ),
            _check("minimized-output", not _has_forbidden_key(spec), "customer-failure-overlap.output.not-minimized"),
        )
    )
    return checks


def _summary(
    evidence: Sequence[Mapping[str, Any]], checks: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    passed_evidence = sum(item.get("status") == "passed" for item in evidence)
    passed_checks = sum(item.get("status") == "passed" for item in checks)
    status = "qualified" if passed_checks == len(CHECK_IDS) else "not-qualified"
    return {
        "totalEvidence": len(REQUIREMENTS),
        "passedEvidence": passed_evidence,
        "failedEvidence": len(REQUIREMENTS) - passed_evidence,
        "totalChecks": len(CHECK_IDS),
        "passedChecks": passed_checks,
        "failedChecks": len(CHECK_IDS) - passed_checks,
        "qualificationWindows": 3,
        "overallStatus": status,
    }


def _window(
    identifier: str, started: datetime, completed: datetime
) -> dict[str, Any]:
    return {
        "id": identifier,
        "startedAt": _timestamp(started),
        "completedAt": _timestamp(completed),
        "durationMilliseconds": _elapsed_milliseconds(started, completed),
    }


def _source_windows(
    sources: Mapping[str, EvidenceDocument],
) -> tuple[datetime, datetime, list[dict[str, Any]]]:
    sustained_started = _parse_time(
        _path(
            sources["sustained-core-workload"].document,
            ("spec", "measurements", "startedAt"),
        ),
        "customer-failure-overlap.sustained-workload.invalid",
    )
    sustained_completed = _parse_time(
        _path(
            sources["sustained-core-workload"].document,
            ("spec", "measurements", "completedAt"),
        ),
        "customer-failure-overlap.sustained-workload.invalid",
    )
    windows = []
    for identifier in (
        "control-plane-continuity",
        "worker-receiver-continuity",
        "postgresql-primary-promotion",
    ):
        document = sources[identifier].document
        started = _parse_time(
            _path(document, ("spec", "measurements", "startedAt")),
            f"customer-failure-overlap.{identifier}.invalid",
        )
        completed = _parse_time(
            _path(document, ("spec", "measurements", "completedAt")),
            f"customer-failure-overlap.{identifier}.invalid",
        )
        windows.append(_window(identifier, started, completed))
    return sustained_started, sustained_completed, windows


def build_report(
    *,
    profile: Mapping[str, Any],
    sustained_profile: Mapping[str, Any],
    sustained_profile_digest: str,
    sources: Mapping[str, EvidenceDocument],
    generated_at: datetime,
) -> Mapping[str, Any]:
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        _fail("customer-failure-overlap.time.invalid")
    current = generated_at.astimezone(timezone.utc).replace(microsecond=0)
    validate_profile(profile)
    try:
        sustained_workload.validate_profile(sustained_profile)
    except sustained_workload.CustomerSustainedWorkloadError:
        _fail("customer-failure-overlap.sustained-profile.invalid")
    exact_release = _cross_check_sources(
        profile, sustained_profile, sustained_profile_digest, sources
    )
    metadata = _mapping(profile.get("metadata"), "customer-failure-overlap.profile.invalid")
    profile_spec = _mapping(profile.get("spec"), "customer-failure-overlap.profile.invalid")
    bindings = _mapping(profile_spec.get("bindings"), "customer-failure-overlap.profile.invalid")
    review = _mapping(profile_spec.get("review"), "customer-failure-overlap.profile.invalid")
    objective = _mapping(profile_spec.get("objective"), "customer-failure-overlap.profile.invalid")
    reviewed = _parse_time(metadata.get("reviewedAt"), "customer-failure-overlap.profile.invalid")
    profile_valid_until = _parse_time(metadata.get("validUntil"), "customer-failure-overlap.profile.invalid")
    skew = int(objective["maximumClockSkewSeconds"])
    profile_current = (
        reviewed <= current + timedelta(seconds=skew)
        and current < reviewed + timedelta(seconds=int(objective["maximumProfileAgeSeconds"]))
        and current < profile_valid_until
    )
    if not profile_current:
        _fail("customer-failure-overlap.profile.expired")

    evidence = [
        _evidence_item(
            requirement,
            sources[requirement.identifier],
            current=current,
            maximum_age_seconds=int(objective["maximumEvidenceAgeSeconds"]),
            maximum_clock_skew_seconds=skew,
        )
        for requirement in REQUIREMENTS
    ]
    sustained_started, sustained_completed, windows = _source_windows(sources)
    window_starts = [
        _parse_time(item["startedAt"], "customer-failure-overlap.window.invalid")
        for item in windows
    ]
    window_ends = [
        _parse_time(item["completedAt"], "customer-failure-overlap.window.invalid")
        for item in windows
    ]
    first_failure = min(window_starts)
    last_failure = max(window_ends)
    deployment_time = sources["customer-deployment"].generated_at
    post_deployment = sustained_started >= deployment_time - timedelta(seconds=skew) and all(
        started >= deployment_time - timedelta(seconds=skew)
        for started in window_starts
    )
    enclosed = all(
        started >= sustained_started - timedelta(seconds=skew)
        and completed <= sustained_completed + timedelta(seconds=skew)
        for started, completed in zip(window_starts, window_ends)
    )
    measurements = {
        "profileReviewedAt": _timestamp(reviewed),
        "sustainedStartedAt": _timestamp(sustained_started),
        "sustainedCompletedAt": _timestamp(sustained_completed),
        "firstFailureQualificationStartedAt": _timestamp(first_failure),
        "lastFailureQualificationCompletedAt": _timestamp(last_failure),
        "preFailureLoadSeconds": max(
            0, int((first_failure - sustained_started).total_seconds())
        ),
        "postFailureLoadSeconds": max(
            0, int((sustained_completed - last_failure).total_seconds())
        ),
        "qualificationWindows": windows,
        "assessedAt": _timestamp(current),
        "oldestEvidenceAgeSeconds": max(
            0,
            int(
                (
                    current
                    - min(source.generated_at for source in sources.values())
                ).total_seconds()
            ),
        ),
    }
    report_metadata: dict[str, Any] = {
        "generatedAt": _timestamp(current),
        "validUntil": "",
        "sourceRevision": exact_release["sourceRevision"],
        "sourceDirty": False,
    }
    report_bindings = {
        "profileDigest": _digest(profile),
        "approvalRecordDigest": review["approvalRecordDigest"],
        "deploymentReportDigest": sources["customer-deployment"].file_digest,
        "sustainedWorkloadProfileDigest": sustained_profile_digest,
        "sustainedWorkloadReportDigest": sources["sustained-core-workload"].file_digest,
        "controlPlaneContinuityReportDigest": sources["control-plane-continuity"].file_digest,
        "processingContinuityReportDigest": sources["worker-receiver-continuity"].file_digest,
        "postgresqlContinuityReportDigest": sources["postgresql-primary-promotion"].file_digest,
        **{
            key: bindings[key]
            for key in (
                "clusterBindingDigest",
                "kubernetesContextBindingDigest",
                "namespaceBindingDigest",
                "apiTargetBindingDigest",
                "otlpTargetBindingDigest",
                "databaseTargetBindingDigest",
                "processingProfileDigest",
                "postgresqlProfileDigest",
            )
        },
    }
    report_spec: dict[str, Any] = {
        "qualificationLevel": QUALIFICATION_LEVEL,
        "qualificationBoundary": QUALIFICATION_BOUNDARY,
        "status": "not-qualified",
        "subject": dict(exact_release),
        "environment": {
            "correlationMode": "source-report-window-containment-v1",
            "failureMode": "operator-coordinated-planned",
            "reviewAuthority": "protected-customer-profile",
        },
        "bindings": report_bindings,
        "objective": dict(objective),
        "evidence": evidence,
        "measurements": measurements,
        "checks": [],
        "limitations": list(LIMITATIONS),
        "summary": {},
    }
    evidence_by_id = {str(item["id"]): item for item in evidence}
    report_digest_keys = {
        "customer-deployment": "deploymentReportDigest",
        "sustained-core-workload": "sustainedWorkloadReportDigest",
        "control-plane-continuity": "controlPlaneContinuityReportDigest",
        "worker-receiver-continuity": "processingContinuityReportDigest",
        "postgresql-primary-promotion": "postgresqlContinuityReportDigest",
    }
    checks = _derived_checks(
        metadata=report_metadata,
        spec=report_spec,
        profile_current=profile_current,
        customer_approved=(
            review.get("basis") == "customer-approved-private-pilot-core-proxy"
            and review.get("dataHandlingReviewed") is True
            and review.get("recoveryObjectivesReviewed") is True
        ),
        exact_release=True,
        deployment_binding=(
            bindings.get("deploymentReportDigest")
            == evidence_by_id["customer-deployment"].get("reportDigest")
        ),
        sustained_profile_binding=(
            bindings.get("sustainedWorkloadProfileDigest")
            == sustained_profile_digest
        ),
        api_chain=True,
        otlp_chain=True,
        kubernetes_chain=True,
        database_chain=True,
        post_deployment=post_deployment,
        enclosed=enclosed,
    )
    report_spec["checks"] = checks
    report_spec["summary"] = _summary(evidence, checks)
    report_spec["status"] = report_spec["summary"]["overallStatus"]

    validity_limits = [
        profile_valid_until,
        reviewed + timedelta(seconds=int(objective["maximumProfileAgeSeconds"])),
        current + timedelta(seconds=int(objective["reportValiditySeconds"])),
    ]
    for requirement in REQUIREMENTS:
        item = evidence_by_id[requirement.identifier]
        if item.get("errorCode") in TEMPORAL_ERRORS:
            continue
        validity_limits.append(
            sources[requirement.identifier].generated_at
            + timedelta(seconds=int(objective["maximumEvidenceAgeSeconds"]))
        )
        source_until = _source_valid_until(
            requirement, sources[requirement.identifier]
        )
        if source_until is not None:
            validity_limits.append(source_until)
    valid_until = min(validity_limits)
    if valid_until <= current:
        _fail("customer-failure-overlap.report.no-validity")
    report_metadata["validUntil"] = _timestamp(valid_until)
    if any(
        report_bindings.get(binding_key)
        != evidence_by_id[requirement_id].get("reportDigest")
        for requirement_id, binding_key in report_digest_keys.items()
    ):
        _fail("customer-failure-overlap.report.binding-mismatch")
    if _has_forbidden_key(report_spec):
        _fail("customer-failure-overlap.output.not-minimized")
    report_metadata["id"] = _report_id(report_metadata, report_spec)
    report = {
        "apiVersion": API_VERSION,
        "kind": REPORT_KIND,
        "metadata": report_metadata,
        "spec": report_spec,
    }
    validate_report_document(report)
    return report


def _expected_evidence(
    item: Mapping[str, Any],
    requirement: Requirement,
    *,
    assessed_at: datetime,
    maximum_age_seconds: int,
    maximum_clock_skew_seconds: int,
) -> Mapping[str, Any]:
    generated = _parse_time(item.get("generatedAt"), "customer-failure-overlap.report.invalid")
    valid_value = item.get("validUntil")
    valid_until = (
        _parse_time(valid_value, "customer-failure-overlap.report.invalid")
        if valid_value is not None
        else None
    )
    age = max(0, int((assessed_at - generated).total_seconds()))
    observed = item.get("observedStatus")
    error: str | None = None
    if generated > assessed_at + timedelta(seconds=maximum_clock_skew_seconds):
        error = "customer-failure-overlap.evidence.future"
    elif assessed_at - generated > timedelta(seconds=maximum_age_seconds):
        error = "customer-failure-overlap.evidence.stale"
    elif valid_until is not None and assessed_at >= valid_until:
        error = "customer-failure-overlap.evidence.expired"
    elif item.get("errorCode") == "customer-failure-overlap.evidence.source-dirty":
        error = "customer-failure-overlap.evidence.source-dirty"
    elif observed != requirement.success_status:
        error = "customer-failure-overlap.evidence.status-not-qualified"
    expected = dict(item)
    expected["ageSeconds"] = age
    expected["status"] = "passed" if error is None else "failed"
    expected.pop("errorCode", None)
    if error is not None:
        expected["errorCode"] = error
    return expected


def validate_report_document(report: Mapping[str, Any]) -> None:
    code = "customer-failure-overlap.report.invalid"
    _validate_schema(
        report, "customer-failure-overlap-qualification-report.schema.json", code
    )
    metadata = _mapping(report.get("metadata"), code)
    spec = _mapping(report.get("spec"), code)
    identifier = metadata.get("id")
    metadata_without_id = dict(metadata)
    metadata_without_id.pop("id", None)
    generated = _parse_time(metadata.get("generatedAt"), code)
    valid_until = _parse_time(metadata.get("validUntil"), code)
    measurements = _mapping(spec.get("measurements"), code)
    objective = _mapping(spec.get("objective"), code)
    assessed_at = _parse_time(measurements.get("assessedAt"), code)
    reviewed = _parse_time(measurements.get("profileReviewedAt"), code)
    sustained_started = _parse_time(measurements.get("sustainedStartedAt"), code)
    sustained_completed = _parse_time(measurements.get("sustainedCompletedAt"), code)
    first_failure = _parse_time(
        measurements.get("firstFailureQualificationStartedAt"), code
    )
    last_failure = _parse_time(
        measurements.get("lastFailureQualificationCompletedAt"), code
    )
    if generated != assessed_at:
        _fail(code)
    windows = [
        _mapping(item, code)
        for item in _sequence(measurements.get("qualificationWindows"), code)
    ]
    starts: list[datetime] = []
    ends: list[datetime] = []
    for window in windows:
        started = _parse_time(window.get("startedAt"), code)
        completed = _parse_time(window.get("completedAt"), code)
        if window.get("durationMilliseconds") != _elapsed_milliseconds(started, completed):
            _fail(code)
        starts.append(started)
        ends.append(completed)
    pre_seconds = max(0, int((min(starts) - sustained_started).total_seconds()))
    post_seconds = max(0, int((sustained_completed - max(ends)).total_seconds()))
    oldest_age = max(
        int(item.get("ageSeconds", -1))
        for item in _sequence(spec.get("evidence"), code)
    )
    if (
        min(starts) != first_failure
        or max(ends) != last_failure
        or measurements.get("preFailureLoadSeconds") != pre_seconds
        or measurements.get("postFailureLoadSeconds") != post_seconds
        or measurements.get("oldestEvidenceAgeSeconds") != oldest_age
    ):
        _fail(code)

    evidence = [
        _mapping(item, code) for item in _sequence(spec.get("evidence"), code)
    ]
    if tuple(item.get("id") for item in evidence) != tuple(
        item.identifier for item in REQUIREMENTS
    ):
        _fail(code)
    expected_evidence = [
        _expected_evidence(
            item,
            requirement,
            assessed_at=assessed_at,
            maximum_age_seconds=int(objective["maximumEvidenceAgeSeconds"]),
            maximum_clock_skew_seconds=int(objective["maximumClockSkewSeconds"]),
        )
        for item, requirement in zip(evidence, REQUIREMENTS)
    ]
    if evidence != expected_evidence:
        _fail(code)
    bindings = _mapping(spec.get("bindings"), code)
    evidence_by_id = {str(item["id"]): item for item in evidence}
    report_digest_keys = {
        "customer-deployment": "deploymentReportDigest",
        "sustained-core-workload": "sustainedWorkloadReportDigest",
        "control-plane-continuity": "controlPlaneContinuityReportDigest",
        "worker-receiver-continuity": "processingContinuityReportDigest",
        "postgresql-primary-promotion": "postgresqlContinuityReportDigest",
    }
    report_bindings_match = all(
        bindings.get(binding_key) == evidence_by_id[source_id].get("reportDigest")
        for source_id, binding_key in report_digest_keys.items()
    )
    skew = int(objective["maximumClockSkewSeconds"])
    deployment_time = _parse_time(
        evidence_by_id["customer-deployment"].get("generatedAt"), code
    )
    post_deployment = sustained_started >= deployment_time - timedelta(seconds=skew) and all(
        started >= deployment_time - timedelta(seconds=skew) for started in starts
    )
    enclosed = all(
        started >= sustained_started - timedelta(seconds=skew)
        and completed <= sustained_completed + timedelta(seconds=skew)
        for started, completed in zip(starts, ends)
    )
    profile_current = (
        reviewed <= assessed_at + timedelta(seconds=skew)
        and assessed_at
        < reviewed + timedelta(seconds=int(objective["maximumProfileAgeSeconds"]))
    )
    expected_checks = _derived_checks(
        metadata=metadata,
        spec=spec,
        profile_current=profile_current,
        customer_approved=True,
        exact_release=True,
        deployment_binding=report_bindings_match,
        sustained_profile_binding=True,
        api_chain=True,
        otlp_chain=True,
        kubernetes_chain=True,
        database_chain=True,
        post_deployment=post_deployment,
        enclosed=enclosed,
    )
    checks = _sequence(spec.get("checks"), code)
    summary = _summary(evidence, expected_checks)
    validity_ceiling = min(
        reviewed + timedelta(seconds=int(objective["maximumProfileAgeSeconds"])),
        generated + timedelta(seconds=int(objective["reportValiditySeconds"])),
    )
    for item in evidence:
        if item.get("errorCode") in TEMPORAL_ERRORS:
            continue
        evidence_generated = _parse_time(item.get("generatedAt"), code)
        validity_ceiling = min(
            validity_ceiling,
            evidence_generated
            + timedelta(seconds=int(objective["maximumEvidenceAgeSeconds"])),
        )
        if item.get("validUntil") is not None:
            validity_ceiling = min(
                validity_ceiling, _parse_time(item.get("validUntil"), code)
            )
    if (
        report.get("apiVersion") != API_VERSION
        or report.get("kind") != REPORT_KIND
        or spec.get("qualificationLevel") != QUALIFICATION_LEVEL
        or spec.get("qualificationBoundary") != QUALIFICATION_BOUNDARY
        or metadata.get("sourceRevision")
        != _path(spec, ("subject", "sourceRevision"))
        or valid_until <= generated
        or valid_until > validity_ceiling
        or checks != expected_checks
        or spec.get("summary") != summary
        or spec.get("status") != summary["overallStatus"]
        or tuple(spec.get("limitations", ())) != LIMITATIONS
        or _has_forbidden_key(spec)
        or not isinstance(identifier, str)
        or REPORT_ID.fullmatch(identifier) is None
        or identifier != _report_id(metadata_without_id, spec)
    ):
        _fail(code)


def _source_identity() -> tuple[str, bool]:
    try:
        revision = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ("git", "status", "--porcelain", "--untracked-files=normal"),
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        _fail("customer-failure-overlap.source.unavailable")
    return revision, dirty


def _distinct(paths: Sequence[Path]) -> None:
    normalized = [str(path.expanduser().resolve(strict=False)) for path in paths]
    if len(normalized) != len(set(normalized)):
        _fail("customer-failure-overlap.paths.overlap")


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    output = path.expanduser()
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{output.name}.", suffix=".tmp", dir=output.parent
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(report, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, output)
        finally:
            if temporary.exists():
                temporary.unlink()
    except OSError:
        _fail("customer-failure-overlap.output.invalid")


def assess(
    *,
    profile_path: Path,
    sustained_profile_path: Path,
    paths: Mapping[str, Path],
    output: Path,
    now: datetime | None = None,
) -> Mapping[str, Any]:
    _distinct((profile_path, sustained_profile_path, *paths.values(), output))
    profile = load_profile(profile_path)
    sustained_profile, sustained_profile_digest = _load_sustained_profile(
        sustained_profile_path
    )
    sources = _load_sources(paths)
    revision, dirty = _source_identity()
    expected_revision = _path(profile, ("spec", "release", "sourceRevision"))
    if dirty:
        _fail("customer-failure-overlap.source.dirty")
    if revision != expected_revision:
        _fail("customer-failure-overlap.source.mismatch")
    report = build_report(
        profile=profile,
        sustained_profile=sustained_profile,
        sustained_profile_digest=sustained_profile_digest,
        sources=sources,
        generated_at=now or datetime.now(timezone.utc),
    )
    _write_report(output, report)
    return report


def verify(
    *,
    report_path: Path,
    profile_path: Path,
    sustained_profile_path: Path,
    paths: Mapping[str, Path],
    require_qualified: bool = True,
    now: datetime | None = None,
) -> Mapping[str, Any]:
    _distinct(
        (report_path, profile_path, sustained_profile_path, *paths.values())
    )
    report, _ = _read_document(
        report_path, code="customer-failure-overlap.report.unreadable"
    )
    validate_report_document(report)
    profile = load_profile(profile_path)
    sustained_profile, sustained_profile_digest = _load_sustained_profile(
        sustained_profile_path
    )
    sources = _load_sources(paths)
    revision, dirty = _source_identity()
    if dirty:
        _fail("customer-failure-overlap.source.dirty")
    if revision != _path(profile, ("spec", "release", "sourceRevision")):
        _fail("customer-failure-overlap.source.mismatch")
    generated = _parse_time(
        _path(report, ("metadata", "generatedAt")),
        "customer-failure-overlap.report.invalid",
    )
    rebuilt = build_report(
        profile=profile,
        sustained_profile=sustained_profile,
        sustained_profile_digest=sustained_profile_digest,
        sources=sources,
        generated_at=generated,
    )
    if rebuilt != report:
        _fail("customer-failure-overlap.report.binding-mismatch")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if current >= _parse_time(
        _path(report, ("metadata", "validUntil")),
        "customer-failure-overlap.report.invalid",
    ):
        _fail("customer-failure-overlap.report.expired")
    if require_qualified and _path(report, ("spec", "status")) != "qualified":
        _fail("customer-failure-overlap.report.not-qualified")
    return report


def _paths(arguments: argparse.Namespace) -> Mapping[str, Path]:
    return {
        "customer-deployment": arguments.customer_deployment,
        "sustained-core-workload": arguments.sustained_workload,
        "control-plane-continuity": arguments.control_plane_continuity,
        "worker-receiver-continuity": arguments.processing_continuity,
        "postgresql-primary-promotion": arguments.postgresql_continuity,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    profile_id = commands.add_parser("profile-id")
    profile_id.add_argument("--profile", type=Path, required=True)
    for name in ("assess", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--profile", type=Path, required=True)
        command.add_argument("--sustained-profile", type=Path, required=True)
        command.add_argument("--customer-deployment", type=Path, required=True)
        command.add_argument("--sustained-workload", type=Path, required=True)
        command.add_argument("--control-plane-continuity", type=Path, required=True)
        command.add_argument("--processing-continuity", type=Path, required=True)
        command.add_argument("--postgresql-continuity", type=Path, required=True)
    assess_command = commands.choices["assess"]
    assess_command.add_argument("--output", type=Path, required=True)
    verify_command = commands.choices["verify"]
    verify_command.add_argument("--report", type=Path, required=True)
    verify_command.add_argument("--allow-not-qualified", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "profile-id":
            profile, _ = _read_document(
                arguments.profile,
                code="customer-failure-overlap.profile.unreadable",
                protected=True,
            )
            metadata = _mapping(
                profile.get("metadata"), "customer-failure-overlap.profile.invalid"
            )
            spec = _mapping(
                profile.get("spec"), "customer-failure-overlap.profile.invalid"
            )
            candidate = dict(profile)
            candidate_metadata = dict(metadata)
            candidate_metadata["id"] = _profile_id(candidate_metadata, spec)
            candidate["metadata"] = candidate_metadata
            validate_profile(candidate)
            print(candidate_metadata["id"])
            return 0
        if arguments.command == "assess":
            report = assess(
                profile_path=arguments.profile,
                sustained_profile_path=arguments.sustained_profile,
                paths=_paths(arguments),
                output=arguments.output,
            )
        else:
            report = verify(
                report_path=arguments.report,
                profile_path=arguments.profile,
                sustained_profile_path=arguments.sustained_profile,
                paths=_paths(arguments),
                require_qualified=not arguments.allow_not_qualified,
            )
    except CustomerFailureOverlapError as error:
        print(str(error), file=sys.stderr)
        return 1
    print("customer failure overlap qualification: " + str(_path(report, ("spec", "status"))))
    return 0 if _path(report, ("spec", "status")) == "qualified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
