#!/usr/bin/env python3
"""Generate and verify minimized external ingress availability evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from http.client import HTTPException
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)


ROOT = Path(__file__).resolve().parents[1]
API_VERSION = "iip.platform/v1alpha1"
KIND = "IngressAvailabilityQualificationReport"
PROFILES = ("local-loopback", "customer-ingress")
PATHS = (
    ("liveness", "/healthz", False),
    ("readiness", "/readyz", False),
    ("runtime-identity", "/v1/system/version", True),
)
CHECK_IDS = (
    "source-binding",
    "minimized-output",
    "direct-no-redirect-client",
    "transport-profile",
    "response-contracts-consistent",
    "liveness-observed",
    "readiness-observed",
    "runtime-identity-consistent",
    "availability-objective",
    "latency-objective",
)
FAILURE_CATEGORIES = ("transport", "http-status", "contract", "identity")
REPORT_ID = re.compile(r"^iaq_[a-f0-9]{32}$")
REVISION = re.compile(r"^[a-f0-9]{40,64}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
MIGRATION = re.compile(r"^[0-9]{4}_[a-z0-9_]+\.sql$")
BEARER = re.compile(r"^[a-zA-Z0-9._~+/-]{32,8192}=*$")
PLATFORM = re.compile(r"^[a-z0-9_.-]+/[a-z0-9_.-]+$")
PYTHON_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
MAX_RESPONSE_BYTES = 65_536


class IngressQualificationError(RuntimeError):
    """Stable external ingress qualification failure."""


def _fail(code: str) -> None:
    raise IngressQualificationError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _timestamp(value: datetime | None = None) -> str:
    instant = value or datetime.now(timezone.utc)
    return instant.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        _fail("ingress-qualification.report.timestamp-invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("ingress-qualification.report.timestamp-invalid")
    if parsed.tzinfo is None:
        _fail("ingress-qualification.report.timestamp-invalid")
    return parsed.astimezone(timezone.utc)


def _run(command: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            list(command),
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        _fail("ingress-qualification.source.unavailable")
    if completed.returncode != 0:
        _fail("ingress-qualification.source.unavailable")
    return completed.stdout


def _git_state() -> tuple[str, bool]:
    revision = _run(("git", "rev-parse", "HEAD")).strip()
    dirty = bool(
        _run(("git", "status", "--porcelain", "--untracked-files=normal")).strip()
    )
    if REVISION.fullmatch(revision) is None:
        _fail("ingress-qualification.source.invalid")
    return revision, dirty


def _repository_identity() -> dict[str, str]:
    try:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        application_version = project["project"]["version"]
        chart = (ROOT / "deploy/helm/infra-intelligence/Chart.yaml").read_text(
            encoding="utf-8"
        )
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        _fail("ingress-qualification.source.identity-invalid")
    chart_match = re.search(r"^version:\s*([^\s]+)\s*$", chart, re.MULTILINE)
    migrations = sorted(
        (ROOT / "src/iip/adapters/postgres/migrations").glob("*.sql")
    )
    if (
        not isinstance(application_version, str)
        or SEMVER.fullmatch(application_version) is None
        or chart_match is None
        or SEMVER.fullmatch(chart_match.group(1)) is None
        or not migrations
        or MIGRATION.fullmatch(migrations[-1].name) is None
    ):
        _fail("ingress-qualification.source.identity-invalid")
    return {
        "applicationVersion": application_version,
        "chartVersion": chart_match.group(1),
        "requiredMigration": migrations[-1].name,
    }


def _checked_url(profile: str, raw: str) -> tuple[str, str, str]:
    if (
        not isinstance(raw, str)
        or not 1 <= len(raw) <= 2048
        or re.search(r"[\x00-\x20\x7f]", raw) is not None
    ):
        _fail("ingress-qualification.target.invalid")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        _fail("ingress-qualification.target.invalid")
    if (
        parsed.hostname is None
        or not parsed.hostname.isascii()
        or len(parsed.hostname) > 253
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        _fail("ingress-qualification.target.invalid")
    scheme = parsed.scheme.lower()
    host = parsed.hostname.lower()
    if profile == "local-loopback":
        if scheme != "http" or host not in ("localhost", "127.0.0.1", "::1"):
            _fail("ingress-qualification.transport.loopback-required")
        transport = "loopback-http"
    elif profile == "customer-ingress":
        if scheme != "https":
            _fail("ingress-qualification.transport.https-required")
        transport = "verified-https"
    else:
        _fail("ingress-qualification.profile.invalid")
    default_port = 80 if scheme == "http" else 443
    host_text = f"[{host}]" if ":" in host else host
    authority = host_text if port in (None, default_port) else f"{host_text}:{port}"
    base_url = f"{scheme}://{authority}"
    return base_url, transport, _digest(base_url)


def _load_token(path: Path) -> str:
    descriptor = -1
    try:
        if path.is_symlink():
            _fail("ingress-qualification.credential.invalid")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or details.st_size > 8194:
            _fail("ingress-qualification.credential.invalid")
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            encoded = handle.read(8195)
        raw = encoded.decode("ascii")
    except (OSError, UnicodeError):
        _fail("ingress-qualification.credential.invalid")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    token = raw.rstrip("\r\n")
    if raw not in (token, token + "\n", token + "\r\n") or BEARER.fullmatch(token) is None:
        _fail("ingress-qualification.credential.invalid")
    return token


class _DenyRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, url):
        del request, file_pointer, code, message, headers, url
        return None


@dataclass(frozen=True)
class _Attempt:
    path_id: str
    success: bool
    duration_milliseconds: int
    failure_category: str | None = None
    document: Mapping[str, Any] | None = None


def _elapsed_milliseconds(started: float, completed: float) -> int:
    return max(0, math.ceil((completed - started) * 1000))


def _request_json(
    opener: Any,
    *,
    path_id: str,
    url: str,
    token: str | None,
    timeout_milliseconds: int,
    monotonic: Callable[[], float],
) -> _Attempt:
    headers = {
        "Accept": "application/json",
        "Connection": "close",
        "User-Agent": "iip-ingress-qualification/1",
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers, method="GET")
    started = monotonic()
    try:
        with opener.open(request, timeout=timeout_milliseconds / 1000) as response:
            status = getattr(response, "status", None)
            media_type = response.headers.get_content_type()
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        exc.close()
        return _Attempt(
            path_id,
            False,
            _elapsed_milliseconds(started, monotonic()),
            "http-status",
        )
    except (URLError, OSError, TimeoutError, ssl.SSLError, HTTPException, ValueError):
        return _Attempt(
            path_id,
            False,
            _elapsed_milliseconds(started, monotonic()),
            "transport",
        )
    duration = _elapsed_milliseconds(started, monotonic())
    if status != 200:
        return _Attempt(path_id, False, duration, "http-status")
    if media_type != "application/json" or len(body) > MAX_RESPONSE_BYTES:
        return _Attempt(path_id, False, duration, "contract")
    try:
        document = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _Attempt(path_id, False, duration, "contract")
    if not isinstance(document, Mapping):
        return _Attempt(path_id, False, duration, "contract")
    return _Attempt(path_id, True, duration, document=document)


def _runtime_identity(document: Mapping[str, Any]) -> dict[str, str] | None:
    if set(document) != {"apiVersion", "kind", "metadata", "spec"}:
        return None
    if document.get("apiVersion") != API_VERSION or document.get("kind") != "RuntimeVersionReport":
        return None
    metadata = document.get("metadata")
    spec = document.get("spec")
    if not isinstance(metadata, Mapping) or set(metadata) != {"tenantId", "evaluatedAt"}:
        return None
    if (
        not isinstance(metadata.get("tenantId"), str)
        or not 1 <= len(metadata["tenantId"]) <= 128
    ):
        return None
    try:
        _parse_timestamp(metadata.get("evaluatedAt"))
    except IngressQualificationError:
        return None
    if not isinstance(spec, Mapping) or set(spec) != {
        "application",
        "contracts",
        "storage",
        "build",
        "deployment",
    }:
        return None
    application = spec.get("application")
    contracts = spec.get("contracts")
    storage = spec.get("storage")
    build = spec.get("build")
    deployment = spec.get("deployment")
    if (
        not isinstance(application, Mapping)
        or set(application) != {"version"}
        or not isinstance(application.get("version"), str)
        or SEMVER.fullmatch(application["version"]) is None
        or not isinstance(contracts, Mapping)
        or dict(contracts) != {"apiVersion": API_VERSION}
        or not isinstance(storage, Mapping)
        or set(storage) != {"requiredMigration"}
        or not isinstance(storage.get("requiredMigration"), str)
        or MIGRATION.fullmatch(storage["requiredMigration"]) is None
        or not isinstance(build, Mapping)
        or not isinstance(deployment, Mapping)
    ):
        return None
    identity = {
        "applicationVersion": application["version"],
        "contractsApiVersion": API_VERSION,
        "requiredMigration": storage["requiredMigration"],
    }
    mode = build.get("mode")
    if mode == "development" and set(build) == {"mode"} and not deployment:
        identity["buildMode"] = "development"
        return identity
    if mode != "release" or set(build) != {"mode", "revision"}:
        return None
    revision = build.get("revision")
    if not isinstance(revision, str) or REVISION.fullmatch(revision) is None:
        return None
    if set(deployment) != {"helmChartVersion", "imageDigest"}:
        return None
    chart = deployment.get("helmChartVersion")
    image = deployment.get("imageDigest")
    if (
        not isinstance(chart, str)
        or SEMVER.fullmatch(chart) is None
        or not isinstance(image, str)
        or DIGEST.fullmatch(image) is None
    ):
        return None
    identity.update(
        {
            "buildMode": "release",
            "sourceRevision": revision,
            "chartVersion": chart,
            "imageDigest": image,
        }
    )
    return identity


def _expected_identity(
    profile: str,
    *,
    revision: str,
    repository: Mapping[str, str],
    image_digest: str | None,
) -> dict[str, str]:
    identity = {
        "applicationVersion": repository["applicationVersion"],
        "contractsApiVersion": API_VERSION,
        "requiredMigration": repository["requiredMigration"],
        "buildMode": "development" if profile == "local-loopback" else "release",
    }
    if profile == "local-loopback":
        if image_digest is not None:
            _fail("ingress-qualification.image.not-allowed")
        return identity
    if image_digest is None or DIGEST.fullmatch(image_digest) is None:
        _fail("ingress-qualification.image.required")
    identity.update(
        {
            "sourceRevision": revision,
            "chartVersion": repository["chartVersion"],
            "imageDigest": image_digest,
        }
    )
    return identity


def _validate_path_document(
    attempt: _Attempt, expected_identity: Mapping[str, str]
) -> _Attempt:
    if not attempt.success or attempt.document is None:
        return attempt
    if attempt.path_id in ("liveness", "readiness"):
        if dict(attempt.document) != {"status": "ok"}:
            return _Attempt(
                attempt.path_id,
                False,
                attempt.duration_milliseconds,
                "contract",
            )
        return attempt
    identity = _runtime_identity(attempt.document)
    if identity is None:
        return _Attempt(
            attempt.path_id,
            False,
            attempt.duration_milliseconds,
            "contract",
        )
    if identity != dict(expected_identity):
        return _Attempt(
            attempt.path_id,
            False,
            attempt.duration_milliseconds,
            "identity",
        )
    return attempt


def _percentile95(values: Sequence[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)]


def _check(identifier: str, passed: bool, error_code: str) -> dict[str, str]:
    result = {"id": identifier, "status": "passed" if passed else "failed"}
    if not passed:
        result["errorCode"] = error_code
    return result


def _report_identifier(metadata: Mapping[str, object], spec: Mapping[str, object]) -> str:
    return "iaq_" + hashlib.sha256(
        _canonical({"metadata": metadata, "spec": spec})
    ).hexdigest()[:32]


def _bounded_integer(value: int, *, minimum: int, maximum: int, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _fail(code)
    return value


def generate_report(
    *,
    profile: str,
    base_url: str,
    token_file: Path,
    output: Path,
    ca_file: Path | None = None,
    image_digest: str | None = None,
    sample_count: int | None = None,
    minimum_availability_basis_points: int = 9990,
    maximum_p95_latency_milliseconds: int = 2000,
    request_timeout_milliseconds: int = 2000,
    interval_milliseconds: int | None = None,
    now: Callable[[], datetime] | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    if profile not in PROFILES:
        _fail("ingress-qualification.profile.invalid")
    selected_samples = sample_count if sample_count is not None else (
        3 if profile == "local-loopback" else 100
    )
    selected_interval = interval_milliseconds if interval_milliseconds is not None else (
        0 if profile == "local-loopback" else 1000
    )
    _bounded_integer(
        selected_samples,
        minimum=3,
        maximum=10_000,
        code="ingress-qualification.objective.invalid",
    )
    if profile == "customer-ingress" and selected_samples < 100:
        _fail("ingress-qualification.objective.customer-samples-insufficient")
    for value, minimum, maximum in (
        (minimum_availability_basis_points, 1, 10_000),
        (maximum_p95_latency_milliseconds, 1, 60_000),
        (request_timeout_milliseconds, 100, 30_000),
        (selected_interval, 0, 60_000),
    ):
        _bounded_integer(
            value,
            minimum=minimum,
            maximum=maximum,
            code="ingress-qualification.objective.invalid",
        )
    canonical_base, transport, target_digest = _checked_url(profile, base_url)
    token = _load_token(token_file.expanduser().absolute())
    revision, dirty = _git_state()
    repository = _repository_identity()
    expected_identity = _expected_identity(
        profile,
        revision=revision,
        repository=repository,
        image_digest=image_digest,
    )
    if ca_file is not None:
        resolved_ca = ca_file.expanduser().resolve()
        if profile != "customer-ingress" or not resolved_ca.is_file():
            _fail("ingress-qualification.tls.ca-invalid")
        ca_source = "custom"
    else:
        resolved_ca = None
        ca_source = "system" if profile == "customer-ingress" else "not-applicable"
    try:
        context = ssl.create_default_context(cafile=str(resolved_ca) if resolved_ca else None)
    except (OSError, ssl.SSLError):
        _fail("ingress-qualification.tls.ca-invalid")
    opener = build_opener(ProxyHandler({}), _DenyRedirects(), HTTPSHandler(context=context))
    clock = now or (lambda: datetime.now(timezone.utc))
    started_at = clock()
    path_attempts: dict[str, list[_Attempt]] = {identifier: [] for identifier, _, _ in PATHS}
    cycle_success_latencies: list[int] = []
    failure_categories = {category: 0 for category in FAILURE_CATEGORIES}
    successful_samples = 0
    for sample_index in range(selected_samples):
        cycle_started = monotonic()
        cycle: list[_Attempt] = []
        for identifier, path, authenticated in PATHS:
            attempted = _request_json(
                opener,
                path_id=identifier,
                url=canonical_base + path,
                token=token if authenticated else None,
                timeout_milliseconds=request_timeout_milliseconds,
                monotonic=monotonic,
            )
            checked = _validate_path_document(attempted, expected_identity)
            path_attempts[identifier].append(checked)
            cycle.append(checked)
            if checked.failure_category is not None:
                failure_categories[checked.failure_category] += 1
        cycle_latency = _elapsed_milliseconds(cycle_started, monotonic())
        if all(item.success for item in cycle):
            successful_samples += 1
            cycle_success_latencies.append(cycle_latency)
        if sample_index + 1 < selected_samples and selected_interval:
            sleeper(selected_interval / 1000)
    completed_at = clock()
    failed_samples = selected_samples - successful_samples
    availability = (successful_samples * 10_000) // selected_samples
    path_measurements: list[dict[str, object]] = []
    for identifier, _, _ in PATHS:
        attempts = path_attempts[identifier]
        latencies = [item.duration_milliseconds for item in attempts if item.success]
        successes = len(latencies)
        path_measurements.append(
            {
                "id": identifier,
                "attempts": selected_samples,
                "successes": successes,
                "failures": selected_samples - successes,
                "p95LatencyMilliseconds": _percentile95(latencies),
            }
        )
    measurement = {
        "startedAt": _timestamp(started_at),
        "completedAt": _timestamp(completed_at),
        "sampleCount": selected_samples,
        "successfulSamples": successful_samples,
        "failedSamples": failed_samples,
        "availabilityBasisPoints": availability,
        "p95CycleLatencyMilliseconds": _percentile95(cycle_success_latencies),
        "paths": path_measurements,
        "failureCategories": failure_categories,
    }
    by_path = {item["id"]: item for item in path_measurements}
    p95 = measurement["p95CycleLatencyMilliseconds"]
    checks = [
        _check(
            "source-binding",
            profile == "local-loopback" or not dirty,
            "ingress-qualification.source.dirty",
        ),
        _check("minimized-output", True, "ingress-qualification.output.not-minimized"),
        _check(
            "direct-no-redirect-client",
            True,
            "ingress-qualification.client.redirect-or-proxy-enabled",
        ),
        _check("transport-profile", True, "ingress-qualification.transport.invalid"),
        _check(
            "response-contracts-consistent",
            failure_categories["contract"] == 0,
            "ingress-qualification.response.contract-invalid",
        ),
        _check(
            "liveness-observed",
            by_path["liveness"]["successes"] > 0,
            "ingress-qualification.liveness.unavailable",
        ),
        _check(
            "readiness-observed",
            by_path["readiness"]["successes"] > 0,
            "ingress-qualification.readiness.unavailable",
        ),
        _check(
            "runtime-identity-consistent",
            by_path["runtime-identity"]["successes"] > 0
            and failure_categories["identity"] == 0,
            "ingress-qualification.runtime-identity.mismatch",
        ),
        _check(
            "availability-objective",
            availability >= minimum_availability_basis_points,
            "ingress-qualification.availability.objective-missed",
        ),
        _check(
            "latency-objective",
            isinstance(p95, int) and p95 <= maximum_p95_latency_milliseconds,
            "ingress-qualification.latency.objective-missed",
        ),
    ]
    failed_checks = sum(item["status"] == "failed" for item in checks)
    status = "qualified" if failed_checks == 0 else "not-qualified"
    spec: dict[str, object] = {
        "status": status,
        "qualificationLevel": profile,
        "targetBindingDigest": target_digest,
        "targetIdentity": expected_identity,
        "objective": {
            "sampleCount": selected_samples,
            "minimumAvailabilityBasisPoints": minimum_availability_basis_points,
            "maximumP95LatencyMilliseconds": maximum_p95_latency_milliseconds,
            "requestTimeoutMilliseconds": request_timeout_milliseconds,
            "intervalMilliseconds": selected_interval,
        },
        "environment": {
            "platform": f"{platform.system().lower()}/{platform.machine().lower()}",
            "pythonVersion": platform.python_version(),
            "transport": transport,
            "caSource": ca_source,
            "proxyMode": "disabled",
            "redirectMode": "deny",
        },
        "measurements": measurement,
        "checks": checks,
        "summary": {
            "totalChecks": len(checks),
            "passedChecks": len(checks) - failed_checks,
            "failedChecks": failed_checks,
            "overallStatus": status,
        },
    }
    metadata_without_id: dict[str, object] = {
        "generatedAt": _timestamp(completed_at),
        "sourceRevision": revision,
        "sourceDirty": dirty,
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
    _write_report(output, report)
    return report


def _write_report(path: Path, report: Mapping[str, object]) -> None:
    candidate = path.expanduser()
    if candidate.is_symlink():
        _fail("ingress-qualification.output.invalid")
    destination = candidate.absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
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
        _fail("ingress-qualification.output.invalid")


def _require_exact_keys(value: object, keys: set[str], code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != keys:
        _fail(code)
    return value


def _require_int(value: object, minimum: int, maximum: int, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _fail(code)
    return value


def validate_report_document(report: Mapping[str, Any]) -> None:
    root = _require_exact_keys(
        report,
        {"apiVersion", "kind", "metadata", "spec"},
        "ingress-qualification.report.invalid",
    )
    if root.get("apiVersion") != API_VERSION or root.get("kind") != KIND:
        _fail("ingress-qualification.report.invalid")
    metadata = _require_exact_keys(
        root.get("metadata"),
        {"id", "generatedAt", "sourceRevision", "sourceDirty"},
        "ingress-qualification.report.metadata-invalid",
    )
    if (
        not isinstance(metadata.get("id"), str)
        or REPORT_ID.fullmatch(metadata["id"]) is None
        or not isinstance(metadata.get("sourceRevision"), str)
        or REVISION.fullmatch(metadata["sourceRevision"]) is None
        or not isinstance(metadata.get("sourceDirty"), bool)
    ):
        _fail("ingress-qualification.report.metadata-invalid")
    generated = _parse_timestamp(metadata.get("generatedAt"))
    spec = _require_exact_keys(
        root.get("spec"),
        {
            "status",
            "qualificationLevel",
            "targetBindingDigest",
            "targetIdentity",
            "objective",
            "environment",
            "measurements",
            "checks",
            "summary",
        },
        "ingress-qualification.report.spec-invalid",
    )
    status = spec.get("status")
    profile = spec.get("qualificationLevel")
    if status not in ("qualified", "not-qualified") or profile not in PROFILES:
        _fail("ingress-qualification.report.spec-invalid")
    if (
        not isinstance(spec.get("targetBindingDigest"), str)
        or DIGEST.fullmatch(spec["targetBindingDigest"]) is None
    ):
        _fail("ingress-qualification.report.target-invalid")
    identity = _require_exact_keys(
        spec.get("targetIdentity"),
        {
            "applicationVersion",
            "contractsApiVersion",
            "requiredMigration",
            "buildMode",
        }
        if profile == "local-loopback"
        else {
            "applicationVersion",
            "contractsApiVersion",
            "requiredMigration",
            "buildMode",
            "sourceRevision",
            "chartVersion",
            "imageDigest",
        },
        "ingress-qualification.report.target-invalid",
    )
    if (
        not isinstance(identity.get("applicationVersion"), str)
        or SEMVER.fullmatch(identity["applicationVersion"]) is None
        or identity.get("contractsApiVersion") != API_VERSION
        or not isinstance(identity.get("requiredMigration"), str)
        or MIGRATION.fullmatch(identity["requiredMigration"]) is None
    ):
        _fail("ingress-qualification.report.target-invalid")
    if profile == "local-loopback":
        if identity.get("buildMode") != "development":
            _fail("ingress-qualification.report.profile-invalid")
    else:
        if (
            identity.get("buildMode") != "release"
            or identity.get("sourceRevision") != metadata["sourceRevision"]
            or not isinstance(identity.get("chartVersion"), str)
            or SEMVER.fullmatch(identity["chartVersion"]) is None
            or not isinstance(identity.get("imageDigest"), str)
            or DIGEST.fullmatch(identity["imageDigest"]) is None
        ):
            _fail("ingress-qualification.report.profile-invalid")
    objective = _require_exact_keys(
        spec.get("objective"),
        {
            "sampleCount",
            "minimumAvailabilityBasisPoints",
            "maximumP95LatencyMilliseconds",
            "requestTimeoutMilliseconds",
            "intervalMilliseconds",
        },
        "ingress-qualification.report.objective-invalid",
    )
    samples = _require_int(
        objective.get("sampleCount"),
        3,
        10_000,
        "ingress-qualification.report.objective-invalid",
    )
    if profile == "customer-ingress" and samples < 100:
        _fail("ingress-qualification.report.profile-invalid")
    minimum_availability = _require_int(
        objective.get("minimumAvailabilityBasisPoints"),
        1,
        10_000,
        "ingress-qualification.report.objective-invalid",
    )
    maximum_p95 = _require_int(
        objective.get("maximumP95LatencyMilliseconds"),
        1,
        60_000,
        "ingress-qualification.report.objective-invalid",
    )
    _require_int(
        objective.get("requestTimeoutMilliseconds"),
        100,
        30_000,
        "ingress-qualification.report.objective-invalid",
    )
    _require_int(
        objective.get("intervalMilliseconds"),
        0,
        60_000,
        "ingress-qualification.report.objective-invalid",
    )
    environment = _require_exact_keys(
        spec.get("environment"),
        {"platform", "pythonVersion", "transport", "caSource", "proxyMode", "redirectMode"},
        "ingress-qualification.report.environment-invalid",
    )
    if (
        not isinstance(environment.get("platform"), str)
        or len(environment["platform"]) > 128
        or PLATFORM.fullmatch(environment["platform"]) is None
        or not isinstance(environment.get("pythonVersion"), str)
        or PYTHON_VERSION.fullmatch(environment["pythonVersion"]) is None
        or environment.get("proxyMode") != "disabled"
        or environment.get("redirectMode") != "deny"
    ):
        _fail("ingress-qualification.report.environment-invalid")
    expected_transport = "loopback-http" if profile == "local-loopback" else "verified-https"
    expected_ca = {"not-applicable"} if profile == "local-loopback" else {"system", "custom"}
    if environment.get("transport") != expected_transport or environment.get("caSource") not in expected_ca:
        _fail("ingress-qualification.report.profile-invalid")
    measurements = _require_exact_keys(
        spec.get("measurements"),
        {
            "startedAt",
            "completedAt",
            "sampleCount",
            "successfulSamples",
            "failedSamples",
            "availabilityBasisPoints",
            "p95CycleLatencyMilliseconds",
            "paths",
            "failureCategories",
        },
        "ingress-qualification.report.measurements-invalid",
    )
    started = _parse_timestamp(measurements.get("startedAt"))
    completed = _parse_timestamp(measurements.get("completedAt"))
    if started > completed or generated != completed or measurements.get("sampleCount") != samples:
        _fail("ingress-qualification.report.measurements-invalid")
    successful = _require_int(
        measurements.get("successfulSamples"),
        0,
        samples,
        "ingress-qualification.report.measurements-invalid",
    )
    failed = _require_int(
        measurements.get("failedSamples"),
        0,
        samples,
        "ingress-qualification.report.measurements-invalid",
    )
    availability = _require_int(
        measurements.get("availabilityBasisPoints"),
        0,
        10_000,
        "ingress-qualification.report.measurements-invalid",
    )
    if successful + failed != samples or availability != (successful * 10_000) // samples:
        _fail("ingress-qualification.report.measurements-invalid")
    cycle_p95 = measurements.get("p95CycleLatencyMilliseconds")
    if successful == 0:
        if cycle_p95 is not None:
            _fail("ingress-qualification.report.measurements-invalid")
    else:
        _require_int(
            cycle_p95,
            0,
            360_000,
            "ingress-qualification.report.measurements-invalid",
        )
    paths = measurements.get("paths")
    if not isinstance(paths, list) or len(paths) != len(PATHS):
        _fail("ingress-qualification.report.measurements-invalid")
    total_path_failures = 0
    path_successes: dict[str, int] = {}
    for item, (expected_id, _, _) in zip(paths, PATHS):
        path = _require_exact_keys(
            item,
            {"id", "attempts", "successes", "failures", "p95LatencyMilliseconds"},
            "ingress-qualification.report.measurements-invalid",
        )
        if path.get("id") != expected_id or path.get("attempts") != samples:
            _fail("ingress-qualification.report.measurements-invalid")
        path_success = _require_int(
            path.get("successes"),
            0,
            samples,
            "ingress-qualification.report.measurements-invalid",
        )
        path_failure = _require_int(
            path.get("failures"),
            0,
            samples,
            "ingress-qualification.report.measurements-invalid",
        )
        if path_success + path_failure != samples or successful > path_success:
            _fail("ingress-qualification.report.measurements-invalid")
        path_p95 = path.get("p95LatencyMilliseconds")
        if path_success == 0:
            if path_p95 is not None:
                _fail("ingress-qualification.report.measurements-invalid")
        else:
            _require_int(
                path_p95,
                0,
                120_000,
                "ingress-qualification.report.measurements-invalid",
            )
        path_successes[expected_id] = path_success
        total_path_failures += path_failure
    categories = _require_exact_keys(
        measurements.get("failureCategories"),
        set(FAILURE_CATEGORIES),
        "ingress-qualification.report.measurements-invalid",
    )
    category_values = {}
    for category in FAILURE_CATEGORIES:
        category_values[category] = _require_int(
            categories.get(category),
            0,
            samples if category == "identity" else samples * len(PATHS),
            "ingress-qualification.report.measurements-invalid",
        )
    if sum(category_values.values()) != total_path_failures:
        _fail("ingress-qualification.report.measurements-invalid")
    expected_passes = (
        profile == "local-loopback" or metadata["sourceDirty"] is False,
        True,
        True,
        True,
        category_values["contract"] == 0,
        path_successes["liveness"] > 0,
        path_successes["readiness"] > 0,
        path_successes["runtime-identity"] > 0 and category_values["identity"] == 0,
        availability >= minimum_availability,
        isinstance(cycle_p95, int) and cycle_p95 <= maximum_p95,
    )
    error_codes = (
        "ingress-qualification.source.dirty",
        "ingress-qualification.output.not-minimized",
        "ingress-qualification.client.redirect-or-proxy-enabled",
        "ingress-qualification.transport.invalid",
        "ingress-qualification.response.contract-invalid",
        "ingress-qualification.liveness.unavailable",
        "ingress-qualification.readiness.unavailable",
        "ingress-qualification.runtime-identity.mismatch",
        "ingress-qualification.availability.objective-missed",
        "ingress-qualification.latency.objective-missed",
    )
    checks = spec.get("checks")
    if not isinstance(checks, list) or len(checks) != len(CHECK_IDS):
        _fail("ingress-qualification.report.checks-invalid")
    failed_checks = 0
    for item, identifier, passed, error_code in zip(
        checks, CHECK_IDS, expected_passes, error_codes
    ):
        expected = _check(identifier, passed, error_code)
        if item != expected:
            _fail("ingress-qualification.report.checks-invalid")
        failed_checks += not passed
    expected_status = "qualified" if failed_checks == 0 else "not-qualified"
    summary = _require_exact_keys(
        spec.get("summary"),
        {"totalChecks", "passedChecks", "failedChecks", "overallStatus"},
        "ingress-qualification.report.summary-invalid",
    )
    if spec.get("status") != expected_status or summary != {
        "totalChecks": len(CHECK_IDS),
        "passedChecks": len(CHECK_IDS) - failed_checks,
        "failedChecks": failed_checks,
        "overallStatus": expected_status,
    }:
        _fail("ingress-qualification.report.summary-invalid")
    metadata_without_id = {
        "generatedAt": metadata["generatedAt"],
        "sourceRevision": metadata["sourceRevision"],
        "sourceDirty": metadata["sourceDirty"],
    }
    if metadata.get("id") != _report_identifier(metadata_without_id, spec):
        _fail("ingress-qualification.report.id-invalid")


def verify_report(
    path: Path, *, require_clean: bool = False, require_qualified: bool = False
) -> dict[str, Any]:
    try:
        report = json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _fail("ingress-qualification.report.unreadable")
    if not isinstance(report, Mapping):
        _fail("ingress-qualification.report.invalid")
    validate_report_document(report)
    revision, dirty = _git_state()
    metadata = report["metadata"]
    spec = report["spec"]
    if metadata["sourceRevision"] != revision or metadata["sourceDirty"] != dirty:
        _fail("ingress-qualification.report.source-mismatch")
    if require_clean and metadata["sourceDirty"]:
        _fail("ingress-qualification.report.dirty")
    if require_qualified and spec["status"] != "qualified":
        _fail("ingress-qualification.report.not-qualified")
    repository = _repository_identity()
    target = spec["targetIdentity"]
    if (
        target["applicationVersion"] != repository["applicationVersion"]
        or target["requiredMigration"] != repository["requiredMigration"]
        or (
            spec["qualificationLevel"] == "customer-ingress"
            and target["chartVersion"] != repository["chartVersion"]
        )
    ):
        _fail("ingress-qualification.report.version-mismatch")
    return dict(report)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="probe one target and write a report")
    run.add_argument("--profile", choices=PROFILES, required=True)
    run.add_argument("--base-url", required=True)
    run.add_argument("--token-file", type=Path, required=True)
    run.add_argument("--ca-file", type=Path)
    run.add_argument("--image-digest")
    run.add_argument("--samples", type=int)
    run.add_argument("--minimum-availability-basis-points", type=int, default=9990)
    run.add_argument("--maximum-p95-latency-milliseconds", type=int, default=2000)
    run.add_argument("--request-timeout-milliseconds", type=int, default=2000)
    run.add_argument("--interval-milliseconds", type=int)
    run.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify", help="verify retained evidence offline")
    verify.add_argument("--report", type=Path, required=True)
    verify.add_argument("--require-clean", action="store_true")
    verify.add_argument("--require-qualified", action="store_true")
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    try:
        if arguments.command == "run":
            report = generate_report(
                profile=arguments.profile,
                base_url=arguments.base_url,
                token_file=arguments.token_file,
                ca_file=arguments.ca_file,
                image_digest=arguments.image_digest,
                sample_count=arguments.samples,
                minimum_availability_basis_points=arguments.minimum_availability_basis_points,
                maximum_p95_latency_milliseconds=arguments.maximum_p95_latency_milliseconds,
                request_timeout_milliseconds=arguments.request_timeout_milliseconds,
                interval_milliseconds=arguments.interval_milliseconds,
                output=arguments.output,
            )
            print(
                f"ingress availability {report['spec']['status']}: {arguments.output}"
            )
            if report["spec"]["status"] != "qualified":
                raise SystemExit(1)
        else:
            report = verify_report(
                arguments.report,
                require_clean=arguments.require_clean,
                require_qualified=arguments.require_qualified,
            )
            print(f"ingress availability verified: {report['spec']['status']}")
    except IngressQualificationError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
