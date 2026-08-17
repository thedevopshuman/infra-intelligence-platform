"""Process surface for tenant-explicit durable workflow workers."""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event

from iip.application.investigation_worker import (
    InvestigationWorker,
    InvestigationWorkerResult,
)
from iip.bootstrap import (
    build_action_reconciler_from_env,
    build_event_delivery_from_env,
    build_ingestion_freshness_sampler_from_env,
    build_investigation_worker_from_env,
    build_workflow_worker_runtime_from_env,
)


@dataclass(frozen=True)
class InvestigationSchedulerPass:
    """Value-minimized result from one process-local scheduling pass."""

    results: tuple[InvestigationWorkerResult, ...]
    failures: int


class TenantFairInvestigationScheduler:
    """Keep at most one process-local task in flight for each tenant."""

    def __init__(
        self,
        worker: InvestigationWorker,
        tenants: tuple[str, ...],
        *,
        concurrency: int,
    ) -> None:
        if (
            not tenants
            or len(tenants) != len(set(tenants))
            or len(tenants) > 1000
            or any(not tenant or len(tenant) > 128 for tenant in tenants)
            or isinstance(concurrency, bool)
            or not isinstance(concurrency, int)
            or not 1 <= concurrency <= 64
        ):
            raise ValueError("workflow.worker.scheduling.invalid")
        self._worker = worker
        self._tenants = tenants
        self._executor = ThreadPoolExecutor(
            max_workers=min(concurrency, len(tenants)),
            thread_name_prefix="iip-investigation",
        )
        self._concurrency = concurrency
        self._cursor = 0
        self._inflight: dict[
            str, Future[InvestigationWorkerResult | None]
        ] = {}

    def poll(self) -> InvestigationSchedulerPass:
        results, failures = self._collect_completed()
        capacity = self._concurrency - len(self._inflight)
        if capacity > 0:
            for offset in range(len(self._tenants)):
                tenant = self._tenants[
                    (self._cursor + offset) % len(self._tenants)
                ]
                if tenant in self._inflight:
                    continue
                self._inflight[tenant] = self._executor.submit(
                    self._worker.run_once, tenant
                )
                capacity -= 1
                if capacity == 0:
                    break
            self._cursor = (self._cursor + 1) % len(self._tenants)
        return InvestigationSchedulerPass(tuple(results), failures)

    def run_bounded_pass(self) -> InvestigationSchedulerPass:
        if self._inflight:
            raise RuntimeError("workflow.worker.scheduling.active")
        futures = tuple(
            self._executor.submit(self._worker.run_once, tenant)
            for tenant in self._tenants
        )
        results: list[InvestigationWorkerResult] = []
        failures = 0
        for future in futures:
            try:
                result = future.result()
                if result is not None:
                    results.append(result)
            except Exception:
                failures += 1
        return InvestigationSchedulerPass(tuple(results), failures)

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _collect_completed(
        self,
    ) -> tuple[list[InvestigationWorkerResult], int]:
        results: list[InvestigationWorkerResult] = []
        failures = 0
        for tenant, future in tuple(self._inflight.items()):
            if not future.done():
                continue
            del self._inflight[tenant]
            try:
                result = future.result()
                if result is not None:
                    results.append(result)
            except Exception:
                # Repository/provider details never enter process logs.
                failures += 1
        return results, failures


def configured_tenants() -> tuple[str, ...]:
    raw = os.environ.get("IIP_WORKER_TENANTS", "")
    tenants = tuple(value.strip() for value in raw.split(",") if value.strip())
    if (
        not tenants
        or len(tenants) > 1000
        or len(tenants) != len(set(tenants))
        or any(len(value) > 128 for value in tenants)
    ):
        raise ValueError("workflow.worker.tenants.required")
    return tenants


def monitor_interval_seconds() -> int:
    try:
        interval = int(
            os.environ.get("IIP_INGESTION_MONITOR_INTERVAL_SECONDS", "60")
        )
    except ValueError:
        raise ValueError("ingestion.monitor.configuration.invalid") from None
    if interval < 5 or interval > 3600:
        raise ValueError("ingestion.monitor.configuration.invalid")
    return interval


def investigation_concurrency() -> int:
    try:
        concurrency = int(
            os.environ.get("IIP_WORKER_INVESTIGATION_CONCURRENCY", "4")
        )
    except ValueError:
        raise ValueError("workflow.worker.scheduling.invalid") from None
    if concurrency < 1 or concurrency > 64:
        raise ValueError("workflow.worker.scheduling.invalid")
    return concurrency


def main() -> None:
    parser = argparse.ArgumentParser(description="Run durable tenant workflow timers")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one bounded pass per configured tenant and exit",
    )
    arguments = parser.parse_args()
    tenants = configured_tenants()
    runtime = build_workflow_worker_runtime_from_env()
    worker = build_investigation_worker_from_env(runtime)
    scheduler = TenantFairInvestigationScheduler(
        worker,
        tenants,
        concurrency=investigation_concurrency(),
    )
    action_reconciler = build_action_reconciler_from_env(runtime)
    event_delivery = build_event_delivery_from_env(runtime, tenants)
    ingestion_sampler = build_ingestion_freshness_sampler_from_env(runtime, tenants)
    sampling_interval = (
        monitor_interval_seconds() if ingestion_sampler is not None else 60
    )
    next_sample_at = time.monotonic()
    stopped = Event()

    def stop(_signum: int, _frame: object) -> None:
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        while not stopped.is_set():
            dispatch = (
                scheduler.run_bounded_pass()
                if arguments.once
                else scheduler.poll()
            )
            worked = bool(dispatch.results)
            if dispatch.results or dispatch.failures:
                print(
                    json.dumps(
                        {
                            "event": "investigation.dispatch.pass",
                            "processed": len(dispatch.results),
                            "schedulerFailures": dispatch.failures,
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )
            for tenant_id in tenants:
                if stopped.is_set():
                    break
                reconciliation = action_reconciler.run_once(tenant_id)
                worked = worked or reconciliation.transitioned > 0
                if event_delivery is not None:
                    delivery = event_delivery.run_once(tenant_id)
                    if delivery.claimed > 0:
                        print(
                            json.dumps(
                                {
                                    "event": "outbox.delivery.completed",
                                    "claimed": delivery.claimed,
                                    "delivered": delivery.delivered,
                                    "released": delivery.released,
                                    "quarantined": delivery.quarantined,
                                    "ambiguous": delivery.ambiguous,
                                },
                                separators=(",", ":"),
                                sort_keys=True,
                            )
                        )
                        worked = True
            if ingestion_sampler is not None and time.monotonic() >= next_sample_at:
                summary = ingestion_sampler.run_once()
                print(
                    json.dumps(
                        {
                            "event": "ingestion-freshness.sampled",
                            "sampled": summary.sampled,
                            "breached": summary.breached,
                            "missing": summary.missing,
                            "denied": summary.denied,
                            "failed": summary.failed,
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )
                next_sample_at = time.monotonic() + sampling_interval
                worked = True
            if arguments.once:
                break
            if not worked:
                stopped.wait(0.5)
    finally:
        scheduler.close()
        runtime.close()


if __name__ == "__main__":
    main()
