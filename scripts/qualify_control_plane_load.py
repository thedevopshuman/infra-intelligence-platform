#!/usr/bin/env python3
"""Run and verify a bounded fixed-rate customer control-plane read load profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import ssl
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.request import HTTPSHandler, ProxyHandler, build_opener

from jsonschema import Draft202012Validator, FormatChecker

import qualify_ingress_availability as ingress


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "contracts/schemas/control-plane-load-qualification-report.schema.json"
API_VERSION = "iip.platform/v1alpha1"
KIND = "ControlPlaneLoadQualificationReport"
PROFILE = "customer-control-plane-read-load-v1"
MAX_REQUESTS = 250_000
CHECK_IDS = (
    "source-binding",
    "minimized-output",
    "explicit-traffic-enable",
    "direct-no-redirect-client",
    "verified-https",
    "exact-release-identity",
    "bounded-request-volume",
    "observation-window",
    "scheduler-attainment",
    "successful-request-attainment",
    "p95-latency-objective",
    "p99-latency-objective",
)
LIMITATIONS = (
    "authenticated-runtime-identity-read-only",
    "single-endpoint",
    "fixed-rate-synthetic-traffic",
    "single-ingress-path",
    "write-database-worker-receiver-capacity-not-qualified",
    "failure-regional-and-long-window-slo-not-qualified",
)


class ControlPlaneLoadQualificationError(RuntimeError):
    """Stable fixed-rate qualification failure."""


def _fail(code: str) -> None:
    raise ControlPlaneLoadQualificationError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        _fail("control-plane-load.time.invalid")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        _fail("control-plane-load.report.time-invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail("control-plane-load.report.time-invalid")
    if parsed.tzinfo is None:
        _fail("control-plane-load.report.time-invalid")
    return parsed.astimezone(timezone.utc)


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _integer(value: object, *, minimum: int, maximum: int, code: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        _fail(code)
    return value


def _percentile(values: Sequence[int], percentile: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percentile / 100) - 1)]


def _check(identifier: str, passed: bool, error_code: str) -> dict[str, str]:
    result = {"id": identifier, "status": "passed" if passed else "failed"}
    if not passed:
        result["errorCode"] = error_code
    return result


def _report_identifier(
    metadata_without_id: Mapping[str, object], spec: Mapping[str, object]
) -> str:
    return "clq_" + hashlib.sha256(
        _canonical({"metadata": metadata_without_id, "spec": spec})
    ).hexdigest()[:32]


def _validate_objective(
    *,
    duration_seconds: int,
    target_requests_per_second: int,
    concurrency: int,
    minimum_successful_request_basis_points: int,
    maximum_scheduler_miss_basis_points: int,
    maximum_p95_latency_milliseconds: int,
    maximum_p99_latency_milliseconds: int,
    request_timeout_milliseconds: int,
    maximum_scheduler_lag_milliseconds: int,
) -> int:
    for value, minimum, maximum in (
        (duration_seconds, 300, 3600),
        (target_requests_per_second, 1, 250),
        (concurrency, 1, 128),
        (minimum_successful_request_basis_points, 9000, 10_000),
        (maximum_scheduler_miss_basis_points, 0, 1000),
        (maximum_p95_latency_milliseconds, 1, 60_000),
        (maximum_p99_latency_milliseconds, 1, 120_000),
        (request_timeout_milliseconds, 100, 30_000),
        (maximum_scheduler_lag_milliseconds, 10, 5000),
    ):
        _integer(
            value,
            minimum=minimum,
            maximum=maximum,
            code="control-plane-load.objective.invalid",
        )
    if maximum_p99_latency_milliseconds < maximum_p95_latency_milliseconds:
        _fail("control-plane-load.objective.invalid")
    total = duration_seconds * target_requests_per_second
    if total > MAX_REQUESTS:
        _fail("control-plane-load.objective.request-volume-exceeded")
    return total


@dataclass(frozen=True)
class RawLoadResult:
    started_at: datetime
    completed_at: datetime
    actual_duration_milliseconds: int
    target_request_count: int
    attempts: tuple[ingress._Attempt, ...]
    scheduler_missed_requests: int
    scheduler_lags_milliseconds: tuple[int, ...]


def _run_fixed_rate(
    *,
    duration_seconds: int,
    target_requests_per_second: int,
    concurrency: int,
    maximum_scheduler_lag_milliseconds: int,
    requester_factory: Callable[[], Callable[[], ingress._Attempt]],
    clock: Callable[[], datetime],
    monotonic: Callable[[], float],
    sleeper: Callable[[float], None],
) -> RawLoadResult:
    target = duration_seconds * target_requests_per_second
    barrier = threading.Barrier(concurrency + 1, timeout=30)
    start: dict[str, float] = {}

    def worker(worker_index: int) -> tuple[list[ingress._Attempt], int, list[int]]:
        try:
            requester = requester_factory()
            barrier.wait()
        except Exception as error:
            raise ControlPlaneLoadQualificationError(
                "control-plane-load.generator.start-failed"
            ) from error
        attempts: list[ingress._Attempt] = []
        missed = 0
        scheduler_lags: list[int] = []
        for request_index in range(worker_index, target, concurrency):
            due = start["monotonic"] + request_index / target_requests_per_second
            current = monotonic()
            if current < due:
                sleeper(due - current)
                current = monotonic()
            lag = max(0, math.ceil((current - due) * 1000))
            scheduler_lags.append(lag)
            if lag > maximum_scheduler_lag_milliseconds:
                missed += 1
                continue
            try:
                attempts.append(requester())
            except Exception:
                attempts.append(
                    ingress._Attempt(
                        "runtime-identity", False, 0, "transport"
                    )
                )
        return attempts, missed, scheduler_lags

    executor = ThreadPoolExecutor(
        max_workers=concurrency, thread_name_prefix="iip-control-load"
    )
    futures = [executor.submit(worker, index) for index in range(concurrency)]
    try:
        start["monotonic"] = monotonic()
        started_at = clock()
        barrier.wait()
        results = [future.result() for future in futures]
        completed_mono = monotonic()
        completed_at = clock()
    except Exception:
        _fail("control-plane-load.generator.failed")
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    attempts = tuple(attempt for result in results for attempt in result[0])
    missed = sum(result[1] for result in results)
    lags = tuple(value for result in results for value in result[2])
    if len(attempts) + missed != target or len(lags) != target:
        _fail("control-plane-load.generator.accounting-invalid")
    return RawLoadResult(
        started_at=started_at,
        completed_at=completed_at,
        actual_duration_milliseconds=max(
            1, math.ceil((completed_mono - start["monotonic"]) * 1000)
        ),
        target_request_count=target,
        attempts=attempts,
        scheduler_missed_requests=missed,
        scheduler_lags_milliseconds=lags,
    )


def _measurements(raw: RawLoadResult) -> dict[str, Any]:
    attempted = len(raw.attempts)
    successful_attempts = [attempt for attempt in raw.attempts if attempt.success]
    successful = len(successful_attempts)
    failed = attempted - successful
    target = raw.target_request_count
    failure_categories = {category: 0 for category in ingress.FAILURE_CATEGORIES}
    for attempt in raw.attempts:
        if not attempt.success:
            category = attempt.failure_category
            if category not in failure_categories:
                _fail("control-plane-load.measurement.failure-category-invalid")
            failure_categories[str(category)] += 1
    latencies = [attempt.duration_milliseconds for attempt in successful_attempts]
    return {
        "startedAt": _timestamp(raw.started_at),
        "completedAt": _timestamp(raw.completed_at),
        "actualDurationMilliseconds": raw.actual_duration_milliseconds,
        "targetRequestCount": target,
        "attemptedRequests": attempted,
        "successfulRequests": successful,
        "failedRequests": failed,
        "schedulerMissedRequests": raw.scheduler_missed_requests,
        "successfulRequestBasisPoints": (successful * 10_000) // target,
        "schedulerMissBasisPoints": (
            raw.scheduler_missed_requests * 10_000
        )
        // target,
        "achievedSuccessfulMilliRequestsPerSecond": (
            successful * 1_000_000
        )
        // raw.actual_duration_milliseconds,
        "schedulerLagP95Milliseconds": _percentile(
            raw.scheduler_lags_milliseconds, 95
        ),
        "requestLatency": {
            "p50Milliseconds": _percentile(latencies, 50),
            "p95Milliseconds": _percentile(latencies, 95),
            "p99Milliseconds": _percentile(latencies, 99),
            "maximumMilliseconds": max(latencies) if latencies else None,
        },
        "failureCategories": failure_categories,
    }


def _derive_checks(
    *, metadata: Mapping[str, Any], spec: Mapping[str, Any]
) -> list[dict[str, str]]:
    objective = _mapping(spec.get("objective"), "control-plane-load.report.invalid")
    measurements = _mapping(
        spec.get("measurements"), "control-plane-load.report.invalid"
    )
    latency = _mapping(
        measurements.get("requestLatency"), "control-plane-load.report.invalid"
    )
    failures = _mapping(
        measurements.get("failureCategories"), "control-plane-load.report.invalid"
    )
    duration = int(objective.get("durationSeconds", 0))
    rate = int(objective.get("targetRequestsPerSecond", 0))
    actual = int(measurements.get("actualDurationMilliseconds", 0))
    p95 = latency.get("p95Milliseconds")
    p99 = latency.get("p99Milliseconds")
    minimum_window = duration * 1000 - math.ceil(1000 / max(1, rate))
    maximum_window = (
        duration * 1000
        + int(objective.get("requestTimeoutMilliseconds", 0))
        + int(objective.get("maximumSchedulerLagMilliseconds", 0))
        + 5000
    )
    return [
        _check(
            "source-binding",
            metadata.get("sourceDirty") is False,
            "control-plane-load.source.dirty",
        ),
        _check("minimized-output", True, "control-plane-load.output.not-minimized"),
        _check(
            "explicit-traffic-enable",
            True,
            "control-plane-load.traffic.explicit-enable-required",
        ),
        _check(
            "direct-no-redirect-client",
            True,
            "control-plane-load.client.redirect-or-proxy-enabled",
        ),
        _check("verified-https", True, "control-plane-load.transport.invalid"),
        _check(
            "exact-release-identity",
            int(measurements.get("successfulRequests", 0)) > 0
            and int(failures.get("identity", 0)) == 0,
            "control-plane-load.identity.mismatch-or-unobserved",
        ),
        _check(
            "bounded-request-volume",
            int(measurements.get("targetRequestCount", 0))
            == duration * rate
            <= MAX_REQUESTS,
            "control-plane-load.request-volume.invalid",
        ),
        _check(
            "observation-window",
            minimum_window <= actual <= maximum_window,
            "control-plane-load.window.invalid",
        ),
        _check(
            "scheduler-attainment",
            int(measurements.get("schedulerMissBasisPoints", 10_001))
            <= int(objective.get("maximumSchedulerMissBasisPoints", -1)),
            "control-plane-load.scheduler.objective-missed",
        ),
        _check(
            "successful-request-attainment",
            int(measurements.get("successfulRequestBasisPoints", -1))
            >= int(objective.get("minimumSuccessfulRequestBasisPoints", 10_001)),
            "control-plane-load.success.objective-missed",
        ),
        _check(
            "p95-latency-objective",
            isinstance(p95, int)
            and not isinstance(p95, bool)
            and p95 <= int(objective.get("maximumP95LatencyMilliseconds", -1)),
            "control-plane-load.latency.p95-objective-missed",
        ),
        _check(
            "p99-latency-objective",
            isinstance(p99, int)
            and not isinstance(p99, bool)
            and p99 <= int(objective.get("maximumP99LatencyMilliseconds", -1)),
            "control-plane-load.latency.p99-objective-missed",
        ),
    ]


def build_report(
    *,
    revision: str,
    repository: Mapping[str, str],
    image_digest: str,
    target_binding_digest: str,
    ca_source: str,
    raw: RawLoadResult,
    duration_seconds: int,
    target_requests_per_second: int,
    concurrency: int,
    minimum_successful_request_basis_points: int,
    maximum_scheduler_miss_basis_points: int,
    maximum_p95_latency_milliseconds: int,
    maximum_p99_latency_milliseconds: int,
    request_timeout_milliseconds: int,
    maximum_scheduler_lag_milliseconds: int,
) -> dict[str, Any]:
    target = _validate_objective(
        duration_seconds=duration_seconds,
        target_requests_per_second=target_requests_per_second,
        concurrency=concurrency,
        minimum_successful_request_basis_points=minimum_successful_request_basis_points,
        maximum_scheduler_miss_basis_points=maximum_scheduler_miss_basis_points,
        maximum_p95_latency_milliseconds=maximum_p95_latency_milliseconds,
        maximum_p99_latency_milliseconds=maximum_p99_latency_milliseconds,
        request_timeout_milliseconds=request_timeout_milliseconds,
        maximum_scheduler_lag_milliseconds=maximum_scheduler_lag_milliseconds,
    )
    if raw.target_request_count != target:
        _fail("control-plane-load.measurement.target-invalid")
    measurements = _measurements(raw)
    identity = {
        "applicationVersion": repository["applicationVersion"],
        "contractsApiVersion": API_VERSION,
        "requiredMigration": repository["requiredMigration"],
        "buildMode": "release",
        "sourceRevision": revision,
        "chartVersion": repository["chartVersion"],
        "imageDigest": image_digest,
    }
    objective = {
        "durationSeconds": duration_seconds,
        "targetRequestsPerSecond": target_requests_per_second,
        "concurrency": concurrency,
        "minimumSuccessfulRequestBasisPoints": minimum_successful_request_basis_points,
        "maximumSchedulerMissBasisPoints": maximum_scheduler_miss_basis_points,
        "maximumP95LatencyMilliseconds": maximum_p95_latency_milliseconds,
        "maximumP99LatencyMilliseconds": maximum_p99_latency_milliseconds,
        "requestTimeoutMilliseconds": request_timeout_milliseconds,
        "maximumSchedulerLagMilliseconds": maximum_scheduler_lag_milliseconds,
    }
    spec_without_checks: dict[str, Any] = {
        "status": "not-qualified",
        "qualificationLevel": PROFILE,
        "targetBindingDigest": target_binding_digest,
        "targetIdentity": identity,
        "objective": objective,
        "environment": {
            "platform": f"{platform.system().lower()}/{platform.machine().lower()}",
            "pythonVersion": platform.python_version(),
            "transport": "verified-https",
            "caSource": ca_source,
            "proxyMode": "disabled",
            "redirectMode": "deny",
            "connectionMode": "close-per-request",
            "scheduler": "bounded-fixed-rate-v1",
        },
        "measurements": measurements,
    }
    metadata_without_id: dict[str, object] = {
        "generatedAt": measurements["completedAt"],
        "sourceRevision": revision,
        "sourceDirty": False,
    }
    checks = _derive_checks(metadata=metadata_without_id, spec=spec_without_checks)
    failed = sum(item["status"] == "failed" for item in checks)
    status = "qualified" if failed == 0 else "not-qualified"
    spec = {
        **spec_without_checks,
        "status": status,
        "checks": checks,
        "limitations": list(LIMITATIONS),
        "summary": {
            "totalChecks": len(checks),
            "passedChecks": len(checks) - failed,
            "failedChecks": failed,
            "overallStatus": status,
        },
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


def _validate_measurements(
    objective: Mapping[str, Any], measurements: Mapping[str, Any]
) -> None:
    duration = _integer(
        objective.get("durationSeconds"),
        minimum=300,
        maximum=3600,
        code="control-plane-load.report.objective-invalid",
    )
    rate = _integer(
        objective.get("targetRequestsPerSecond"),
        minimum=1,
        maximum=250,
        code="control-plane-load.report.objective-invalid",
    )
    target = duration * rate
    if target > MAX_REQUESTS or measurements.get("targetRequestCount") != target:
        _fail("control-plane-load.report.accounting-invalid")
    attempted = _integer(
        measurements.get("attemptedRequests"),
        minimum=0,
        maximum=target,
        code="control-plane-load.report.accounting-invalid",
    )
    successful = _integer(
        measurements.get("successfulRequests"),
        minimum=0,
        maximum=attempted,
        code="control-plane-load.report.accounting-invalid",
    )
    failed = _integer(
        measurements.get("failedRequests"),
        minimum=0,
        maximum=attempted,
        code="control-plane-load.report.accounting-invalid",
    )
    missed = _integer(
        measurements.get("schedulerMissedRequests"),
        minimum=0,
        maximum=target,
        code="control-plane-load.report.accounting-invalid",
    )
    actual = _integer(
        measurements.get("actualDurationMilliseconds"),
        minimum=1,
        maximum=4_000_000,
        code="control-plane-load.report.accounting-invalid",
    )
    if attempted + missed != target or successful + failed != attempted:
        _fail("control-plane-load.report.accounting-invalid")
    if (
        measurements.get("successfulRequestBasisPoints")
        != successful * 10_000 // target
        or measurements.get("schedulerMissBasisPoints")
        != missed * 10_000 // target
        or measurements.get("achievedSuccessfulMilliRequestsPerSecond")
        != successful * 1_000_000 // actual
    ):
        _fail("control-plane-load.report.arithmetic-invalid")
    failures = _mapping(
        measurements.get("failureCategories"),
        "control-plane-load.report.accounting-invalid",
    )
    if set(failures) != set(ingress.FAILURE_CATEGORIES):
        _fail("control-plane-load.report.accounting-invalid")
    failure_total = sum(
        _integer(
            failures.get(category),
            minimum=0,
            maximum=failed,
            code="control-plane-load.report.accounting-invalid",
        )
        for category in ingress.FAILURE_CATEGORIES
    )
    if failure_total != failed:
        _fail("control-plane-load.report.accounting-invalid")
    latency = _mapping(
        measurements.get("requestLatency"),
        "control-plane-load.report.latency-invalid",
    )
    values = [
        latency.get("p50Milliseconds"),
        latency.get("p95Milliseconds"),
        latency.get("p99Milliseconds"),
        latency.get("maximumMilliseconds"),
    ]
    if successful == 0:
        if any(value is not None for value in values):
            _fail("control-plane-load.report.latency-invalid")
    elif any(
        isinstance(value, bool) or not isinstance(value, int) or value < 0
        for value in values
    ) or values != sorted(values):
        _fail("control-plane-load.report.latency-invalid")
    scheduler_p95 = measurements.get("schedulerLagP95Milliseconds")
    if target > 0 and (
        isinstance(scheduler_p95, bool)
        or not isinstance(scheduler_p95, int)
        or scheduler_p95 < 0
    ):
        _fail("control-plane-load.report.scheduler-invalid")
    started = _parse_timestamp(measurements.get("startedAt"))
    completed = _parse_timestamp(measurements.get("completedAt"))
    if started > completed:
        _fail("control-plane-load.report.time-invalid")


def validate_report_document(report: Mapping[str, Any]) -> None:
    try:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("control-plane-load.schema.unavailable")
    errors = sorted(
        Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(
            report
        ),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        _fail("control-plane-load.report.schema-invalid")
    metadata = _mapping(report.get("metadata"), "control-plane-load.report.invalid")
    spec = _mapping(report.get("spec"), "control-plane-load.report.invalid")
    identity = _mapping(
        spec.get("targetIdentity"), "control-plane-load.report.identity-invalid"
    )
    objective = _mapping(
        spec.get("objective"), "control-plane-load.report.objective-invalid"
    )
    measurements = _mapping(
        spec.get("measurements"), "control-plane-load.report.measurements-invalid"
    )
    if (
        metadata.get("sourceRevision") != identity.get("sourceRevision")
        or metadata.get("generatedAt") != measurements.get("completedAt")
    ):
        _fail("control-plane-load.report.binding-invalid")
    _validate_objective(
        duration_seconds=int(objective["durationSeconds"]),
        target_requests_per_second=int(objective["targetRequestsPerSecond"]),
        concurrency=int(objective["concurrency"]),
        minimum_successful_request_basis_points=int(
            objective["minimumSuccessfulRequestBasisPoints"]
        ),
        maximum_scheduler_miss_basis_points=int(
            objective["maximumSchedulerMissBasisPoints"]
        ),
        maximum_p95_latency_milliseconds=int(
            objective["maximumP95LatencyMilliseconds"]
        ),
        maximum_p99_latency_milliseconds=int(
            objective["maximumP99LatencyMilliseconds"]
        ),
        request_timeout_milliseconds=int(objective["requestTimeoutMilliseconds"]),
        maximum_scheduler_lag_milliseconds=int(
            objective["maximumSchedulerLagMilliseconds"]
        ),
    )
    _validate_measurements(objective, measurements)
    expected_checks = _derive_checks(metadata=metadata, spec=spec)
    if spec.get("checks") != expected_checks or [
        item["id"] for item in expected_checks
    ] != list(CHECK_IDS):
        _fail("control-plane-load.report.checks-invalid")
    if spec.get("limitations") != list(LIMITATIONS):
        _fail("control-plane-load.report.limitations-invalid")
    failed = sum(item["status"] == "failed" for item in expected_checks)
    status = "qualified" if failed == 0 else "not-qualified"
    if spec.get("status") != status or spec.get("summary") != {
        "totalChecks": 12,
        "passedChecks": 12 - failed,
        "failedChecks": failed,
        "overallStatus": status,
    }:
        _fail("control-plane-load.report.summary-invalid")
    metadata_without_id = dict(metadata)
    report_id = metadata_without_id.pop("id", None)
    if (
        not isinstance(report_id, str)
        or report_id != _report_identifier(metadata_without_id, spec)
    ):
        _fail("control-plane-load.report.id-invalid")


def _write_report(path: Path, report: Mapping[str, object]) -> None:
    candidate = path.expanduser()
    if candidate.is_symlink():
        _fail("control-plane-load.output.invalid")
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
        _fail("control-plane-load.output.invalid")


def generate_report(
    *,
    allow_traffic: bool,
    base_url: str,
    token_file: Path,
    image_digest: str,
    output: Path,
    ca_file: Path | None = None,
    duration_seconds: int = 300,
    target_requests_per_second: int = 10,
    concurrency: int = 8,
    minimum_successful_request_basis_points: int = 9990,
    maximum_scheduler_miss_basis_points: int = 10,
    maximum_p95_latency_milliseconds: int = 2000,
    maximum_p99_latency_milliseconds: int = 5000,
    request_timeout_milliseconds: int = 2000,
    maximum_scheduler_lag_milliseconds: int = 1000,
    runner: Callable[..., RawLoadResult] = _run_fixed_rate,
) -> Mapping[str, Any]:
    if allow_traffic is not True:
        _fail("control-plane-load.traffic.explicit-enable-required")
    _validate_objective(
        duration_seconds=duration_seconds,
        target_requests_per_second=target_requests_per_second,
        concurrency=concurrency,
        minimum_successful_request_basis_points=minimum_successful_request_basis_points,
        maximum_scheduler_miss_basis_points=maximum_scheduler_miss_basis_points,
        maximum_p95_latency_milliseconds=maximum_p95_latency_milliseconds,
        maximum_p99_latency_milliseconds=maximum_p99_latency_milliseconds,
        request_timeout_milliseconds=request_timeout_milliseconds,
        maximum_scheduler_lag_milliseconds=maximum_scheduler_lag_milliseconds,
    )
    try:
        canonical_base, transport, target_digest = ingress._checked_url(
            "customer-ingress", base_url
        )
        revision, dirty = ingress._git_state()
        repository = ingress._repository_identity()
        identity = ingress._expected_identity(
            "customer-ingress",
            revision=revision,
            repository=repository,
            image_digest=image_digest,
        )
        token = ingress._load_token(token_file.expanduser().absolute())
    except ingress.IngressQualificationError:
        _fail("control-plane-load.input.invalid")
    if transport != "verified-https" or dirty:
        _fail("control-plane-load.source.dirty")
    if ca_file is not None:
        resolved_ca = ca_file.expanduser().resolve()
        if not resolved_ca.is_file():
            _fail("control-plane-load.tls.ca-invalid")
        ca_source = "custom"
    else:
        resolved_ca = None
        ca_source = "system"
    try:
        tls_context = ssl.create_default_context(
            cafile=str(resolved_ca) if resolved_ca else None
        )
    except (OSError, ssl.SSLError):
        _fail("control-plane-load.tls.ca-invalid")

    def requester_factory() -> Callable[[], ingress._Attempt]:
        opener = build_opener(
            ProxyHandler({}),
            ingress._DenyRedirects(),
            HTTPSHandler(context=tls_context),
        )

        def request() -> ingress._Attempt:
            attempted = ingress._request_json(
                opener,
                path_id="runtime-identity",
                url=canonical_base + "/v1/system/version",
                token=token,
                timeout_milliseconds=request_timeout_milliseconds,
                monotonic=time.monotonic,
            )
            validated = ingress._validate_path_document(attempted, identity)
            return ingress._Attempt(
                path_id=validated.path_id,
                success=validated.success,
                duration_milliseconds=validated.duration_milliseconds,
                failure_category=validated.failure_category,
            )

        return request

    raw = runner(
        duration_seconds=duration_seconds,
        target_requests_per_second=target_requests_per_second,
        concurrency=concurrency,
        maximum_scheduler_lag_milliseconds=maximum_scheduler_lag_milliseconds,
        requester_factory=requester_factory,
        clock=lambda: datetime.now(timezone.utc),
        monotonic=time.monotonic,
        sleeper=time.sleep,
    )
    report = build_report(
        revision=revision,
        repository=repository,
        image_digest=image_digest,
        target_binding_digest=target_digest,
        ca_source=ca_source,
        raw=raw,
        duration_seconds=duration_seconds,
        target_requests_per_second=target_requests_per_second,
        concurrency=concurrency,
        minimum_successful_request_basis_points=minimum_successful_request_basis_points,
        maximum_scheduler_miss_basis_points=maximum_scheduler_miss_basis_points,
        maximum_p95_latency_milliseconds=maximum_p95_latency_milliseconds,
        maximum_p99_latency_milliseconds=maximum_p99_latency_milliseconds,
        request_timeout_milliseconds=request_timeout_milliseconds,
        maximum_scheduler_lag_milliseconds=maximum_scheduler_lag_milliseconds,
    )
    _write_report(output, report)
    return report


def _load_report(path: Path) -> Mapping[str, Any]:
    try:
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > 4 * 1024 * 1024
        ):
            _fail("control-plane-load.report.unreadable")
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("control-plane-load.report.unreadable")
    return _mapping(report, "control-plane-load.report.invalid")


def verify_report(
    *,
    report_path: Path,
    base_url: str,
    image_digest: str,
    require_qualified: bool = False,
) -> Mapping[str, Any]:
    report = _load_report(report_path.expanduser())
    validate_report_document(report)
    try:
        _, transport, target_digest = ingress._checked_url(
            "customer-ingress", base_url
        )
        revision, dirty = ingress._git_state()
        repository = ingress._repository_identity()
        expected_identity = ingress._expected_identity(
            "customer-ingress",
            revision=revision,
            repository=repository,
            image_digest=image_digest,
        )
    except ingress.IngressQualificationError:
        _fail("control-plane-load.input.invalid")
    metadata = _mapping(report.get("metadata"), "control-plane-load.report.invalid")
    spec = _mapping(report.get("spec"), "control-plane-load.report.invalid")
    if (
        transport != "verified-https"
        or dirty
        or metadata.get("sourceRevision") != revision
        or spec.get("targetBindingDigest") != target_digest
        or spec.get("targetIdentity") != expected_identity
    ):
        _fail("control-plane-load.report.binding-mismatch")
    if require_qualified and spec.get("status") != "qualified":
        _fail("control-plane-load.report.not-qualified")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--allow-traffic", action="store_true")
    run.add_argument("--base-url", required=True)
    run.add_argument("--token-file", type=Path, required=True)
    run.add_argument("--image-digest", required=True)
    run.add_argument("--ca-file", type=Path)
    run.add_argument("--duration-seconds", type=int, default=300)
    run.add_argument("--target-requests-per-second", type=int, default=10)
    run.add_argument("--concurrency", type=int, default=8)
    run.add_argument("--minimum-successful-request-basis-points", type=int, default=9990)
    run.add_argument("--maximum-scheduler-miss-basis-points", type=int, default=10)
    run.add_argument("--maximum-p95-latency-milliseconds", type=int, default=2000)
    run.add_argument("--maximum-p99-latency-milliseconds", type=int, default=5000)
    run.add_argument("--request-timeout-milliseconds", type=int, default=2000)
    run.add_argument("--maximum-scheduler-lag-milliseconds", type=int, default=1000)
    run.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--report", type=Path, required=True)
    verify.add_argument("--base-url", required=True)
    verify.add_argument("--image-digest", required=True)
    verify.add_argument("--require-qualified", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "run":
            report = generate_report(
                allow_traffic=arguments.allow_traffic,
                base_url=arguments.base_url,
                token_file=arguments.token_file,
                image_digest=arguments.image_digest,
                output=arguments.output,
                ca_file=arguments.ca_file,
                duration_seconds=arguments.duration_seconds,
                target_requests_per_second=arguments.target_requests_per_second,
                concurrency=arguments.concurrency,
                minimum_successful_request_basis_points=arguments.minimum_successful_request_basis_points,
                maximum_scheduler_miss_basis_points=arguments.maximum_scheduler_miss_basis_points,
                maximum_p95_latency_milliseconds=arguments.maximum_p95_latency_milliseconds,
                maximum_p99_latency_milliseconds=arguments.maximum_p99_latency_milliseconds,
                request_timeout_milliseconds=arguments.request_timeout_milliseconds,
                maximum_scheduler_lag_milliseconds=arguments.maximum_scheduler_lag_milliseconds,
            )
            print(f"control-plane load qualification {report['spec']['status']}: {arguments.output}")
            return 0 if report["spec"]["status"] == "qualified" else 1
        report = verify_report(
            report_path=arguments.report,
            base_url=arguments.base_url,
            image_digest=arguments.image_digest,
            require_qualified=arguments.require_qualified,
        )
        print(f"control-plane load qualification verified: {report['spec']['status']}")
        return 0
    except ControlPlaneLoadQualificationError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
