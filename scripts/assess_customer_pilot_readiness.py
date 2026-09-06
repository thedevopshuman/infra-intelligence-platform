#!/usr/bin/env python3
"""Aggregate exact release and customer evidence before a private pilot."""

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

import qualify_control_plane_load as control_plane_load  # noqa: E402
import qualify_customer_ai_finops as ai_prerequisite  # noqa: E402
import qualify_customer_ai_finops_flow as ai_flow  # noqa: E402
import qualify_customer_deployment as customer_deployment  # noqa: E402
import qualify_customer_sustained_workload as sustained_workload  # noqa: E402
import release_publication  # noqa: E402
import release_readiness  # noqa: E402
import release_signature_verification as release_signature  # noqa: E402


API_VERSION = "iip.platform/v1alpha1"
PROFILE_KIND = "CustomerPilotReadinessProfile"
REPORT_KIND = "CustomerPilotReadinessReport"
QUALIFICATION_LEVEL = "customer-ai-finops-design-partner-v1"
QUALIFICATION_BOUNDARY = "private-design-partner-preflight"
PROFILE_ID = re.compile(r"^cprp_[a-f0-9]{32}$")
REPORT_ID = re.compile(r"^cpr_[a-f0-9]{32}$")
MAX_DOCUMENT_BYTES = 4 * 1024 * 1024

CHECK_IDS = (
    "source-binding",
    "profile-review",
    "evidence-freshness",
    "exact-release-identity",
    "local-release-readiness",
    "registry-publication",
    "organizational-signatures",
    "customer-deployment",
    "control-plane-load",
    "sustained-core-workload",
    "ai-finops-prerequisites",
    "same-invocation-ai-finops",
    "publication-signature-chain",
    "deployed-image-chain",
    "customer-environment-chain",
    "sustained-workload-environment-chain",
    "post-deployment-load-window",
    "post-deployment-sustained-workload-window",
    "minimized-output",
)

LIMITATIONS = (
    "private-design-partner-evaluation-only",
    "design-partner-operation-and-acceptance-not-qualified",
    "public-license-legal-brand-and-governance-not-qualified",
    "invoice-private-rates-discounts-and-commitments-not-qualified",
    "customer-workload-representativeness-and-failure-overlap-not-qualified",
    "node-zone-region-and-long-window-slo-not-qualified",
    "additional-integrations-models-providers-and-backends-not-qualified",
)

EXTERNAL_GATES = (
    (
        "design-partner-operation-and-acceptance",
        "customer-pilot-readiness.external.design-partner",
    ),
    (
        "customer-production-operating-qualification",
        "customer-pilot-readiness.external.production-operations",
    ),
    (
        "public-license-legal-brand-and-governance",
        "customer-pilot-readiness.external.public-governance",
    ),
)

TEMPORAL_EVIDENCE_ERRORS = frozenset(
    {
        "customer-pilot-readiness.evidence.expired",
        "customer-pilot-readiness.evidence.future",
        "customer-pilot-readiness.evidence.stale",
    }
)
ALLOWED_EVIDENCE_ERRORS = TEMPORAL_EVIDENCE_ERRORS | {
    "customer-pilot-readiness.evidence.source-dirty",
    "customer-pilot-readiness.evidence.status-not-qualified",
}

FORBIDDEN_RETAINED_KEYS = frozenset(
    {
        "accessToken",
        "apiBaseUrl",
        "applicationId",
        "authorization",
        "caBundle",
        "credential",
        "endpoint",
        "environmentId",
        "modelId",
        "namespace",
        "password",
        "prompt",
        "rate",
        "region",
        "repository",
        "response",
        "secret",
        "spanId",
        "teamId",
        "tenantId",
        "token",
        "traceId",
    }
)


class CustomerPilotReadinessError(RuntimeError):
    """Stable failure for unsafe, crossed, or irreproducible pilot evidence."""


@dataclass(frozen=True)
class Requirement:
    identifier: str
    kind: str
    boundary: str
    success_status: str
    evidence_class: str
    validator: Callable[[Mapping[str, Any]], None]


@dataclass(frozen=True)
class EvidenceDocument:
    document: Mapping[str, Any]
    file_digest: str
    generated_at: datetime


def _fail(code: str) -> None:
    raise CustomerPilotReadinessError(code)


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


def _validate_schema(document: object, schema_name: str, code: str) -> None:
    try:
        schema = json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail("customer-pilot-readiness.schema.unavailable")
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
            document
        ),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        _fail(code)


def _has_forbidden_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in FORBIDDEN_RETAINED_KEYS or _has_forbidden_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_forbidden_key(item) for item in value)
    return False


def _profile_id(metadata: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    return "cprp_" + hashlib.sha256(
        _canonical(
            {
                "reviewedAt": metadata.get("reviewedAt"),
                "validUntil": metadata.get("validUntil"),
                "spec": spec,
            }
        )
    ).hexdigest()[:32]


def validate_profile(profile: Mapping[str, Any]) -> None:
    code = "customer-pilot-readiness.profile.invalid"
    _validate_schema(profile, "customer-pilot-readiness-profile.schema.json", code)
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


def _read_document(
    path: Path,
    *,
    code: str,
    protected: bool = False,
) -> tuple[Mapping[str, Any], str]:
    candidate = path.expanduser()
    descriptor = -1
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


def load_profile(path: Path) -> Mapping[str, Any]:
    profile, _ = _read_document(
        path,
        code="customer-pilot-readiness.profile.unreadable",
        protected=True,
    )
    validate_profile(profile)
    return profile


def _validate_release_readiness(report: Mapping[str, Any]) -> None:
    code = "customer-pilot-readiness.release-readiness.invalid"
    _validate_schema(report, "release-readiness-report.schema.json", code)
    metadata = _mapping(report.get("metadata"), code)
    spec = _mapping(report.get("spec"), code)
    release = _mapping(spec.get("release"), code)
    evidence = _sequence(spec.get("evidence"), code)
    external = _sequence(spec.get("externalGates"), code)
    expected_ids = tuple(item.identifier for item in release_readiness.REQUIREMENTS)
    observed_ids = tuple(_mapping(item, code).get("id") for item in evidence)
    passed = sum(_mapping(item, code).get("status") == "passed" for item in evidence)
    missing = sum(_mapping(item, code).get("status") == "missing" for item in evidence)
    rejected = sum(
        _mapping(item, code).get("status") == "rejected" for item in evidence
    )
    status = "locally-qualified" if passed == len(expected_ids) else "incomplete"
    expected_summary = {
        "requiredEvidence": len(expected_ids),
        "passedEvidence": passed,
        "missingEvidence": missing,
        "rejectedEvidence": rejected,
        "externalGateCount": len(release_readiness.EXTERNAL_GATES),
        "overallStatus": status,
    }
    if (
        report.get("apiVersion") != "iip.dev/v1alpha1"
        or report.get("kind") != "ReleaseReadinessReport"
        or metadata.get("sourceRevision") != release.get("revision")
        or spec.get("qualificationBoundary") != "local-candidate-only"
        or spec.get("status") != status
        or observed_ids != expected_ids
        or list(external) != release_readiness._external_gates()
        or spec.get("summary") != expected_summary
    ):
        _fail(code)


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
        "release-readiness",
        "ReleaseReadinessReport",
        "local-candidate",
        "locally-qualified",
        "foundation",
        _validate_release_readiness,
    ),
    Requirement(
        "registry-publication",
        "ReleasePublicationReport",
        "registry-publication",
        "published-unsigned",
        "foundation",
        _wrap_validator(
            release_publication.validate_report,
            release_publication.ReleasePublicationError,
            "customer-pilot-readiness.registry-publication.invalid",
        ),
    ),
    Requirement(
        "organizational-signatures",
        "ReleaseSignatureVerificationReport",
        "organizational-trust",
        "verified",
        "foundation",
        _wrap_validator(
            release_signature.validate_report_document,
            release_signature.ReleaseSignatureError,
            "customer-pilot-readiness.release-signature.invalid",
        ),
    ),
    Requirement(
        "customer-deployment",
        "CustomerDeploymentQualificationReport",
        "customer-deployment",
        "qualified",
        "customer",
        _wrap_validator(
            customer_deployment.validate_report_document,
            customer_deployment.CustomerDeploymentQualificationError,
            "customer-pilot-readiness.customer-deployment.invalid",
        ),
    ),
    Requirement(
        "control-plane-load",
        "ControlPlaneLoadQualificationReport",
        "customer-load",
        "qualified",
        "customer",
        _wrap_validator(
            control_plane_load.validate_report_document,
            control_plane_load.ControlPlaneLoadQualificationError,
            "customer-pilot-readiness.control-plane-load.invalid",
        ),
    ),
    Requirement(
        "sustained-core-workload",
        "CustomerSustainedWorkloadQualificationReport",
        "customer-environment-sustained-workload",
        "qualified",
        "customer",
        _wrap_validator(
            sustained_workload.validate_report_document,
            sustained_workload.CustomerSustainedWorkloadError,
            "customer-pilot-readiness.sustained-workload.invalid",
        ),
    ),
    Requirement(
        "ai-finops-prerequisites",
        "CustomerAiFinopsPrerequisiteReport",
        "ai-finops-prerequisites",
        "prerequisites-ready",
        "customer",
        _wrap_validator(
            ai_prerequisite.validate_report_document,
            ai_prerequisite.CustomerAiFinopsPrerequisiteError,
            "customer-pilot-readiness.ai-prerequisites.invalid",
        ),
    ),
    Requirement(
        "same-invocation-ai-finops",
        "CustomerAiFinopsFlowQualificationReport",
        "same-invocation-customer-runtime",
        "qualified",
        "customer",
        _wrap_validator(
            ai_flow.validate_report_document,
            ai_flow.CustomerAiFinopsFlowError,
            "customer-pilot-readiness.ai-flow.invalid",
        ),
    ),
)


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
        _fail("customer-pilot-readiness.source.unavailable")
    return revision, dirty


def _load_sources(paths: Mapping[str, Path]) -> Mapping[str, EvidenceDocument]:
    documents: dict[str, EvidenceDocument] = {}
    for requirement in REQUIREMENTS:
        document, digest = _read_document(
            paths[requirement.identifier],
            code=f"customer-pilot-readiness.{requirement.identifier}.unreadable",
        )
        requirement.validator(document)
        documents[requirement.identifier] = EvidenceDocument(
            document=document,
            file_digest=digest,
            generated_at=_parse_time(
                _path(document, ("metadata", "generatedAt")),
                f"customer-pilot-readiness.{requirement.identifier}.invalid",
            ),
        )
    return documents


def _status(document: Mapping[str, Any]) -> str:
    value = _path(document, ("spec", "status"))
    if not isinstance(value, str):
        _fail("customer-pilot-readiness.evidence.invalid")
    return value


def _release_and_environment_bindings(
    profile: Mapping[str, Any], sources: Mapping[str, EvidenceDocument]
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    code = "customer-pilot-readiness.evidence.crossed"
    profile_spec = _mapping(profile.get("spec"), code)
    expected_release = _mapping(profile_spec.get("release"), code)
    expected_bindings = _mapping(profile_spec.get("bindings"), code)
    readiness = sources["release-readiness"].document
    publication = sources["registry-publication"].document
    signature = sources["organizational-signatures"].document
    deployment = sources["customer-deployment"].document
    load = sources["control-plane-load"].document
    sustained = sources["sustained-core-workload"].document
    prerequisites = sources["ai-finops-prerequisites"].document
    flow = sources["same-invocation-ai-finops"].document

    readiness_release = _mapping(_path(readiness, ("spec", "release")), code)
    publication_release = _mapping(_path(publication, ("spec", "release")), code)
    signature_release = _mapping(_path(signature, ("spec", "release")), code)
    deployment_subject = _mapping(_path(deployment, ("spec", "subject")), code)
    deployment_bindings = _mapping(_path(deployment, ("spec", "bindings")), code)
    load_identity = _mapping(_path(load, ("spec", "targetIdentity")), code)
    sustained_subject = _mapping(_path(sustained, ("spec", "subject")), code)
    sustained_bindings = _mapping(_path(sustained, ("spec", "bindings")), code)
    prerequisite_subject = _mapping(_path(prerequisites, ("spec", "subject")), code)
    prerequisite_bindings = _mapping(_path(prerequisites, ("spec", "bindings")), code)
    flow_subject = _mapping(_path(flow, ("spec", "subject")), code)
    flow_bindings = _mapping(_path(flow, ("spec", "bindings")), code)
    source_metadata = tuple(
        _mapping(_path(source.document, ("metadata",)), code)
        for source in sources.values()
    )
    publication_targets = tuple(
        _mapping(item, code)
        for item in _sequence(_path(publication, ("spec", "targets")), code)
    )
    signature_artifacts = tuple(
        _mapping(item, code)
        for item in _sequence(_path(signature, ("spec", "artifacts")), code)
    )
    if (
        tuple(item.get("role") for item in publication_targets)
        != ("control-plane-image", "plugin-mediation-bridge-image")
        or tuple(item.get("role") for item in signature_artifacts)
        != ("control-plane-image", "plugin-mediation-bridge-image")
    ):
        _fail(code)
    control_digest = publication_targets[0].get("indexDigest")
    bridge_digest = publication_targets[1].get("indexDigest")
    exact_release = {
        "applicationVersion": readiness_release.get("version"),
        "chartVersion": readiness_release.get("chartVersion"),
        "sourceRevision": readiness_release.get("revision"),
        "manifestDigest": readiness_release.get("manifestDigest"),
        "controlPlaneImageDigest": control_digest,
        "pluginMediationBridgeImageDigest": bridge_digest,
    }
    expected_customer_subject = {
        "deploymentProfile": "production-ai-finops-v0",
        "applicationVersion": exact_release["applicationVersion"],
        "chartVersion": exact_release["chartVersion"],
        "contractsApiVersion": API_VERSION,
        "sourceRevision": exact_release["sourceRevision"],
        "imageDigest": control_digest,
    }
    expected_sustained_subject = {
        "applicationVersion": exact_release["applicationVersion"],
        "chartVersion": exact_release["chartVersion"],
        "contractsApiVersion": API_VERSION,
        "requiredMigration": deployment_subject.get("requiredMigration"),
        "sourceRevision": exact_release["sourceRevision"],
        "imageDigest": control_digest,
    }
    if (
        dict(expected_release) != exact_release
        or publication_release.get("version") != exact_release["applicationVersion"]
        or publication_release.get("manifestDigest") != exact_release["manifestDigest"]
        or _path(publication, ("metadata", "sourceRevision"))
        != exact_release["sourceRevision"]
        or signature_release
        != {
            "version": exact_release["applicationVersion"],
            "revision": exact_release["sourceRevision"],
            "manifestDigest": exact_release["manifestDigest"],
        }
        or tuple(item.get("indexDigest") for item in signature_artifacts)
        != (control_digest, bridge_digest)
        or deployment_subject.get("profile") != "production-ai-finops-v0"
        or {
            key: deployment_subject.get(key)
            for key in (
                "applicationVersion",
                "chartVersion",
                "contractsApiVersion",
                "sourceRevision",
                "imageDigest",
            )
        }
        != {key: expected_customer_subject[key] for key in (
            "applicationVersion",
            "chartVersion",
            "contractsApiVersion",
            "sourceRevision",
            "imageDigest",
        )}
        or prerequisite_subject != expected_customer_subject
        or flow_subject != expected_customer_subject
        or sustained_subject != expected_sustained_subject
        or any(
            metadata.get("sourceRevision") != exact_release["sourceRevision"]
            for metadata in source_metadata
        )
        or load_identity
        != {
            "applicationVersion": exact_release["applicationVersion"],
            "contractsApiVersion": API_VERSION,
            "requiredMigration": deployment_subject.get("requiredMigration"),
            "buildMode": "release",
            "sourceRevision": exact_release["sourceRevision"],
            "chartVersion": exact_release["chartVersion"],
            "imageDigest": control_digest,
        }
    ):
        _fail(code)

    policy_digest = _path(signature, ("spec", "policy", "digest"))
    target_set_digest = _digest(list(publication_targets))
    cluster_digest = deployment_bindings.get("clusterBindingDigest")
    environment_digest = prerequisite_bindings.get("environmentBindingDigest")
    control_target_digest = deployment_bindings.get("continuityTargetBindingDigest")
    environment_set = {
        "clusterBindingDigest": cluster_digest,
        "environmentBindingDigest": environment_digest,
        "controlPlaneTargetDigest": control_target_digest,
        "otlpTargetDigest": sustained_bindings.get("otlpTargetBindingDigest"),
        "sustainedWorkloadProfileDigest": sustained_bindings.get("profileDigest"),
    }
    if (
        expected_bindings.get("signaturePolicyDigest") != policy_digest
        or expected_bindings.get("publicationTargetSetDigest") != target_set_digest
        or expected_bindings.get("clusterBindingDigest") != cluster_digest
        or expected_bindings.get("environmentBindingDigest") != environment_digest
        or expected_bindings.get("controlPlaneTargetDigest") != control_target_digest
        or expected_bindings.get("otlpTargetDigest")
        != sustained_bindings.get("otlpTargetBindingDigest")
        or expected_bindings.get("sustainedWorkloadProfileDigest")
        != sustained_bindings.get("profileDigest")
        or _path(load, ("spec", "targetBindingDigest")) != control_target_digest
        or sustained_bindings.get("apiTargetBindingDigest")
        != control_target_digest
        or sustained_bindings.get("otlpTargetBindingDigest")
        != deployment_bindings.get("processingOtlpTargetBindingDigest")
        or sustained_bindings.get("otlpTargetBindingDigest")
        != deployment_bindings.get("otlpReceiverEndpointBindingDigest")
        or flow_bindings.get("controlPlaneTargetDigest") != control_target_digest
        or flow_bindings.get("environmentBindingDigest") != environment_digest
        or prerequisite_bindings.get("releaseReadinessReportDigest")
        != sources["release-readiness"].file_digest
        or prerequisite_bindings.get("customerDeploymentReportDigest")
        != sources["customer-deployment"].file_digest
        or flow_bindings.get("prerequisiteReportDigest")
        != sources["ai-finops-prerequisites"].file_digest
    ):
        _fail(code)
    return exact_release, environment_set


def _check(identifier: str, passed: bool, error: str | None = None) -> dict[str, str]:
    result = {"id": identifier, "status": "passed" if passed else "failed"}
    if not passed:
        result["errorCode"] = error or f"customer-pilot-readiness.{identifier}.failed"
    return result


def _evidence_item(
    requirement: Requirement,
    source: EvidenceDocument,
    *,
    current: datetime,
    maximum_age_seconds: int,
    maximum_clock_skew_seconds: int,
) -> dict[str, Any]:
    document = source.document
    status = _status(document)
    result: dict[str, Any] = {
        "id": requirement.identifier,
        "contractKind": requirement.kind,
        "qualificationBoundary": requirement.boundary,
        "reportId": _path(document, ("metadata", "id")),
        "reportDigest": source.file_digest,
        "observedStatus": status,
        "status": "passed",
    }
    reason: str | None = None
    if source.generated_at > current + timedelta(seconds=maximum_clock_skew_seconds):
        reason = "customer-pilot-readiness.evidence.future"
    elif current - source.generated_at >= timedelta(seconds=maximum_age_seconds):
        reason = "customer-pilot-readiness.evidence.stale"
    elif requirement.identifier in {
        "sustained-core-workload",
        "ai-finops-prerequisites",
        "same-invocation-ai-finops",
    }:
        expires = _parse_time(
            _path(document, ("metadata", "validUntil")),
            "customer-pilot-readiness.evidence.invalid",
        )
        if current >= expires:
            reason = "customer-pilot-readiness.evidence.expired"
    if reason is None and _path(document, ("metadata", "sourceDirty")) is not False:
        reason = "customer-pilot-readiness.evidence.source-dirty"
    if reason is None and status != requirement.success_status:
        reason = "customer-pilot-readiness.evidence.status-not-qualified"
    if reason is not None:
        result["status"] = "rejected"
        result["errorCode"] = reason
    return result


def _summary(
    evidence: Sequence[Mapping[str, Any]], checks: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    passed_evidence = sum(item.get("status") == "passed" for item in evidence)
    failed_checks = sum(item.get("status") == "failed" for item in checks)
    status = (
        "design-partner-candidate"
        if passed_evidence == len(REQUIREMENTS) and failed_checks == 0
        else "not-candidate"
    )
    return {
        "requiredEvidence": len(REQUIREMENTS),
        "passedEvidence": passed_evidence,
        "rejectedEvidence": len(REQUIREMENTS) - passed_evidence,
        "totalChecks": len(CHECK_IDS),
        "passedChecks": len(CHECK_IDS) - failed_checks,
        "failedChecks": failed_checks,
        "externalGateCount": len(EXTERNAL_GATES),
        "overallStatus": status,
    }


def _derived_checks(
    evidence: Sequence[Mapping[str, Any]],
    *,
    post_deployment_load: bool,
    post_deployment_sustained_workload: bool,
) -> list[dict[str, str]]:
    evidence_pass = {
        str(item["id"]): item["status"] == "passed" for item in evidence
    }
    evidence_fresh = {
        str(item["id"]): item.get("errorCode") not in TEMPORAL_EVIDENCE_ERRORS
        for item in evidence
    }
    return [
        _check("source-binding", True),
        _check("profile-review", True),
        _check(
            "evidence-freshness",
            all(evidence_fresh.values()),
            "customer-pilot-readiness.evidence.not-current",
        ),
        _check("exact-release-identity", True),
        _check("local-release-readiness", evidence_pass["release-readiness"]),
        _check("registry-publication", evidence_pass["registry-publication"]),
        _check(
            "organizational-signatures", evidence_pass["organizational-signatures"]
        ),
        _check("customer-deployment", evidence_pass["customer-deployment"]),
        _check("control-plane-load", evidence_pass["control-plane-load"]),
        _check(
            "sustained-core-workload",
            evidence_pass["sustained-core-workload"],
        ),
        _check(
            "ai-finops-prerequisites", evidence_pass["ai-finops-prerequisites"]
        ),
        _check(
            "same-invocation-ai-finops",
            evidence_pass["same-invocation-ai-finops"],
        ),
        _check("publication-signature-chain", True),
        _check("deployed-image-chain", True),
        _check("customer-environment-chain", True),
        _check("sustained-workload-environment-chain", True),
        _check(
            "post-deployment-load-window",
            post_deployment_load,
            "customer-pilot-readiness.load.before-deployment",
        ),
        _check(
            "post-deployment-sustained-workload-window",
            post_deployment_sustained_workload,
            "customer-pilot-readiness.sustained-workload.before-deployment",
        ),
        _check("minimized-output", True),
    ]


def _report_id(metadata: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    return "cpr_" + hashlib.sha256(
        _canonical({"metadata": metadata, "spec": spec})
    ).hexdigest()[:32]


def build_report(
    *,
    profile: Mapping[str, Any],
    sources: Mapping[str, EvidenceDocument],
    generated_at: datetime,
) -> Mapping[str, Any]:
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        _fail("customer-pilot-readiness.time.invalid")
    current = generated_at.astimezone(timezone.utc).replace(microsecond=0)
    validate_profile(profile)
    exact_release, environment_set = _release_and_environment_bindings(
        profile, sources
    )
    profile_metadata = _mapping(
        profile.get("metadata"), "customer-pilot-readiness.profile.invalid"
    )
    profile_spec = _mapping(
        profile.get("spec"), "customer-pilot-readiness.profile.invalid"
    )
    objective = _mapping(
        profile_spec.get("objective"), "customer-pilot-readiness.profile.invalid"
    )
    reviewed = _parse_time(
        profile_metadata.get("reviewedAt"),
        "customer-pilot-readiness.profile.invalid",
    )
    profile_valid_until = _parse_time(
        profile_metadata.get("validUntil"),
        "customer-pilot-readiness.profile.invalid",
    )
    clock_skew = int(objective["maximumClockSkewSeconds"])
    profile_current = (
        reviewed <= current + timedelta(seconds=clock_skew)
        and current
        < reviewed
        + timedelta(seconds=int(objective["maximumProfileAgeSeconds"]))
        and current < profile_valid_until
    )
    if not profile_current:
        _fail("customer-pilot-readiness.profile.expired")

    evidence = []
    for requirement in REQUIREMENTS:
        maximum_age = int(
            objective[
                "maximumFoundationEvidenceAgeSeconds"
                if requirement.evidence_class == "foundation"
                else "maximumCustomerEvidenceAgeSeconds"
            ]
        )
        evidence.append(
            _evidence_item(
                requirement,
                sources[requirement.identifier],
                current=current,
                maximum_age_seconds=maximum_age,
                maximum_clock_skew_seconds=clock_skew,
            )
        )

    foundation_times = [
        sources[item.identifier].generated_at
        for item in REQUIREMENTS
        if item.evidence_class == "foundation"
    ]
    customer_times = [
        sources[item.identifier].generated_at
        for item in REQUIREMENTS
        if item.evidence_class == "customer"
    ]
    deployment_time = sources["customer-deployment"].generated_at
    load_started = _parse_time(
        _path(
            sources["control-plane-load"].document,
            ("spec", "measurements", "startedAt"),
        ),
        "customer-pilot-readiness.control-plane-load.invalid",
    )
    load_completed = sources["control-plane-load"].generated_at
    sustained_started = _parse_time(
        _path(
            sources["sustained-core-workload"].document,
            ("spec", "measurements", "startedAt"),
        ),
        "customer-pilot-readiness.sustained-workload.invalid",
    )
    sustained_completed = sources["sustained-core-workload"].generated_at
    flow_completed = sources["same-invocation-ai-finops"].generated_at
    post_deployment_load = deployment_time <= load_started <= load_completed
    post_deployment_sustained_workload = (
        deployment_time <= sustained_started <= sustained_completed
    )
    evidence_fresh = {
        str(item["id"]): item.get("errorCode") not in TEMPORAL_EVIDENCE_ERRORS
        for item in evidence
    }
    checks = _derived_checks(
        evidence,
        post_deployment_load=post_deployment_load,
        post_deployment_sustained_workload=post_deployment_sustained_workload,
    )
    summary = _summary(evidence, checks)
    validity_limits = [
        profile_valid_until,
        reviewed
        + timedelta(seconds=int(objective["maximumProfileAgeSeconds"])),
        current + timedelta(seconds=int(objective["reportValiditySeconds"])),
    ]
    for requirement, item in zip(REQUIREMENTS, evidence):
        if not evidence_fresh[requirement.identifier]:
            continue
        maximum_age = int(
            objective[
                "maximumFoundationEvidenceAgeSeconds"
                if requirement.evidence_class == "foundation"
                else "maximumCustomerEvidenceAgeSeconds"
            ]
        )
        validity_limits.append(
            sources[requirement.identifier].generated_at
            + timedelta(seconds=maximum_age)
        )
        if requirement.identifier in {
            "sustained-core-workload",
            "ai-finops-prerequisites",
            "same-invocation-ai-finops",
        }:
            validity_limits.append(
                _parse_time(
                    _path(
                        sources[requirement.identifier].document,
                        ("metadata", "validUntil"),
                    ),
                    "customer-pilot-readiness.evidence.invalid",
                )
            )
    valid_until = min(validity_limits)
    if valid_until <= current:
        _fail("customer-pilot-readiness.report.no-validity")
    report_metadata: dict[str, Any] = {
        "generatedAt": _timestamp(current),
        "validUntil": _timestamp(valid_until),
        "sourceRevision": exact_release["sourceRevision"],
        "sourceDirty": False,
    }
    report_spec: dict[str, Any] = {
        "status": summary["overallStatus"],
        "qualificationLevel": QUALIFICATION_LEVEL,
        "qualificationBoundary": QUALIFICATION_BOUNDARY,
        "subject": {
            **exact_release,
            "contractsApiVersion": API_VERSION,
        },
        "bindings": {
            "profileDigest": _digest(profile),
            "releaseReadinessReportDigest": sources[
                "release-readiness"
            ].file_digest,
            "releasePublicationReportDigest": sources[
                "registry-publication"
            ].file_digest,
            "releaseSignatureReportDigest": sources[
                "organizational-signatures"
            ].file_digest,
            "customerDeploymentReportDigest": sources[
                "customer-deployment"
            ].file_digest,
            "controlPlaneLoadReportDigest": sources[
                "control-plane-load"
            ].file_digest,
            "sustainedWorkloadReportDigest": sources[
                "sustained-core-workload"
            ].file_digest,
            "aiFinopsPrerequisiteReportDigest": sources[
                "ai-finops-prerequisites"
            ].file_digest,
            "aiFinopsFlowReportDigest": sources[
                "same-invocation-ai-finops"
            ].file_digest,
            "signaturePolicyDigest": _path(
                sources["organizational-signatures"].document,
                ("spec", "policy", "digest"),
            ),
            "publicationTargetSetDigest": _digest(
                _path(
                    sources["registry-publication"].document,
                    ("spec", "targets"),
                )
            ),
            "customerEnvironmentSetDigest": _digest(environment_set),
        },
        "objective": {
            key: objective[key]
            for key in (
                "maximumProfileAgeSeconds",
                "maximumFoundationEvidenceAgeSeconds",
                "maximumCustomerEvidenceAgeSeconds",
                "maximumClockSkewSeconds",
                "reportValiditySeconds",
            )
        },
        "measurements": {
            "profileReviewedAt": _timestamp(reviewed),
            "oldestFoundationEvidenceAt": _timestamp(min(foundation_times)),
            "newestFoundationEvidenceAt": _timestamp(max(foundation_times)),
            "oldestCustomerEvidenceAt": _timestamp(min(customer_times)),
            "customerDeploymentQualifiedAt": _timestamp(deployment_time),
            "controlPlaneLoadStartedAt": _timestamp(load_started),
            "controlPlaneLoadCompletedAt": _timestamp(load_completed),
            "sustainedWorkloadStartedAt": _timestamp(sustained_started),
            "sustainedWorkloadCompletedAt": _timestamp(sustained_completed),
            "aiFinopsFlowCompletedAt": _timestamp(flow_completed),
            "assessedAt": _timestamp(current),
            "oldestFoundationEvidenceAgeSeconds": max(
                0, int((current - min(foundation_times)).total_seconds())
            ),
            "oldestCustomerEvidenceAgeSeconds": max(
                0, int((current - min(customer_times)).total_seconds())
            ),
        },
        "evidence": evidence,
        "checks": checks,
        "limitations": list(LIMITATIONS),
        "externalGates": [
            {"id": identifier, "status": "external-required", "reasonCode": reason}
            for identifier, reason in EXTERNAL_GATES
        ],
        "summary": summary,
    }
    if _has_forbidden_key(report_spec):
        _fail("customer-pilot-readiness.output.not-minimized")
    report_metadata["id"] = _report_id(report_metadata, report_spec)
    report = {
        "apiVersion": API_VERSION,
        "kind": REPORT_KIND,
        "metadata": report_metadata,
        "spec": report_spec,
    }
    validate_report_document(report)
    return report


def validate_report_document(report: Mapping[str, Any]) -> None:
    code = "customer-pilot-readiness.report.invalid"
    _validate_schema(report, "customer-pilot-readiness-report.schema.json", code)
    metadata = _mapping(report.get("metadata"), code)
    spec = _mapping(report.get("spec"), code)
    subject = _mapping(spec.get("subject"), code)
    evidence = tuple(
        _mapping(item, code) for item in _sequence(spec.get("evidence"), code)
    )
    checks = tuple(
        _mapping(item, code) for item in _sequence(spec.get("checks"), code)
    )
    without_id = dict(metadata)
    identifier = without_id.pop("id", None)
    expected_summary = _summary(evidence, checks)
    expected_evidence = tuple(
        (definition.identifier, definition.kind, definition.boundary)
        for definition in REQUIREMENTS
    )
    observed_evidence = tuple(
        (item.get("id"), item.get("contractKind"), item.get("qualificationBoundary"))
        for item in evidence
    )
    generated = _parse_time(metadata.get("generatedAt"), code)
    valid_until = _parse_time(metadata.get("validUntil"), code)
    measurements = _mapping(spec.get("measurements"), code)
    objective = _mapping(spec.get("objective"), code)
    evidence_by_id = {str(item.get("id")): item for item in evidence}
    for requirement in REQUIREMENTS:
        item = evidence_by_id.get(requirement.identifier)
        if item is None:
            _fail(code)
        observed_status = item.get("observedStatus")
        item_status = item.get("status")
        error_code = item.get("errorCode")
        if (
            (item_status == "passed" and observed_status != requirement.success_status)
            or (item_status == "rejected" and error_code not in ALLOWED_EVIDENCE_ERRORS)
            or (
                error_code
                == "customer-pilot-readiness.evidence.status-not-qualified"
                and observed_status == requirement.success_status
            )
        ):
            _fail(code)
    profile_reviewed = _parse_time(measurements.get("profileReviewedAt"), code)
    oldest_foundation = _parse_time(
        measurements.get("oldestFoundationEvidenceAt"), code
    )
    newest_foundation = _parse_time(
        measurements.get("newestFoundationEvidenceAt"), code
    )
    oldest_customer = _parse_time(
        measurements.get("oldestCustomerEvidenceAt"), code
    )
    deployment = _parse_time(
        measurements.get("customerDeploymentQualifiedAt"), code
    )
    load_started = _parse_time(measurements.get("controlPlaneLoadStartedAt"), code)
    load_completed = _parse_time(
        measurements.get("controlPlaneLoadCompletedAt"), code
    )
    sustained_started = _parse_time(
        measurements.get("sustainedWorkloadStartedAt"), code
    )
    sustained_completed = _parse_time(
        measurements.get("sustainedWorkloadCompletedAt"), code
    )
    flow_completed = _parse_time(measurements.get("aiFinopsFlowCompletedAt"), code)
    expected_checks = _derived_checks(
        evidence,
        post_deployment_load=deployment <= load_started <= load_completed,
        post_deployment_sustained_workload=(
            deployment <= sustained_started <= sustained_completed
        ),
    )
    expected_foundation_age = max(
        0, int((generated - oldest_foundation).total_seconds())
    )
    expected_customer_age = max(
        0, int((generated - oldest_customer).total_seconds())
    )
    validity_ceiling = min(
        generated + timedelta(seconds=int(objective["reportValiditySeconds"])),
        profile_reviewed
        + timedelta(seconds=int(objective["maximumProfileAgeSeconds"])),
    )
    if all(
        evidence_by_id[item.identifier].get("errorCode")
        not in TEMPORAL_EVIDENCE_ERRORS
        for item in REQUIREMENTS
        if item.evidence_class == "foundation"
    ):
        validity_ceiling = min(
            validity_ceiling,
            oldest_foundation
            + timedelta(
                seconds=int(objective["maximumFoundationEvidenceAgeSeconds"])
            ),
        )
    if all(
        evidence_by_id[item.identifier].get("errorCode")
        not in TEMPORAL_EVIDENCE_ERRORS
        for item in REQUIREMENTS
        if item.evidence_class == "customer"
    ):
        validity_ceiling = min(
            validity_ceiling,
            oldest_customer
            + timedelta(seconds=int(objective["maximumCustomerEvidenceAgeSeconds"])),
        )
    if (
        report.get("apiVersion") != API_VERSION
        or report.get("kind") != REPORT_KIND
        or spec.get("qualificationLevel") != QUALIFICATION_LEVEL
        or spec.get("qualificationBoundary") != QUALIFICATION_BOUNDARY
        or metadata.get("sourceRevision") != subject.get("sourceRevision")
        or spec.get("status") != expected_summary["overallStatus"]
        or spec.get("summary") != expected_summary
        or observed_evidence != expected_evidence
        or any(
            _path(spec, ("bindings", binding_name))
            != evidence_by_id[evidence_id].get("reportDigest")
            for binding_name, evidence_id in (
                ("releaseReadinessReportDigest", "release-readiness"),
                ("releasePublicationReportDigest", "registry-publication"),
                ("releaseSignatureReportDigest", "organizational-signatures"),
                ("customerDeploymentReportDigest", "customer-deployment"),
                ("controlPlaneLoadReportDigest", "control-plane-load"),
                ("sustainedWorkloadReportDigest", "sustained-core-workload"),
                ("aiFinopsPrerequisiteReportDigest", "ai-finops-prerequisites"),
                ("aiFinopsFlowReportDigest", "same-invocation-ai-finops"),
            )
        )
        or list(checks) != expected_checks
        or tuple(item.get("id") for item in checks) != CHECK_IDS
        or spec.get("limitations") != list(LIMITATIONS)
        or spec.get("externalGates")
        != [
            {"id": item, "status": "external-required", "reasonCode": reason}
            for item, reason in EXTERNAL_GATES
        ]
        or valid_until <= generated
        or valid_until > validity_ceiling
        or oldest_foundation > newest_foundation
        or flow_completed > generated + timedelta(
            seconds=int(objective["maximumClockSkewSeconds"])
        )
        or sustained_completed > generated + timedelta(
            seconds=int(objective["maximumClockSkewSeconds"])
        )
        or measurements.get("oldestFoundationEvidenceAgeSeconds")
        != expected_foundation_age
        or measurements.get("oldestCustomerEvidenceAgeSeconds")
        != expected_customer_age
        or measurements.get("assessedAt") != metadata.get("generatedAt")
        or _has_forbidden_key(report)
        or not isinstance(identifier, str)
        or REPORT_ID.fullmatch(identifier) is None
        or identifier != _report_id(without_id, spec)
    ):
        _fail(code)


def _ensure_distinct(paths: Sequence[Path]) -> None:
    resolved = [path.expanduser().absolute() for path in paths]
    if len(resolved) != len(set(resolved)):
        _fail("customer-pilot-readiness.paths.overlap")


def qualify(
    *,
    profile_path: Path,
    paths: Mapping[str, Path],
    now: datetime | None = None,
) -> Mapping[str, Any]:
    profile = load_profile(profile_path)
    revision, dirty = _source_identity()
    if dirty:
        _fail("customer-pilot-readiness.source.dirty")
    if revision != _path(profile, ("spec", "release", "sourceRevision")):
        _fail("customer-pilot-readiness.source.revision-mismatch")
    return build_report(
        profile=profile,
        sources=_load_sources(paths),
        generated_at=now or datetime.now(timezone.utc),
    )


def verify(
    *,
    report_path: Path,
    profile_path: Path,
    paths: Mapping[str, Path],
    require_candidate: bool = False,
    now: datetime | None = None,
) -> Mapping[str, Any]:
    report, _ = _read_document(
        report_path, code="customer-pilot-readiness.report.unreadable"
    )
    validate_report_document(report)
    revision, dirty = _source_identity()
    if dirty:
        _fail("customer-pilot-readiness.source.dirty")
    if revision != _path(report, ("metadata", "sourceRevision")):
        _fail("customer-pilot-readiness.source.revision-mismatch")
    expected = build_report(
        profile=load_profile(profile_path),
        sources=_load_sources(paths),
        generated_at=_parse_time(
            _path(report, ("metadata", "generatedAt")),
            "customer-pilot-readiness.report.invalid",
        ),
    )
    if report != expected:
        _fail("customer-pilot-readiness.report.evidence-mismatch")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if current >= _parse_time(
        _path(report, ("metadata", "validUntil")),
        "customer-pilot-readiness.report.invalid",
    ):
        _fail("customer-pilot-readiness.report.expired")
    if require_candidate and _status(report) != "design-partner-candidate":
        _fail("customer-pilot-readiness.report.not-candidate")
    return report


def _write(path: Path, report: Mapping[str, Any]) -> None:
    destination = path.expanduser().absolute()
    if destination.is_symlink():
        _fail("customer-pilot-readiness.output.invalid")
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
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
    except OSError:
        _fail("customer-pilot-readiness.output.invalid")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _source_paths(arguments: argparse.Namespace) -> dict[str, Path]:
    return {
        "release-readiness": arguments.release_readiness,
        "registry-publication": arguments.release_publication,
        "organizational-signatures": arguments.release_signatures,
        "customer-deployment": arguments.customer_deployment,
        "control-plane-load": arguments.control_plane_load,
        "sustained-core-workload": arguments.sustained_workload,
        "ai-finops-prerequisites": arguments.ai_finops_prerequisites,
        "same-invocation-ai-finops": arguments.ai_finops_flow,
    }


def _add_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--release-readiness", type=Path, required=True)
    parser.add_argument("--release-publication", type=Path, required=True)
    parser.add_argument("--release-signatures", type=Path, required=True)
    parser.add_argument("--customer-deployment", type=Path, required=True)
    parser.add_argument("--control-plane-load", type=Path, required=True)
    parser.add_argument("--sustained-workload", type=Path, required=True)
    parser.add_argument("--ai-finops-prerequisites", type=Path, required=True)
    parser.add_argument("--ai-finops-flow", type=Path, required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    profile_id = commands.add_parser("profile-id")
    profile_id.add_argument("--profile", type=Path, required=True)
    publication_digest = commands.add_parser("publication-target-set-digest")
    publication_digest.add_argument(
        "--release-publication", type=Path, required=True
    )
    generate = commands.add_parser("generate")
    _add_inputs(generate)
    generate.add_argument("--output", type=Path, required=True)
    verify_command = commands.add_parser("verify")
    _add_inputs(verify_command)
    verify_command.add_argument("--report", type=Path, required=True)
    verify_command.add_argument("--require-candidate", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "profile-id":
            profile, _ = _read_document(
                arguments.profile,
                code="customer-pilot-readiness.profile.unreadable",
                protected=True,
            )
            _validate_schema(
                profile,
                "customer-pilot-readiness-profile.schema.json",
                "customer-pilot-readiness.profile.invalid",
            )
            metadata = _mapping(
                profile.get("metadata"),
                "customer-pilot-readiness.profile.invalid",
            )
            spec = _mapping(
                profile.get("spec"),
                "customer-pilot-readiness.profile.invalid",
            )
            print(_profile_id(metadata, spec))
            return 0
        if arguments.command == "publication-target-set-digest":
            publication, _ = _read_document(
                arguments.release_publication,
                code="customer-pilot-readiness.registry-publication.unreadable",
            )
            REQUIREMENTS[1].validator(publication)
            print(_digest(_path(publication, ("spec", "targets"))))
            return 0
        paths = _source_paths(arguments)
        if arguments.command == "generate":
            _ensure_distinct(
                [arguments.output, arguments.profile, *paths.values()]
            )
            report = qualify(profile_path=arguments.profile, paths=paths)
            _write(arguments.output, report)
            print(f"customer pilot readiness {report['spec']['status']}: {arguments.output}")
            return 0 if _status(report) == "design-partner-candidate" else 1
        _ensure_distinct(
            [arguments.report, arguments.profile, *paths.values()]
        )
        verify(
            report_path=arguments.report,
            profile_path=arguments.profile,
            paths=paths,
            require_candidate=arguments.require_candidate,
        )
        print("customer pilot readiness report verified")
    except CustomerPilotReadinessError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
