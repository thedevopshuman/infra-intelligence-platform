#!/usr/bin/env python3
"""Qualify one customer policy endpoint and reviewed immutable bundle."""

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
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from iip import __version__ as APPLICATION_VERSION  # noqa: E402
from iip.adapters.policy import (  # noqa: E402
    ExternalHttpPolicyDecisionPoint,
    ExternalPolicyConfiguration,
)
from iip.application.ports import ActorContext, PolicyDecision  # noqa: E402


API_VERSION = "iip.platform/v1alpha1"
PROFILE_KIND = "CustomerPolicyQualificationProfile"
REPORT_KIND = "CustomerPolicyQualificationReport"
QUALIFICATION = "customer-policy-engine-bundle-prerequisites-v1"
PROFILE_SCHEMA = ROOT / "contracts/schemas/customer-policy-qualification-profile.schema.json"
REPORT_SCHEMA = ROOT / "contracts/schemas/customer-policy-qualification-report.schema.json"
REPORT_ID = re.compile(r"^cpq_[a-f0-9]{32}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
REVISION = re.compile(r"^[a-f0-9]{40,64}$")
BEARER = re.compile(r"[A-Za-z0-9._~+/-]{32,8192}=*")
CHECK_IDS = (
    "profile-binding",
    "source-binding",
    "protected-input-files",
    "endpoint-binding",
    "ca-verified-tls",
    "credential-accepted",
    "exact-input-digest-binding",
    "tenant-binding",
    "snapshot-binding",
    "allow-cases",
    "deny-cases",
    "repeat-consistency",
    "latency-objective",
    "minimized-output",
)
LIMITATIONS = (
    "policy-engine-ha-failover-and-network-path-not-qualified",
    "credential-rotation-revocation-and-emergency-access-not-qualified",
    "bundle-review-change-control-and-break-glass-not-qualified",
    "audit-delivery-retention-and-siem-integration-not-qualified",
)
FORBIDDEN_RETAINED_KEYS = frozenset(
    {
        "action",
        "actorId",
        "allowed",
        "authorization",
        "baseUrl",
        "caseId",
        "credential",
        "endpoint",
        "policySnapshotRef",
        "reasonCode",
        "resource",
        "roles",
        "secret",
        "tenantId",
        "token",
        "url",
    }
)
FORBIDDEN_PROFILE_KEY_PARTS = (
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "password",
    "privatekey",
    "secret",
    "token",
)


class CustomerPolicyQualificationError(RuntimeError):
    """Stable failure for unsafe or crossed customer policy qualification input."""


class DecisionPoint(Protocol):
    def decide(
        self,
        actor: ActorContext,
        action: str,
        resource: Mapping[str, object],
    ) -> PolicyDecision:
        """Evaluate one exact policy case."""


def _fail(code: str) -> None:
    raise CustomerPolicyQualificationError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest_value(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        _fail("customer-policy-qualification.time.invalid")
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


def _integer(value: object, minimum: int, maximum: int, code: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        _fail(code)
    return value


def _schema(path: Path, code: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail(code)
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _validate_schema(document: Mapping[str, Any], path: Path, code: str) -> None:
    errors = sorted(
        Draft202012Validator(
            _schema(path, code), format_checker=FormatChecker()
        ).iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        _fail(code)


def _protected_bytes(path: Path, *, maximum_bytes: int, code: str) -> bytes:
    descriptor = -1
    candidate = path.expanduser()
    try:
        if candidate.is_symlink():
            _fail(code)
        descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or stat.S_IMODE(details.st_mode) != 0o600
            or details.st_size > maximum_bytes
        ):
            _fail(code)
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            payload = handle.read(maximum_bytes + 1)
        if len(payload) > maximum_bytes:
            _fail(code)
        return payload
    except CustomerPolicyQualificationError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _regular_file(path: Path, *, maximum_bytes: int, code: str) -> Path:
    candidate = path.expanduser()
    try:
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or candidate.stat().st_size > maximum_bytes
        ):
            _fail(code)
        return candidate.resolve()
    except CustomerPolicyQualificationError:
        raise
    except OSError:
        _fail(code)


def load_profile(path: Path) -> Mapping[str, Any]:
    payload = _protected_bytes(
        path,
        maximum_bytes=262_144,
        code="customer-policy-qualification.profile.invalid",
    )
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("customer-policy-qualification.profile.invalid")
    if not isinstance(document, Mapping):
        _fail("customer-policy-qualification.profile.invalid")
    validate_profile(document)
    return document


def validate_bearer_file(path: Path) -> Path:
    payload = _protected_bytes(
        path,
        maximum_bytes=8194,
        code="customer-policy-qualification.credential.invalid",
    )
    try:
        raw = payload.decode("ascii")
    except UnicodeDecodeError:
        _fail("customer-policy-qualification.credential.invalid")
    token = raw.rstrip("\r\n")
    if (
        raw not in (token, token + "\n", token + "\r\n")
        or BEARER.fullmatch(token) is None
    ):
        _fail("customer-policy-qualification.credential.invalid")
    return path.expanduser().resolve()


def _strict_https_url(value: object, code: str) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 2048
        or re.search(r"[\x00-\x20\x7f]", value) is not None
    ):
        _fail(code)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        _fail(code)
    if (
        parsed.scheme.lower() != "https"
        or parsed.hostname is None
        or not parsed.hostname.isascii()
        or len(parsed.hostname) > 253
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path in ("", "/")
    ):
        _fail(code)
    host = parsed.hostname.lower()
    host_text = f"[{host}]" if ":" in host else host
    authority = host_text if port in (None, 443) else f"{host_text}:{port}"
    return f"https://{authority}{parsed.path}"


def _resource_depth(value: object, depth: int = 0) -> int:
    if depth > 8:
        return depth
    if isinstance(value, Mapping):
        return max((_resource_depth(item, depth + 1) for item in value.values()), default=depth)
    if isinstance(value, list):
        return max((_resource_depth(item, depth + 1) for item in value), default=depth)
    return depth


def _contains_sensitive_key(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if any(part in normalized for part in FORBIDDEN_PROFILE_KEY_PARTS):
                return True
            if _contains_sensitive_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def validate_profile(profile: Mapping[str, Any]) -> None:
    code = "customer-policy-qualification.profile.invalid"
    _validate_schema(profile, PROFILE_SCHEMA, code)
    metadata = _mapping(profile.get("metadata"), code)
    spec = _mapping(profile.get("spec"), code)
    identity = _mapping(spec.get("identity"), code)
    objective = _mapping(spec.get("objective"), code)
    cases = spec.get("cases")
    endpoint = _strict_https_url(spec.get("endpoint"), code)
    roles = identity.get("roles")
    if (
        profile.get("apiVersion") != API_VERSION
        or profile.get("kind") != PROFILE_KIND
        or not isinstance(metadata.get("name"), str)
        or not isinstance(cases, list)
        or not isinstance(roles, list)
        or roles != sorted(roles)
        or endpoint != spec.get("endpoint")
        or len(_canonical(profile)) > 262_144
    ):
        _fail(code)
    _parse_timestamp(metadata.get("reviewedAt"), code)
    timeout = _integer(objective.get("requestTimeoutSeconds"), 1, 30, code)
    maximum_latency = _integer(
        objective.get("maximumDecisionLatencyMilliseconds"), 1, 30_000, code
    )
    if maximum_latency > timeout * 1000:
        _fail(code)
    identifiers: list[str] = []
    outcomes: list[bool] = []
    snapshots: set[str] = set()
    tenant = identity.get("tenantId")
    for raw_case in cases:
        case = _mapping(raw_case, code)
        resource = _mapping(case.get("resource"), code)
        expected = _mapping(case.get("expected"), code)
        identifier = case.get("id")
        snapshot = expected.get("policySnapshotRef")
        if (
            not isinstance(identifier, str)
            or resource.get("tenantId") != tenant
            or not isinstance(snapshot, str)
            or not snapshot.startswith(f"policy://{tenant}/snapshots/")
            or snapshot.endswith(("/unavailable", "/input-invalid", "/input-too-large"))
            or expected.get("reasonCode") in {"policy.unavailable", "policy.input-invalid", "policy.input-too-large"}
            or _resource_depth(resource) > 6
            or len(_canonical(resource)) > 65_536
            or _contains_sensitive_key(resource)
        ):
            _fail(code)
        identifiers.append(identifier)
        outcomes.append(bool(expected.get("allowed")))
        snapshots.add(snapshot)
    if (
        identifiers != sorted(identifiers)
        or len(identifiers) != len(set(identifiers))
        or set(outcomes) != {False, True}
        or len(snapshots) != 1
    ):
        _fail(code)


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
                ["git", "status", "--porcelain", "--untracked-files=all"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        _fail("customer-policy-qualification.source.unavailable")
    if REVISION.fullmatch(revision) is None:
        _fail("customer-policy-qualification.source.invalid")
    return revision, dirty


def _check(identifier: str, passed: bool) -> dict[str, str]:
    result = {"id": identifier, "status": "passed" if passed else "failed"}
    if not passed:
        result["errorCode"] = f"customer-policy-qualification.{identifier}.failed"
    return result


def _expected_summary(checks: Sequence[Mapping[str, Any]]) -> dict[str, object]:
    passed = sum(1 for item in checks if item.get("status") == "passed")
    failed = len(checks) - passed
    return {
        "totalChecks": len(checks),
        "passedChecks": passed,
        "failedChecks": failed,
        "overallStatus": "qualified" if failed == 0 else "not-qualified",
    }


def _has_forbidden_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in FORBIDDEN_RETAINED_KEYS or _has_forbidden_key(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_forbidden_key(item) for item in value)
    return False


def _report_identifier(metadata_without_id: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    return "cpq_" + hashlib.sha256(
        _canonical({"metadata": metadata_without_id, "spec": spec})
    ).hexdigest()[:32]


def _matches(decision: PolicyDecision, expected: Mapping[str, Any]) -> bool:
    return (
        decision.allowed is expected.get("allowed")
        and decision.reason_code == expected.get("reasonCode")
        and decision.policy_snapshot_ref == expected.get("policySnapshotRef")
    )


def build_report(
    *,
    revision: str,
    profile: Mapping[str, Any],
    image_digest: str,
    started_at: datetime,
    completed_at: datetime,
    maximum_latency_milliseconds: int,
    observations: Mapping[str, bool],
) -> dict[str, Any]:
    code = "customer-policy-qualification.report.invalid"
    validate_profile(profile)
    if REVISION.fullmatch(revision) is None or DIGEST.fullmatch(image_digest) is None:
        _fail(code)
    spec_profile = _mapping(profile.get("spec"), code)
    metadata_profile = _mapping(profile.get("metadata"), code)
    objective = _mapping(spec_profile.get("objective"), code)
    cases = spec_profile.get("cases")
    if not isinstance(cases, list):
        _fail(code)
    expected_snapshots = sorted(
        {
            _mapping(_mapping(item, code).get("expected"), code).get("policySnapshotRef")
            for item in cases
        }
    )
    allow_count = sum(
        1
        for item in cases
        if _mapping(_mapping(item, code).get("expected"), code).get("allowed") is True
    )
    checks = [
        _check(identifier, observations.get(identifier, False))
        for identifier in CHECK_IDS
    ]
    summary = _expected_summary(checks)
    report_spec: dict[str, Any] = {
        "status": summary["overallStatus"],
        "qualification": QUALIFICATION,
        "subject": {
            "applicationVersion": APPLICATION_VERSION,
            "contractsApiVersion": API_VERSION,
            "sourceRevision": revision,
            "imageDigest": image_digest,
        },
        "bindings": {
            "endpointBindingDigest": _digest_value(spec_profile["endpoint"]),
            "profileDigest": _digest_value(profile),
            "caseSetDigest": _digest_value(cases),
            "snapshotSetDigest": _digest_value(expected_snapshots),
        },
        "profile": {
            "name": "customer-external-http-policy-v1",
            "transport": "ca-verified-https-json",
            "requestWrapper": "opa-input",
            "credentialMode": "protected-bearer-file-read-per-request",
            "redirectMode": "denied",
            "proxyMode": "disabled",
        },
        "objective": dict(objective),
        "measurements": {
            "profileReviewedAt": metadata_profile["reviewedAt"],
            "startedAt": _timestamp(started_at),
            "completedAt": _timestamp(completed_at),
            "caseCount": len(cases),
            "allowCaseCount": allow_count,
            "denyCaseCount": len(cases) - allow_count,
            "requestCount": len(cases) + 1,
            "maximumDecisionLatencyMilliseconds": maximum_latency_milliseconds,
        },
        "checks": checks,
        "limitations": list(LIMITATIONS),
        "summary": summary,
    }
    metadata_without_id = {
        "generatedAt": _timestamp(completed_at),
        "sourceRevision": revision,
        "sourceDirty": False,
    }
    report = {
        "apiVersion": API_VERSION,
        "kind": REPORT_KIND,
        "metadata": {
            "id": _report_identifier(metadata_without_id, report_spec),
            **metadata_without_id,
        },
        "spec": report_spec,
    }
    if _has_forbidden_key(report):
        _fail("customer-policy-qualification.report.not-minimized")
    validate_report_document(report)
    return report


def validate_report_document(report: Mapping[str, Any]) -> None:
    _validate_schema(
        report,
        REPORT_SCHEMA,
        "customer-policy-qualification.report.schema-invalid",
    )
    metadata = _mapping(
        report.get("metadata"), "customer-policy-qualification.report.invalid"
    )
    spec = _mapping(report.get("spec"), "customer-policy-qualification.report.invalid")
    subject = _mapping(spec.get("subject"), "customer-policy-qualification.report.invalid")
    bindings = _mapping(spec.get("bindings"), "customer-policy-qualification.report.invalid")
    measurements = _mapping(
        spec.get("measurements"), "customer-policy-qualification.report.invalid"
    )
    objective = _mapping(spec.get("objective"), "customer-policy-qualification.report.invalid")
    checks = spec.get("checks")
    if not isinstance(checks, list) or [
        item.get("id") for item in checks if isinstance(item, Mapping)
    ] != list(CHECK_IDS):
        _fail("customer-policy-qualification.report.checks-invalid")
    for identifier, item in zip(CHECK_IDS, checks):
        current = _mapping(item, "customer-policy-qualification.report.checks-invalid")
        expected = _check(identifier, current.get("status") == "passed")
        if current != expected:
            _fail("customer-policy-qualification.report.checks-invalid")
    reviewed = _parse_timestamp(
        measurements.get("profileReviewedAt"),
        "customer-policy-qualification.report.time-invalid",
    )
    started = _parse_timestamp(
        measurements.get("startedAt"),
        "customer-policy-qualification.report.time-invalid",
    )
    completed = _parse_timestamp(
        measurements.get("completedAt"),
        "customer-policy-qualification.report.time-invalid",
    )
    case_count = _integer(
        measurements.get("caseCount"), 2, 64,
        "customer-policy-qualification.report.measurements-invalid",
    )
    allow_count = _integer(
        measurements.get("allowCaseCount"), 1, 63,
        "customer-policy-qualification.report.measurements-invalid",
    )
    deny_count = _integer(
        measurements.get("denyCaseCount"), 1, 63,
        "customer-policy-qualification.report.measurements-invalid",
    )
    maximum_latency = _integer(
        measurements.get("maximumDecisionLatencyMilliseconds"), 0, 60_000,
        "customer-policy-qualification.report.measurements-invalid",
    )
    if (
        not reviewed <= started <= completed
        or completed != _parse_timestamp(metadata.get("generatedAt"), "customer-policy-qualification.report.time-invalid")
        or metadata.get("sourceRevision") != subject.get("sourceRevision")
        or case_count != allow_count + deny_count
        or measurements.get("requestCount") != case_count + 1
        or maximum_latency > objective.get("requestTimeoutSeconds") * 1000
        or spec.get("limitations") != list(LIMITATIONS)
        or _has_forbidden_key(report)
        or any(DIGEST.fullmatch(str(bindings.get(key))) is None for key in (
            "endpointBindingDigest", "profileDigest", "caseSetDigest", "snapshotSetDigest"
        ))
    ):
        _fail("customer-policy-qualification.report.invalid")
    summary = _expected_summary(checks)
    check_by_id = {
        item.get("id"): item.get("status") == "passed"
        for item in checks
        if isinstance(item, Mapping)
    }
    if check_by_id.get("profile-binding") != (
        (started - reviewed).total_seconds()
        <= objective.get("maximumProfileAgeSeconds")
    ) or check_by_id.get("latency-objective") != (
        maximum_latency <= objective.get("maximumDecisionLatencyMilliseconds")
    ):
        _fail("customer-policy-qualification.report.checks-invalid")
    if any(
        check_by_id.get(identifier) is not True
        for identifier in (
            "source-binding",
            "protected-input-files",
            "endpoint-binding",
            "minimized-output",
        )
    ):
        _fail("customer-policy-qualification.report.checks-invalid")
    if spec.get("summary") != summary or spec.get("status") != summary["overallStatus"]:
        _fail("customer-policy-qualification.report.summary-invalid")
    metadata_without_id = dict(metadata)
    identifier = metadata_without_id.pop("id", None)
    if (
        not isinstance(identifier, str)
        or REPORT_ID.fullmatch(identifier) is None
        or identifier != _report_identifier(metadata_without_id, spec)
    ):
        _fail("customer-policy-qualification.report.id-invalid")


def _decision_point(
    profile: Mapping[str, Any], bearer_path: Path, ca_path: Path
) -> ExternalHttpPolicyDecisionPoint:
    spec = _mapping(profile.get("spec"), "customer-policy-qualification.profile.invalid")
    objective = _mapping(
        spec.get("objective"), "customer-policy-qualification.profile.invalid"
    )
    configuration = ExternalPolicyConfiguration(
        endpoint=str(spec["endpoint"]),
        ca_bundle_path=str(
            _regular_file(
                ca_path,
                maximum_bytes=2 * 1024 * 1024,
                code="customer-policy-qualification.ca.invalid",
            )
        ),
        bearer_token_path=str(bearer_path),
        timeout_seconds=int(objective["requestTimeoutSeconds"]),
        max_response_bytes=int(objective["maximumResponseBytes"]),
    )
    try:
        return ExternalHttpPolicyDecisionPoint(configuration)
    except Exception:
        _fail("customer-policy-qualification.configuration.invalid")


def qualify(
    *,
    profile: Mapping[str, Any],
    endpoint: str,
    bearer_token_path: Path,
    ca_bundle_path: Path,
    image_digest: str,
    allow_policy_observation: bool,
    decision_point: DecisionPoint | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not allow_policy_observation:
        _fail("customer-policy-qualification.enable.required")
    if DIGEST.fullmatch(image_digest) is None:
        _fail("customer-policy-qualification.image.invalid")
    validate_profile(profile)
    canonical_endpoint = _strict_https_url(
        endpoint, "customer-policy-qualification.endpoint.invalid"
    )
    profile_spec = _mapping(
        profile.get("spec"), "customer-policy-qualification.profile.invalid"
    )
    if profile_spec.get("endpoint") != canonical_endpoint:
        _fail("customer-policy-qualification.endpoint.crossed")
    bearer_path = validate_bearer_file(bearer_token_path)
    ca_path = _regular_file(
        ca_bundle_path,
        maximum_bytes=2 * 1024 * 1024,
        code="customer-policy-qualification.ca.invalid",
    )
    revision, dirty = _source_identity()
    if dirty:
        _fail("customer-policy-qualification.source.dirty")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        _fail("customer-policy-qualification.time.invalid")
    started = current.astimezone(timezone.utc)
    spec = _mapping(profile.get("spec"), "customer-policy-qualification.profile.invalid")
    metadata = _mapping(
        profile.get("metadata"), "customer-policy-qualification.profile.invalid"
    )
    identity = _mapping(spec.get("identity"), "customer-policy-qualification.profile.invalid")
    objective = _mapping(spec.get("objective"), "customer-policy-qualification.profile.invalid")
    cases = spec.get("cases")
    if not isinstance(cases, list):
        _fail("customer-policy-qualification.profile.invalid")
    reviewed = _parse_timestamp(
        metadata["reviewedAt"], "customer-policy-qualification.profile.invalid"
    )
    if reviewed > started:
        _fail("customer-policy-qualification.profile.invalid")
    point = decision_point or _decision_point(profile, bearer_path, ca_path)
    actor = ActorContext(
        str(identity["actorId"]),
        str(identity["tenantId"]),
        tuple(str(role) for role in identity["roles"]),
    )
    decisions: list[tuple[Mapping[str, Any], PolicyDecision, int]] = []
    for raw_case in cases:
        case = _mapping(raw_case, "customer-policy-qualification.profile.invalid")
        began = time.monotonic_ns()
        decision = point.decide(actor, str(case["action"]), _mapping(case["resource"], "customer-policy-qualification.profile.invalid"))
        elapsed = max(0, (time.monotonic_ns() - began + 999_999) // 1_000_000)
        decisions.append((case, decision, elapsed))
    first_case = _mapping(cases[0], "customer-policy-qualification.profile.invalid")
    began = time.monotonic_ns()
    repeated = point.decide(
        actor,
        str(first_case["action"]),
        _mapping(first_case["resource"], "customer-policy-qualification.profile.invalid"),
    )
    repeat_latency = max(0, (time.monotonic_ns() - began + 999_999) // 1_000_000)
    maximum_latency = max([item[2] for item in decisions] + [repeat_latency])
    returned = [item[1].reason_code != "policy.unavailable" for item in decisions]
    returned.append(repeated.reason_code != "policy.unavailable")
    transport_observed = any(returned)
    complete_transport = all(returned)
    exact = [
        _matches(decision, _mapping(case["expected"], "customer-policy-qualification.profile.invalid"))
        for case, decision, _ in decisions
    ]
    allow_indexes = [
        index
        for index, (case, _, _) in enumerate(decisions)
        if _mapping(case["expected"], "customer-policy-qualification.profile.invalid").get("allowed") is True
    ]
    deny_indexes = [index for index in range(len(decisions)) if index not in allow_indexes]
    expected_first = _mapping(first_case["expected"], "customer-policy-qualification.profile.invalid")
    profile_age = (started - reviewed).total_seconds()
    observations = {
        "profile-binding": 0 <= profile_age <= int(objective["maximumProfileAgeSeconds"]),
        "source-binding": True,
        "protected-input-files": True,
        "endpoint-binding": True,
        "ca-verified-tls": transport_observed,
        "credential-accepted": transport_observed,
        "exact-input-digest-binding": complete_transport,
        "tenant-binding": complete_transport,
        "snapshot-binding": all(
            decision.policy_snapshot_ref
            == _mapping(case["expected"], "customer-policy-qualification.profile.invalid").get("policySnapshotRef")
            for case, decision, _ in decisions
        ),
        "allow-cases": all(exact[index] for index in allow_indexes),
        "deny-cases": all(exact[index] for index in deny_indexes),
        "repeat-consistency": _matches(repeated, expected_first) and repeated == decisions[0][1],
        "latency-objective": maximum_latency <= int(objective["maximumDecisionLatencyMilliseconds"]),
        "minimized-output": True,
    }
    completed = datetime.now(timezone.utc) if now is None else started
    return build_report(
        revision=revision,
        profile=profile,
        image_digest=image_digest,
        started_at=started,
        completed_at=completed,
        maximum_latency_milliseconds=maximum_latency,
        observations=observations,
    )


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    destination = path.expanduser().absolute()
    if destination.is_symlink():
        _fail("customer-policy-qualification.output.invalid")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(report, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except OSError:
        _fail("customer-policy-qualification.output.invalid")
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def verify_report(
    *,
    report_path: Path,
    profile_path: Path,
    endpoint: str,
    image_digest: str,
    require_qualified: bool = False,
) -> Mapping[str, Any]:
    candidate = report_path.expanduser()
    try:
        if (
            candidate.is_symlink()
            or not candidate.is_file()
            or candidate.stat().st_size > 1024 * 1024
        ):
            _fail("customer-policy-qualification.report.unreadable")
        report = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("customer-policy-qualification.report.unreadable")
    if not isinstance(report, Mapping):
        _fail("customer-policy-qualification.report.unreadable")
    validate_report_document(report)
    profile = load_profile(profile_path)
    revision, dirty = _source_identity()
    metadata = _mapping(report.get("metadata"), "customer-policy-qualification.report.invalid")
    spec = _mapping(report.get("spec"), "customer-policy-qualification.report.invalid")
    subject = _mapping(spec.get("subject"), "customer-policy-qualification.report.invalid")
    bindings = _mapping(spec.get("bindings"), "customer-policy-qualification.report.invalid")
    profile_spec = _mapping(profile.get("spec"), "customer-policy-qualification.profile.invalid")
    canonical_endpoint = _strict_https_url(endpoint, "customer-policy-qualification.endpoint.invalid")
    cases = profile_spec.get("cases")
    if not isinstance(cases, list):
        _fail("customer-policy-qualification.profile.invalid")
    snapshots = sorted(
        {
            _mapping(_mapping(item, "customer-policy-qualification.profile.invalid").get("expected"), "customer-policy-qualification.profile.invalid").get("policySnapshotRef")
            for item in cases
        }
    )
    if (
        dirty
        or metadata.get("sourceRevision") != revision
        or subject.get("sourceRevision") != revision
        or subject.get("applicationVersion") != APPLICATION_VERSION
        or subject.get("imageDigest") != image_digest
        or profile_spec.get("endpoint") != canonical_endpoint
        or bindings.get("endpointBindingDigest") != _digest_value(canonical_endpoint)
        or bindings.get("profileDigest") != _digest_value(profile)
        or bindings.get("caseSetDigest") != _digest_value(cases)
        or bindings.get("snapshotSetDigest") != _digest_value(snapshots)
    ):
        _fail("customer-policy-qualification.report.crossed")
    if require_qualified and spec.get("status") != "qualified":
        _fail("customer-policy-qualification.report.not-qualified")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    qualify_parser = subparsers.add_parser("qualify")
    qualify_parser.add_argument("--profile", type=Path, required=True)
    qualify_parser.add_argument("--endpoint", required=True)
    qualify_parser.add_argument("--bearer-token-file", type=Path, required=True)
    qualify_parser.add_argument("--ca-bundle-file", type=Path, required=True)
    qualify_parser.add_argument("--image-digest", required=True)
    qualify_parser.add_argument("--output", type=Path, required=True)
    qualify_parser.add_argument("--allow-policy-observation", action="store_true")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--report", type=Path, required=True)
    verify_parser.add_argument("--profile", type=Path, required=True)
    verify_parser.add_argument("--endpoint", required=True)
    verify_parser.add_argument("--image-digest", required=True)
    verify_parser.add_argument("--require-qualified", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "qualify":
            profile = load_profile(arguments.profile)
            report = qualify(
                profile=profile,
                endpoint=arguments.endpoint,
                bearer_token_path=arguments.bearer_token_file,
                ca_bundle_path=arguments.ca_bundle_file,
                image_digest=arguments.image_digest,
                allow_policy_observation=arguments.allow_policy_observation,
            )
            _write_report(arguments.output, report)
            print(
                f"customer policy qualification {report['spec']['status']}: "
                f"{arguments.output}"
            )
            return 0 if report["spec"]["status"] == "qualified" else 1
        verify_report(
            report_path=arguments.report,
            profile_path=arguments.profile,
            endpoint=arguments.endpoint,
            image_digest=arguments.image_digest,
            require_qualified=arguments.require_qualified,
        )
        print("customer policy qualification report verified")
    except CustomerPolicyQualificationError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
