#!/usr/bin/env python3
"""Qualify one exact customer Amazon Bedrock live-provider profile."""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iip import __version__ as APPLICATION_VERSION  # noqa: E402


API_VERSION = "iip.platform/v1alpha1"
PROFILE_KIND = "CustomerBedrockQualificationProfile"
REPORT_KIND = "CustomerBedrockQualificationReport"
QUALIFICATION = "customer-bedrock-live-interoperability-v1"
PROFILE_SCHEMA = (
    ROOT / "contracts/schemas/customer-bedrock-qualification-profile.schema.json"
)
REPORT_SCHEMA = (
    ROOT / "contracts/schemas/customer-bedrock-qualification-report.schema.json"
)
COMPATIBILITY_SCHEMA = (
    ROOT / "contracts/schemas/bedrock-instrumentation-compatibility-report.schema.json"
)
REPORT_ID = re.compile(r"^cbq_[a-f0-9]{32}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
REVISION = re.compile(r"^[a-f0-9]{40,64}$")
CHECK_IDS = (
    "profile-binding",
    "source-binding",
    "immutable-release",
    "protected-profile",
    "protected-session-credentials",
    "exact-provider",
    "exact-model",
    "exact-region",
    "exact-operation",
    "live-provider-call",
    "official-instrumentation",
    "supported-instrumentation-scope",
    "metadata-only-span",
    "provider-token-totals",
    "receiver-normalization",
    "async-export-failure-isolated",
    "cost-eligibility-honest",
    "minimized-output",
)
COMPATIBILITY_COMMON_CHECKS = (
    "provider-call-completed",
    "official-instrumentation-span",
    "supported-instrumentation-scope",
    "provider-alias-normalized",
    "metadata-only-span",
    "provider-token-totals",
    "receiver-normalization",
    "async-export-failure-isolated",
)
LIMITATIONS = (
    "credential-expiration-iam-least-authority-and-revocation-not-qualified",
    "model-quality-safety-and-output-correctness-not-qualified",
    "customer-collector-pki-and-network-path-not-qualified",
    "authoritative-pricing-private-rates-and-invoice-agreement-not-qualified",
    "sustained-load-quota-throttling-and-regional-ha-not-qualified",
    "additional-models-regions-operations-and-provider-apis-not-qualified",
)
FORBIDDEN_RETAINED_KEYS = frozenset(
    {
        "accessKeyId",
        "awsAccessKeyId",
        "awsSecretAccessKey",
        "awsSessionToken",
        "credential",
        "credentialsProfile",
        "environmentId",
        "modelId",
        "prompt",
        "region",
        "response",
        "secret",
        "sessionToken",
        "token",
    }
)
PINNED_VERSIONS = {
    "boto3Version": "1.43.73",
    "botocoreVersion": "1.43.73",
    "instrumentationVersion": "0.65b0",
    "otelSdkVersion": "1.44.0",
}
CREDENTIAL_KEYS = frozenset(
    {"aws_access_key_id", "aws_secret_access_key", "aws_session_token"}
)


class CustomerBedrockQualificationError(RuntimeError):
    """Stable failure for unsafe, stale, or crossed customer Bedrock evidence."""


Runner = Callable[[Mapping[str, Any], Path, str], Mapping[str, Any]]
Clock = Callable[[], datetime]


def _fail(code: str) -> None:
    raise CustomerBedrockQualificationError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest_value(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _timestamp(value: datetime) -> str:
    return _utc_datetime(
        value, "customer-bedrock-qualification.time.invalid"
    ).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _utc_datetime(value: datetime, code: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        _fail(code)
    return value.astimezone(timezone.utc)


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


def _schema(path: Path, code: str) -> Mapping[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail(code)
    if not isinstance(document, Mapping):
        _fail(code)
    return document


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
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or stat.S_IMODE(information.st_mode) != 0o600
            or information.st_uid != os.getuid()
            or not 1 <= information.st_size <= maximum_bytes
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


def _regular_bytes(path: Path, *, maximum_bytes: int, code: str) -> bytes:
    descriptor = -1
    candidate = path.expanduser()
    try:
        if candidate.is_symlink():
            _fail(code)
        descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or not 1 <= information.st_size <= maximum_bytes
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


def _json(payload: bytes, code: str) -> Mapping[str, Any]:
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail(code)
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def load_profile(path: Path) -> Mapping[str, Any]:
    profile = _json(
        _protected_bytes(
            path,
            maximum_bytes=65_536,
            code="customer-bedrock-qualification.profile.unreadable",
        ),
        "customer-bedrock-qualification.profile.unreadable",
    )
    validate_profile(profile)
    return profile


def validate_profile(profile: Mapping[str, Any]) -> None:
    code = "customer-bedrock-qualification.profile.invalid"
    _validate_schema(profile, PROFILE_SCHEMA, code)
    if profile.get("apiVersion") != API_VERSION or profile.get("kind") != PROFILE_KIND:
        _fail(code)
    spec = _mapping(profile.get("spec"), code)
    release = _mapping(spec.get("release"), code)
    if release.get("applicationVersion") != APPLICATION_VERSION:
        _fail("customer-bedrock-qualification.profile.version-mismatch")


def validate_credentials_file(path: Path, profile_name: str) -> Path:
    code = "customer-bedrock-qualification.credentials.invalid"
    payload = _protected_bytes(path, maximum_bytes=65_536, code=code)
    try:
        text = payload.decode("ascii")
        parser = configparser.RawConfigParser(interpolation=None, strict=True)
        parser.read_string(text)
    except (UnicodeDecodeError, configparser.Error):
        _fail(code)
    if parser.defaults() or parser.sections() != [profile_name]:
        _fail(code)
    values = {key: value for key, value in parser.items(profile_name, raw=True)}
    if set(values) != CREDENTIAL_KEYS:
        _fail(code)
    bounds = {
        "aws_access_key_id": (16, 128),
        "aws_secret_access_key": (32, 256),
        "aws_session_token": (32, 8192),
    }
    for key, value in values.items():
        minimum, maximum = bounds[key]
        if (
            not minimum <= len(value) <= maximum
            or value != value.strip()
            or any(character.isspace() for character in value)
        ):
            _fail(code)
    return path.expanduser().absolute()


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
        _fail("customer-bedrock-qualification.source.unavailable")
    if REVISION.fullmatch(revision) is None:
        _fail("customer-bedrock-qualification.source.invalid")
    return revision, dirty


def _operation_argument(operation: str) -> str:
    return "converse-stream" if operation == "ConverseStream" else "converse"


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


def _run_live_gate(
    profile: Mapping[str, Any], credentials_path: Path, docker_bin: str
) -> Mapping[str, Any]:
    code = "customer-bedrock-qualification.live-gate.failed"
    spec = _mapping(profile.get("spec"), code)
    target = _mapping(spec.get("target"), code)
    objective = _mapping(spec.get("objective"), code)
    environment = dict(os.environ)
    for key in (
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "AWS_SECURITY_TOKEN",
        "AWS_WEB_IDENTITY_TOKEN_FILE",
        "AWS_ROLE_ARN",
        "AWS_ROLE_SESSION_NAME",
        "AWS_CONTAINER_CREDENTIALS_FULL_URI",
        "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN",
        "AWS_CONTAINER_AUTHORIZATION_TOKEN_FILE",
        "AWS_ENDPOINT_URL",
        "AWS_ENDPOINT_URL_BEDROCK_RUNTIME",
    ):
        environment.pop(key, None)
    with tempfile.TemporaryDirectory(prefix="iip-customer-bedrock-") as temporary:
        output_dir = Path(temporary)
        report_name = "customer-bedrock-live-compatibility.json"
        environment.update(
            {
                "IIP_DOCKER_BIN": docker_bin,
                "IIP_BEDROCK_COMPATIBILITY_MODE": "live",
                "IIP_BEDROCK_LIVE_TEST_ENABLED": "true",
                "IIP_BEDROCK_MODEL_ID": str(target["modelId"]),
                "IIP_BEDROCK_OPERATION": _operation_argument(str(target["operation"])),
                "IIP_BEDROCK_CREDENTIALS_PROFILE": str(
                    target["credentialsProfile"]
                ),
                "IIP_BEDROCK_AWS_CREDENTIALS_FILE": str(credentials_path),
                "IIP_BEDROCK_MAXIMUM_PROVIDER_CALL_MILLISECONDS": str(
                    objective["maximumProviderCallLatencyMilliseconds"]
                ),
                "IIP_BEDROCK_OUTPUT_DIR": str(output_dir),
                "IIP_BEDROCK_REPORT_BASENAME": report_name,
                "AWS_REGION": str(target["region"]),
            }
        )
        try:
            process = subprocess.Popen(
                [str(ROOT / "scripts/test_bedrock_instrumentation.sh")],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            try:
                return_code = process.wait(timeout=900)
            except subprocess.TimeoutExpired:
                _terminate(process)
                _fail("customer-bedrock-qualification.live-gate.timeout")
        except OSError:
            _fail(code)
        if return_code != 0:
            _fail(code)
        return _json(
            _regular_bytes(
                output_dir / report_name,
                maximum_bytes=1_048_576,
                code="customer-bedrock-qualification.live-report.invalid",
            ),
            "customer-bedrock-qualification.live-report.invalid",
        )


def _expected_compatibility_id(report: Mapping[str, Any]) -> str:
    code = "customer-bedrock-qualification.live-report.invalid"
    metadata = _mapping(report.get("metadata"), code)
    spec = _mapping(report.get("spec"), code)
    profile = _mapping(spec.get("profile"), code)
    environment = _mapping(spec.get("environment"), code)
    operation = _operation_argument(str(profile.get("operation")))
    identity = {
        "sourceRevision": metadata.get("sourceRevision"),
        "qualificationLevel": spec.get("qualificationLevel"),
        "operation": operation,
        "modelId": profile.get("modelId"),
        "region": profile.get("region"),
        "versions": {
            "boto3": environment.get("boto3Version"),
            "botocore": environment.get("botocoreVersion"),
            "instrumentation": environment.get("instrumentationVersion"),
            "otelSdk": environment.get("otelSdkVersion"),
        },
    }
    return "bic_" + hashlib.sha256(_canonical(identity)).hexdigest()[:32]


def validate_live_report(
    profile_document: Mapping[str, Any],
    live_report: Mapping[str, Any],
    revision: str,
) -> None:
    code = "customer-bedrock-qualification.live-report.invalid"
    _validate_schema(live_report, COMPATIBILITY_SCHEMA, code)
    metadata = _mapping(live_report.get("metadata"), code)
    spec = _mapping(live_report.get("spec"), code)
    environment = _mapping(spec.get("environment"), code)
    observed_profile = _mapping(spec.get("profile"), code)
    result = _mapping(spec.get("result"), code)
    requested_spec = _mapping(profile_document.get("spec"), code)
    target = _mapping(requested_spec.get("target"), code)
    expected_checks = COMPATIBILITY_COMMON_CHECKS + (
        ("stream-consumption-completed",)
        if target.get("operation") == "ConverseStream"
        else ()
    )
    checks = spec.get("checks")
    if (
        metadata.get("sourceRevision") != revision
        or metadata.get("sourceDirty") is not False
        or metadata.get("id") != _expected_compatibility_id(live_report)
        or spec.get("status") != "compatible"
        or spec.get("qualificationLevel") != "live-provider-interoperability"
        or environment.get("applicationVersion") != APPLICATION_VERSION
        or any(environment.get(key) != value for key, value in PINNED_VERSIONS.items())
        or observed_profile.get("provider") != "aws.bedrock"
        or observed_profile.get("modelId") != target.get("modelId")
        or observed_profile.get("region") != target.get("region")
        or observed_profile.get("operation") != target.get("operation")
        or observed_profile.get("invocationTarget") != "aws-bedrock"
        or observed_profile.get("instrumentationScope")
        != "opentelemetry.instrumentation.botocore.bedrock-runtime"
        or observed_profile.get("contentCapture") is not False
        or observed_profile.get("requestPath") != "direct-to-provider"
        or observed_profile.get("telemetryPath") != "asynchronous-otel"
        or result.get("normalizedProvider") != "aws.bedrock"
        or result.get("usageCompleteness") != "partial"
        or result.get("missingUsageFields")
        != [
            "cacheReadInputTokens",
            "cacheWriteInputTokens",
            "reasoningOutputTokens",
        ]
        or result.get("contentCaptured") is not False
        or result.get("rawPayloadPersisted") is not False
        or result.get("exactCostEligible") is not False
        or result.get("liveProviderVerified") is not True
        or (result.get("streamingVerified") is True)
        != (target.get("operation") == "ConverseStream")
        or isinstance(result.get("providerCallLatencyMilliseconds"), bool)
        or not isinstance(result.get("providerCallLatencyMilliseconds"), int)
        or result["providerCallLatencyMilliseconds"]
        > _mapping(requested_spec.get("objective"), code).get(
            "maximumProviderCallLatencyMilliseconds"
        )
        or not isinstance(checks, list)
        or tuple(
            item.get("id") if isinstance(item, Mapping) else None for item in checks
        )
        != expected_checks
        or any(
            not isinstance(item, Mapping)
            or item.get("status") != "passed"
            or "errorCode" in item
            for item in checks
        )
    ):
        _fail(code)


def _has_forbidden_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in FORBIDDEN_RETAINED_KEYS or _has_forbidden_key(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_has_forbidden_key(child) for child in value)
    return False


def _check(identifier: str, passed: bool) -> dict[str, str]:
    item = {"id": identifier, "status": "passed" if passed else "failed"}
    if not passed:
        item["errorCode"] = f"customer-bedrock-qualification.{identifier}.failed"
    return item


def _summary(checks: Sequence[Mapping[str, Any]]) -> dict[str, object]:
    passed = sum(item.get("status") == "passed" for item in checks)
    return {
        "totalChecks": len(CHECK_IDS),
        "passedChecks": passed,
        "failedChecks": len(CHECK_IDS) - passed,
        "overallStatus": "qualified" if passed == len(CHECK_IDS) else "not-qualified",
    }


def _report_id(metadata: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    return "cbq_" + hashlib.sha256(
        _canonical({"metadata": metadata, "spec": spec})
    ).hexdigest()[:32]


def build_report(
    *,
    profile_document: Mapping[str, Any],
    live_report: Mapping[str, Any],
    revision: str,
    image_digest: str,
    started_at: datetime,
    completed_at: datetime,
    protected_credentials: bool,
) -> dict[str, Any]:
    code = "customer-bedrock-qualification.report.invalid"
    validate_profile(profile_document)
    validate_live_report(profile_document, live_report, revision)
    if DIGEST.fullmatch(image_digest) is None or REVISION.fullmatch(revision) is None:
        _fail(code)
    started_at = _utc_datetime(started_at, code)
    completed_at = _utc_datetime(completed_at, code)
    spec = _mapping(profile_document.get("spec"), code)
    metadata = _mapping(profile_document.get("metadata"), code)
    target = _mapping(spec.get("target"), code)
    release = _mapping(spec.get("release"), code)
    objective = _mapping(spec.get("objective"), code)
    result = _mapping(_mapping(live_report.get("spec"), code).get("result"), code)
    reviewed_at = _parse_timestamp(metadata.get("reviewedAt"), code)
    if (
        release.get("imageDigest") != image_digest
        or release.get("applicationVersion") != APPLICATION_VERSION
        or completed_at < started_at
        or reviewed_at > started_at
    ):
        _fail(code)
    latency = result.get("providerCallLatencyMilliseconds")
    observations = {
        "profile-binding": (started_at - reviewed_at).total_seconds()
        <= int(objective["maximumProfileAgeSeconds"]),
        "source-binding": True,
        "immutable-release": True,
        "protected-profile": True,
        "protected-session-credentials": protected_credentials,
        "exact-provider": True,
        "exact-model": True,
        "exact-region": True,
        "exact-operation": True,
        "live-provider-call": True,
        "official-instrumentation": True,
        "supported-instrumentation-scope": True,
        "metadata-only-span": True,
        "provider-token-totals": True,
        "receiver-normalization": True,
        "async-export-failure-isolated": True,
        "cost-eligibility-honest": True,
        "minimized-output": True,
    }
    checks = [_check(identifier, observations[identifier]) for identifier in CHECK_IDS]
    summary = _summary(checks)
    generated_at = completed_at
    report_spec = {
        "status": summary["overallStatus"],
        "qualification": QUALIFICATION,
        "subject": {
            "applicationVersion": APPLICATION_VERSION,
            "contractsApiVersion": API_VERSION,
            "sourceRevision": revision,
            "imageDigest": image_digest,
        },
        "bindings": {
            "profileDigest": _digest_value(profile_document),
            "environmentBindingDigest": _digest_value(metadata["environmentId"]),
            "targetBindingDigest": _digest_value(target),
            "liveCompatibilityReportDigest": _digest_value(live_report),
        },
        "profile": {
            "name": QUALIFICATION,
            "provider": "aws.bedrock",
            "operation": target["operation"],
            "invocationTarget": "aws-bedrock",
            "instrumentation": "official-pinned-otel-python-botocore",
            "credentialMode": "protected-dedicated-session-credentials-file",
            "requestPath": "direct-to-provider",
            "telemetryPath": "asynchronous-otel",
            "contentPolicy": "fixed-synthetic-request-not-retained",
            "costEligibility": "partial-usage-not-exact-cost-eligible",
        },
        "objective": dict(objective),
        "measurements": {
            "profileReviewedAt": _timestamp(reviewed_at),
            "startedAt": _timestamp(started_at),
            "completedAt": _timestamp(completed_at),
            "providerCallCount": 1,
            "normalizedUsageRecordCount": 1,
            "providerCallLatencyMilliseconds": latency,
        },
        "checks": checks,
        "limitations": list(LIMITATIONS),
        "summary": summary,
    }
    report_metadata = {
        "generatedAt": _timestamp(generated_at),
        "validUntil": _timestamp(
            generated_at + timedelta(seconds=int(objective["maximumReportAgeSeconds"]))
        ),
        "sourceRevision": revision,
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
    code = "customer-bedrock-qualification.report.invalid"
    _validate_schema(report, REPORT_SCHEMA, code)
    metadata = _mapping(report.get("metadata"), code)
    spec = _mapping(report.get("spec"), code)
    subject = _mapping(spec.get("subject"), code)
    profile = _mapping(spec.get("profile"), code)
    objective = _mapping(spec.get("objective"), code)
    measurements = _mapping(spec.get("measurements"), code)
    checks = spec.get("checks")
    if not isinstance(checks, list):
        _fail(code)
    generated = _parse_timestamp(metadata.get("generatedAt"), code)
    valid_until = _parse_timestamp(metadata.get("validUntil"), code)
    reviewed = _parse_timestamp(measurements.get("profileReviewedAt"), code)
    started = _parse_timestamp(measurements.get("startedAt"), code)
    completed = _parse_timestamp(measurements.get("completedAt"), code)
    check_values = {
        item.get("id"): item.get("status") == "passed"
        for item in checks
        if isinstance(item, Mapping)
    }
    expected_profile_binding = (
        reviewed <= started
        and (started - reviewed).total_seconds()
        <= objective.get("maximumProfileAgeSeconds")
    )
    latency = measurements.get("providerCallLatencyMilliseconds")
    if (
        report.get("apiVersion") != API_VERSION
        or report.get("kind") != REPORT_KIND
        or metadata.get("sourceRevision") != subject.get("sourceRevision")
        or subject.get("applicationVersion") != APPLICATION_VERSION
        or generated != completed
        or valid_until
        != generated
        + timedelta(seconds=int(objective.get("maximumReportAgeSeconds", 0)))
        or not reviewed <= started <= completed
        or tuple(
            item.get("id") if isinstance(item, Mapping) else None for item in checks
        )
        != CHECK_IDS
        or check_values.get("profile-binding") != expected_profile_binding
        or any(
            check_values.get(identifier) is not True
            for identifier in CHECK_IDS
            if identifier != "profile-binding"
        )
        or isinstance(latency, bool)
        or not isinstance(latency, int)
        or not 0 <= latency <= int(
            objective.get("maximumProviderCallLatencyMilliseconds", -1)
        )
        or spec.get("limitations") != list(LIMITATIONS)
        or _has_forbidden_key(report)
    ):
        _fail(code)
    expected_summary = _summary(checks)
    if spec.get("summary") != expected_summary or spec.get("status") != expected_summary[
        "overallStatus"
    ]:
        _fail("customer-bedrock-qualification.report.summary-invalid")
    without_id = dict(metadata)
    identifier = without_id.pop("id", None)
    if identifier != _report_id(without_id, spec) or not isinstance(
        identifier, str
    ) or REPORT_ID.fullmatch(identifier) is None:
        _fail("customer-bedrock-qualification.report.id-invalid")


def qualify(
    *,
    profile_document: Mapping[str, Any],
    credentials_path: Path,
    image_digest: str,
    allow_provider_call: bool,
    docker_bin: str = "docker",
    runner: Runner | None = None,
    clock: Clock | None = None,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if not allow_provider_call:
        _fail("customer-bedrock-qualification.enable.required")
    validate_profile(profile_document)
    spec = _mapping(
        profile_document.get("spec"),
        "customer-bedrock-qualification.profile.invalid",
    )
    release = _mapping(
        spec.get("release"), "customer-bedrock-qualification.profile.invalid"
    )
    target = _mapping(
        spec.get("target"), "customer-bedrock-qualification.profile.invalid"
    )
    if image_digest != release.get("imageDigest") or DIGEST.fullmatch(image_digest) is None:
        _fail("customer-bedrock-qualification.image.invalid")
    protected_path = validate_credentials_file(
        credentials_path, str(target["credentialsProfile"])
    )
    revision, dirty = _source_identity()
    if dirty:
        _fail("customer-bedrock-qualification.source.dirty")
    current = clock or (lambda: datetime.now(timezone.utc))
    started = _utc_datetime(
        current(), "customer-bedrock-qualification.time.invalid"
    )
    reviewed = _parse_timestamp(
        _mapping(profile_document.get("metadata"), "customer-bedrock-qualification.profile.invalid").get(
            "reviewedAt"
        ),
        "customer-bedrock-qualification.profile.invalid",
    )
    objective = _mapping(
        spec.get("objective"), "customer-bedrock-qualification.profile.invalid"
    )
    if reviewed > started or (started - reviewed).total_seconds() > int(
        objective["maximumProfileAgeSeconds"]
    ):
        _fail("customer-bedrock-qualification.profile.stale")
    observed = (runner or _run_live_gate)(profile_document, protected_path, docker_bin)
    completed = _utc_datetime(
        current(), "customer-bedrock-qualification.time.invalid"
    )
    current_revision, current_dirty = _source_identity()
    if current_dirty or current_revision != revision:
        _fail("customer-bedrock-qualification.source.changed")
    raw_generated = _parse_timestamp(
        _mapping(observed.get("metadata"), "customer-bedrock-qualification.live-report.invalid").get(
            "generatedAt"
        ),
        "customer-bedrock-qualification.live-report.invalid",
    )
    if raw_generated < started - timedelta(seconds=5) or raw_generated > completed + timedelta(
        seconds=5
    ):
        _fail("customer-bedrock-qualification.live-report.stale")
    report = build_report(
        profile_document=profile_document,
        live_report=observed,
        revision=revision,
        image_digest=image_digest,
        started_at=started,
        completed_at=completed,
        protected_credentials=True,
    )
    return report, observed


def verify_report(
    *,
    report_path: Path,
    profile_path: Path,
    live_report_path: Path,
    image_digest: str,
    require_qualified: bool = False,
    now: datetime | None = None,
) -> Mapping[str, Any]:
    report = _json(
        _regular_bytes(
            report_path,
            maximum_bytes=1_048_576,
            code="customer-bedrock-qualification.report.unreadable",
        ),
        "customer-bedrock-qualification.report.unreadable",
    )
    validate_report_document(report)
    profile_document = load_profile(profile_path)
    live_report = _json(
        _protected_bytes(
            live_report_path,
            maximum_bytes=1_048_576,
            code="customer-bedrock-qualification.live-report.unreadable",
        ),
        "customer-bedrock-qualification.live-report.unreadable",
    )
    revision, dirty = _source_identity()
    if dirty:
        _fail("customer-bedrock-qualification.source.dirty")
    validate_live_report(profile_document, live_report, revision)
    metadata = _mapping(report.get("metadata"), "customer-bedrock-qualification.report.invalid")
    spec = _mapping(report.get("spec"), "customer-bedrock-qualification.report.invalid")
    subject = _mapping(spec.get("subject"), "customer-bedrock-qualification.report.invalid")
    bindings = _mapping(spec.get("bindings"), "customer-bedrock-qualification.report.invalid")
    profile_metadata = _mapping(
        profile_document.get("metadata"),
        "customer-bedrock-qualification.profile.invalid",
    )
    profile_spec = _mapping(
        profile_document.get("spec"),
        "customer-bedrock-qualification.profile.invalid",
    )
    release = _mapping(
        profile_spec.get("release"),
        "customer-bedrock-qualification.profile.invalid",
    )
    if (
        image_digest != release.get("imageDigest")
        or subject.get("imageDigest") != image_digest
        or subject.get("sourceRevision") != revision
        or metadata.get("sourceRevision") != revision
        or bindings.get("profileDigest") != _digest_value(profile_document)
        or bindings.get("environmentBindingDigest")
        != _digest_value(profile_metadata.get("environmentId"))
        or bindings.get("targetBindingDigest")
        != _digest_value(profile_spec.get("target"))
        or bindings.get("liveCompatibilityReportDigest")
        != _digest_value(live_report)
    ):
        _fail("customer-bedrock-qualification.report.crossed")
    current = _utc_datetime(
        now or datetime.now(timezone.utc),
        "customer-bedrock-qualification.time.invalid",
    )
    if current > _parse_timestamp(metadata.get("validUntil"), "customer-bedrock-qualification.report.invalid"):
        _fail("customer-bedrock-qualification.report.expired")
    if require_qualified and spec.get("status") != "qualified":
        _fail("customer-bedrock-qualification.report.not-qualified")
    return report


def _write_document(path: Path, document: Mapping[str, Any], *, mode: int) -> None:
    destination = path.expanduser().absolute()
    if destination.is_symlink():
        _fail("customer-bedrock-qualification.output.invalid")
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
            json.dump(document, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, destination)
    except OSError:
        _fail("customer-bedrock-qualification.output.invalid")
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    qualify_parser = subparsers.add_parser("qualify")
    qualify_parser.add_argument("--profile", type=Path, required=True)
    qualify_parser.add_argument("--aws-credentials-file", type=Path, required=True)
    qualify_parser.add_argument("--image-digest", required=True)
    qualify_parser.add_argument("--live-report-output", type=Path, required=True)
    qualify_parser.add_argument("--output", type=Path, required=True)
    qualify_parser.add_argument("--docker-bin", default="docker")
    qualify_parser.add_argument("--allow-provider-call", action="store_true")
    verify_parser = subparsers.add_parser("verify")
    verify_parser.add_argument("--report", type=Path, required=True)
    verify_parser.add_argument("--profile", type=Path, required=True)
    verify_parser.add_argument("--live-report", type=Path, required=True)
    verify_parser.add_argument("--image-digest", required=True)
    verify_parser.add_argument("--require-qualified", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "qualify":
            profile_document = load_profile(arguments.profile)
            report, live_report = qualify(
                profile_document=profile_document,
                credentials_path=arguments.aws_credentials_file,
                image_digest=arguments.image_digest,
                allow_provider_call=arguments.allow_provider_call,
                docker_bin=arguments.docker_bin,
            )
            _write_document(arguments.live_report_output, live_report, mode=0o600)
            _write_document(arguments.output, report, mode=0o644)
            print(f"customer Bedrock qualification {report['spec']['status']}: {arguments.output}")
            return 0 if report["spec"]["status"] == "qualified" else 1
        verify_report(
            report_path=arguments.report,
            profile_path=arguments.profile,
            live_report_path=arguments.live_report,
            image_digest=arguments.image_digest,
            require_qualified=arguments.require_qualified,
        )
        print("customer Bedrock qualification report verified")
    except CustomerBedrockQualificationError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
