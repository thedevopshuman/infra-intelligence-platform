#!/usr/bin/env python3
"""Qualify one exact customer Bedrock-to-IIP-to-dashboard invocation flow."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import signal
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "contracts" / "schemas"
sys.path.insert(0, str(ROOT / "scripts"))

import qualify_customer_ai_finops as prerequisite  # noqa: E402
import qualify_customer_bedrock as bedrock  # noqa: E402


API_VERSION = "iip.platform/v1alpha1"
PROFILE_KIND = "CustomerAiFinopsFlowQualificationProfile"
RUN_EVIDENCE_KIND = "CustomerAiFinopsFlowRunEvidence"
REPORT_KIND = "CustomerAiFinopsFlowQualificationReport"
QUALIFICATION_LEVEL = "customer-ai-finops-flow-v1"
QUALIFICATION_BOUNDARY = "same-invocation-customer-runtime"
REPORT_ID = re.compile(r"^caff_[a-f0-9]{32}$")
REVISION = re.compile(r"^[a-f0-9]{40,64}$")
CHECK_IDS = (
    "source-binding",
    "profile-review",
    "prerequisite-binding",
    "bedrock-profile-binding",
    "protected-inputs",
    "metadata-only-direct-request-path",
    "live-provider-call",
    "otlp-delivery-correlation",
    "exact-usage-record",
    "active-attribution",
    "active-pricing",
    "bounded-processing-latency",
    "prometheus-aggregate-delta",
    "grafana-dashboard",
    "minimized-output",
)
LIMITATIONS = (
    "dashboard-proof-is-protected-dimension-aggregate-not-trace-labelled",
    "invoice-private-rates-discounts-and-commitments-not-qualified",
    "customer-long-running-collector-and-backend-lifecycle-not-qualified",
    "sustained-load-node-zone-region-and-backend-ha-not-qualified",
    "additional-models-regions-operations-providers-and-backends-not-qualified",
)
REQUIRED_PANELS = frozenset(
    {
        "How much usage?",
        "How much cost?",
        "Where is spend happening?",
        "What changed?",
        "One potential saving",
        "Cost by protected application",
    }
)
FORBIDDEN_REPORT_KEYS = frozenset(
    {
        "accessKeyId",
        "applicationId",
        "awsAccessKeyId",
        "awsSecretAccessKey",
        "awsSessionToken",
        "credential",
        "endpoint",
        "environmentId",
        "modelId",
        "prompt",
        "rate",
        "region",
        "response",
        "secret",
        "spanId",
        "teamId",
        "tenantId",
        "token",
        "traceId",
    }
)


class CustomerAiFinopsFlowError(RuntimeError):
    """Stable failure for unsafe or irreproducible customer-flow evidence."""


@dataclass(frozen=True)
class FileDocument:
    document: Mapping[str, Any]
    file_digest: str


@dataclass(frozen=True)
class RuntimeDocuments:
    run_evidence: Mapping[str, Any]
    live_report: Mapping[str, Any]
    observation: Mapping[str, Any]


RuntimeRunner = Callable[
    [Mapping[str, Any], Mapping[str, Any]], RuntimeDocuments
]


def _fail(code: str) -> None:
    raise CustomerAiFinopsFlowError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _raw_digest(value: str | bytes) -> str:
    payload = value.encode("utf-8") if isinstance(value, str) else value
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _sequence(value: object, code: str) -> Sequence[Any]:
    if not isinstance(value, list):
        _fail(code)
    return value


def _parse_time(value: object, code: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        _fail(code)
    return parsed.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        _fail("customer-ai-finops-flow.time.invalid")
    return (
        value.astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _schema(name: str) -> Mapping[str, Any]:
    try:
        value = json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("customer-ai-finops-flow.schema.unavailable")
    return _mapping(value, "customer-ai-finops-flow.schema.unavailable")


def _validate_schema(document: Mapping[str, Any], name: str, code: str) -> None:
    error = next(
        Draft202012Validator(
            _schema(name), format_checker=FormatChecker()
        ).iter_errors(document),
        None,
    )
    if error is not None:
        _fail(code)


def _read_bytes(
    path: Path, *, protected: bool, maximum_bytes: int, code: str
) -> bytes:
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


def _decode(payload: bytes, code: str) -> Mapping[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail(code)
    return _mapping(value, code)


def _load_document(path: Path, *, protected: bool, maximum_bytes: int) -> FileDocument:
    payload = _read_bytes(
        path,
        protected=protected,
        maximum_bytes=maximum_bytes,
        code="customer-ai-finops-flow.document.unreadable",
    )
    return FileDocument(
        _decode(payload, "customer-ai-finops-flow.document.invalid"),
        _raw_digest(payload),
    )


def load_profile(path: Path) -> Mapping[str, Any]:
    document = _load_document(path, protected=True, maximum_bytes=262_144).document
    validate_profile(document)
    return document


def _validated_https(value: object, *, traces: bool = False) -> str:
    if not isinstance(value, str):
        _fail("customer-ai-finops-flow.profile.endpoint-invalid")
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except ValueError:
        _fail("customer-ai-finops-flow.profile.endpoint-invalid")
    decoded_path = urllib.parse.unquote(parsed.path)
    unsafe_base_path = (
        "\\" in decoded_path
        or "//" in decoded_path
        or any(segment in (".", "..") for segment in decoded_path.split("/"))
        or any(ord(character) < 32 for character in decoded_path)
    )
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port == 0
        or (traces and parsed.path != "/v1/traces")
        or (not traces and unsafe_base_path)
    ):
        _fail("customer-ai-finops-flow.profile.endpoint-invalid")
    return value.rstrip("/") if not traces else value


def validate_profile(profile: Mapping[str, Any]) -> None:
    _validate_schema(
        profile,
        "customer-ai-finops-flow-qualification-profile.schema.json",
        "customer-ai-finops-flow.profile.invalid",
    )
    if profile.get("apiVersion") != API_VERSION or profile.get("kind") != PROFILE_KIND:
        _fail("customer-ai-finops-flow.profile.invalid")
    targets = _mapping(
        _mapping(profile.get("spec"), "customer-ai-finops-flow.profile.invalid").get(
            "targets"
        ),
        "customer-ai-finops-flow.profile.invalid",
    )
    _validated_https(targets.get("controlPlaneBaseUrl"))
    _validated_https(targets.get("otlpTracesEndpoint"), traces=True)
    _validated_https(targets.get("prometheusBaseUrl"))
    _validated_https(targets.get("grafanaBaseUrl"))


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
        _fail("customer-ai-finops-flow.source.unavailable")
    if REVISION.fullmatch(revision) is None:
        _fail("customer-ai-finops-flow.source.invalid")
    return revision, dirty


def _has_forbidden_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in FORBIDDEN_REPORT_KEYS or _has_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_has_forbidden_key(child) for child in value)
    return False


def _check(identifier: str, passed: bool) -> dict[str, str]:
    item = {"id": identifier, "status": "passed" if passed else "failed"}
    if not passed:
        item["errorCode"] = f"customer-ai-finops-flow.{identifier}.failed"
    return item


def _summary(checks: Sequence[Mapping[str, Any]]) -> Mapping[str, object]:
    passed = sum(item.get("status") == "passed" for item in checks)
    status = "qualified" if passed == len(CHECK_IDS) else "not-qualified"
    return {
        "totalChecks": len(CHECK_IDS),
        "passedChecks": passed,
        "failedChecks": len(CHECK_IDS) - passed,
        "overallStatus": status,
    }


def _report_id(metadata: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    return "caff_" + hashlib.sha256(
        _canonical({"metadata": metadata, "spec": spec})
    ).hexdigest()[:32]


def _validate_prerequisites(
    flow_profile: Mapping[str, Any],
    prerequisite_profile: FileDocument,
    prerequisite_report: FileDocument,
    *,
    now: datetime,
) -> None:
    code = "customer-ai-finops-flow.prerequisite.invalid"
    try:
        prerequisite.validate_profile(prerequisite_profile.document)
        prerequisite.validate_report_document(prerequisite_report.document)
    except prerequisite.CustomerAiFinopsPrerequisiteError:
        _fail(code)
    flow_spec = _mapping(flow_profile.get("spec"), code)
    selection = _mapping(flow_spec.get("prerequisites"), code)
    release = _mapping(flow_spec.get("release"), code)
    flow_metadata = _mapping(flow_profile.get("metadata"), code)
    prerequisite_metadata = _mapping(prerequisite_profile.document.get("metadata"), code)
    prerequisite_profile_spec = _mapping(
        prerequisite_profile.document.get("spec"), code
    )
    prerequisite_release = _mapping(prerequisite_profile_spec.get("release"), code)
    report_metadata = _mapping(prerequisite_report.document.get("metadata"), code)
    report_spec = _mapping(prerequisite_report.document.get("spec"), code)
    report_subject = _mapping(report_spec.get("subject"), code)
    report_bindings = _mapping(report_spec.get("bindings"), code)
    generated = _parse_time(report_metadata.get("generatedAt"), code)
    valid_until = _parse_time(report_metadata.get("validUntil"), code)
    objective = _mapping(flow_spec.get("objective"), code)
    maximum_skew = int(objective["maximumClockSkewSeconds"])
    maximum_age = int(objective["maximumPrerequisiteAgeSeconds"])
    if (
        selection.get("profileDigest") != _digest(prerequisite_profile.document)
        or selection.get("reportDigest") != prerequisite_report.file_digest
        or report_bindings.get("profileDigest")
        != _digest(prerequisite_profile.document)
        or report_spec.get("status") != "prerequisites-ready"
        or report_spec.get("qualificationBoundary") != "prerequisite-aggregation-only"
        or generated > now + timedelta(seconds=maximum_skew)
        or (now - generated).total_seconds() > maximum_age
        or now > valid_until
        or flow_metadata.get("environmentId")
        != prerequisite_metadata.get("environmentId")
        or any(
            release.get(field) != prerequisite_release.get(field)
            for field in (
                "applicationVersion",
                "chartVersion",
                "sourceRevision",
                "imageDigest",
            )
        )
        or any(
            report_subject.get(field) != release.get(field)
            for field in (
                "applicationVersion",
                "chartVersion",
                "sourceRevision",
                "imageDigest",
            )
        )
    ):
        _fail(code)


def _validate_bedrock_profile(
    flow_profile: Mapping[str, Any],
    bedrock_profile: Mapping[str, Any],
    *,
    now: datetime,
) -> None:
    code = "customer-ai-finops-flow.bedrock-profile.invalid"
    try:
        bedrock.validate_profile(bedrock_profile)
    except bedrock.CustomerBedrockQualificationError:
        _fail(code)
    flow_metadata = _mapping(flow_profile.get("metadata"), code)
    flow_spec = _mapping(flow_profile.get("spec"), code)
    flow_release = _mapping(flow_spec.get("release"), code)
    flow_collection = _mapping(flow_spec.get("collection"), code)
    selection = _mapping(flow_spec.get("bedrock"), code)
    selected_metadata = _mapping(bedrock_profile.get("metadata"), code)
    selected_spec = _mapping(bedrock_profile.get("spec"), code)
    selected_release = _mapping(selected_spec.get("release"), code)
    target = _mapping(selected_spec.get("target"), code)
    selected_objective = _mapping(selected_spec.get("objective"), code)
    flow_objective = _mapping(flow_spec.get("objective"), code)
    reviewed = _parse_time(selected_metadata.get("reviewedAt"), code)
    if (
        selection.get("profileDigest") != _digest(bedrock_profile)
        or flow_metadata.get("environmentId") != selected_metadata.get("environmentId")
        or target.get("provider") != flow_collection.get("provider")
        or target.get("operation") != flow_collection.get("operation")
        or selected_release.get("applicationVersion")
        != flow_release.get("applicationVersion")
        or selected_release.get("imageDigest") != flow_release.get("imageDigest")
        or reviewed
        > now
        + timedelta(seconds=int(flow_objective["maximumClockSkewSeconds"]))
        or (now - reviewed).total_seconds()
        > int(selected_objective["maximumProfileAgeSeconds"])
    ):
        _fail(code)


def _validate_observation(
    flow_profile: Mapping[str, Any],
    prerequisite_profile: Mapping[str, Any],
    run_evidence: Mapping[str, Any],
    live_report: Mapping[str, Any],
    observation: Mapping[str, Any],
    bedrock_profile: Mapping[str, Any],
) -> None:
    code = "customer-ai-finops-flow.run-evidence.invalid"
    _validate_schema(
        run_evidence,
        "customer-ai-finops-flow-run-evidence.schema.json",
        code,
    )
    _validate_schema(
        observation,
        "ai-economics-invocation-observation.schema.json",
        code,
    )
    evidence_metadata = _mapping(run_evidence.get("metadata"), code)
    evidence_spec = _mapping(run_evidence.get("spec"), code)
    observation_metadata = _mapping(observation.get("metadata"), code)
    observation_spec = _mapping(observation.get("spec"), code)
    trace_id = evidence_spec.get("traceId")
    span_id = evidence_spec.get("spanId")
    expected_correlation = _digest(
        {
            "tenantId": observation_metadata.get("tenantId"),
            "traceId": trace_id,
            "spanId": span_id,
        }
    )
    flow_spec = _mapping(flow_profile.get("spec"), code)
    release = _mapping(flow_spec.get("release"), code)
    targets = _mapping(flow_spec.get("targets"), code)
    selected_attribution = _mapping(flow_spec.get("attribution"), code)
    prerequisite_pricing = _mapping(
        _mapping(prerequisite_profile.get("spec"), code).get("pricing"), code
    )
    sources = _mapping(observation_spec.get("sources"), code)
    attribution_source = _mapping(sources.get("attribution"), code)
    pricing = _mapping(sources.get("pricing"), code)
    usage = _mapping(observation_spec.get("usage"), code)
    attribution = _mapping(observation_spec.get("attribution"), code)
    cost = _mapping(observation_spec.get("cost"), code)
    priced = _mapping(cost.get("pricedCost"), code)
    evidence_usage = _mapping(evidence_spec.get("usage"), code)
    evidence_attribution = _mapping(evidence_spec.get("attribution"), code)
    evidence_cost = _mapping(evidence_spec.get("cost"), code)
    telemetry = _mapping(evidence_spec.get("telemetry"), code)
    timing = _mapping(evidence_spec.get("timing"), code)
    try:
        bedrock.validate_live_report(
            bedrock_profile, live_report, str(release.get("sourceRevision"))
        )
    except bedrock.CustomerBedrockQualificationError:
        _fail(code)
    started = _parse_time(timing.get("startedAt"), code)
    completed = _parse_time(timing.get("completedAt"), code)
    objective = _mapping(flow_spec.get("objective"), code)
    if (
        evidence_metadata.get("sourceRevision") != release.get("sourceRevision")
        or evidence_metadata.get("sourceDirty") is not False
        or evidence_spec.get("correlationDigest") != expected_correlation
        or observation_spec.get("correlationDigest") != expected_correlation
        or evidence_spec.get("deliveryEndpointDigest")
        != _raw_digest(str(targets.get("otlpTracesEndpoint")))
        or evidence_spec.get("liveCompatibilityReportDigest") != _digest(live_report)
        or evidence_spec.get("invocationObservationDigest") != _digest(observation)
        or observation_spec.get("status") != "complete"
        or observation_metadata.get("tenantId")
        != prerequisite_pricing.get("tenantId")
        or usage.get("status") != "recorded"
        or attribution.get("status") != "allocated"
        or cost.get("status") != "priced"
        or attribution.get("applicationId")
        != selected_attribution.get("applicationId")
        or attribution.get("teamId") != selected_attribution.get("teamId")
        or pricing.get("id") != prerequisite_pricing.get("catalogId")
        or pricing.get("version") != prerequisite_pricing.get("catalogVersion")
        or pricing.get("documentDigest")
        != prerequisite_pricing.get("catalogDocumentDigest")
        or evidence_usage.get("recordId") != usage.get("recordId")
        or evidence_usage.get("recordDigest") != usage.get("recordDigest")
        or evidence_attribution.get("recordId") != attribution.get("recordId")
        or evidence_attribution.get("recordDigest")
        != attribution.get("recordDigest")
        or evidence_attribution.get("applicationId")
        != attribution.get("applicationId")
        or evidence_attribution.get("teamId") != attribution.get("teamId")
        or evidence_attribution.get("sourceDocumentDigest")
        != attribution_source.get("documentDigest")
        or evidence_cost.get("recordId") != cost.get("recordId")
        or evidence_cost.get("recordDigest") != cost.get("recordDigest")
        or evidence_cost.get("currency") != priced.get("currency")
        or evidence_cost.get("currencyScale") != priced.get("currencyScale")
        or evidence_cost.get("totalSubunits") != priced.get("totalSubunits")
        or evidence_cost.get("costBasis") != priced.get("costBasis")
        or evidence_cost.get("sourceDocumentDigest")
        != pricing.get("documentDigest")
        or int(telemetry.get("requestCountAfter", 0))
        < int(telemetry.get("requestCountBefore", 0)) + 1
        or completed < started
        or evidence_metadata.get("generatedAt") != timing.get("completedAt")
        or abs(
            int((completed - started).total_seconds() * 1000)
            - int(timing.get("endToEndLatencyMilliseconds", -1))
        )
        > 1000
        or timing.get("endToEndLatencyMilliseconds")
        > objective.get("maximumEndToEndLatencyMilliseconds")
    ):
        _fail(code)


def build_report(
    *,
    flow_profile: Mapping[str, Any],
    prerequisite_profile: FileDocument,
    prerequisite_report: FileDocument,
    bedrock_profile: Mapping[str, Any],
    runtime: RuntimeDocuments,
    protected_inputs: bool,
) -> Mapping[str, Any]:
    validate_profile(flow_profile)
    evidence = runtime.run_evidence
    evidence_metadata = _mapping(
        evidence.get("metadata"), "customer-ai-finops-flow.run-evidence.invalid"
    )
    generated = _parse_time(
        evidence_metadata.get("generatedAt"),
        "customer-ai-finops-flow.run-evidence.invalid",
    )
    _validate_prerequisites(
        flow_profile,
        prerequisite_profile,
        prerequisite_report,
        now=generated,
    )
    _validate_bedrock_profile(flow_profile, bedrock_profile, now=generated)
    _validate_observation(
        flow_profile,
        prerequisite_profile.document,
        evidence,
        runtime.live_report,
        runtime.observation,
        bedrock_profile,
    )
    metadata = _mapping(flow_profile.get("metadata"), "customer-ai-finops-flow.profile.invalid")
    spec = _mapping(flow_profile.get("spec"), "customer-ai-finops-flow.profile.invalid")
    release = _mapping(spec.get("release"), "customer-ai-finops-flow.profile.invalid")
    targets = _mapping(spec.get("targets"), "customer-ai-finops-flow.profile.invalid")
    collection = _mapping(spec.get("collection"), "customer-ai-finops-flow.profile.invalid")
    presentation = _mapping(spec.get("presentation"), "customer-ai-finops-flow.profile.invalid")
    objective = _mapping(spec.get("objective"), "customer-ai-finops-flow.profile.invalid")
    evidence_spec = _mapping(evidence.get("spec"), "customer-ai-finops-flow.run-evidence.invalid")
    timing = _mapping(evidence_spec.get("timing"), "customer-ai-finops-flow.run-evidence.invalid")
    telemetry = _mapping(evidence_spec.get("telemetry"), "customer-ai-finops-flow.run-evidence.invalid")
    checks = [_check(identifier, True) for identifier in CHECK_IDS]
    checks[CHECK_IDS.index("protected-inputs")] = _check(
        "protected-inputs", protected_inputs
    )
    summary = _summary(checks)
    report_spec = {
        "status": summary["overallStatus"],
        "qualificationLevel": QUALIFICATION_LEVEL,
        "qualificationBoundary": QUALIFICATION_BOUNDARY,
        "subject": {
            "deploymentProfile": "production-ai-finops-v0",
            "applicationVersion": release["applicationVersion"],
            "chartVersion": release["chartVersion"],
            "contractsApiVersion": API_VERSION,
            "sourceRevision": release["sourceRevision"],
            "imageDigest": release["imageDigest"],
        },
        "bindings": {
            "profileDigest": _digest(flow_profile),
            "environmentBindingDigest": _digest(metadata["environmentId"]),
            "prerequisiteProfileDigest": _digest(prerequisite_profile.document),
            "prerequisiteReportDigest": prerequisite_report.file_digest,
            "bedrockProfileDigest": _digest(bedrock_profile),
            "controlPlaneTargetDigest": _digest(targets["controlPlaneBaseUrl"]),
            "otlpTargetDigest": _digest(targets["otlpTracesEndpoint"]),
            "prometheusTargetDigest": _digest(targets["prometheusBaseUrl"]),
            "grafanaTargetDigest": _digest(targets["grafanaBaseUrl"]),
            "runEvidenceDigest": _digest(evidence),
            "liveCompatibilityReportDigest": evidence_spec[
                "liveCompatibilityReportDigest"
            ],
            "invocationObservationDigest": evidence_spec[
                "invocationObservationDigest"
            ],
            "correlationDigest": evidence_spec["correlationDigest"],
            "usageRecordDigest": _mapping(
                evidence_spec["usage"], "customer-ai-finops-flow.run-evidence.invalid"
            )["recordDigest"],
            "attributionRecordDigest": _mapping(
                evidence_spec["attribution"], "customer-ai-finops-flow.run-evidence.invalid"
            )["recordDigest"],
            "activeAttributionPolicyDocumentDigest": _mapping(
                evidence_spec["attribution"],
                "customer-ai-finops-flow.run-evidence.invalid",
            )["sourceDocumentDigest"],
            "costRecordDigest": _mapping(
                evidence_spec["cost"], "customer-ai-finops-flow.run-evidence.invalid"
            )["recordDigest"],
            "activePriceCatalogDocumentDigest": _mapping(
                evidence_spec["cost"],
                "customer-ai-finops-flow.run-evidence.invalid",
            )["sourceDocumentDigest"],
        },
        "profile": {
            "provider": collection["provider"],
            "operation": collection["operation"],
            "requestPath": collection["requestPath"],
            "telemetryPath": collection["telemetryPath"],
            "contentPolicy": collection["contentPolicy"],
            "costBasis": "calculated-estimate",
            "telemetryBackend": presentation["telemetryBackend"],
            "dashboard": presentation["dashboard"],
            "correlationMode": "protected-request-digest-response",
        },
        "measurements": {
            "profileReviewedAt": metadata["reviewedAt"],
            "startedAt": timing["startedAt"],
            "completedAt": timing["completedAt"],
            "endToEndLatencyMilliseconds": timing[
                "endToEndLatencyMilliseconds"
            ],
            "observationPolls": timing["observationPolls"],
            "providerCallCount": 1,
            "usageRecordCount": 1,
            "attributionRecordCount": 1,
            "costRecordCount": 1,
            "prometheusRequestDelta": int(telemetry["requestCountAfter"])
            - int(telemetry["requestCountBefore"]),
            "dashboardPanelCount": telemetry["dashboardPanelCount"],
        },
        "results": {
            "providerCall": "completed",
            "otlpDelivery": "correlated",
            "usage": "recorded",
            "attribution": "allocated",
            "cost": "priced-calculated-estimate",
            "telemetryAggregate": "observed",
            "dashboard": "provisioned",
        },
        "checks": checks,
        "limitations": list(LIMITATIONS),
        "summary": summary,
    }
    prerequisite_expiry = _parse_time(
        _mapping(prerequisite_report.document.get("metadata"), "customer-ai-finops-flow.prerequisite.invalid").get("validUntil"),
        "customer-ai-finops-flow.prerequisite.invalid",
    )
    valid_until = min(
        generated + timedelta(seconds=int(objective["reportValiditySeconds"])),
        prerequisite_expiry,
    )
    if valid_until <= generated:
        _fail("customer-ai-finops-flow.prerequisite.expired")
    report_metadata = {
        "generatedAt": _timestamp(generated),
        "validUntil": _timestamp(valid_until),
        "sourceRevision": release["sourceRevision"],
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
    code = "customer-ai-finops-flow.report.invalid"
    _validate_schema(
        report,
        "customer-ai-finops-flow-qualification-report.schema.json",
        code,
    )
    metadata = _mapping(report.get("metadata"), code)
    spec = _mapping(report.get("spec"), code)
    checks = _sequence(spec.get("checks"), code)
    summary = _mapping(spec.get("summary"), code)
    generated = _parse_time(metadata.get("generatedAt"), code)
    valid_until = _parse_time(metadata.get("validUntil"), code)
    without_id = dict(metadata)
    identifier = without_id.pop("id", None)
    expected_summary = _summary(
        tuple(_mapping(item, code) for item in checks)
    )
    if (
        report.get("apiVersion") != API_VERSION
        or report.get("kind") != REPORT_KIND
        or spec.get("qualificationLevel") != QUALIFICATION_LEVEL
        or spec.get("qualificationBoundary") != QUALIFICATION_BOUNDARY
        or tuple(_mapping(item, code).get("id") for item in checks) != CHECK_IDS
        or summary != expected_summary
        or spec.get("status") != expected_summary["overallStatus"]
        or spec.get("limitations") != list(LIMITATIONS)
        or valid_until <= generated
        or metadata.get("sourceRevision")
        != _mapping(spec.get("subject"), code).get("sourceRevision")
        or _has_forbidden_key(report)
        or not isinstance(identifier, str)
        or REPORT_ID.fullmatch(identifier) is None
        or identifier != _report_id(without_id, spec)
    ):
        _fail(code)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _headers(path: Path) -> Mapping[str, str]:
    value = _decode(
        _read_bytes(
            path,
            protected=True,
            maximum_bytes=16_384,
            code="customer-ai-finops-flow.headers.invalid",
        ),
        "customer-ai-finops-flow.headers.invalid",
    )
    prohibited = {"host", "content-length", "connection", "proxy-authorization"}
    if (
        not 1 <= len(value) <= 16
        or any(
            not isinstance(name, str)
            or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", name) is None
            or name.lower() in prohibited
            or not isinstance(header_value, str)
            or not 1 <= len(header_value) <= 4096
            or "\r" in header_value
            or "\n" in header_value
            for name, header_value in value.items()
        )
    ):
        _fail("customer-ai-finops-flow.headers.invalid")
    return {str(name): str(value) for name, value in value.items()}


def _tls_context(ca_path: Path | None) -> ssl.SSLContext:
    if ca_path is None:
        return ssl.create_default_context()
    _read_bytes(
        ca_path,
        protected=False,
        maximum_bytes=1_048_576,
        code="customer-ai-finops-flow.ca.invalid",
    )
    try:
        return ssl.create_default_context(cafile=str(ca_path.expanduser().absolute()))
    except (OSError, ssl.SSLError):
        _fail("customer-ai-finops-flow.ca.invalid")


def _http_json(
    url: str,
    *,
    headers: Mapping[str, str],
    ca_path: Path | None,
    timeout: int,
    body: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    request_headers = dict(headers)
    data = None
    if body is not None:
        data = _canonical(body)
        request_headers["Content-Type"] = "application/json"
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=_tls_context(ca_path)),
        _NoRedirect(),
    )
    request = urllib.request.Request(
        url,
        data=data,
        headers=request_headers,
        method="POST" if data is not None else "GET",
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > 2_097_152:
                _fail("customer-ai-finops-flow.http.response-too-large")
            payload = response.read(2_097_153)
            if response.status != 200 or len(payload) > 2_097_152:
                _fail("customer-ai-finops-flow.http.failed")
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, ssl.SSLError):
        _fail("customer-ai-finops-flow.http.failed")
    return _decode(payload, "customer-ai-finops-flow.http.invalid")


def _prometheus_count(
    base_url: str,
    application_id: str,
    *,
    headers: Mapping[str, str],
    ca_path: Path | None,
    timeout: int,
) -> tuple[int, str]:
    query = (
        "sum(iip_ai_allocation_requests{"
        'iip_ai_allocation_dimension="application",'
        f'iip_ai_application_id="{application_id}"'
        "})"
    )
    url = base_url.rstrip("/") + "/api/v1/query?" + urllib.parse.urlencode(
        {"query": query}
    )
    document = _http_json(
        url, headers=headers, ca_path=ca_path, timeout=timeout
    )
    data = _mapping(document.get("data"), "customer-ai-finops-flow.prometheus.invalid")
    result = _sequence(data.get("result"), "customer-ai-finops-flow.prometheus.invalid")
    if document.get("status") != "success":
        _fail("customer-ai-finops-flow.prometheus.invalid")
    if not result:
        return 0, query
    if len(result) != 1:
        _fail("customer-ai-finops-flow.prometheus.invalid")
    value = _mapping(result[0], "customer-ai-finops-flow.prometheus.invalid").get("value")
    if not isinstance(value, list) or len(value) != 2:
        _fail("customer-ai-finops-flow.prometheus.invalid")
    try:
        count = float(value[1])
    except (TypeError, ValueError):
        _fail("customer-ai-finops-flow.prometheus.invalid")
    if not math.isfinite(count) or count < 0 or not count.is_integer():
        _fail("customer-ai-finops-flow.prometheus.invalid")
    return int(count), query


def _dashboard(
    base_url: str,
    uid: str,
    *,
    headers: Mapping[str, str],
    ca_path: Path | None,
    timeout: int,
) -> tuple[Mapping[str, Any], int]:
    response = _http_json(
        base_url.rstrip("/") + "/api/dashboards/uid/" + urllib.parse.quote(uid),
        headers=headers,
        ca_path=ca_path,
        timeout=timeout,
    )
    dashboard = _mapping(
        response.get("dashboard"), "customer-ai-finops-flow.grafana.invalid"
    )
    panels = _sequence(
        dashboard.get("panels"), "customer-ai-finops-flow.grafana.invalid"
    )
    titles = {
        item.get("title")
        for item in panels
        if isinstance(item, Mapping) and isinstance(item.get("title"), str)
    }
    if dashboard.get("uid") != uid or not REQUIRED_PANELS.issubset(titles):
        _fail("customer-ai-finops-flow.grafana.invalid")
    return dashboard, len(panels)


def _terminate(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass
        process.wait()


def _execute_runtime(
    flow_profile: Mapping[str, Any],
    bedrock_profile: Mapping[str, Any],
    *,
    aws_credentials: Path,
    control_headers_path: Path,
    otlp_headers_path: Path,
    prometheus_headers_path: Path,
    grafana_headers_path: Path,
    control_ca: Path | None,
    otlp_ca: Path | None,
    prometheus_ca: Path | None,
    grafana_ca: Path | None,
    otlp_client_cert: Path | None,
    otlp_client_key: Path | None,
    docker_bin: str,
) -> RuntimeDocuments:
    code = "customer-ai-finops-flow.runtime.failed"
    flow_spec = _mapping(flow_profile.get("spec"), code)
    targets = _mapping(flow_spec.get("targets"), code)
    attribution = _mapping(flow_spec.get("attribution"), code)
    presentation = _mapping(flow_spec.get("presentation"), code)
    objective = _mapping(flow_spec.get("objective"), code)
    bedrock_spec = _mapping(bedrock_profile.get("spec"), code)
    target = _mapping(bedrock_spec.get("target"), code)
    bedrock_objective = _mapping(bedrock_spec.get("objective"), code)
    credentials = bedrock.validate_credentials_file(
        aws_credentials, str(target["credentialsProfile"])
    )
    control_headers = _headers(control_headers_path)
    _headers(otlp_headers_path)
    prometheus_headers = _headers(prometheus_headers_path)
    grafana_headers = _headers(grafana_headers_path)
    protected_paths = (
        control_headers_path,
        otlp_headers_path,
        prometheus_headers_path,
        grafana_headers_path,
        credentials,
    )
    if len({str(path.expanduser().absolute()) for path in protected_paths}) != len(
        protected_paths
    ):
        _fail("customer-ai-finops-flow.protected-inputs.invalid")
    if (otlp_client_cert is None) != (otlp_client_key is None):
        _fail("customer-ai-finops-flow.otlp-client-certificate.invalid")
    if otlp_client_cert is not None and otlp_client_key is not None:
        _read_bytes(
            otlp_client_cert,
            protected=False,
            maximum_bytes=1_048_576,
            code="customer-ai-finops-flow.otlp-client-certificate.invalid",
        )
        _read_bytes(
            otlp_client_key,
            protected=True,
            maximum_bytes=1_048_576,
            code="customer-ai-finops-flow.otlp-client-certificate.invalid",
        )
    baseline, query = _prometheus_count(
        str(targets["prometheusBaseUrl"]),
        str(attribution["applicationId"]),
        headers=prometheus_headers,
        ca_path=prometheus_ca,
        timeout=int(objective["requestTimeoutSeconds"]),
    )
    dashboard, panel_count = _dashboard(
        str(targets["grafanaBaseUrl"]),
        str(presentation["dashboardUid"]),
        headers=grafana_headers,
        ca_path=grafana_ca,
        timeout=int(objective["requestTimeoutSeconds"]),
    )
    started_at = datetime.now(timezone.utc)
    started_monotonic = time.monotonic()
    deadline = started_monotonic + int(objective["maximumEndToEndLatencyMilliseconds"]) / 1000
    environment = dict(os.environ)
    for key in tuple(environment):
        if key.startswith(("AWS_", "IIP_BEDROCK_")) or key == "IIP_DOCKER_BIN":
            environment.pop(key, None)
    with tempfile.TemporaryDirectory(prefix="iip-customer-ai-finops-flow-") as temporary:
        output_dir = Path(temporary)
        live_name = "live-compatibility.json"
        correlation_name = "invocation-correlation.json"
        environment.update(
            {
                "IIP_DOCKER_BIN": docker_bin,
                "IIP_BEDROCK_COMPATIBILITY_MODE": "live",
                "IIP_BEDROCK_LIVE_TEST_ENABLED": "true",
                "IIP_BEDROCK_MODEL_ID": str(target["modelId"]),
                "IIP_BEDROCK_OPERATION": "converse-stream",
                "IIP_BEDROCK_CREDENTIALS_PROFILE": str(
                    target["credentialsProfile"]
                ),
                "IIP_BEDROCK_AWS_CREDENTIALS_FILE": str(credentials),
                "IIP_BEDROCK_MAXIMUM_PROVIDER_CALL_MILLISECONDS": str(
                    bedrock_objective["maximumProviderCallLatencyMilliseconds"]
                ),
                "IIP_BEDROCK_OUTPUT_DIR": str(output_dir),
                "IIP_BEDROCK_REPORT_BASENAME": live_name,
                "IIP_BEDROCK_OTLP_TRACES_ENDPOINT": str(
                    targets["otlpTracesEndpoint"]
                ),
                "IIP_BEDROCK_OTLP_HEADERS_FILE": str(otlp_headers_path),
                "IIP_BEDROCK_CORRELATION_BASENAME": correlation_name,
                "AWS_REGION": str(target["region"]),
            }
        )
        if otlp_ca is not None:
            environment["IIP_BEDROCK_OTLP_CA_FILE"] = str(otlp_ca)
        if otlp_client_cert is not None and otlp_client_key is not None:
            environment["IIP_BEDROCK_OTLP_CLIENT_CERT_FILE"] = str(otlp_client_cert)
            environment["IIP_BEDROCK_OTLP_CLIENT_KEY_FILE"] = str(otlp_client_key)
        try:
            process = subprocess.Popen(
                [str(ROOT / "scripts/test_bedrock_instrumentation.sh")],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            remaining = max(1, int(deadline - time.monotonic()))
            try:
                return_code = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                _terminate(process)
                _fail("customer-ai-finops-flow.runtime.timeout")
        except OSError:
            _fail(code)
        if return_code != 0:
            _fail(code)
        live_report = _load_document(
            output_dir / live_name, protected=False, maximum_bytes=1_048_576
        ).document
        correlation = _load_document(
            output_dir / correlation_name, protected=True, maximum_bytes=131_072
        ).document
        _validate_schema(
            correlation,
            "bedrock-invocation-correlation.schema.json",
            "customer-ai-finops-flow.correlation.invalid",
        )
        correlation_metadata = _mapping(
            correlation.get("metadata"), "customer-ai-finops-flow.correlation.invalid"
        )
        correlation_spec = _mapping(
            correlation.get("spec"), "customer-ai-finops-flow.correlation.invalid"
        )
        release = _mapping(flow_spec.get("release"), code)
        if (
            correlation_metadata.get("sourceRevision")
            != release.get("sourceRevision")
            or correlation_metadata.get("sourceDirty") is not False
            or correlation_spec.get("deliveryEndpointDigest")
            != _raw_digest(str(targets["otlpTracesEndpoint"]))
            or correlation_spec.get("contentCaptured") is not False
        ):
            _fail("customer-ai-finops-flow.correlation.invalid")
        observation_request = {
            "apiVersion": API_VERSION,
            "kind": "AiEconomicsInvocationObservationRequest",
            "spec": {
                "traceId": correlation_spec["traceId"],
                "spanId": correlation_spec["spanId"],
            },
        }
        polls = 0
        observation: Mapping[str, Any] = {}
        while time.monotonic() < deadline:
            polls += 1
            observation = _http_json(
                str(targets["controlPlaneBaseUrl"]).rstrip("/")
                + "/v1/operations/ai-economics/invocation-observations",
                headers=control_headers,
                ca_path=control_ca,
                timeout=int(objective["requestTimeoutSeconds"]),
                body=observation_request,
            )
            if _mapping(observation.get("spec"), code).get("status") == "complete":
                break
            time.sleep(int(objective["pollIntervalMilliseconds"]) / 1000)
        else:
            _fail("customer-ai-finops-flow.runtime.timeout")
        observed_count = baseline
        while time.monotonic() < deadline:
            observed_count, observed_query = _prometheus_count(
                str(targets["prometheusBaseUrl"]),
                str(attribution["applicationId"]),
                headers=prometheus_headers,
                ca_path=prometheus_ca,
                timeout=int(objective["requestTimeoutSeconds"]),
            )
            if observed_query != query:
                _fail("customer-ai-finops-flow.prometheus.invalid")
            if observed_count >= baseline + 1:
                break
            time.sleep(int(objective["pollIntervalMilliseconds"]) / 1000)
        else:
            _fail("customer-ai-finops-flow.runtime.timeout")
        dashboard, panel_count = _dashboard(
            str(targets["grafanaBaseUrl"]),
            str(presentation["dashboardUid"]),
            headers=grafana_headers,
            ca_path=grafana_ca,
            timeout=int(objective["requestTimeoutSeconds"]),
        )
    completed_at = datetime.now(timezone.utc)
    elapsed_milliseconds = int((time.monotonic() - started_monotonic) * 1000 + 0.5)
    observation_spec = _mapping(observation.get("spec"), code)
    sources = _mapping(observation_spec.get("sources"), code)
    attribution_source = _mapping(sources.get("attribution"), code)
    pricing_source = _mapping(sources.get("pricing"), code)
    usage = _mapping(observation_spec.get("usage"), code)
    observed_attribution = _mapping(observation_spec.get("attribution"), code)
    cost = _mapping(observation_spec.get("cost"), code)
    priced = _mapping(cost.get("pricedCost"), code)
    tenant_id = _mapping(observation.get("metadata"), code).get("tenantId")
    correlation_digest = _digest(
        {
            "tenantId": tenant_id,
            "traceId": correlation_spec["traceId"],
            "spanId": correlation_spec["spanId"],
        }
    )
    run_evidence = {
        "apiVersion": API_VERSION,
        "kind": RUN_EVIDENCE_KIND,
        "metadata": {
            "generatedAt": _timestamp(completed_at),
            "sourceRevision": _mapping(flow_spec.get("release"), code)[
                "sourceRevision"
            ],
            "sourceDirty": False,
        },
        "spec": {
            "traceId": correlation_spec["traceId"],
            "spanId": correlation_spec["spanId"],
            "correlationDigest": correlation_digest,
            "deliveryEndpointDigest": correlation_spec["deliveryEndpointDigest"],
            "liveCompatibilityReportDigest": _digest(live_report),
            "invocationObservationDigest": _digest(observation),
            "usage": {
                "recordId": usage.get("recordId"),
                "recordDigest": usage.get("recordDigest"),
            },
            "attribution": {
                "recordId": observed_attribution.get("recordId"),
                "recordDigest": observed_attribution.get("recordDigest"),
                "sourceDocumentDigest": attribution_source.get("documentDigest"),
                "applicationId": observed_attribution.get("applicationId"),
                "teamId": observed_attribution.get("teamId"),
            },
            "cost": {
                "recordId": cost.get("recordId"),
                "recordDigest": cost.get("recordDigest"),
                "sourceDocumentDigest": pricing_source.get("documentDigest"),
                "currency": priced.get("currency"),
                "currencyScale": priced.get("currencyScale"),
                "totalSubunits": priced.get("totalSubunits"),
                "costBasis": priced.get("costBasis"),
            },
            "telemetry": {
                "queryDigest": _digest(query),
                "requestCountBefore": baseline,
                "requestCountAfter": observed_count,
                "dashboardDocumentDigest": _digest(dashboard),
                "dashboardPanelCount": panel_count,
            },
            "timing": {
                "startedAt": _timestamp(started_at),
                "completedAt": _timestamp(completed_at),
                "endToEndLatencyMilliseconds": elapsed_milliseconds,
                "observationPolls": polls,
            },
            "privacy": {
                "contentCaptured": False,
                "rawPayloadPersisted": False,
                "transportable": False,
            },
        },
    }
    return RuntimeDocuments(run_evidence, live_report, observation)


def qualify(
    *,
    flow_profile: Mapping[str, Any],
    prerequisite_profile: FileDocument,
    prerequisite_report: FileDocument,
    bedrock_profile: Mapping[str, Any],
    allow_provider_call: bool,
    runner: RuntimeRunner,
    now: datetime | None = None,
) -> tuple[Mapping[str, Any], RuntimeDocuments]:
    if not allow_provider_call:
        _fail("customer-ai-finops-flow.enable.required")
    validate_profile(flow_profile)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        _fail("customer-ai-finops-flow.time.invalid")
    current = current.astimezone(timezone.utc)
    release = _mapping(
        _mapping(flow_profile.get("spec"), "customer-ai-finops-flow.profile.invalid").get("release"),
        "customer-ai-finops-flow.profile.invalid",
    )
    revision, dirty = _source_identity()
    if dirty or revision != release.get("sourceRevision"):
        _fail("customer-ai-finops-flow.source.mismatch")
    reviewed = _parse_time(
        _mapping(flow_profile.get("metadata"), "customer-ai-finops-flow.profile.invalid").get("reviewedAt"),
        "customer-ai-finops-flow.profile.invalid",
    )
    objective = _mapping(
        _mapping(flow_profile.get("spec"), "customer-ai-finops-flow.profile.invalid").get("objective"),
        "customer-ai-finops-flow.profile.invalid",
    )
    if (
        reviewed > current + timedelta(seconds=int(objective["maximumClockSkewSeconds"]))
        or (current - reviewed).total_seconds()
        > int(objective["maximumProfileAgeSeconds"])
    ):
        _fail("customer-ai-finops-flow.profile.stale")
    _validate_prerequisites(
        flow_profile,
        prerequisite_profile,
        prerequisite_report,
        now=current,
    )
    _validate_bedrock_profile(flow_profile, bedrock_profile, now=current)
    runtime = runner(flow_profile, bedrock_profile)
    current_revision, current_dirty = _source_identity()
    if current_dirty or current_revision != revision:
        _fail("customer-ai-finops-flow.source.changed")
    report = build_report(
        flow_profile=flow_profile,
        prerequisite_profile=prerequisite_profile,
        prerequisite_report=prerequisite_report,
        bedrock_profile=bedrock_profile,
        runtime=runtime,
        protected_inputs=True,
    )
    return report, runtime


def verify(
    *,
    report: Mapping[str, Any],
    flow_profile: Mapping[str, Any],
    prerequisite_profile: FileDocument,
    prerequisite_report: FileDocument,
    bedrock_profile: Mapping[str, Any],
    runtime: RuntimeDocuments,
    require_qualified: bool,
    now: datetime | None = None,
) -> Mapping[str, Any]:
    validate_report_document(report)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        _fail("customer-ai-finops-flow.time.invalid")
    current = current.astimezone(timezone.utc)
    revision, dirty = _source_identity()
    if dirty or revision != _mapping(report.get("metadata"), "customer-ai-finops-flow.report.invalid").get("sourceRevision"):
        _fail("customer-ai-finops-flow.source.mismatch")
    validate_profile(flow_profile)
    profile_metadata = _mapping(
        flow_profile.get("metadata"), "customer-ai-finops-flow.profile.invalid"
    )
    profile_objective = _mapping(
        _mapping(
            flow_profile.get("spec"), "customer-ai-finops-flow.profile.invalid"
        ).get("objective"),
        "customer-ai-finops-flow.profile.invalid",
    )
    reviewed = _parse_time(
        profile_metadata.get("reviewedAt"),
        "customer-ai-finops-flow.profile.invalid",
    )
    if (
        reviewed
        > current
        + timedelta(seconds=int(profile_objective["maximumClockSkewSeconds"]))
        or (current - reviewed).total_seconds()
        > int(profile_objective["maximumProfileAgeSeconds"])
    ):
        _fail("customer-ai-finops-flow.profile.stale")
    _validate_prerequisites(
        flow_profile,
        prerequisite_profile,
        prerequisite_report,
        now=current,
    )
    _validate_bedrock_profile(flow_profile, bedrock_profile, now=current)
    expected = build_report(
        flow_profile=flow_profile,
        prerequisite_profile=prerequisite_profile,
        prerequisite_report=prerequisite_report,
        bedrock_profile=bedrock_profile,
        runtime=runtime,
        protected_inputs=True,
    )
    if expected != report:
        _fail("customer-ai-finops-flow.report.evidence-mismatch")
    if current > _parse_time(
        _mapping(report.get("metadata"), "customer-ai-finops-flow.report.invalid").get("validUntil"),
        "customer-ai-finops-flow.report.invalid",
    ):
        _fail("customer-ai-finops-flow.report.expired")
    if require_qualified and _mapping(report.get("spec"), "customer-ai-finops-flow.report.invalid").get("status") != "qualified":
        _fail("customer-ai-finops-flow.report.not-qualified")
    return report


def _write(path: Path, document: Mapping[str, Any], *, mode: int) -> None:
    destination = path.expanduser().absolute()
    if destination.is_symlink():
        _fail("customer-ai-finops-flow.output.invalid")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        descriptor, name = tempfile.mkstemp(
            dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp"
        )
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    except OSError:
        _fail("customer-ai-finops-flow.output.invalid")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _require_distinct_paths(paths: Sequence[Path]) -> None:
    resolved = tuple(str(path.expanduser().absolute()) for path in paths)
    if len(set(resolved)) != len(resolved):
        _fail("customer-ai-finops-flow.output-paths.invalid")


def _require_disjoint_outputs(
    inputs: Sequence[Path], outputs: Sequence[Path]
) -> None:
    input_paths = {str(path.expanduser().absolute()) for path in inputs}
    output_paths = tuple(str(path.expanduser().absolute()) for path in outputs)
    if len(set(output_paths)) != len(output_paths) or input_paths.intersection(
        output_paths
    ):
        _fail("customer-ai-finops-flow.output-paths.invalid")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    qualify_parser = commands.add_parser("qualify")
    qualify_parser.add_argument("--profile", type=Path, required=True)
    qualify_parser.add_argument("--prerequisite-profile", type=Path, required=True)
    qualify_parser.add_argument("--prerequisite-report", type=Path, required=True)
    qualify_parser.add_argument("--bedrock-profile", type=Path, required=True)
    qualify_parser.add_argument("--aws-credentials-file", type=Path, required=True)
    qualify_parser.add_argument("--control-plane-headers", type=Path, required=True)
    qualify_parser.add_argument("--otlp-headers", type=Path, required=True)
    qualify_parser.add_argument("--prometheus-headers", type=Path, required=True)
    qualify_parser.add_argument("--grafana-headers", type=Path, required=True)
    qualify_parser.add_argument("--control-plane-ca", type=Path)
    qualify_parser.add_argument("--otlp-ca", type=Path)
    qualify_parser.add_argument("--prometheus-ca", type=Path)
    qualify_parser.add_argument("--grafana-ca", type=Path)
    qualify_parser.add_argument("--otlp-client-cert", type=Path)
    qualify_parser.add_argument("--otlp-client-key", type=Path)
    qualify_parser.add_argument("--docker-bin", default="docker")
    qualify_parser.add_argument("--run-evidence-output", type=Path, required=True)
    qualify_parser.add_argument("--live-report-output", type=Path, required=True)
    qualify_parser.add_argument("--observation-output", type=Path, required=True)
    qualify_parser.add_argument("--output", type=Path, required=True)
    qualify_parser.add_argument("--allow-provider-call", action="store_true")
    verify_parser = commands.add_parser("verify")
    verify_parser.add_argument("--profile", type=Path, required=True)
    verify_parser.add_argument("--prerequisite-profile", type=Path, required=True)
    verify_parser.add_argument("--prerequisite-report", type=Path, required=True)
    verify_parser.add_argument("--bedrock-profile", type=Path, required=True)
    verify_parser.add_argument("--run-evidence", type=Path, required=True)
    verify_parser.add_argument("--live-report", type=Path, required=True)
    verify_parser.add_argument("--observation", type=Path, required=True)
    verify_parser.add_argument("--report", type=Path, required=True)
    verify_parser.add_argument("--require-qualified", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        flow_profile = load_profile(arguments.profile)
        prerequisite_profile = _load_document(
            arguments.prerequisite_profile, protected=True, maximum_bytes=262_144
        )
        prerequisite_report = _load_document(
            arguments.prerequisite_report, protected=False, maximum_bytes=2_097_152
        )
        bedrock_profile_document = bedrock.load_profile(arguments.bedrock_profile)
        if arguments.command == "qualify":
            protected_inputs = (
                arguments.aws_credentials_file,
                arguments.control_plane_headers,
                arguments.otlp_headers,
                arguments.prometheus_headers,
                arguments.grafana_headers,
            ) + (
                (arguments.otlp_client_key,)
                if arguments.otlp_client_key is not None
                else ()
            )
            _require_distinct_paths(protected_inputs)
            outputs = (
                arguments.run_evidence_output,
                arguments.live_report_output,
                arguments.observation_output,
                arguments.output,
            )
            _require_disjoint_outputs(
                (
                    arguments.profile,
                    arguments.prerequisite_profile,
                    arguments.prerequisite_report,
                    arguments.bedrock_profile,
                    *protected_inputs,
                )
                + tuple(
                    path
                    for path in (
                        arguments.control_plane_ca,
                        arguments.otlp_ca,
                        arguments.prometheus_ca,
                        arguments.grafana_ca,
                        arguments.otlp_client_cert,
                        arguments.otlp_client_key,
                    )
                    if path is not None
                ),
                outputs,
            )
            runner = lambda flow, selected: _execute_runtime(
                flow,
                selected,
                aws_credentials=arguments.aws_credentials_file,
                control_headers_path=arguments.control_plane_headers,
                otlp_headers_path=arguments.otlp_headers,
                prometheus_headers_path=arguments.prometheus_headers,
                grafana_headers_path=arguments.grafana_headers,
                control_ca=arguments.control_plane_ca,
                otlp_ca=arguments.otlp_ca,
                prometheus_ca=arguments.prometheus_ca,
                grafana_ca=arguments.grafana_ca,
                otlp_client_cert=arguments.otlp_client_cert,
                otlp_client_key=arguments.otlp_client_key,
                docker_bin=arguments.docker_bin,
            )
            report, runtime = qualify(
                flow_profile=flow_profile,
                prerequisite_profile=prerequisite_profile,
                prerequisite_report=prerequisite_report,
                bedrock_profile=bedrock_profile_document,
                allow_provider_call=arguments.allow_provider_call,
                runner=runner,
            )
            _write(arguments.run_evidence_output, runtime.run_evidence, mode=0o600)
            _write(arguments.live_report_output, runtime.live_report, mode=0o600)
            _write(arguments.observation_output, runtime.observation, mode=0o600)
            _write(arguments.output, report, mode=0o644)
            print(f"customer AI FinOps flow qualified: {arguments.output}")
            return 0
        runtime = RuntimeDocuments(
            _load_document(
                arguments.run_evidence, protected=True, maximum_bytes=1_048_576
            ).document,
            _load_document(
                arguments.live_report, protected=True, maximum_bytes=1_048_576
            ).document,
            _load_document(
                arguments.observation, protected=True, maximum_bytes=1_048_576
            ).document,
        )
        report = _load_document(
            arguments.report, protected=False, maximum_bytes=2_097_152
        ).document
        verify(
            report=report,
            flow_profile=flow_profile,
            prerequisite_profile=prerequisite_profile,
            prerequisite_report=prerequisite_report,
            bedrock_profile=bedrock_profile_document,
            runtime=runtime,
            require_qualified=arguments.require_qualified,
        )
        print("customer AI FinOps flow report verified")
    except (
        CustomerAiFinopsFlowError,
        bedrock.CustomerBedrockQualificationError,
    ) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
