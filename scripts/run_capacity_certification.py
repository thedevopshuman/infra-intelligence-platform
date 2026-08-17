#!/usr/bin/env python3
"""Measure PostgreSQL investigation admission, isolation, and claim capacity."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import platform
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter
from typing import Callable, Iterable, Mapping, TypeVar

import psycopg


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from iip import __version__ as application_version  # noqa: E402
from iip.adapters.postgres import (  # noqa: E402
    PostgresOperationalStore,
    PostgresResourceStore,
)
from iip.adapters.postgres.store import SCHEMA_MIGRATIONS  # noqa: E402
from iip.application.investigation_dispatch import (  # noqa: E402
    InvestigationDispatchService,
)
from iip.application.ports import ActorContext, PersistenceError  # noqa: E402
import validate_schemas  # noqa: E402


T = TypeVar("T")
EXAMPLES = ROOT / "contracts" / "examples"
SCHEMAS = ROOT / "contracts" / "schemas"


@dataclass(frozen=True)
class CapacityProfile:
    tenant_count: int = 128
    jobs_per_tenant: int = 4
    parallel_clients: int = 16
    maximum_operation_p95_milliseconds: int = 5_000
    maximum_suite_duration_milliseconds: int = 120_000

    def validate(self) -> None:
        values = (
            self.tenant_count,
            self.jobs_per_tenant,
            self.parallel_clients,
            self.maximum_operation_p95_milliseconds,
            self.maximum_suite_duration_milliseconds,
        )
        if (
            any(isinstance(value, bool) or not isinstance(value, int) for value in values)
            or not 2 <= self.tenant_count <= 10_000
            or not 2 <= self.jobs_per_tenant <= 1_000
            or not 1 <= self.parallel_clients <= 64
            or not 1 <= self.maximum_operation_p95_milliseconds <= 60_000
            or not 1_000 <= self.maximum_suite_duration_milliseconds <= 3_600_000
        ):
            raise ValueError("capacity.profile.invalid")

    def to_document(self) -> dict[str, object]:
        return {
            "name": "postgresql-investigation-dispatch-v1",
            "tenantCount": self.tenant_count,
            "jobsPerTenant": self.jobs_per_tenant,
            "parallelClients": self.parallel_clients,
            "maxOutstandingJobsPerTenant": self.jobs_per_tenant,
            "maxTenantConcurrency": 1,
            "maximumOperationP95Milliseconds": (
                self.maximum_operation_p95_milliseconds
            ),
            "maximumSuiteDurationMilliseconds": (
                self.maximum_suite_duration_milliseconds
            ),
        }


@dataclass(frozen=True)
class TimedResult:
    value: object
    milliseconds: int


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _shift(value: str, seconds: int) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return (parsed + timedelta(seconds=seconds)).isoformat().replace("+00:00", "Z")


def _timed(operation: Callable[[], T]) -> TimedResult:
    started = perf_counter()
    value = operation()
    elapsed = max(0, math.ceil((perf_counter() - started) * 1_000))
    return TimedResult(value, elapsed)


def latency_document(values: Iterable[int]) -> dict[str, int]:
    ordered = sorted(values)
    if not ordered:
        return {"p50Milliseconds": 0, "p95Milliseconds": 0, "maximumMilliseconds": 0}

    def nearest_rank(percent: float) -> int:
        index = max(0, math.ceil(len(ordered) * percent) - 1)
        return ordered[index]

    return {
        "p50Milliseconds": nearest_rank(0.50),
        "p95Milliseconds": nearest_rank(0.95),
        "maximumMilliseconds": ordered[-1],
    }


def _identity(run_key: str, tenant_index: int, job_index: int) -> str:
    value = f"{run_key}:{tenant_index}:{job_index}".encode()
    return "inv_" + hashlib.sha256(value).hexdigest()[:32]


def _tenant(run_key: str, tenant_index: int) -> str:
    return f"capacity-{run_key[:10]}-{tenant_index:05d}"


def _documents(
    *,
    run_key: str,
    tenant_index: int,
    job_index: int,
    timestamp: str,
) -> tuple[ActorContext, str, dict[str, object], dict[str, object]]:
    tenant_id = _tenant(run_key, tenant_index)
    investigation_id = _identity(run_key, tenant_index, job_index)
    actor = ActorContext("capacity-certifier", tenant_id, ("developer",))
    request = json.loads(
        (EXAMPLES / "investigation-request.json").read_text(encoding="utf-8")
    )
    metadata = request["metadata"]
    assert isinstance(metadata, dict)
    metadata.update(
        {
            "id": investigation_id,
            "tenantId": tenant_id,
            "actorId": actor.actor_id,
            "correlationId": "corr_" + investigation_id.removeprefix("inv_"),
            "requestedAt": timestamp,
        }
    )
    status = InvestigationDispatchService.queued_status(
        actor,
        investigation_id,
        request,
        timestamp,
        attempts=0,
    )
    return actor, investigation_id, request, status


def _enqueue(
    store: PostgresOperationalStore,
    *,
    run_key: str,
    tenant_index: int,
    job_index: int,
    timestamp: str,
    limit: int,
) -> Mapping[str, object]:
    actor, investigation_id, request, status = _documents(
        run_key=run_key,
        tenant_index=tenant_index,
        job_index=job_index,
        timestamp=timestamp,
    )
    return store.enqueue_investigation_job(
        actor,
        investigation_id,
        request,
        status,
        max_outstanding_jobs_per_tenant=limit,
    )


def _parallel(
    operations: Iterable[Callable[[], T]], *, workers: int
) -> list[TimedResult]:
    with ThreadPoolExecutor(max_workers=workers) as executor:
        return list(executor.map(_timed, operations))


def _check(identifier: str, passed: bool) -> dict[str, str]:
    if passed:
        return {"id": identifier, "status": "passed"}
    return {
        "id": identifier,
        "status": "failed",
        "errorCode": f"capacity.{identifier}.failed",
    }


def source_identity() -> tuple[str, bool]:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    )
    return revision, dirty


def database_version(database_url: str) -> str:
    with psycopg.connect(database_url) as connection:
        row = connection.execute("SHOW server_version").fetchone()
    if row is None or not isinstance(row[0], str):
        raise RuntimeError("capacity.database.identity-unavailable")
    return row[0]


def certify(
    database_url: str,
    profile: CapacityProfile,
    *,
    revision: str,
    dirty: bool,
) -> dict[str, object]:
    profile.validate()
    suite_started = perf_counter()
    timestamp = _now()
    run_key = hashlib.sha256(f"{revision}:{timestamp}".encode()).hexdigest()
    store = PostgresOperationalStore(database_url)

    admission_operations: list[Callable[[], Mapping[str, object]]] = []
    for tenant_index in range(profile.tenant_count):
        for job_index in range(profile.jobs_per_tenant):
            admission_operations.append(
                lambda tenant_index=tenant_index, job_index=job_index: _enqueue(
                    store,
                    run_key=run_key,
                    tenant_index=tenant_index,
                    job_index=job_index,
                    timestamp=timestamp,
                    limit=profile.jobs_per_tenant,
                )
            )
    admission_results = _parallel(
        admission_operations, workers=profile.parallel_clients
    )
    admission_accepted = len(admission_results)
    admitted_tenants = {
        result.value["metadata"]["tenantId"]
        for result in admission_results
        if isinstance(result.value, Mapping)
        and isinstance(result.value.get("metadata"), Mapping)
    }

    overload_tenant_index = profile.tenant_count
    overload_attempts = profile.jobs_per_tenant * 4

    def overload_operation(job_index: int) -> TimedResult:
        started = perf_counter()
        try:
            value = _enqueue(
                store,
                run_key=run_key,
                tenant_index=overload_tenant_index,
                job_index=job_index,
                timestamp=timestamp,
                limit=profile.jobs_per_tenant,
            )
            disposition = ("accepted", job_index, value)
        except PersistenceError as exc:
            if str(exc) != "storage.capacity-exceeded":
                raise
            disposition = ("capacity-rejected", job_index, None)
        elapsed = max(0, math.ceil((perf_counter() - started) * 1_000))
        return TimedResult(disposition, elapsed)

    with ThreadPoolExecutor(max_workers=profile.parallel_clients) as executor:
        overload_results = list(
            executor.map(overload_operation, range(overload_attempts))
        )
    accepted_overload_indices = [
        int(result.value[1])
        for result in overload_results
        if isinstance(result.value, tuple) and result.value[0] == "accepted"
    ]
    overload_rejections = sum(
        1
        for result in overload_results
        if isinstance(result.value, tuple) and result.value[0] == "capacity-rejected"
    )
    idempotent_results = _parallel(
        (
            lambda job_index=job_index: _enqueue(
                store,
                run_key=run_key,
                tenant_index=overload_tenant_index,
                job_index=job_index,
                timestamp=timestamp,
                limit=profile.jobs_per_tenant,
            )
            for job_index in accepted_overload_indices
        ),
        workers=profile.parallel_clients,
    )
    companion = _timed(
        lambda: _enqueue(
            store,
            run_key=run_key,
            tenant_index=profile.tenant_count + 1,
            job_index=0,
            timestamp=timestamp,
            limit=profile.jobs_per_tenant,
        )
    )

    claim_timestamp = _shift(timestamp, 1)
    lease_expires_at = _shift(claim_timestamp, 30)
    claim_results = _parallel(
        (
            lambda tenant_index=tenant_index: store.claim_investigation_job(
                _tenant(run_key, tenant_index),
                f"capacity-worker-{tenant_index:05d}",
                claim_timestamp,
                lease_expires_at,
                max_tenant_concurrency=1,
            )
            for tenant_index in range(profile.tenant_count)
        ),
        workers=profile.parallel_clients,
    )
    claims = [result.value for result in claim_results if result.value is not None]
    second_claim_results = _parallel(
        (
            lambda tenant_index=tenant_index: store.claim_investigation_job(
                _tenant(run_key, tenant_index),
                f"capacity-second-worker-{tenant_index:05d}",
                claim_timestamp,
                lease_expires_at,
                max_tenant_concurrency=1,
            )
            for tenant_index in range(profile.tenant_count)
        ),
        workers=profile.parallel_clients,
    )
    second_claims_blocked = sum(
        1 for result in second_claim_results if result.value is None
    )

    completed_at = _shift(claim_timestamp, 1)

    def terminalize(tenant_index: int, claim: object) -> bool:
        actor = ActorContext(
            "capacity-certifier", _tenant(run_key, tenant_index), ("developer",)
        )
        investigation_id = getattr(claim, "investigation_id")
        current = store.get_investigation_job(actor, investigation_id)
        if current is None:
            return False
        terminal = InvestigationDispatchService.terminal_status(
            copy.deepcopy(current), state="completed", completed_at=completed_at
        )
        return store.finish_investigation_job(
            actor.tenant_id,
            investigation_id,
            f"capacity-worker-{tenant_index:05d}",
            getattr(claim, "claim_token"),
            terminal,
        )

    terminal_results = _parallel(
        (
            lambda tenant_index=tenant_index, claim=claim: terminalize(
                tenant_index, claim
            )
            for tenant_index, claim in enumerate(claims)
        ),
        workers=profile.parallel_clients,
    )
    replacement_results = _parallel(
        (
            lambda tenant_index=tenant_index: _enqueue(
                store,
                run_key=run_key,
                tenant_index=tenant_index,
                job_index=profile.jobs_per_tenant,
                timestamp=completed_at,
                limit=profile.jobs_per_tenant,
            )
            for tenant_index in range(profile.tenant_count)
        ),
        workers=profile.parallel_clients,
    )

    terminalized = sum(result.value is True for result in terminal_results)
    replacement_accepted = len(replacement_results)
    suite_duration = max(0, math.ceil((perf_counter() - suite_started) * 1_000))
    admission_latency = latency_document(
        result.milliseconds for result in admission_results
    )
    overload_latency = latency_document(
        [result.milliseconds for result in overload_results]
        + [result.milliseconds for result in idempotent_results]
        + [companion.milliseconds]
    )
    claim_latency = latency_document(
        [result.milliseconds for result in claim_results]
        + [result.milliseconds for result in second_claim_results]
    )
    recovery_latency = latency_document(
        [result.milliseconds for result in terminal_results]
        + [result.milliseconds for result in replacement_results]
    )
    maximum_p95 = max(
        value["p95Milliseconds"]
        for value in (
            admission_latency,
            overload_latency,
            claim_latency,
            recovery_latency,
        )
    )
    checks = [
        _check(
            "large-tenant-admission",
            admission_accepted == profile.tenant_count * profile.jobs_per_tenant
            and len(admitted_tenants) == profile.tenant_count,
        ),
        _check(
            "overload-cap",
            len(accepted_overload_indices) == profile.jobs_per_tenant
            and overload_rejections == overload_attempts - profile.jobs_per_tenant,
        ),
        _check(
            "idempotency-at-capacity",
            len(idempotent_results) == profile.jobs_per_tenant,
        ),
        _check("cross-tenant-isolation", companion.value is not None),
        _check(
            "tenant-live-lease-cap",
            second_claims_blocked == profile.tenant_count,
        ),
        _check("first-pass-coverage", len(claims) == profile.tenant_count),
        _check(
            "terminal-capacity-release",
            terminalized == profile.tenant_count
            and replacement_accepted == profile.tenant_count,
        ),
        _check(
            "operation-p95-objective",
            maximum_p95 <= profile.maximum_operation_p95_milliseconds,
        ),
        _check(
            "suite-duration-objective",
            suite_duration <= profile.maximum_suite_duration_milliseconds,
        ),
    ]
    status = (
        "certified" if all(check["status"] == "passed" for check in checks) else "failed"
    )
    identity = json.dumps(
        {"revision": revision, "timestamp": timestamp, "profile": profile.to_document()},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "InvestigationCapacityReport",
        "metadata": {
            "id": "icr_" + hashlib.sha256(identity).hexdigest()[:32],
            "generatedAt": timestamp,
            "sourceRevision": revision,
            "sourceDirty": dirty,
        },
        "spec": {
            "status": status,
            "environment": {
                "platform": f"{platform.system().lower()}/{platform.machine().lower()}",
                "pythonVersion": platform.python_version(),
                "applicationVersion": application_version,
                "database": {
                    "engine": "postgresql",
                    "version": database_version(database_url),
                    "migration": SCHEMA_MIGRATIONS[-1],
                },
            },
            "profile": profile.to_document(),
            "measurements": {
                "admission": {
                    "attemptedJobs": profile.tenant_count * profile.jobs_per_tenant,
                    "acceptedJobs": admission_accepted,
                    "rejectedJobs": (
                        profile.tenant_count * profile.jobs_per_tenant
                        - admission_accepted
                    ),
                    "tenantsAccepted": len(admitted_tenants),
                    "latency": admission_latency,
                },
                "overload": {
                    "attemptedJobs": overload_attempts,
                    "acceptedJobs": len(accepted_overload_indices),
                    "capacityRejectedJobs": overload_rejections,
                    "idempotentRetriesAccepted": len(idempotent_results),
                    "companionTenantJobsAccepted": int(companion.value is not None),
                    "latency": overload_latency,
                },
                "claims": {
                    "eligibleTenants": profile.tenant_count,
                    "tenantsClaimed": len(claims),
                    "sameTenantSecondClaimsBlocked": second_claims_blocked,
                    "maximumLiveClaimsPerTenant": (
                        1 if second_claims_blocked == profile.tenant_count else 2
                    ),
                    "latency": claim_latency,
                },
                "recovery": {
                    "terminalizedClaims": terminalized,
                    "replacementJobsAccepted": replacement_accepted,
                    "latency": recovery_latency,
                },
                "suiteDurationMilliseconds": suite_duration,
            },
            "checks": checks,
        },
    }


def validate_report(document: object) -> None:
    schema = json.loads(
        (SCHEMAS / "investigation-capacity-report.schema.json").read_text(
            encoding="utf-8"
        )
    )
    errors = validate_schemas.instance_validation_errors(
        schema, document, label="generated investigation capacity report"
    )
    if errors:
        raise RuntimeError("; ".join(errors))


def arguments(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("IIP_CAPACITY_DATABASE_URL"),
        help="isolated migrated PostgreSQL URL (or IIP_CAPACITY_DATABASE_URL)",
    )
    parser.add_argument("--tenants", type=int, default=128)
    parser.add_argument("--jobs-per-tenant", type=int, default=4)
    parser.add_argument("--parallel-clients", type=int, default=16)
    parser.add_argument("--maximum-operation-p95-milliseconds", type=int, default=5_000)
    parser.add_argument("--maximum-suite-duration-milliseconds", type=int, default=120_000)
    parser.add_argument(
        "--report",
        type=Path,
        default=ROOT / "dist" / "investigation-capacity-report.json",
    )
    return parser.parse_args(tuple(argv) if argv is not None else None)


def main(argv: Iterable[str] | None = None) -> int:
    options = arguments(argv)
    options.report.unlink(missing_ok=True)
    if not options.database_url:
        raise SystemExit("IIP_CAPACITY_DATABASE_URL or --database-url is required")
    profile = CapacityProfile(
        tenant_count=options.tenants,
        jobs_per_tenant=options.jobs_per_tenant,
        parallel_clients=options.parallel_clients,
        maximum_operation_p95_milliseconds=(
            options.maximum_operation_p95_milliseconds
        ),
        maximum_suite_duration_milliseconds=(
            options.maximum_suite_duration_milliseconds
        ),
    )
    profile.validate()
    PostgresResourceStore(options.database_url).migrate()
    revision, dirty = source_identity()
    report = certify(
        options.database_url,
        profile,
        revision=revision,
        dirty=dirty,
    )
    validate_report(report)
    options.report.parent.mkdir(parents=True, exist_ok=True)
    options.report.write_text(
        json.dumps(report, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    measurements = report["spec"]["measurements"]
    print(
        "investigation capacity certification "
        f"{report['spec']['status']}: "
        f"{report['spec']['profile']['tenantCount']} tenants, "
        f"{measurements['admission']['acceptedJobs']} admitted jobs, "
        f"{measurements['suiteDurationMilliseconds']} ms"
    )
    print(f"report: {options.report}")
    return 0 if report["spec"]["status"] == "certified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
