#!/usr/bin/env python3
"""Qualify a bounded sustained customer core workload without server authority."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker

import qualify_customer_processing_continuity as processing
import qualify_ingress_availability as ingress


ROOT = Path(__file__).resolve().parents[1]
PROFILE_SCHEMA = (
    ROOT / "contracts/schemas/customer-sustained-workload-profile.schema.json"
)
REPORT_SCHEMA = (
    ROOT
    / "contracts/schemas/customer-sustained-workload-qualification-report.schema.json"
)
API_VERSION = "iip.platform/v1alpha1"
PROFILE_KIND = "CustomerSustainedWorkloadProfile"
REPORT_KIND = "CustomerSustainedWorkloadQualificationReport"
QUALIFICATION_LEVEL = "customer-sustained-core-workload-v1"
QUALIFICATION_BOUNDARY = "customer-environment-sustained-workload"
PROFILE_ID = re.compile(r"^cswp_[a-f0-9]{32}$")
REPORT_ID = re.compile(r"^cswq_[a-f0-9]{32}$")
MAX_PROFILE_BYTES = 128 * 1024
MAX_REPORT_BYTES = 4 * 1024 * 1024
MAX_PROBE_CYCLES = 500_000
MAX_WORKFLOWS = 5_000
CHECK_IDS = (
    "source-binding",
    "profile-review",
    "explicit-traffic-enable",
    "direct-no-proxy-no-redirect",
    "api-verified-https",
    "receiver-mutual-tls",
    "immutable-release-identity",
    "bounded-workload-volume",
    "observation-window",
    "probe-scheduler-attainment",
    "workflow-scheduler-attainment",
    "api-success-attainment",
    "api-p95-latency",
    "api-p99-latency",
    "receiver-success-attainment",
    "receiver-p95-latency",
    "receiver-p99-latency",
    "receiver-durable-intake",
    "workflow-completion-attainment",
    "workflow-p95-completion",
    "workflow-p99-completion",
    "minimized-output",
)
LIMITATIONS = (
    "bounded-synthetic-core-workload",
    "single-tenant-resource-and-target-pair",
    "api-identity-read-otlp-metric-and-investigation-only",
    "no-provider-call-or-inference-path",
    "no-failure-injection-or-database-failover",
    "node-zone-region-and-long-window-slo-not-qualified",
    "customer-workload-representativeness-not-approved",
)
FORBIDDEN_RETAINED_KEYS = frozenset(
    {
        "accessToken",
        "actorId",
        "apiBaseUrl",
        "authorization",
        "certificate",
        "credential",
        "endpoint",
        "metricName",
        "modelId",
        "otlpBaseUrl",
        "password",
        "prompt",
        "resourceUid",
        "response",
        "secret",
        "serviceName",
        "spanId",
        "tenantId",
        "token",
        "traceId",
    }
)


class CustomerSustainedWorkloadError(RuntimeError):
    """Stable failure for unsafe or irreproducible customer load evidence."""


def _fail(code: str) -> None:
    raise CustomerSustainedWorkloadError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        _fail("customer-sustained-workload.time.invalid")
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


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
        return _mapping(json.loads(path.read_text(encoding="utf-8")), code)
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail(code)


def _validate_schema(document: object, path: Path, code: str) -> None:
    errors = sorted(
        Draft202012Validator(
            _schema(path, code), format_checker=FormatChecker()
        ).iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        _fail(code)


def _read_json(
    path: Path, *, maximum_bytes: int, code: str, protected: bool = False
) -> Mapping[str, Any]:
    candidate = path.expanduser()
    descriptor = -1
    try:
        if candidate.is_symlink():
            _fail(code)
        descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or not 1 <= details.st_size <= maximum_bytes
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
            raw = handle.read(maximum_bytes + 1)
        if len(raw) > maximum_bytes:
            _fail(code)
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return _mapping(value, code)


def _profile_identifier(
    metadata_without_id: Mapping[str, Any], spec: Mapping[str, Any]
) -> str:
    return "cswp_" + hashlib.sha256(
        _canonical({"metadata": metadata_without_id, "spec": spec})
    ).hexdigest()[:32]


def _report_identifier(
    metadata_without_id: Mapping[str, Any], spec: Mapping[str, Any]
) -> str:
    return "cswq_" + hashlib.sha256(
        _canonical({"metadata": metadata_without_id, "spec": spec})
    ).hexdigest()[:32]


def _objective_values(objective: Mapping[str, Any]) -> tuple[int, int]:
    code = "customer-sustained-workload.objective.invalid"
    bounds = {
        "durationSeconds": (900, 14_400),
        "probeCyclesPerSecond": (1, 30),
        "probeConcurrency": (1, 64),
        "workflowSubmissionsPerMinute": (1, 20),
        "workflowConcurrency": (1, 32),
        "minimumApiSuccessBasisPoints": (9000, 10_000),
        "minimumReceiverSuccessBasisPoints": (9000, 10_000),
        "minimumWorkflowCompletionBasisPoints": (9000, 10_000),
        "maximumProbeSchedulerMissBasisPoints": (0, 1000),
        "maximumWorkflowSchedulerMissBasisPoints": (0, 1000),
        "maximumApiP95LatencyMilliseconds": (1, 60_000),
        "maximumApiP99LatencyMilliseconds": (1, 120_000),
        "maximumReceiverP95LatencyMilliseconds": (1, 60_000),
        "maximumReceiverP99LatencyMilliseconds": (1, 120_000),
        "maximumWorkflowP95CompletionMilliseconds": (1000, 300_000),
        "maximumWorkflowP99CompletionMilliseconds": (1000, 600_000),
        "requestTimeoutMilliseconds": (100, 30_000),
        "maximumSchedulerLagMilliseconds": (10, 5000),
        "maximumWorkflowCompletionMilliseconds": (1000, 600_000),
        "maximumProfileAgeSeconds": (3600, 7_776_000),
        "reportValiditySeconds": (300, 604_800),
    }
    values = {
        key: _integer(objective.get(key), minimum, maximum, code)
        for key, (minimum, maximum) in bounds.items()
    }
    if (
        values["probeConcurrency"] + values["workflowConcurrency"] > 96
        or values["maximumApiP99LatencyMilliseconds"]
        < values["maximumApiP95LatencyMilliseconds"]
        or values["maximumReceiverP99LatencyMilliseconds"]
        < values["maximumReceiverP95LatencyMilliseconds"]
        or values["maximumWorkflowP99CompletionMilliseconds"]
        < values["maximumWorkflowP95CompletionMilliseconds"]
        or values["maximumWorkflowCompletionMilliseconds"]
        < values["maximumWorkflowP99CompletionMilliseconds"]
    ):
        _fail(code)
    probe_count = values["durationSeconds"] * values["probeCyclesPerSecond"]
    workflow_count = (
        values["durationSeconds"] * values["workflowSubmissionsPerMinute"] // 60
    )
    if (
        not 1 <= probe_count <= MAX_PROBE_CYCLES
        or not 1 <= workflow_count <= MAX_WORKFLOWS
    ):
        _fail("customer-sustained-workload.objective.volume-exceeded")
    return probe_count, workflow_count


def validate_profile(profile: Mapping[str, Any]) -> None:
    code = "customer-sustained-workload.profile.invalid"
    _validate_schema(profile, PROFILE_SCHEMA, code)
    metadata = _mapping(profile.get("metadata"), code)
    spec = _mapping(profile.get("spec"), code)
    objective = _mapping(spec.get("objective"), code)
    reviewed = _parse_time(metadata.get("reviewedAt"), code)
    valid_until = _parse_time(metadata.get("validUntil"), code)
    without_id = dict(metadata)
    identifier = without_id.pop("id", None)
    try:
        processing._https_target(
            str(_mapping(spec.get("targets"), code).get("apiBaseUrl"))
        )
        processing._https_target(
            str(_mapping(spec.get("targets"), code).get("otlpBaseUrl"))
        )
    except processing.CustomerProcessingContinuityError:
        _fail(code)
    _objective_values(objective)
    if (
        profile.get("apiVersion") != API_VERSION
        or profile.get("kind") != PROFILE_KIND
        or spec.get("qualificationLevel") != QUALIFICATION_LEVEL
        or valid_until <= reviewed
        or valid_until - reviewed > timedelta(days=90)
        or not isinstance(identifier, str)
        or PROFILE_ID.fullmatch(identifier) is None
        or identifier != _profile_identifier(without_id, spec)
    ):
        _fail(code)


def load_profile(path: Path) -> Mapping[str, Any]:
    profile = _read_json(
        path,
        maximum_bytes=MAX_PROFILE_BYTES,
        code="customer-sustained-workload.profile.unreadable",
        protected=True,
    )
    validate_profile(profile)
    return profile


def _profile_is_current(profile: Mapping[str, Any], current: datetime) -> bool:
    metadata = _mapping(
        profile.get("metadata"), "customer-sustained-workload.profile.invalid"
    )
    objective = _mapping(
        _mapping(profile.get("spec"), "customer-sustained-workload.profile.invalid").get(
            "objective"
        ),
        "customer-sustained-workload.profile.invalid",
    )
    reviewed = _parse_time(
        metadata.get("reviewedAt"), "customer-sustained-workload.profile.invalid"
    )
    valid_until = _parse_time(
        metadata.get("validUntil"), "customer-sustained-workload.profile.invalid"
    )
    return (
        reviewed <= current
        and current
        < reviewed + timedelta(seconds=int(objective["maximumProfileAgeSeconds"]))
        and current < valid_until
    )


@dataclass(frozen=True)
class ProbeAttempt:
    api_success: bool
    api_latency_milliseconds: int
    receiver_success: bool
    receiver_latency_milliseconds: int


@dataclass(frozen=True)
class WorkflowAttempt:
    accepted: bool
    completed: bool
    poll_attempts: int
    completion_milliseconds: int


@dataclass(frozen=True)
class RawWorkloadResult:
    started_at: datetime
    completed_at: datetime
    actual_duration_milliseconds: int
    target_probe_cycles: int
    target_workflows: int
    probe_attempts: tuple[ProbeAttempt, ...]
    probe_scheduler_missed: int
    probe_scheduler_lags_milliseconds: tuple[int, ...]
    workflow_attempts: tuple[WorkflowAttempt, ...]
    workflow_scheduler_missed: int
    workflow_scheduler_lags_milliseconds: tuple[int, ...]


def _run_sustained_workload(
    *,
    duration_seconds: int,
    probe_cycles_per_second: int,
    probe_concurrency: int,
    workflow_submissions_per_minute: int,
    workflow_concurrency: int,
    maximum_scheduler_lag_milliseconds: int,
    maximum_workflow_completion_milliseconds: int,
    client_factory: Callable[[], processing.ProcessingClient],
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> RawWorkloadResult:
    target_probes = duration_seconds * probe_cycles_per_second
    target_workflows = duration_seconds * workflow_submissions_per_minute // 60
    if target_probes < 1 or target_workflows < 1:
        _fail("customer-sustained-workload.generator.target-invalid")
    probe_clients = [client_factory() for _ in range(probe_concurrency)]
    workflow_clients = [client_factory() for _ in range(workflow_concurrency)]
    start_event = threading.Event()
    start: dict[str, float] = {}

    def wait_for_slot(due: float) -> int:
        current = monotonic()
        if current < due:
            sleeper(due - current)
            current = monotonic()
        return max(0, math.ceil((current - due) * 1000))

    def probe_worker(
        worker_index: int,
    ) -> tuple[list[ProbeAttempt], int, list[int]]:
        client = probe_clients[worker_index]
        start_event.wait()
        attempts: list[ProbeAttempt] = []
        missed = 0
        lags: list[int] = []
        for index in range(worker_index, target_probes, probe_concurrency):
            due = start["monotonic"] + index / probe_cycles_per_second
            lag = wait_for_slot(due)
            lags.append(lag)
            if lag > maximum_scheduler_lag_milliseconds:
                missed += 1
                continue
            try:
                api_ok, api_latency, receiver_ok, receiver_latency = (
                    client.probe_cycle_measurement()
                )
                attempts.append(
                    ProbeAttempt(
                        api_success=api_ok,
                        api_latency_milliseconds=max(0, int(api_latency)),
                        receiver_success=receiver_ok,
                        receiver_latency_milliseconds=max(0, int(receiver_latency)),
                    )
                )
            except processing.CustomerProcessingContinuityError:
                attempts.append(ProbeAttempt(False, 0, False, 0))
        return attempts, missed, lags

    def workflow_worker(
        worker_index: int,
    ) -> tuple[list[WorkflowAttempt], int, list[int]]:
        client = workflow_clients[worker_index]
        start_event.wait()
        attempts: list[WorkflowAttempt] = []
        missed = 0
        lags: list[int] = []
        for index in range(worker_index, target_workflows, workflow_concurrency):
            due = start["monotonic"] + index * 60 / workflow_submissions_per_minute
            lag = wait_for_slot(due)
            lags.append(lag)
            if lag > maximum_scheduler_lag_milliseconds:
                missed += 1
                continue
            accepted = False
            started = monotonic()
            try:
                investigation_id, workflow_started = client.start_workflow(
                    f"load-{index:05d}"
                )
                accepted = True
                result = client.finish_workflow(
                    investigation_id,
                    workflow_started,
                    maximum_workflow_completion_milliseconds,
                    sleeper,
                )
                attempts.append(
                    WorkflowAttempt(
                        accepted=True,
                        completed=result.get("workflowCompleted") == 1
                        and result.get("workflowFailures") == 0,
                        poll_attempts=max(0, int(result.get("workflowPollAttempts", 0))),
                        completion_milliseconds=max(
                            0,
                            int(
                                result.get(
                                    "workflowCompletionMilliseconds",
                                    maximum_workflow_completion_milliseconds,
                                )
                            ),
                        ),
                    )
                )
            except processing.CustomerProcessingContinuityError:
                attempts.append(
                    WorkflowAttempt(
                        accepted=accepted,
                        completed=False,
                        poll_attempts=0,
                        completion_milliseconds=min(
                            maximum_workflow_completion_milliseconds,
                            max(0, math.ceil((monotonic() - started) * 1000)),
                        ),
                    )
                )
        return attempts, missed, lags

    executor = ThreadPoolExecutor(
        max_workers=probe_concurrency + workflow_concurrency,
        thread_name_prefix="iip-customer-sustained-workload",
    )
    probe_futures = [
        executor.submit(probe_worker, index) for index in range(probe_concurrency)
    ]
    workflow_futures = [
        executor.submit(workflow_worker, index)
        for index in range(workflow_concurrency)
    ]
    try:
        start["monotonic"] = monotonic()
        started_at = clock()
        start_event.set()
        probe_results = [future.result() for future in probe_futures]
        workflow_results = [future.result() for future in workflow_futures]
        completed_mono = monotonic()
        completed_at = clock()
    except Exception:
        _fail("customer-sustained-workload.generator.failed")
    finally:
        start_event.set()
        executor.shutdown(wait=True, cancel_futures=True)
    probe_attempts = tuple(item for result in probe_results for item in result[0])
    probe_missed = sum(result[1] for result in probe_results)
    probe_lags = tuple(item for result in probe_results for item in result[2])
    workflow_attempts = tuple(
        item for result in workflow_results for item in result[0]
    )
    workflow_missed = sum(result[1] for result in workflow_results)
    workflow_lags = tuple(item for result in workflow_results for item in result[2])
    if (
        len(probe_attempts) + probe_missed != target_probes
        or len(probe_lags) != target_probes
        or len(workflow_attempts) + workflow_missed != target_workflows
        or len(workflow_lags) != target_workflows
    ):
        _fail("customer-sustained-workload.generator.accounting-invalid")
    return RawWorkloadResult(
        started_at=started_at,
        completed_at=completed_at,
        actual_duration_milliseconds=max(
            1, math.ceil((completed_mono - start["monotonic"]) * 1000)
        ),
        target_probe_cycles=target_probes,
        target_workflows=target_workflows,
        probe_attempts=probe_attempts,
        probe_scheduler_missed=probe_missed,
        probe_scheduler_lags_milliseconds=probe_lags,
        workflow_attempts=workflow_attempts,
        workflow_scheduler_missed=workflow_missed,
        workflow_scheduler_lags_milliseconds=workflow_lags,
    )


def _percentile(values: Sequence[int], percentile: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percentile / 100) - 1)]


def _latency(values: Sequence[int]) -> dict[str, int | None]:
    return {
        "p50Milliseconds": _percentile(values, 50),
        "p95Milliseconds": _percentile(values, 95),
        "p99Milliseconds": _percentile(values, 99),
        "maximumMilliseconds": max(values) if values else None,
    }


def _measurements(
    raw: RawWorkloadResult, *, profile_reviewed_at: str
) -> dict[str, Any]:
    scheduled_probes = raw.target_probe_cycles
    attempted_probes = len(raw.probe_attempts)
    api_successes = sum(item.api_success for item in raw.probe_attempts)
    receiver_successes = sum(item.receiver_success for item in raw.probe_attempts)
    api_latencies = [
        item.api_latency_milliseconds for item in raw.probe_attempts if item.api_success
    ]
    receiver_latencies = [
        item.receiver_latency_milliseconds
        for item in raw.probe_attempts
        if item.receiver_success
    ]
    scheduled_workflows = raw.target_workflows
    attempted_workflows = len(raw.workflow_attempts)
    accepted = sum(item.accepted for item in raw.workflow_attempts)
    completed = sum(item.completed for item in raw.workflow_attempts)
    workflow_latencies = [
        item.completion_milliseconds
        for item in raw.workflow_attempts
        if item.completed
    ]
    return {
        "profileReviewedAt": profile_reviewed_at,
        "startedAt": _timestamp(raw.started_at),
        "completedAt": _timestamp(raw.completed_at),
        "actualDurationMilliseconds": raw.actual_duration_milliseconds,
        "probes": {
            "scheduledCycles": scheduled_probes,
            "attemptedCycles": attempted_probes,
            "schedulerMissedCycles": raw.probe_scheduler_missed,
            "schedulerMissBasisPoints": raw.probe_scheduler_missed
            * 10_000
            // scheduled_probes,
            "schedulerLagP95Milliseconds": _percentile(
                raw.probe_scheduler_lags_milliseconds, 95
            ),
            "api": {
                "attempts": attempted_probes,
                "successes": api_successes,
                "failures": attempted_probes - api_successes,
                "successBasisPoints": api_successes * 10_000 // scheduled_probes,
                "latency": _latency(api_latencies),
            },
            "receiver": {
                "attempts": attempted_probes,
                "successes": receiver_successes,
                "failures": attempted_probes - receiver_successes,
                "successBasisPoints": receiver_successes
                * 10_000
                // scheduled_probes,
                "latency": _latency(receiver_latencies),
            },
        },
        "workflows": {
            "scheduledWorkflows": scheduled_workflows,
            "attemptedSubmissions": attempted_workflows,
            "acceptedSubmissions": accepted,
            "submissionFailures": attempted_workflows - accepted,
            "completedWorkflows": completed,
            "terminalFailures": accepted - completed,
            "schedulerMissedWorkflows": raw.workflow_scheduler_missed,
            "completionBasisPoints": completed * 10_000 // scheduled_workflows,
            "schedulerMissBasisPoints": raw.workflow_scheduler_missed
            * 10_000
            // scheduled_workflows,
            "schedulerLagP95Milliseconds": _percentile(
                raw.workflow_scheduler_lags_milliseconds, 95
            ),
            "pollAttempts": sum(
                item.poll_attempts for item in raw.workflow_attempts
            ),
            "completionLatency": _latency(workflow_latencies),
        },
    }


def _check(identifier: str, passed: bool, error: str) -> dict[str, str]:
    result = {"id": identifier, "status": "passed" if passed else "failed"}
    if not passed:
        result["errorCode"] = error
    return result


def _valid_latency(latency: Mapping[str, Any], successes: int) -> bool:
    values = [
        latency.get("p50Milliseconds"),
        latency.get("p95Milliseconds"),
        latency.get("p99Milliseconds"),
        latency.get("maximumMilliseconds"),
    ]
    if successes == 0:
        return all(value is None for value in values)
    return all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in values
    ) and values == sorted(values)


def _validate_measurements(
    objective: Mapping[str, Any], measurements: Mapping[str, Any]
) -> None:
    code = "customer-sustained-workload.report.measurements-invalid"
    target_probes, target_workflows = _objective_values(objective)
    probes = _mapping(measurements.get("probes"), code)
    workflows = _mapping(measurements.get("workflows"), code)
    if probes.get("scheduledCycles") != target_probes:
        _fail(code)
    attempted = _integer(probes.get("attemptedCycles"), 0, target_probes, code)
    missed = _integer(probes.get("schedulerMissedCycles"), 0, target_probes, code)
    if attempted + missed != target_probes:
        _fail(code)
    if probes.get("schedulerMissBasisPoints") != missed * 10_000 // target_probes:
        _fail(code)
    _integer(probes.get("schedulerLagP95Milliseconds"), 0, 1_000_000, code)
    for path in ("api", "receiver"):
        values = _mapping(probes.get(path), code)
        successes = _integer(values.get("successes"), 0, attempted, code)
        failures = _integer(values.get("failures"), 0, attempted, code)
        if (
            values.get("attempts") != attempted
            or successes + failures != attempted
            or values.get("successBasisPoints")
            != successes * 10_000 // target_probes
            or not _valid_latency(_mapping(values.get("latency"), code), successes)
        ):
            _fail(code)
    if workflows.get("scheduledWorkflows") != target_workflows:
        _fail(code)
    workflow_attempts = _integer(
        workflows.get("attemptedSubmissions"), 0, target_workflows, code
    )
    accepted = _integer(
        workflows.get("acceptedSubmissions"), 0, workflow_attempts, code
    )
    submission_failures = _integer(
        workflows.get("submissionFailures"), 0, workflow_attempts, code
    )
    completed = _integer(workflows.get("completedWorkflows"), 0, accepted, code)
    terminal_failures = _integer(
        workflows.get("terminalFailures"), 0, accepted, code
    )
    workflow_missed = _integer(
        workflows.get("schedulerMissedWorkflows"), 0, target_workflows, code
    )
    if (
        workflow_attempts + workflow_missed != target_workflows
        or accepted + submission_failures != workflow_attempts
        or completed + terminal_failures != accepted
        or workflows.get("completionBasisPoints")
        != completed * 10_000 // target_workflows
        or workflows.get("schedulerMissBasisPoints")
        != workflow_missed * 10_000 // target_workflows
        or not _valid_latency(
            _mapping(workflows.get("completionLatency"), code), completed
        )
    ):
        _fail(code)
    _integer(workflows.get("schedulerLagP95Milliseconds"), 0, 1_000_000, code)
    _integer(workflows.get("pollAttempts"), 0, 1_000_000, code)
    started = _parse_time(measurements.get("startedAt"), code)
    completed_at = _parse_time(measurements.get("completedAt"), code)
    actual = _integer(
        measurements.get("actualDurationMilliseconds"), 1, 15_100_000, code
    )
    duration = int(objective["durationSeconds"])
    minimum_window = duration * 1000 - math.ceil(
        1000 / int(objective["probeCyclesPerSecond"])
    )
    maximum_window = (
        duration * 1000
        + int(objective["maximumWorkflowCompletionMilliseconds"])
        + int(objective["requestTimeoutMilliseconds"])
        + int(objective["maximumSchedulerLagMilliseconds"])
        + 10_000
    )
    if started > completed_at or not minimum_window <= actual <= maximum_window:
        _fail(code)


def _derived_checks(
    *,
    metadata: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> list[dict[str, str]]:
    subject = _mapping(
        spec.get("subject"), "customer-sustained-workload.report.invalid"
    )
    objective = _mapping(
        spec.get("objective"), "customer-sustained-workload.report.invalid"
    )
    environment = _mapping(
        spec.get("environment"), "customer-sustained-workload.report.invalid"
    )
    measurements = _mapping(
        spec.get("measurements"), "customer-sustained-workload.report.invalid"
    )
    probes = _mapping(
        measurements.get("probes"), "customer-sustained-workload.report.invalid"
    )
    api = _mapping(probes.get("api"), "customer-sustained-workload.report.invalid")
    receiver = _mapping(
        probes.get("receiver"), "customer-sustained-workload.report.invalid"
    )
    workflows = _mapping(
        measurements.get("workflows"), "customer-sustained-workload.report.invalid"
    )
    api_latency = _mapping(
        api.get("latency"), "customer-sustained-workload.report.invalid"
    )
    receiver_latency = _mapping(
        receiver.get("latency"), "customer-sustained-workload.report.invalid"
    )
    workflow_latency = _mapping(
        workflows.get("completionLatency"),
        "customer-sustained-workload.report.invalid",
    )
    reviewed = _parse_time(
        measurements.get("profileReviewedAt"),
        "customer-sustained-workload.report.invalid",
    )
    started = _parse_time(
        measurements.get("startedAt"), "customer-sustained-workload.report.invalid"
    )
    duration = int(objective["durationSeconds"])
    actual = int(measurements["actualDurationMilliseconds"])
    minimum_window = duration * 1000 - math.ceil(
        1000 / int(objective["probeCyclesPerSecond"])
    )
    maximum_window = (
        duration * 1000
        + int(objective["maximumWorkflowCompletionMilliseconds"])
        + int(objective["requestTimeoutMilliseconds"])
        + int(objective["maximumSchedulerLagMilliseconds"])
        + 10_000
    )

    def latency_passes(
        values: Mapping[str, Any], field: str, objective_field: str
    ) -> bool:
        value = values.get(field)
        return (
            isinstance(value, int)
            and not isinstance(value, bool)
            and value <= int(objective[objective_field])
        )

    target_probes, target_workflows = _objective_values(objective)
    return [
        _check(
            "source-binding",
            metadata.get("sourceDirty") is False
            and metadata.get("sourceRevision") == subject.get("sourceRevision"),
            "customer-sustained-workload.source.invalid",
        ),
        _check(
            "profile-review",
            reviewed <= started
            and started
            < reviewed
            + timedelta(seconds=int(objective["maximumProfileAgeSeconds"])),
            "customer-sustained-workload.profile.expired",
        ),
        _check(
            "explicit-traffic-enable",
            True,
            "customer-sustained-workload.traffic.enable-required",
        ),
        _check(
            "direct-no-proxy-no-redirect",
            environment.get("proxyMode") == "disabled"
            and environment.get("redirectMode") == "deny"
            and environment.get("connectionMode") == "close-per-request",
            "customer-sustained-workload.client.invalid",
        ),
        _check(
            "api-verified-https",
            environment.get("apiTransport") == "verified-https",
            "customer-sustained-workload.api.transport-invalid",
        ),
        _check(
            "receiver-mutual-tls",
            environment.get("otlpTransport") == "mutual-tls-https",
            "customer-sustained-workload.receiver.transport-invalid",
        ),
        _check(
            "immutable-release-identity",
            api.get("successes", 0) > 0
            and re.fullmatch(r"sha256:[a-f0-9]{64}", str(subject.get("imageDigest")))
            is not None,
            "customer-sustained-workload.release.unobserved",
        ),
        _check(
            "bounded-workload-volume",
            probes.get("scheduledCycles") == target_probes <= MAX_PROBE_CYCLES
            and workflows.get("scheduledWorkflows")
            == target_workflows
            <= MAX_WORKFLOWS,
            "customer-sustained-workload.volume.invalid",
        ),
        _check(
            "observation-window",
            minimum_window <= actual <= maximum_window,
            "customer-sustained-workload.window.invalid",
        ),
        _check(
            "probe-scheduler-attainment",
            probes.get("schedulerMissBasisPoints", 10_001)
            <= objective["maximumProbeSchedulerMissBasisPoints"],
            "customer-sustained-workload.probe.scheduler-missed",
        ),
        _check(
            "workflow-scheduler-attainment",
            workflows.get("schedulerMissBasisPoints", 10_001)
            <= objective["maximumWorkflowSchedulerMissBasisPoints"],
            "customer-sustained-workload.workflow.scheduler-missed",
        ),
        _check(
            "api-success-attainment",
            api.get("successBasisPoints", -1)
            >= objective["minimumApiSuccessBasisPoints"],
            "customer-sustained-workload.api.objective-missed",
        ),
        _check(
            "api-p95-latency",
            latency_passes(
                api_latency, "p95Milliseconds", "maximumApiP95LatencyMilliseconds"
            ),
            "customer-sustained-workload.api.p95-missed",
        ),
        _check(
            "api-p99-latency",
            latency_passes(
                api_latency, "p99Milliseconds", "maximumApiP99LatencyMilliseconds"
            ),
            "customer-sustained-workload.api.p99-missed",
        ),
        _check(
            "receiver-success-attainment",
            receiver.get("successBasisPoints", -1)
            >= objective["minimumReceiverSuccessBasisPoints"],
            "customer-sustained-workload.receiver.objective-missed",
        ),
        _check(
            "receiver-p95-latency",
            latency_passes(
                receiver_latency,
                "p95Milliseconds",
                "maximumReceiverP95LatencyMilliseconds",
            ),
            "customer-sustained-workload.receiver.p95-missed",
        ),
        _check(
            "receiver-p99-latency",
            latency_passes(
                receiver_latency,
                "p99Milliseconds",
                "maximumReceiverP99LatencyMilliseconds",
            ),
            "customer-sustained-workload.receiver.p99-missed",
        ),
        _check(
            "receiver-durable-intake",
            receiver.get("successes", 0) > 0
            and environment.get("receiverSuccessBoundary")
            == "postgresql-commit-before-http-200",
            "customer-sustained-workload.receiver.persistence-unobserved",
        ),
        _check(
            "workflow-completion-attainment",
            workflows.get("completionBasisPoints", -1)
            >= objective["minimumWorkflowCompletionBasisPoints"],
            "customer-sustained-workload.workflow.objective-missed",
        ),
        _check(
            "workflow-p95-completion",
            latency_passes(
                workflow_latency,
                "p95Milliseconds",
                "maximumWorkflowP95CompletionMilliseconds",
            ),
            "customer-sustained-workload.workflow.p95-missed",
        ),
        _check(
            "workflow-p99-completion",
            latency_passes(
                workflow_latency,
                "p99Milliseconds",
                "maximumWorkflowP99CompletionMilliseconds",
            ),
            "customer-sustained-workload.workflow.p99-missed",
        ),
        _check(
            "minimized-output",
            True,
            "customer-sustained-workload.output.not-minimized",
        ),
    ]


def _has_forbidden_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in FORBIDDEN_RETAINED_KEYS or _has_forbidden_key(nested)
            for key, nested in value.items()
        )
    if isinstance(value, list):
        return any(_has_forbidden_key(item) for item in value)
    return False


def build_report(
    *,
    profile: Mapping[str, Any],
    raw: RawWorkloadResult,
    api_target_digest: str,
    otlp_target_digest: str,
    api_ca_source: str,
    otlp_ca_source: str,
) -> Mapping[str, Any]:
    validate_profile(profile)
    metadata = _mapping(
        profile.get("metadata"), "customer-sustained-workload.profile.invalid"
    )
    profile_spec = _mapping(
        profile.get("spec"), "customer-sustained-workload.profile.invalid"
    )
    release = _mapping(
        profile_spec.get("release"), "customer-sustained-workload.profile.invalid"
    )
    objective = _mapping(
        profile_spec.get("objective"), "customer-sustained-workload.profile.invalid"
    )
    expected_probes, expected_workflows = _objective_values(objective)
    if (
        raw.target_probe_cycles != expected_probes
        or raw.target_workflows != expected_workflows
    ):
        _fail("customer-sustained-workload.measurement.target-invalid")
    measurements = _measurements(
        raw, profile_reviewed_at=str(metadata["reviewedAt"])
    )
    report_spec: dict[str, Any] = {
        "status": "not-qualified",
        "qualificationLevel": QUALIFICATION_LEVEL,
        "qualificationBoundary": QUALIFICATION_BOUNDARY,
        "subject": dict(release),
        "bindings": {
            "profileDigest": _digest(profile),
            "apiTargetBindingDigest": api_target_digest,
            "otlpTargetBindingDigest": otlp_target_digest,
        },
        "objective": dict(objective),
        "environment": {
            "platform": f"{platform.system().lower()}/{platform.machine().lower()}",
            "pythonVersion": platform.python_version(),
            "apiTransport": "verified-https",
            "otlpTransport": "mutual-tls-https",
            "apiCaSource": api_ca_source,
            "otlpCaSource": otlp_ca_source,
            "proxyMode": "disabled",
            "redirectMode": "deny",
            "connectionMode": "close-per-request",
            "scheduler": "bounded-dual-fixed-rate-v1",
            "receiverSuccessBoundary": "postgresql-commit-before-http-200",
        },
        "measurements": measurements,
    }
    generated = raw.completed_at.astimezone(timezone.utc).replace(microsecond=0)
    report_metadata: dict[str, Any] = {
        "generatedAt": _timestamp(generated),
        "validUntil": _timestamp(
            min(
                _parse_time(
                    metadata["validUntil"],
                    "customer-sustained-workload.profile.invalid",
                ),
                _parse_time(
                    metadata["reviewedAt"],
                    "customer-sustained-workload.profile.invalid",
                )
                + timedelta(seconds=int(objective["maximumProfileAgeSeconds"])),
                generated
                + timedelta(seconds=int(objective["reportValiditySeconds"])),
            )
        ),
        "sourceRevision": release["sourceRevision"],
        "sourceDirty": False,
    }
    if _parse_time(
        report_metadata["validUntil"], "customer-sustained-workload.profile.invalid"
    ) <= generated:
        _fail("customer-sustained-workload.profile.expired")
    checks = _derived_checks(metadata=report_metadata, spec=report_spec)
    failed = sum(item["status"] == "failed" for item in checks)
    status = "qualified" if failed == 0 else "not-qualified"
    probes = _mapping(measurements["probes"], "customer-sustained-workload.report.invalid")
    workflows = _mapping(
        measurements["workflows"], "customer-sustained-workload.report.invalid"
    )
    api = _mapping(probes["api"], "customer-sustained-workload.report.invalid")
    receiver = _mapping(
        probes["receiver"], "customer-sustained-workload.report.invalid"
    )
    report_spec.update(
        {
            "status": status,
            "checks": checks,
            "limitations": list(LIMITATIONS),
            "summary": {
                "totalChecks": len(CHECK_IDS),
                "passedChecks": len(CHECK_IDS) - failed,
                "failedChecks": failed,
                "scheduledProbeCycles": probes["scheduledCycles"],
                "successfulApiRequests": api["successes"],
                "successfulReceiverWrites": receiver["successes"],
                "scheduledWorkflows": workflows["scheduledWorkflows"],
                "completedWorkflows": workflows["completedWorkflows"],
                "overallStatus": status,
            },
        }
    )
    if _has_forbidden_key(report_spec):
        _fail("customer-sustained-workload.output.not-minimized")
    report_metadata["id"] = _report_identifier(report_metadata, report_spec)
    report = {
        "apiVersion": API_VERSION,
        "kind": REPORT_KIND,
        "metadata": report_metadata,
        "spec": report_spec,
    }
    validate_report_document(report)
    return report


def validate_report_document(report: Mapping[str, Any]) -> None:
    code = "customer-sustained-workload.report.invalid"
    _validate_schema(report, REPORT_SCHEMA, code)
    metadata = _mapping(report.get("metadata"), code)
    spec = _mapping(report.get("spec"), code)
    subject = _mapping(spec.get("subject"), code)
    objective = _mapping(spec.get("objective"), code)
    measurements = _mapping(spec.get("measurements"), code)
    checks = spec.get("checks")
    summary = _mapping(spec.get("summary"), code)
    if not isinstance(checks, list):
        _fail(code)
    _validate_measurements(objective, measurements)
    expected_checks = _derived_checks(metadata=metadata, spec=spec)
    failed = sum(item["status"] == "failed" for item in expected_checks)
    status = "qualified" if failed == 0 else "not-qualified"
    probes = _mapping(measurements.get("probes"), code)
    workflows = _mapping(measurements.get("workflows"), code)
    api = _mapping(probes.get("api"), code)
    receiver = _mapping(probes.get("receiver"), code)
    expected_summary = {
        "totalChecks": len(CHECK_IDS),
        "passedChecks": len(CHECK_IDS) - failed,
        "failedChecks": failed,
        "scheduledProbeCycles": probes["scheduledCycles"],
        "successfulApiRequests": api["successes"],
        "successfulReceiverWrites": receiver["successes"],
        "scheduledWorkflows": workflows["scheduledWorkflows"],
        "completedWorkflows": workflows["completedWorkflows"],
        "overallStatus": status,
    }
    generated = _parse_time(metadata.get("generatedAt"), code)
    valid_until = _parse_time(metadata.get("validUntil"), code)
    reviewed = _parse_time(measurements.get("profileReviewedAt"), code)
    without_id = dict(metadata)
    identifier = without_id.pop("id", None)
    if (
        report.get("apiVersion") != API_VERSION
        or report.get("kind") != REPORT_KIND
        or spec.get("qualificationLevel") != QUALIFICATION_LEVEL
        or spec.get("qualificationBoundary") != QUALIFICATION_BOUNDARY
        or metadata.get("sourceRevision") != subject.get("sourceRevision")
        or metadata.get("generatedAt") != measurements.get("completedAt")
        or list(checks) != expected_checks
        or tuple(item.get("id") for item in checks) != CHECK_IDS
        or spec.get("limitations") != list(LIMITATIONS)
        or spec.get("status") != status
        or summary != expected_summary
        or valid_until <= generated
        or valid_until
        > generated + timedelta(seconds=int(objective["reportValiditySeconds"]))
        or valid_until
        > reviewed + timedelta(seconds=int(objective["maximumProfileAgeSeconds"]))
        or _has_forbidden_key(report)
        or not isinstance(identifier, str)
        or REPORT_ID.fullmatch(identifier) is None
        or identifier != _report_identifier(without_id, spec)
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
        _fail("customer-sustained-workload.source.unavailable")
    return revision, dirty


def _repository_identity() -> Mapping[str, str]:
    try:
        return ingress._repository_identity()
    except ingress.IngressQualificationError:
        _fail("customer-sustained-workload.release.invalid")


def _expected_release(
    revision: str, repository: Mapping[str, str], image: str
) -> dict[str, str]:
    return {
        "applicationVersion": repository["applicationVersion"],
        "chartVersion": repository["chartVersion"],
        "contractsApiVersion": API_VERSION,
        "requiredMigration": repository["requiredMigration"],
        "sourceRevision": revision,
        "imageDigest": image,
    }


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    destination = path.expanduser().absolute()
    if destination.is_symlink():
        _fail("customer-sustained-workload.output.invalid")
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
        _fail("customer-sustained-workload.output.invalid")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _distinct(paths: Sequence[Path | None]) -> None:
    selected = [path.expanduser().absolute() for path in paths if path is not None]
    if len(selected) != len(set(selected)):
        _fail("customer-sustained-workload.paths.overlap")


def generate_report(
    *,
    allow_traffic: bool,
    profile_path: Path,
    api_token_file: Path,
    otlp_token_file: Path,
    otlp_client_cert_file: Path,
    otlp_client_key_file: Path,
    output: Path,
    api_ca_file: Path | None = None,
    otlp_ca_file: Path | None = None,
    runner: Callable[..., RawWorkloadResult] = _run_sustained_workload,
    client_factory: Callable[..., processing.ProcessingClient] = processing.ProcessingClient,
    now: datetime | None = None,
) -> Mapping[str, Any]:
    if allow_traffic is not True:
        _fail("customer-sustained-workload.traffic.enable-required")
    _distinct(
        (
            profile_path,
            api_token_file,
            otlp_token_file,
            otlp_client_cert_file,
            otlp_client_key_file,
            output,
            api_ca_file,
            otlp_ca_file,
        )
    )
    profile = load_profile(profile_path)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not _profile_is_current(profile, current):
        _fail("customer-sustained-workload.profile.expired")
    revision, dirty = _source_identity()
    if dirty:
        _fail("customer-sustained-workload.source.dirty")
    spec = _mapping(
        profile.get("spec"), "customer-sustained-workload.profile.invalid"
    )
    release = _mapping(
        spec.get("release"), "customer-sustained-workload.profile.invalid"
    )
    targets = _mapping(
        spec.get("targets"), "customer-sustained-workload.profile.invalid"
    )
    objective = _mapping(
        spec.get("objective"), "customer-sustained-workload.profile.invalid"
    )
    repository = _repository_identity()
    if dict(release) != _expected_release(
        revision, repository, str(release.get("imageDigest"))
    ):
        _fail("customer-sustained-workload.release.mismatch")

    def create_client() -> processing.ProcessingClient:
        try:
            return client_factory(
                api_base_url=str(targets["apiBaseUrl"]),
                api_token_file=api_token_file,
                otlp_base_url=str(targets["otlpBaseUrl"]),
                otlp_token_file=otlp_token_file,
                otlp_client_cert_file=otlp_client_cert_file,
                otlp_client_key_file=otlp_client_key_file,
                profile=profile,
                subject=release,
                api_ca_file=api_ca_file,
                otlp_ca_file=otlp_ca_file,
                request_timeout_milliseconds=int(
                    objective["requestTimeoutMilliseconds"]
                ),
                qualification_id="customer-sustained-workload",
            )
        except processing.CustomerProcessingContinuityError:
            _fail("customer-sustained-workload.client.invalid")

    preflight = create_client()
    try:
        preflight.validate_resource()
    except processing.CustomerProcessingContinuityError:
        _fail("customer-sustained-workload.resource.invalid")
    raw = runner(
        duration_seconds=int(objective["durationSeconds"]),
        probe_cycles_per_second=int(objective["probeCyclesPerSecond"]),
        probe_concurrency=int(objective["probeConcurrency"]),
        workflow_submissions_per_minute=int(
            objective["workflowSubmissionsPerMinute"]
        ),
        workflow_concurrency=int(objective["workflowConcurrency"]),
        maximum_scheduler_lag_milliseconds=int(
            objective["maximumSchedulerLagMilliseconds"]
        ),
        maximum_workflow_completion_milliseconds=int(
            objective["maximumWorkflowCompletionMilliseconds"]
        ),
        client_factory=create_client,
    )
    report = build_report(
        profile=profile,
        raw=raw,
        api_target_digest=preflight.api_target_digest,
        otlp_target_digest=preflight.otlp_target_digest,
        api_ca_source=preflight.api_ca_source,
        otlp_ca_source=preflight.otlp_ca_source,
    )
    _write_report(output, report)
    return report


def verify_report(
    *,
    profile_path: Path,
    report_path: Path,
    require_qualified: bool = False,
    now: datetime | None = None,
) -> Mapping[str, Any]:
    _distinct((profile_path, report_path))
    profile = load_profile(profile_path)
    report = _read_json(
        report_path,
        maximum_bytes=MAX_REPORT_BYTES,
        code="customer-sustained-workload.report.unreadable",
    )
    validate_report_document(report)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not _profile_is_current(profile, current):
        _fail("customer-sustained-workload.profile.expired")
    revision, dirty = _source_identity()
    if dirty:
        _fail("customer-sustained-workload.source.dirty")
    repository = _repository_identity()
    spec = _mapping(
        profile.get("spec"), "customer-sustained-workload.profile.invalid"
    )
    release = _mapping(
        spec.get("release"), "customer-sustained-workload.profile.invalid"
    )
    targets = _mapping(
        spec.get("targets"), "customer-sustained-workload.profile.invalid"
    )
    try:
        _, api_digest = processing._https_target(str(targets["apiBaseUrl"]))
        _, otlp_digest = processing._https_target(str(targets["otlpBaseUrl"]))
    except processing.CustomerProcessingContinuityError:
        _fail("customer-sustained-workload.profile.invalid")
    expected_release = _expected_release(
        revision, repository, str(release.get("imageDigest"))
    )
    report_spec = _mapping(
        report.get("spec"), "customer-sustained-workload.report.invalid"
    )
    bindings = _mapping(
        report_spec.get("bindings"), "customer-sustained-workload.report.invalid"
    )
    report_metadata = _mapping(
        report.get("metadata"), "customer-sustained-workload.report.invalid"
    )
    profile_metadata = _mapping(
        profile.get("metadata"), "customer-sustained-workload.profile.invalid"
    )
    if (
        dict(release) != expected_release
        or report_spec.get("subject") != expected_release
        or report_spec.get("objective") != spec.get("objective")
        or bindings.get("profileDigest") != _digest(profile)
        or bindings.get("apiTargetBindingDigest") != api_digest
        or bindings.get("otlpTargetBindingDigest") != otlp_digest
        or _parse_time(
            report_metadata.get("validUntil"),
            "customer-sustained-workload.report.invalid",
        )
        > _parse_time(
            profile_metadata.get("validUntil"),
            "customer-sustained-workload.profile.invalid",
        )
    ):
        _fail("customer-sustained-workload.report.binding-mismatch")
    if current >= _parse_time(
        report_metadata.get("validUntil"),
        "customer-sustained-workload.report.invalid",
    ):
        _fail("customer-sustained-workload.report.expired")
    if require_qualified and report_spec.get("status") != "qualified":
        _fail("customer-sustained-workload.report.not-qualified")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    profile_id = commands.add_parser("profile-id")
    profile_id.add_argument("--profile", type=Path, required=True)
    generate = commands.add_parser("generate")
    generate.add_argument("--profile", type=Path, required=True)
    generate.add_argument("--api-token-file", type=Path, required=True)
    generate.add_argument("--otlp-token-file", type=Path, required=True)
    generate.add_argument("--otlp-client-cert-file", type=Path, required=True)
    generate.add_argument("--otlp-client-key-file", type=Path, required=True)
    generate.add_argument("--api-ca-file", type=Path)
    generate.add_argument("--otlp-ca-file", type=Path)
    generate.add_argument("--output", type=Path, required=True)
    generate.add_argument("--allow-traffic", action="store_true")
    verify = commands.add_parser("verify")
    verify.add_argument("--profile", type=Path, required=True)
    verify.add_argument("--report", type=Path, required=True)
    verify.add_argument("--require-qualified", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "profile-id":
            profile = _read_json(
                arguments.profile,
                maximum_bytes=MAX_PROFILE_BYTES,
                code="customer-sustained-workload.profile.unreadable",
                protected=True,
            )
            metadata = _mapping(
                profile.get("metadata"), "customer-sustained-workload.profile.invalid"
            )
            spec = _mapping(
                profile.get("spec"), "customer-sustained-workload.profile.invalid"
            )
            without_id = dict(metadata)
            without_id.pop("id", None)
            identifier = _profile_identifier(without_id, spec)
            candidate = dict(profile)
            candidate["metadata"] = {**without_id, "id": identifier}
            validate_profile(candidate)
            print(identifier)
            return 0
        if arguments.command == "generate":
            report = generate_report(
                allow_traffic=arguments.allow_traffic,
                profile_path=arguments.profile,
                api_token_file=arguments.api_token_file,
                otlp_token_file=arguments.otlp_token_file,
                otlp_client_cert_file=arguments.otlp_client_cert_file,
                otlp_client_key_file=arguments.otlp_client_key_file,
                output=arguments.output,
                api_ca_file=arguments.api_ca_file,
                otlp_ca_file=arguments.otlp_ca_file,
            )
            print(
                f"customer sustained workload {report['spec']['status']}: "
                f"{arguments.output}"
            )
            return 0 if report["spec"]["status"] == "qualified" else 1
        verify_report(
            profile_path=arguments.profile,
            report_path=arguments.report,
            require_qualified=arguments.require_qualified,
        )
        print("customer sustained workload report verified")
    except CustomerSustainedWorkloadError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
