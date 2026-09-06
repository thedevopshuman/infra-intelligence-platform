#!/usr/bin/env python3
"""Qualify one customer credential broker against reviewed exact authority cases."""

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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from iip import __version__ as APPLICATION_VERSION  # noqa: E402
from iip.adapters.credential_broker import (  # noqa: E402
    CredentialBrokerUnavailableError,
    ExternalCredentialBrokerConfiguration,
    ExternalHttpCredentialBroker,
)
from iip.application.ports import CredentialLease, CredentialLeaseRequest  # noqa: E402


API_VERSION = "iip.platform/v1alpha1"
PROFILE_KIND = "CustomerCredentialBrokerQualificationProfile"
REPORT_KIND = "CustomerCredentialBrokerQualificationReport"
QUALIFICATION = "customer-credential-broker-authority-prerequisites-v1"
PROFILE_SCHEMA = ROOT / "contracts/schemas/customer-credential-broker-qualification-profile.schema.json"
REPORT_SCHEMA = ROOT / "contracts/schemas/customer-credential-broker-qualification-report.schema.json"
REPORT_ID = re.compile(r"^ccbq_[a-f0-9]{32}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
REVISION = re.compile(r"^[a-f0-9]{40,64}$")
SAFE_SECRET = re.compile(r"[!-~]{16,16384}")
AUTHORITY_DENIAL_ERROR = "credential.broker.request.denied"
CASE_IDS = (
    "exact-authority",
    "cross-tenant-denial",
    "actor-binding-denial",
    "integration-binding-denial",
    "provider-binding-denial",
    "scope-escalation-denial",
    "credential-reference-denial",
)
CHECK_IDS = (
    "profile-binding",
    "source-binding",
    "protected-input-files",
    "endpoint-binding",
    "ca-verified-tls",
    "workload-identity-accepted",
    "exact-authority-issued",
    "cross-tenant-denied",
    "actor-binding-denied",
    "integration-binding-denied",
    "provider-binding-denied",
    "scope-escalation-denied",
    "credential-reference-denied",
    "response-correlation",
    "lease-bounds",
    "repeat-consistency",
    "latency-objective",
    "minimized-output",
)
LIMITATIONS = (
    "workload-identity-rotation-revocation-and-federation-not-qualified",
    "provider-credential-rotation-revocation-and-emergency-access-not-qualified",
    "broker-ha-network-certificate-rotation-and-recovery-not-qualified",
    "audit-delivery-retention-and-siem-integration-not-qualified",
    "non-bearer-provider-credentials-not-qualified",
)
FORBIDDEN_RETAINED_KEYS = frozenset(
    {
        "actorId",
        "authorization",
        "caseId",
        "credential",
        "credentialRef",
        "endpoint",
        "integrationId",
        "provider",
        "scope",
        "scopes",
        "secret",
        "tenantId",
        "token",
        "url",
        "workloadIdentity",
    }
)
FORBIDDEN_PROFILE_KEY_PARTS = (
    "authorization",
    "bearer",
    "cookie",
    "password",
    "privatekey",
    "secret",
    "token",
)


class CustomerCredentialBrokerQualificationError(RuntimeError):
    """Stable failure for unsafe or crossed customer broker evidence."""


class Broker(Protocol):
    def resolve(self, request: CredentialLeaseRequest) -> CredentialLease:
        """Resolve one exact lease or fail closed."""


class _LiveClock:
    def now(self) -> str:
        return _timestamp(datetime.now(timezone.utc))


def _fail(code: str) -> None:
    raise CustomerCredentialBrokerQualificationError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest_value(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        _fail("customer-credential-broker-qualification.time.invalid")
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
    if parsed.tzinfo is None or parsed.utcoffset() is None:
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
    except CustomerCredentialBrokerQualificationError:
        raise
    except OSError:
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _regular_bytes(path: Path, *, maximum_bytes: int, code: str) -> tuple[Path, bytes]:
    candidate = path.expanduser()
    try:
        if candidate.is_symlink() or not candidate.is_file():
            _fail(code)
        payload = candidate.read_bytes()
        if not payload or len(payload) > maximum_bytes:
            _fail(code)
        return candidate.resolve(), payload
    except CustomerCredentialBrokerQualificationError:
        raise
    except OSError:
        _fail(code)


def load_profile(path: Path) -> Mapping[str, Any]:
    payload = _protected_bytes(
        path,
        maximum_bytes=262_144,
        code="customer-credential-broker-qualification.profile.invalid",
    )
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("customer-credential-broker-qualification.profile.invalid")
    if not isinstance(document, Mapping):
        _fail("customer-credential-broker-qualification.profile.invalid")
    validate_profile(document)
    return document


def validate_workload_identity_file(path: Path) -> Path:
    payload = _protected_bytes(
        path,
        maximum_bytes=16_386,
        code="customer-credential-broker-qualification.identity.invalid",
    )
    try:
        raw = payload.decode("ascii")
    except UnicodeDecodeError:
        _fail("customer-credential-broker-qualification.identity.invalid")
    value = raw.rstrip("\r\n")
    if raw not in (value, value + "\n", value + "\r\n") or SAFE_SECRET.fullmatch(value) is None:
        _fail("customer-credential-broker-qualification.identity.invalid")
    return path.expanduser().resolve()


def _strict_endpoint(value: object, code: str) -> str:
    if not isinstance(value, str):
        _fail(code)
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        _fail(code)
    if (
        not 1 <= len(value) <= 2048
        or parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65_535)
        or any(character.isspace() for character in value)
    ):
        _fail(code)
    return value.rstrip("/")


def _contains_sensitive_key(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = "".join(character for character in str(key).lower() if character.isalnum())
            if any(part in normalized for part in FORBIDDEN_PROFILE_KEY_PARTS):
                return True
            if _contains_sensitive_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _same_except(case: Mapping[str, Any], base: Mapping[str, Any], field: str) -> bool:
    keys = ("tenantId", "actorId", "integrationId", "credentialRef", "provider", "scopes")
    return all(case.get(key) == base.get(key) for key in keys if key != field) and case.get(field) != base.get(field)


def validate_profile(profile: Mapping[str, Any]) -> None:
    code = "customer-credential-broker-qualification.profile.invalid"
    _validate_schema(profile, PROFILE_SCHEMA, code)
    if profile.get("apiVersion") != API_VERSION or profile.get("kind") != PROFILE_KIND:
        _fail(code)
    spec = _mapping(profile.get("spec"), code)
    _strict_endpoint(spec.get("endpoint"), code)
    objective = _mapping(spec.get("objective"), code)
    deadline_seconds = _integer(objective.get("leaseRequestDeadlineSeconds"), 15, 900, code)
    max_lease_seconds = _integer(objective.get("maximumLeaseSeconds"), 60, 3600, code)
    if deadline_seconds > max_lease_seconds:
        _fail(code)
    cases = spec.get("cases")
    if not isinstance(cases, list) or len(cases) != len(CASE_IDS):
        _fail(code)
    mapped = [_mapping(item, code) for item in cases]
    if [item.get("id") for item in mapped] != list(CASE_IDS) or _contains_sensitive_key(profile):
        _fail(code)
    base = mapped[0]
    if base.get("expectedOutcome") != "issued":
        _fail(code)
    for case in mapped[1:]:
        if case.get("expectedOutcome") != "denied":
            _fail(code)
    relationships = (
        (mapped[1], "tenantId"),
        (mapped[2], "actorId"),
        (mapped[3], "integrationId"),
        (mapped[4], "provider"),
        (mapped[6], "credentialRef"),
    )
    if any(not _same_except(case, base, field) for case, field in relationships):
        _fail(code)
    scope_case = mapped[5]
    base_scopes = base.get("scopes")
    escalated = scope_case.get("scopes")
    if (
        not _same_except(scope_case, base, "scopes")
        or not isinstance(base_scopes, list)
        or not isinstance(escalated, list)
        or len(escalated) != len(base_scopes) + 1
        or not set(base_scopes).issubset(escalated)
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
                ["git", "status", "--porcelain", "--untracked-files=normal"],
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        _fail("customer-credential-broker-qualification.source.unavailable")
    if REVISION.fullmatch(revision) is None:
        _fail("customer-credential-broker-qualification.source.invalid")
    return revision, dirty


def _check(identifier: str, passed: bool) -> dict[str, str]:
    result = {"id": identifier, "status": "passed" if passed else "failed"}
    if not passed:
        result["errorCode"] = f"customer-credential-broker-qualification.{identifier}.failed"
    return result


def _expected_summary(checks: Sequence[Mapping[str, Any]]) -> dict[str, object]:
    passed = sum(1 for item in checks if item.get("status") == "passed")
    total = len(CHECK_IDS)
    return {
        "totalChecks": total,
        "passedChecks": passed,
        "failedChecks": total - passed,
        "overallStatus": "qualified" if passed == total else "not-qualified",
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


def _report_identifier(metadata: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    return "ccbq_" + hashlib.sha256(_canonical({"metadata": metadata, "spec": spec})).hexdigest()[:32]


def build_report(
    *,
    revision: str,
    profile: Mapping[str, Any],
    image_digest: str,
    ca_bundle_digest: str,
    started_at: datetime,
    completed_at: datetime,
    maximum_latency_milliseconds: int,
    minimum_remaining_lease_seconds: int,
    observations: Mapping[str, bool],
) -> dict[str, Any]:
    code = "customer-credential-broker-qualification.report.invalid"
    validate_profile(profile)
    if REVISION.fullmatch(revision) is None or DIGEST.fullmatch(image_digest) is None or DIGEST.fullmatch(ca_bundle_digest) is None:
        _fail(code)
    spec_profile = _mapping(profile.get("spec"), code)
    metadata_profile = _mapping(profile.get("metadata"), code)
    objective = _mapping(spec_profile.get("objective"), code)
    cases = spec_profile.get("cases")
    if not isinstance(cases, list):
        _fail(code)
    checks = [_check(identifier, observations.get(identifier, False)) for identifier in CHECK_IDS]
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
            "authoritySetDigest": _digest_value(cases),
            "caBundleDigest": ca_bundle_digest,
        },
        "profile": {
            "name": "customer-external-http-credential-broker-v1",
            "brokerProtocol": "iip.broker/v1alpha1",
            "transport": "ca-verified-https-json",
            "identityMode": "protected-token-file-read-per-request",
            "leaseScheme": "bearer",
            "redirectMode": "denied",
            "proxyMode": "disabled",
        },
        "objective": dict(objective),
        "measurements": {
            "profileReviewedAt": metadata_profile["reviewedAt"],
            "startedAt": _timestamp(started_at),
            "completedAt": _timestamp(completed_at),
            "caseCount": 7,
            "issuedCaseCount": 1,
            "deniedCaseCount": 6,
            "requestCount": 8,
            "minimumRemainingLeaseSeconds": minimum_remaining_lease_seconds,
            "maximumLeaseLatencyMilliseconds": maximum_latency_milliseconds,
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
        "metadata": {"id": _report_identifier(metadata_without_id, report_spec), **metadata_without_id},
        "spec": report_spec,
    }
    if _has_forbidden_key(report):
        _fail("customer-credential-broker-qualification.report.not-minimized")
    validate_report_document(report)
    return report


def validate_report_document(report: Mapping[str, Any]) -> None:
    _validate_schema(report, REPORT_SCHEMA, "customer-credential-broker-qualification.report.schema-invalid")
    code = "customer-credential-broker-qualification.report.invalid"
    metadata = _mapping(report.get("metadata"), code)
    spec = _mapping(report.get("spec"), code)
    subject = _mapping(spec.get("subject"), code)
    bindings = _mapping(spec.get("bindings"), code)
    objective = _mapping(spec.get("objective"), code)
    measurements = _mapping(spec.get("measurements"), code)
    checks = spec.get("checks")
    if not isinstance(checks, list) or [item.get("id") for item in checks if isinstance(item, Mapping)] != list(CHECK_IDS):
        _fail("customer-credential-broker-qualification.report.checks-invalid")
    for identifier, item in zip(CHECK_IDS, checks):
        current = _mapping(item, "customer-credential-broker-qualification.report.checks-invalid")
        if current != _check(identifier, current.get("status") == "passed"):
            _fail("customer-credential-broker-qualification.report.checks-invalid")
    reviewed = _parse_timestamp(measurements.get("profileReviewedAt"), "customer-credential-broker-qualification.report.time-invalid")
    started = _parse_timestamp(measurements.get("startedAt"), "customer-credential-broker-qualification.report.time-invalid")
    completed = _parse_timestamp(measurements.get("completedAt"), "customer-credential-broker-qualification.report.time-invalid")
    maximum_latency = _integer(measurements.get("maximumLeaseLatencyMilliseconds"), 0, 30_000, code)
    minimum_remaining = _integer(measurements.get("minimumRemainingLeaseSeconds"), 0, 3_900, code)
    check_by_id = {item.get("id"): item.get("status") == "passed" for item in checks if isinstance(item, Mapping)}
    if (
        not reviewed <= started <= completed
        or completed != _parse_timestamp(metadata.get("generatedAt"), "customer-credential-broker-qualification.report.time-invalid")
        or metadata.get("sourceRevision") != subject.get("sourceRevision")
        or measurements.get("caseCount") != 7
        or measurements.get("issuedCaseCount") != 1
        or measurements.get("deniedCaseCount") != 6
        or measurements.get("requestCount") != 8
        or maximum_latency > objective.get("requestTimeoutSeconds") * 1000
        or minimum_remaining > objective.get("maximumLeaseSeconds") + objective.get("maximumClockSkewSeconds")
        or spec.get("limitations") != list(LIMITATIONS)
        or _has_forbidden_key(report)
        or any(DIGEST.fullmatch(str(value)) is None for value in bindings.values())
    ):
        _fail(code)
    if check_by_id.get("profile-binding") != ((started - reviewed).total_seconds() <= objective.get("maximumProfileAgeSeconds")):
        _fail("customer-credential-broker-qualification.report.checks-invalid")
    if check_by_id.get("latency-objective") != (maximum_latency <= objective.get("maximumLeaseLatencyMilliseconds")):
        _fail("customer-credential-broker-qualification.report.checks-invalid")
    if check_by_id.get("lease-bounds") and minimum_remaining < objective.get("leaseRequestDeadlineSeconds"):
        _fail("customer-credential-broker-qualification.report.checks-invalid")
    for identifier in ("source-binding", "protected-input-files", "endpoint-binding", "minimized-output"):
        if check_by_id.get(identifier) is not True:
            _fail("customer-credential-broker-qualification.report.checks-invalid")
    summary = _expected_summary(checks)
    if spec.get("summary") != summary or spec.get("status") != summary["overallStatus"]:
        _fail("customer-credential-broker-qualification.report.summary-invalid")
    metadata_without_id = dict(metadata)
    identifier = metadata_without_id.pop("id", None)
    if not isinstance(identifier, str) or REPORT_ID.fullmatch(identifier) is None or identifier != _report_identifier(metadata_without_id, spec):
        _fail("customer-credential-broker-qualification.report.id-invalid")


def _broker(profile: Mapping[str, Any], identity_path: Path, ca_path: Path) -> ExternalHttpCredentialBroker:
    spec = _mapping(profile.get("spec"), "customer-credential-broker-qualification.profile.invalid")
    objective = _mapping(spec.get("objective"), "customer-credential-broker-qualification.profile.invalid")
    configuration = {
        "endpoint": spec["endpoint"],
        "caBundlePath": str(ca_path),
        "workloadIdentityTokenPath": str(identity_path),
        "requestTimeoutSeconds": objective["requestTimeoutSeconds"],
        "maxResponseBytes": objective["maximumResponseBytes"],
        "maxLeaseSeconds": objective["maximumLeaseSeconds"],
        "maxClockSkewSeconds": objective["maximumClockSkewSeconds"],
    }
    try:
        parsed = ExternalCredentialBrokerConfiguration.from_json(json.dumps(configuration))
        return ExternalHttpCredentialBroker(parsed, _LiveClock())
    except Exception:
        _fail("customer-credential-broker-qualification.configuration.invalid")


def _request(case: Mapping[str, Any], requested_at: datetime, deadline_seconds: int) -> CredentialLeaseRequest:
    return CredentialLeaseRequest(
        tenant_id=str(case["tenantId"]),
        actor_id=str(case["actorId"]),
        integration_id=str(case["integrationId"]),
        credential_ref=str(case["credentialRef"]),
        provider=str(case["provider"]),
        scopes=tuple(str(scope) for scope in case["scopes"]),
        deadline=_timestamp(requested_at + timedelta(seconds=deadline_seconds)),
    )


def _lease_valid(lease: object, request: CredentialLeaseRequest, requested_at: datetime, objective: Mapping[str, Any]) -> tuple[bool, int]:
    if not isinstance(lease, CredentialLease) or lease.scheme != "bearer" or SAFE_SECRET.fullmatch(lease.secret) is None:
        return False, 0
    try:
        expiry = _parse_timestamp(lease.expires_at, "customer-credential-broker-qualification.lease.invalid")
        deadline = _parse_timestamp(request.deadline, "customer-credential-broker-qualification.lease.invalid")
    except CustomerCredentialBrokerQualificationError:
        return False, 0
    remaining = max(0, int((expiry - requested_at).total_seconds()))
    maximum = int(objective["maximumLeaseSeconds"]) + int(objective["maximumClockSkewSeconds"])
    return deadline <= expiry and requested_at < expiry and remaining <= maximum, remaining


def qualify(
    *,
    profile: Mapping[str, Any],
    endpoint: str,
    workload_identity_token_path: Path,
    ca_bundle_path: Path,
    image_digest: str,
    allow_credential_observation: bool,
    broker: Broker | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not allow_credential_observation:
        _fail("customer-credential-broker-qualification.enable.required")
    if DIGEST.fullmatch(image_digest) is None:
        _fail("customer-credential-broker-qualification.image.invalid")
    validate_profile(profile)
    canonical_endpoint = _strict_endpoint(endpoint, "customer-credential-broker-qualification.endpoint.invalid")
    spec = _mapping(profile.get("spec"), "customer-credential-broker-qualification.profile.invalid")
    if spec.get("endpoint") != canonical_endpoint:
        _fail("customer-credential-broker-qualification.endpoint.crossed")
    identity_path = validate_workload_identity_file(workload_identity_token_path)
    ca_path, ca_payload = _regular_bytes(ca_bundle_path, maximum_bytes=2 * 1024 * 1024, code="customer-credential-broker-qualification.ca.invalid")
    revision, dirty = _source_identity()
    if dirty:
        _fail("customer-credential-broker-qualification.source.dirty")
    current: Callable[[], datetime] = (lambda: now) if now is not None else (lambda: datetime.now(timezone.utc))
    started = current()
    if started.tzinfo is None or started.utcoffset() is None:
        _fail("customer-credential-broker-qualification.time.invalid")
    started = started.astimezone(timezone.utc)
    metadata = _mapping(profile.get("metadata"), "customer-credential-broker-qualification.profile.invalid")
    reviewed = _parse_timestamp(metadata.get("reviewedAt"), "customer-credential-broker-qualification.profile.invalid")
    if reviewed > started:
        _fail("customer-credential-broker-qualification.profile.invalid")
    objective = _mapping(spec.get("objective"), "customer-credential-broker-qualification.profile.invalid")
    cases = spec.get("cases")
    if not isinstance(cases, list):
        _fail("customer-credential-broker-qualification.profile.invalid")
    point = broker or _broker(profile, identity_path, ca_path)
    outcomes: list[dict[str, Any]] = []
    for raw_case in cases:
        case = _mapping(raw_case, "customer-credential-broker-qualification.profile.invalid")
        requested_at = current().astimezone(timezone.utc)
        request = _request(case, requested_at, int(objective["leaseRequestDeadlineSeconds"]))
        began = time.monotonic_ns()
        lease: CredentialLease | None = None
        denied = False
        try:
            lease = point.resolve(request)
        except CredentialBrokerUnavailableError as error:
            denied = str(error) == AUTHORITY_DENIAL_ERROR
        elapsed = max(0, (time.monotonic_ns() - began + 999_999) // 1_000_000)
        valid, remaining = _lease_valid(lease, request, requested_at, objective) if lease is not None else (False, 0)
        outcomes.append({"case": case, "issued": lease is not None, "denied": denied, "valid": valid, "remaining": remaining, "latency": elapsed})
    base_case = _mapping(cases[0], "customer-credential-broker-qualification.profile.invalid")
    repeat_at = current().astimezone(timezone.utc)
    repeat_request = _request(base_case, repeat_at, int(objective["leaseRequestDeadlineSeconds"]))
    began = time.monotonic_ns()
    repeat_lease: CredentialLease | None = None
    try:
        repeat_lease = point.resolve(repeat_request)
    except CredentialBrokerUnavailableError:
        pass
    repeat_latency = max(0, (time.monotonic_ns() - began + 999_999) // 1_000_000)
    repeat_valid, repeat_remaining = _lease_valid(repeat_lease, repeat_request, repeat_at, objective) if repeat_lease is not None else (False, 0)
    maximum_latency = max([int(item["latency"]) for item in outcomes] + [repeat_latency])
    issued_remaining = [int(item["remaining"]) for item in outcomes if item["valid"]]
    if repeat_valid:
        issued_remaining.append(repeat_remaining)
    minimum_remaining = min(issued_remaining) if issued_remaining else 0
    base_ok = bool(outcomes[0]["issued"] and outcomes[0]["valid"])
    denial_ids = CHECK_IDS[7:13]
    observations: dict[str, bool] = {
        "profile-binding": 0 <= (started - reviewed).total_seconds() <= int(objective["maximumProfileAgeSeconds"]),
        "source-binding": True,
        "protected-input-files": True,
        "endpoint-binding": True,
        "ca-verified-tls": base_ok,
        "workload-identity-accepted": base_ok,
        "exact-authority-issued": base_ok,
        "response-correlation": base_ok,
        "lease-bounds": base_ok and repeat_valid,
        "repeat-consistency": base_ok and repeat_valid,
        "latency-objective": maximum_latency <= int(objective["maximumLeaseLatencyMilliseconds"]),
        "minimized-output": True,
    }
    for identifier, outcome in zip(denial_ids, outcomes[1:]):
        observations[identifier] = bool(outcome["denied"] and not outcome["issued"])
    completed = current().astimezone(timezone.utc)
    return build_report(
        revision=revision,
        profile=profile,
        image_digest=image_digest,
        ca_bundle_digest=_digest_bytes(ca_payload),
        started_at=started,
        completed_at=completed,
        maximum_latency_milliseconds=maximum_latency,
        minimum_remaining_lease_seconds=minimum_remaining,
        observations=observations,
    )


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    destination = path.expanduser().absolute()
    if destination.is_symlink():
        _fail("customer-credential-broker-qualification.output.invalid")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, prefix=f".{destination.name}.", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(report, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    except OSError:
        _fail("customer-credential-broker-qualification.output.invalid")
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def verify_report(
    *,
    report_path: Path,
    profile_path: Path,
    endpoint: str,
    ca_bundle_path: Path,
    image_digest: str,
    require_qualified: bool = False,
) -> Mapping[str, Any]:
    candidate = report_path.expanduser()
    try:
        if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > 1024 * 1024:
            _fail("customer-credential-broker-qualification.report.unreadable")
        report = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("customer-credential-broker-qualification.report.unreadable")
    if not isinstance(report, Mapping):
        _fail("customer-credential-broker-qualification.report.unreadable")
    validate_report_document(report)
    profile = load_profile(profile_path)
    _, ca_payload = _regular_bytes(ca_bundle_path, maximum_bytes=2 * 1024 * 1024, code="customer-credential-broker-qualification.ca.invalid")
    revision, dirty = _source_identity()
    metadata = _mapping(report.get("metadata"), "customer-credential-broker-qualification.report.invalid")
    spec = _mapping(report.get("spec"), "customer-credential-broker-qualification.report.invalid")
    subject = _mapping(spec.get("subject"), "customer-credential-broker-qualification.report.invalid")
    bindings = _mapping(spec.get("bindings"), "customer-credential-broker-qualification.report.invalid")
    profile_spec = _mapping(profile.get("spec"), "customer-credential-broker-qualification.profile.invalid")
    canonical_endpoint = _strict_endpoint(endpoint, "customer-credential-broker-qualification.endpoint.invalid")
    cases = profile_spec.get("cases")
    if (
        dirty
        or metadata.get("sourceRevision") != revision
        or subject.get("sourceRevision") != revision
        or subject.get("applicationVersion") != APPLICATION_VERSION
        or subject.get("imageDigest") != image_digest
        or profile_spec.get("endpoint") != canonical_endpoint
        or bindings.get("endpointBindingDigest") != _digest_value(canonical_endpoint)
        or bindings.get("profileDigest") != _digest_value(profile)
        or bindings.get("authoritySetDigest") != _digest_value(cases)
        or bindings.get("caBundleDigest") != _digest_bytes(ca_payload)
    ):
        _fail("customer-credential-broker-qualification.report.crossed")
    if require_qualified and spec.get("status") != "qualified":
        _fail("customer-credential-broker-qualification.report.not-qualified")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    qualify_parser = subparsers.add_parser("qualify")
    qualify_parser.add_argument("--profile", type=Path, required=True)
    qualify_parser.add_argument("--endpoint", required=True)
    qualify_parser.add_argument("--workload-identity-token-file", type=Path, required=True)
    qualify_parser.add_argument("--ca-bundle-file", type=Path, required=True)
    qualify_parser.add_argument("--image-digest", required=True)
    qualify_parser.add_argument("--output", type=Path, required=True)
    qualify_parser.add_argument("--allow-credential-observation", action="store_true")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--report", type=Path, required=True)
    verify_parser.add_argument("--profile", type=Path, required=True)
    verify_parser.add_argument("--endpoint", required=True)
    verify_parser.add_argument("--ca-bundle-file", type=Path, required=True)
    verify_parser.add_argument("--image-digest", required=True)
    verify_parser.add_argument("--require-qualified", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "qualify":
            report = qualify(
                profile=load_profile(arguments.profile),
                endpoint=arguments.endpoint,
                workload_identity_token_path=arguments.workload_identity_token_file,
                ca_bundle_path=arguments.ca_bundle_file,
                image_digest=arguments.image_digest,
                allow_credential_observation=arguments.allow_credential_observation,
            )
            _write_report(arguments.output, report)
            print(f"customer credential broker qualification {report['spec']['status']}: {arguments.output}")
            return 0 if report["spec"]["status"] == "qualified" else 1
        verify_report(
            report_path=arguments.report,
            profile_path=arguments.profile,
            endpoint=arguments.endpoint,
            ca_bundle_path=arguments.ca_bundle_file,
            image_digest=arguments.image_digest,
            require_qualified=arguments.require_qualified,
        )
        print("customer credential broker qualification report verified")
    except CustomerCredentialBrokerQualificationError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
