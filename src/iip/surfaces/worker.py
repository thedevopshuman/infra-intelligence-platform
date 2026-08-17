"""Process surface for tenant-explicit durable workflow workers."""

from __future__ import annotations

import argparse
import json
import os
import signal
import time
from threading import Event

from iip.bootstrap import (
    build_action_reconciler_from_env,
    build_event_delivery_from_env,
    build_ingestion_freshness_sampler_from_env,
    build_investigation_worker_from_env,
    build_workflow_worker_runtime_from_env,
)


def configured_tenants() -> tuple[str, ...]:
    raw = os.environ.get("IIP_WORKER_TENANTS", "")
    tenants = tuple(value.strip() for value in raw.split(",") if value.strip())
    if not tenants or len(tenants) != len(set(tenants)) or any(
        len(value) > 128 for value in tenants
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
            worked = False
            for tenant_id in tenants:
                if stopped.is_set():
                    break
                result = worker.run_once(tenant_id)
                worked = worked or result is not None
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
        runtime.close()


if __name__ == "__main__":
    main()
