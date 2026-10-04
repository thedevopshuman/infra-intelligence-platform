#!/usr/bin/env python3
"""Bind current customer AI FinOps prerequisites without overstating E2E proof."""

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
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "contracts" / "schemas"
API_VERSION = "iip.platform/v1alpha1"
PROFILE_KIND = "CustomerAiFinopsPrerequisiteProfile"
REPORT_KIND = "CustomerAiFinopsPrerequisiteReport"
QUALIFICATION_LEVEL = "customer-ai-finops-prerequisites-v1"
QUALIFICATION_BOUNDARY = "prerequisite-aggregation-only"
CURRENT_DEPLOYMENT_PROFILE = "production-ai-finops-v1"
REPORT_ID = re.compile(r"^cafp_[a-f0-9]{32}$")
REVISION = re.compile(r"^[a-f0-9]{40,64}$")

CHECK_IDS = (
    "source-binding",
    "profile-review",
    "exact-release-identity",
    "ai-finops-deployment-profile",
    "release-readiness",
    "local-ai-finops-runtime",
    "customer-deployment",
    "customer-otlp-receiver",
    "customer-bedrock-live",
    "production-price-catalog",
    "runtime-readiness-chain",
    "receiver-deployment-chain",
    "catalog-profile-binding",
    "metadata-only-direct-request-path",
    "evidence-freshness",
    "minimized-prerequisite-only-output",
)

LIMITATIONS = (
    "same-live-invocation-end-to-end-path-not-qualified",
    "live-bedrock-to-customer-collector-delivery-not-qualified",
    "deployed-price-catalog-secret-binding-not-qualified",
    "customer-long-running-collector-configuration-not-qualified",
    "non-prometheus-dashboard-query-portability-not-qualified",
    "invoice-private-rates-discounts-and-commitments-not-qualified",
    "sustained-load-node-zone-region-and-backend-ha-not-qualified",
)

FORBIDDEN_RETAINED_KEYS = frozenset(
    {
        "accessKeyId",
        "awsAccessKeyId",
        "awsSecretAccessKey",
        "awsSessionToken",
        "credential",
        "credentialsProfile",
        "endpoint",
        "environmentId",
        "modelId",
        "prompt",
        "rate",
        "region",
        "response",
        "secret",
        "tenantId",
        "token",
    }
)


class CustomerAiFinopsPrerequisiteError(RuntimeError):
    """Stable failure for unsafe or irreproducible prerequisite evidence."""


@dataclass(frozen=True)
class Requirement:
    identifier: str
    api_version: str
    kind: str
    schema: str
    boundary: str
    status_path: tuple[str, ...]
    success_status: str
    source_bound: bool = True
    expires: bool = False


@dataclass(frozen=True)
class EvidenceDocument:
    document: Mapping[str, Any]
    digest: str


REQUIREMENTS = (
    Requirement(
        "release-readiness",
        "iip.dev/v1alpha1",
        "ReleaseReadinessReport",
        "release-readiness-report.schema.json",
        "local-candidate",
        ("spec", "status"),
        "locally-qualified",
    ),
    Requirement(
        "local-ai-finops-runtime",
        "iip.dev/v1alpha1",
        "AiFinopsRuntimeCompatibilityReport",
        "ai-finops-runtime-compatibility-report.schema.json",
        "local-runtime",
        ("spec", "status"),
        "compatible",
    ),
    Requirement(
        "customer-deployment",
        API_VERSION,
        "CustomerDeploymentQualificationReport",
        "customer-deployment-qualification-report.schema.json",
        "customer-deployment",
        ("spec", "status"),
        "qualified",
    ),
    Requirement(
        "customer-otlp-receiver",
        API_VERSION,
        "CustomerOtlpReceiverQualificationReport",
        "customer-otlp-receiver-qualification-report.schema.json",
        "customer-receiver",
        ("spec", "status"),
        "qualified",
    ),
    Requirement(
        "customer-bedrock",
        API_VERSION,
        "CustomerBedrockQualificationReport",
        "customer-bedrock-qualification-report.schema.json",
        "live-provider",
        ("spec", "status"),
        "qualified",
        expires=True,
    ),
    Requirement(
        "production-price-catalog",
        API_VERSION,
        "AiPriceCatalogQualificationReport",
        "ai-price-catalog-qualification-report.schema.json",
        "production-pricing",
        ("spec", "status"),
        "qualified",
        source_bound=False,
        expires=True,
    ),
)


def _fail(code: str) -> None:
    raise CustomerAiFinopsPrerequisiteError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest_value(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _path(value: object, parts: Sequence[str]) -> object:
    current = value
    for part in parts:
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[Any]:
    return value if isinstance(value, list) else ()


def _parse_timestamp(value: object, code: str) -> datetime:
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
    if value.tzinfo is None or value.utcoffset() is None:
        _fail("customer-ai-finops-prerequisite.time.invalid")
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _schema(name: str) -> Mapping[str, Any]:
    try:
        document = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("customer-ai-finops-prerequisite.schema.unavailable")
    if not isinstance(document, Mapping):
        _fail("customer-ai-finops-prerequisite.schema.unavailable")
    return document


def _schema_valid(document: Mapping[str, Any], name: str) -> bool:
    return (
        next(
            Draft202012Validator(
                _schema(name), format_checker=FormatChecker()
            ).iter_errors(document),
            None,
        )
        is None
    )


def _read_bytes(path: Path, *, protected: bool, maximum_bytes: int) -> bytes:
    code = "customer-ai-finops-prerequisite.document.unreadable"
    candidate = path.expanduser().absolute()
    descriptor = -1
    try:
        if candidate.is_symlink():
            _fail(code)
        descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or not 1 <= information.st_size <= maximum_bytes
            or (protected and stat.S_IMODE(information.st_mode) != 0o600)
            or (protected and information.st_uid != os.getuid())
        ):
            _fail(code)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, maximum_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum_bytes:
                _fail(code)
        return b"".join(chunks)
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _decode(payload: bytes) -> Mapping[str, Any]:
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("customer-ai-finops-prerequisite.document.invalid")
    if not isinstance(document, Mapping):
        _fail("customer-ai-finops-prerequisite.document.invalid")
    return document


def load_profile(path: Path) -> Mapping[str, Any]:
    profile = _decode(_read_bytes(path, protected=True, maximum_bytes=131_072))
    validate_profile(profile)
    return profile


def validate_profile(profile: Mapping[str, Any]) -> None:
    if (
        not _schema_valid(profile, "customer-ai-finops-prerequisite-profile.schema.json")
        or profile.get("apiVersion") != API_VERSION
        or profile.get("kind") != PROFILE_KIND
    ):
        _fail("customer-ai-finops-prerequisite.profile.invalid")


def load_evidence(path: Path) -> EvidenceDocument | None:
    candidate = path.expanduser().absolute()
    if not candidate.exists():
        return None
    payload = _read_bytes(candidate, protected=False, maximum_bytes=4_194_304)
    return EvidenceDocument(
        document=_decode(payload),
        digest="sha256:" + hashlib.sha256(payload).hexdigest(),
    )


def evidence_from_document(document: Mapping[str, Any]) -> EvidenceDocument:
    """Create deterministic in-memory evidence for tests and semantic examples."""

    payload = _canonical(document)
    return EvidenceDocument(
        document=dict(document),
        digest="sha256:" + hashlib.sha256(payload).hexdigest(),
    )


def _source_identity() -> tuple[str, bool]:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=normal"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        _fail("customer-ai-finops-prerequisite.source.unavailable")
    if REVISION.fullmatch(revision) is None:
        _fail("customer-ai-finops-prerequisite.source.invalid")
    return revision, dirty


def _all_checks_passed(document: Mapping[str, Any], expected: str) -> bool:
    checks = _path(document, ("spec", "checks"))
    summary = _path(document, ("spec", "summary"))
    return (
        isinstance(checks, list)
        and bool(checks)
        and all(isinstance(item, Mapping) and item.get("status") == "passed" for item in checks)
        and isinstance(summary, Mapping)
        and summary.get("failedChecks") == 0
        and summary.get("overallStatus") == expected
    )


def _requirement_result(
    requirement: Requirement,
    source: EvidenceDocument | None,
    *,
    expected_revision: str,
    generated_at: datetime,
    maximum_age: int,
    maximum_skew: int,
) -> tuple[dict[str, Any], int, datetime | None]:
    base = {
        "id": requirement.identifier,
        "contractKind": requirement.kind,
        "qualificationBoundary": requirement.boundary,
    }
    if source is None:
        return (
            {
                **base,
                "status": "missing",
                "errorCode": "customer-ai-finops-prerequisite.evidence.missing",
            },
            0,
            None,
        )
    document = source.document
    report_id = _path(document, ("metadata", "id"))
    observed_status = _path(document, requirement.status_path)
    revision = _path(document, ("metadata", "sourceRevision"))
    rejected = {
        **base,
        "status": "rejected",
        "reportDigest": source.digest,
    }
    if isinstance(report_id, str):
        rejected["reportId"] = report_id
    if isinstance(observed_status, str):
        rejected["observedStatus"] = observed_status
    if isinstance(revision, str):
        rejected["sourceRevision"] = revision
    if (
        not _schema_valid(document, requirement.schema)
        or document.get("apiVersion") != requirement.api_version
        or document.get("kind") != requirement.kind
    ):
        rejected["errorCode"] = (
            "customer-ai-finops-prerequisite.evidence.schema-invalid"
        )
        return rejected, 0, None
    generated = _parse_timestamp(
        _path(document, ("metadata", "generatedAt")),
        "customer-ai-finops-prerequisite.evidence.time-invalid",
    )
    age = max(0, int((generated_at - generated).total_seconds()))
    if generated > generated_at + timedelta(seconds=maximum_skew) or age > maximum_age:
        rejected["errorCode"] = "customer-ai-finops-prerequisite.evidence.stale"
        return rejected, age, None
    valid_until: datetime | None = None
    if requirement.expires:
        valid_until = _parse_timestamp(
            _path(document, ("metadata", "validUntil")),
            "customer-ai-finops-prerequisite.evidence.time-invalid",
        )
        if generated_at > valid_until:
            rejected["errorCode"] = "customer-ai-finops-prerequisite.evidence.expired"
            return rejected, age, valid_until
    if requirement.source_bound and (
        revision != expected_revision
        or _path(document, ("metadata", "sourceDirty")) is not False
    ):
        rejected["errorCode"] = (
            "customer-ai-finops-prerequisite.evidence.source-mismatch"
        )
        return rejected, age, valid_until
    if observed_status != requirement.success_status or not _all_checks_passed(
        document, requirement.success_status
    ):
        # Release readiness uses evidence counters instead of check counters.
        if requirement.identifier != "release-readiness" or not (
            observed_status == "locally-qualified"
            and all(
                isinstance(item, Mapping) and item.get("status") == "passed"
                for item in _sequence(_path(document, ("spec", "evidence")))
            )
        ):
            rejected["errorCode"] = (
                "customer-ai-finops-prerequisite.evidence.status-not-passed"
            )
            return rejected, age, valid_until
    result = {
        **base,
        "status": "passed",
        "reportId": report_id,
        "reportDigest": source.digest,
        "observedStatus": observed_status,
    }
    if requirement.source_bound:
        result["sourceRevision"] = revision
    return result, age, valid_until


def _find(items: object, identifier: str) -> Mapping[str, Any]:
    return next(
        (
            item
            for item in _sequence(items)
            if isinstance(item, Mapping) and item.get("id") == identifier
        ),
        {},
    )


def _receiver_chain(
    deployment: Mapping[str, Any],
    receiver: Mapping[str, Any],
    receiver_source: EvidenceDocument | None,
) -> bool:
    if receiver_source is None:
        return False
    item = _find(_path(deployment, ("spec", "evidence")), "customer-otlp-receiver")
    deployment_bindings = _mapping(_path(deployment, ("spec", "bindings")))
    receiver_bindings = _mapping(_path(receiver, ("spec", "bindings")))
    pairs = (
        ("otlpReceiverApiTargetBindingDigest", "apiTargetBindingDigest"),
        ("otlpReceiverEndpointBindingDigest", "receiverEndpointBindingDigest"),
        ("otlpReceiverProfileDigest", "profileDigest"),
        ("otlpReceiverSignalSetDigest", "signalSetDigest"),
        ("otlpReceiverApiCaBundleDigest", "apiCaBundleDigest"),
        ("otlpReceiverCaBundleDigest", "receiverCaBundleDigest"),
        ("otlpReceiverClientCertificateDigest", "clientCertificateDigest"),
    )
    return (
        item.get("status") == "passed"
        and item.get("reportId") == _path(receiver, ("metadata", "id"))
        and item.get("reportDigest") == receiver_source.digest
        and all(
            deployment_bindings.get(left) == receiver_bindings.get(right)
            for left, right in pairs
        )
    )


def _runtime_chain(
    readiness: Mapping[str, Any], runtime_source: EvidenceDocument | None
) -> bool:
    if runtime_source is None:
        return False
    runtime = _find(_path(readiness, ("spec", "evidence")), "ai-finops-runtime")
    configuration = _find(
        _path(readiness, ("spec", "evidence")),
        "production-ai-finops-configuration",
    )
    return (
        runtime.get("status") == "passed"
        and runtime.get("reportDigest") == runtime_source.digest
        and configuration.get("status") == "passed"
    )


def _catalog_binding(pricing: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "catalogId": pricing.get("catalogId"),
        "catalogVersion": pricing.get("catalogVersion"),
        "catalogDocumentDigest": pricing.get("catalogDocumentDigest"),
        "qualificationPolicyId": pricing.get("qualificationPolicyId"),
        "qualificationPolicyVersion": pricing.get("qualificationPolicyVersion"),
    }


def _check(identifier: str, passed: bool) -> dict[str, str]:
    result = {"id": identifier, "status": "passed" if passed else "failed"}
    if not passed:
        result["errorCode"] = (
            f"customer-ai-finops-prerequisite.{identifier}.failed"
        )
    return result


def _report_id(metadata: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    return "cafp_" + hashlib.sha256(
        _canonical({"metadata": metadata, "spec": spec})
    ).hexdigest()[:32]


def _has_forbidden_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in FORBIDDEN_RETAINED_KEYS or _has_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_has_forbidden_key(child) for child in value)
    return False


def build_report(
    *,
    profile: Mapping[str, Any],
    sources: Mapping[str, EvidenceDocument | None],
    generated_at: datetime,
) -> dict[str, Any]:
    validate_profile(profile)
    if generated_at.tzinfo is None or generated_at.utcoffset() is None:
        _fail("customer-ai-finops-prerequisite.time.invalid")
    generated_at = generated_at.astimezone(timezone.utc).replace(microsecond=0)
    metadata = _mapping(profile.get("metadata"))
    specification = _mapping(profile.get("spec"))
    release = _mapping(specification.get("release"))
    collection = _mapping(specification.get("collection"))
    pricing = _mapping(specification.get("pricing"))
    presentation = _mapping(specification.get("presentation"))
    objective = _mapping(specification.get("objective"))
    expected_revision = str(release.get("sourceRevision"))
    reviewed_at = _parse_timestamp(
        metadata.get("reviewedAt"),
        "customer-ai-finops-prerequisite.profile.time-invalid",
    )

    evidence: list[dict[str, Any]] = []
    ages: list[int] = []
    expiries: list[datetime] = []
    for requirement in REQUIREMENTS:
        result, age, expiry = _requirement_result(
            requirement,
            sources.get(requirement.identifier),
            expected_revision=expected_revision,
            generated_at=generated_at,
            maximum_age=int(objective["maximumEvidenceAgeSeconds"]),
            maximum_skew=int(objective["maximumClockSkewSeconds"]),
        )
        evidence.append(result)
        ages.append(age)
        if expiry is not None:
            expiries.append(expiry)

    documents = {
        identifier: source.document if source is not None else {}
        for identifier, source in sources.items()
    }
    readiness = documents.get("release-readiness", {})
    runtime = documents.get("local-ai-finops-runtime", {})
    deployment = documents.get("customer-deployment", {})
    receiver = documents.get("customer-otlp-receiver", {})
    bedrock = documents.get("customer-bedrock", {})
    price = documents.get("production-price-catalog", {})
    evidence_passed = {
        item["id"]: item.get("status") == "passed" for item in evidence
    }

    release_match = (
        _path(readiness, ("spec", "release", "version"))
        == release.get("applicationVersion")
        and _path(readiness, ("spec", "release", "chartVersion"))
        == release.get("chartVersion")
        and _path(readiness, ("spec", "release", "revision"))
        == expected_revision
        and _path(runtime, ("spec", "environment", "applicationVersion"))
        == release.get("applicationVersion")
        and _path(deployment, ("spec", "subject", "applicationVersion"))
        == release.get("applicationVersion")
        and _path(deployment, ("spec", "subject", "chartVersion"))
        == release.get("chartVersion")
        and _path(deployment, ("spec", "subject", "imageDigest"))
        == release.get("imageDigest")
        and _path(receiver, ("spec", "subject", "applicationVersion"))
        == release.get("applicationVersion")
        and _path(receiver, ("spec", "subject", "chartVersion"))
        == release.get("chartVersion")
        and _path(receiver, ("spec", "subject", "imageDigest"))
        == release.get("imageDigest")
        and _path(bedrock, ("spec", "subject", "applicationVersion"))
        == release.get("applicationVersion")
        and _path(bedrock, ("spec", "subject", "imageDigest"))
        == release.get("imageDigest")
    )
    source_match = all(
        not requirement.source_bound
        or _path(documents.get(requirement.identifier, {}), ("metadata", "sourceRevision"))
        == expected_revision
        for requirement in REQUIREMENTS
    )
    profile_age = (generated_at - reviewed_at).total_seconds()
    profile_current = (
        reviewed_at
        <= generated_at + timedelta(seconds=int(objective["maximumClockSkewSeconds"]))
        and profile_age <= int(objective["maximumProfileAgeSeconds"])
    )
    deployment_profile = (
        _path(deployment, ("spec", "subject", "profile"))
        == CURRENT_DEPLOYMENT_PROFILE
    )
    catalog = _mapping(_path(price, ("spec", "catalog")))
    policy = _mapping(_path(price, ("spec", "policy")))
    catalog_match = (
        _path(price, ("metadata", "tenantId")) == pricing.get("tenantId")
        and catalog.get("id") == pricing.get("catalogId")
        and catalog.get("version") == pricing.get("catalogVersion")
        and catalog.get("documentDigest") == pricing.get("catalogDocumentDigest")
        and catalog.get("sourceKind") == pricing.get("sourceClass")
        and policy.get("id") == pricing.get("qualificationPolicyId")
        and policy.get("version") == pricing.get("qualificationPolicyVersion")
        and _path(price, ("spec", "qualificationLevel")) == "production-catalog"
    )
    bedrock_profile = _mapping(_path(bedrock, ("spec", "profile")))
    runtime_profile = _mapping(_path(runtime, ("spec", "profile")))
    metadata_path = (
        bedrock_profile.get("provider") == collection.get("provider")
        and bedrock_profile.get("operation") == collection.get("operation")
        and bedrock_profile.get("requestPath") == collection.get("requestPath")
        and bedrock_profile.get("telemetryPath") == collection.get("telemetryPath")
        and bedrock_profile.get("contentPolicy")
        == "fixed-synthetic-request-not-retained"
        and runtime_profile.get("contentPolicy") == collection.get("contentPolicy")
        and runtime_profile.get("telemetryBackend")
        == presentation.get("telemetryBackend")
        and runtime_profile.get("dashboard") == presentation.get("dashboard")
    )
    observations = {
        "source-binding": source_match,
        "profile-review": profile_current,
        "exact-release-identity": release_match,
        "ai-finops-deployment-profile": deployment_profile,
        "release-readiness": evidence_passed.get("release-readiness", False),
        "local-ai-finops-runtime": evidence_passed.get(
            "local-ai-finops-runtime", False
        ),
        "customer-deployment": evidence_passed.get("customer-deployment", False),
        "customer-otlp-receiver": evidence_passed.get(
            "customer-otlp-receiver", False
        ),
        "customer-bedrock-live": evidence_passed.get("customer-bedrock", False),
        "production-price-catalog": evidence_passed.get(
            "production-price-catalog", False
        ),
        "runtime-readiness-chain": _runtime_chain(
            readiness, sources.get("local-ai-finops-runtime")
        ),
        "receiver-deployment-chain": _receiver_chain(
            deployment, receiver, sources.get("customer-otlp-receiver")
        ),
        "catalog-profile-binding": catalog_match,
        "metadata-only-direct-request-path": metadata_path,
        "evidence-freshness": all(evidence_passed.values()),
        "minimized-prerequisite-only-output": True,
    }
    checks = [_check(identifier, observations[identifier]) for identifier in CHECK_IDS]
    passed_checks = sum(item["status"] == "passed" for item in checks)
    passed_evidence = sum(item["status"] == "passed" for item in evidence)
    missing_evidence = sum(item["status"] == "missing" for item in evidence)
    rejected_evidence = len(evidence) - passed_evidence - missing_evidence
    status = (
        "prerequisites-ready"
        if passed_checks == len(CHECK_IDS) and passed_evidence == len(REQUIREMENTS)
        else "not-ready"
    )
    report_spec = {
        "status": status,
        "qualificationLevel": QUALIFICATION_LEVEL,
        "qualificationBoundary": QUALIFICATION_BOUNDARY,
        "subject": {
            "deploymentProfile": CURRENT_DEPLOYMENT_PROFILE,
            "applicationVersion": release["applicationVersion"],
            "chartVersion": release["chartVersion"],
            "contractsApiVersion": API_VERSION,
            "sourceRevision": expected_revision,
            "imageDigest": release["imageDigest"],
        },
        "bindings": {
            "profileDigest": _digest_value(profile),
            "environmentBindingDigest": _digest_value(metadata["environmentId"]),
            "priceTenantBindingDigest": _digest_value(pricing["tenantId"]),
            "catalogBindingDigest": _digest_value(_catalog_binding(pricing)),
            "releaseReadinessReportDigest": sources["release-readiness"].digest
            if sources.get("release-readiness")
            else "sha256:" + "0" * 64,
            "aiFinopsRuntimeReportDigest": sources[
                "local-ai-finops-runtime"
            ].digest
            if sources.get("local-ai-finops-runtime")
            else "sha256:" + "0" * 64,
            "customerDeploymentReportDigest": sources["customer-deployment"].digest
            if sources.get("customer-deployment")
            else "sha256:" + "0" * 64,
            "customerOtlpReceiverReportDigest": sources[
                "customer-otlp-receiver"
            ].digest
            if sources.get("customer-otlp-receiver")
            else "sha256:" + "0" * 64,
            "customerBedrockReportDigest": sources["customer-bedrock"].digest
            if sources.get("customer-bedrock")
            else "sha256:" + "0" * 64,
            "priceCatalogQualificationReportDigest": sources[
                "production-price-catalog"
            ].digest
            if sources.get("production-price-catalog")
            else "sha256:" + "0" * 64,
        },
        "profile": {
            "provider": collection["provider"],
            "operation": collection["operation"],
            "requestPath": collection["requestPath"],
            "telemetryPath": collection["telemetryPath"],
            "contentPolicy": collection["contentPolicy"],
            "costBasis": pricing["costBasis"],
            "pricingSourceClass": pricing["sourceClass"],
            "telemetryBackend": presentation["telemetryBackend"],
            "dashboard": presentation["dashboard"],
        },
        "objective": dict(objective),
        "measurements": {
            "profileReviewedAt": _timestamp(reviewed_at),
            "qualifiedAt": _timestamp(generated_at),
            "oldestEvidenceAgeSeconds": max(ages, default=0),
            "productionCatalogEntryCount": int(
                _path(price, ("spec", "measurements", "entryCount")) or 0
            ),
            "productionCatalogRequiredScopeCount": int(
                _path(price, ("spec", "measurements", "requiredScopeCount")) or 0
            ),
            "customerReceiverDeliveredItemCount": int(
                _path(receiver, ("spec", "measurements", "receiverDeliveredItemCount"))
                or 0
            ),
            "liveProviderCallCount": int(
                _path(bedrock, ("spec", "measurements", "providerCallCount")) or 0
            ),
        },
        "evidence": evidence,
        "checks": checks,
        "limitations": list(LIMITATIONS),
        "summary": {
            "requiredEvidence": len(REQUIREMENTS),
            "passedEvidence": passed_evidence,
            "missingEvidence": missing_evidence,
            "rejectedEvidence": rejected_evidence,
            "totalChecks": len(CHECK_IDS),
            "passedChecks": passed_checks,
            "failedChecks": len(CHECK_IDS) - passed_checks,
            "overallStatus": status,
        },
    }
    validity = generated_at + timedelta(seconds=int(objective["reportValiditySeconds"]))
    if status == "prerequisites-ready" and expiries:
        validity = min(validity, *expiries)
    report_metadata = {
        "generatedAt": _timestamp(generated_at),
        "validUntil": _timestamp(validity),
        "sourceRevision": expected_revision,
        "sourceDirty": False,
    }
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
    code = "customer-ai-finops-prerequisite.report.invalid"
    if (
        not _schema_valid(report, "customer-ai-finops-prerequisite-report.schema.json")
        or report.get("apiVersion") != API_VERSION
        or report.get("kind") != REPORT_KIND
        or _has_forbidden_key(report)
    ):
        _fail(code)
    metadata = _mapping(report.get("metadata"))
    spec = _mapping(report.get("spec"))
    summary = _mapping(spec.get("summary"))
    checks = _sequence(spec.get("checks"))
    evidence = _sequence(spec.get("evidence"))
    generated = _parse_timestamp(metadata.get("generatedAt"), code)
    valid_until = _parse_timestamp(metadata.get("validUntil"), code)
    passed_checks = sum(
        isinstance(item, Mapping) and item.get("status") == "passed"
        for item in checks
    )
    passed_evidence = sum(
        isinstance(item, Mapping) and item.get("status") == "passed"
        for item in evidence
    )
    missing_evidence = sum(
        isinstance(item, Mapping) and item.get("status") == "missing"
        for item in evidence
    )
    status = (
        "prerequisites-ready"
        if passed_checks == len(CHECK_IDS) and passed_evidence == len(REQUIREMENTS)
        else "not-ready"
    )
    if (
        valid_until <= generated
        or metadata.get("sourceRevision")
        != _path(report, ("spec", "subject", "sourceRevision"))
        or tuple(
            item.get("id") if isinstance(item, Mapping) else None for item in checks
        )
        != CHECK_IDS
        or tuple(
            item.get("id") if isinstance(item, Mapping) else None for item in evidence
        )
        != tuple(item.identifier for item in REQUIREMENTS)
        or spec.get("limitations") != list(LIMITATIONS)
        or spec.get("qualificationBoundary") != QUALIFICATION_BOUNDARY
        or spec.get("status") != status
        or summary
        != {
            "requiredEvidence": len(REQUIREMENTS),
            "passedEvidence": passed_evidence,
            "missingEvidence": missing_evidence,
            "rejectedEvidence": len(REQUIREMENTS)
            - passed_evidence
            - missing_evidence,
            "totalChecks": len(CHECK_IDS),
            "passedChecks": passed_checks,
            "failedChecks": len(CHECK_IDS) - passed_checks,
            "overallStatus": status,
        }
    ):
        _fail(code)
    without_id = dict(metadata)
    identifier = without_id.pop("id", None)
    if (
        not isinstance(identifier, str)
        or REPORT_ID.fullmatch(identifier) is None
        or identifier != _report_id(without_id, spec)
    ):
        _fail("customer-ai-finops-prerequisite.report.id-invalid")


def _source_paths(args: argparse.Namespace) -> Mapping[str, Path]:
    return {
        "release-readiness": args.release_readiness,
        "local-ai-finops-runtime": args.ai_finops_runtime,
        "customer-deployment": args.customer_deployment,
        "customer-otlp-receiver": args.customer_otlp_receiver,
        "customer-bedrock": args.customer_bedrock,
        "production-price-catalog": args.price_catalog_qualification,
    }


def _load_sources(paths: Mapping[str, Path]) -> dict[str, EvidenceDocument | None]:
    return {identifier: load_evidence(path) for identifier, path in paths.items()}


def _write(path: Path, report: Mapping[str, Any]) -> None:
    destination = path.expanduser().absolute()
    if destination.is_symlink():
        _fail("customer-ai-finops-prerequisite.output.invalid")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
        )
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
    except OSError:
        _fail("customer-ai-finops-prerequisite.output.invalid")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def qualify(
    *, profile_path: Path, paths: Mapping[str, Path], now: datetime | None = None
) -> Mapping[str, Any]:
    profile = load_profile(profile_path)
    revision, dirty = _source_identity()
    if dirty:
        _fail("customer-ai-finops-prerequisite.source.dirty")
    if revision != _path(profile, ("spec", "release", "sourceRevision")):
        _fail("customer-ai-finops-prerequisite.source.revision-mismatch")
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
    require_ready: bool = False,
    now: datetime | None = None,
) -> Mapping[str, Any]:
    report = _decode(_read_bytes(report_path, protected=False, maximum_bytes=2_097_152))
    validate_report_document(report)
    revision, dirty = _source_identity()
    if dirty:
        _fail("customer-ai-finops-prerequisite.source.dirty")
    if revision != _path(report, ("metadata", "sourceRevision")):
        _fail("customer-ai-finops-prerequisite.source.revision-mismatch")
    profile = load_profile(profile_path)
    expected = build_report(
        profile=profile,
        sources=_load_sources(paths),
        generated_at=_parse_timestamp(
            _path(report, ("metadata", "generatedAt")),
            "customer-ai-finops-prerequisite.report.invalid",
        ),
    )
    if expected != report:
        _fail("customer-ai-finops-prerequisite.report.evidence-mismatch")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        _fail("customer-ai-finops-prerequisite.time.invalid")
    if current.astimezone(timezone.utc) > _parse_timestamp(
        _path(report, ("metadata", "validUntil")),
        "customer-ai-finops-prerequisite.report.invalid",
    ):
        _fail("customer-ai-finops-prerequisite.report.expired")
    if require_ready and _path(report, ("spec", "status")) != "prerequisites-ready":
        _fail("customer-ai-finops-prerequisite.report.not-ready")
    return report


def _add_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--release-readiness", type=Path, required=True)
    parser.add_argument("--ai-finops-runtime", type=Path, required=True)
    parser.add_argument("--customer-deployment", type=Path, required=True)
    parser.add_argument("--customer-otlp-receiver", type=Path, required=True)
    parser.add_argument("--customer-bedrock", type=Path, required=True)
    parser.add_argument("--price-catalog-qualification", type=Path, required=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate")
    _add_inputs(generate)
    generate.add_argument("--output", type=Path, required=True)
    verify_command = commands.add_parser("verify")
    _add_inputs(verify_command)
    verify_command.add_argument("--report", type=Path, required=True)
    verify_command.add_argument("--require-ready", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        paths = _source_paths(arguments)
        if arguments.command == "generate":
            report = qualify(profile_path=arguments.profile, paths=paths)
            _write(arguments.output, report)
            print(
                f"customer AI FinOps prerequisites {report['spec']['status']}: "
                f"{arguments.output}"
            )
            return 0 if report["spec"]["status"] == "prerequisites-ready" else 1
        verify(
            report_path=arguments.report,
            profile_path=arguments.profile,
            paths=paths,
            require_ready=arguments.require_ready,
        )
        print("customer AI FinOps prerequisite report verified")
    except CustomerAiFinopsPrerequisiteError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
