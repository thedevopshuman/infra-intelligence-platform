"""Process surface for tenant-explicit durable workflow workers."""

from __future__ import annotations

import argparse
import os
import signal
from threading import Event

from iip.bootstrap import (
    build_action_reconciler_from_env,
    build_investigation_worker_from_env,
    build_runtime_from_env,
)


def configured_tenants() -> tuple[str, ...]:
    raw = os.environ.get("IIP_WORKER_TENANTS", "")
    tenants = tuple(value.strip() for value in raw.split(",") if value.strip())
    if not tenants or len(tenants) != len(set(tenants)) or any(
        len(value) > 128 for value in tenants
    ):
        raise ValueError("workflow.worker.tenants.required")
    return tenants


def main() -> None:
    parser = argparse.ArgumentParser(description="Run durable tenant workflow timers")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Process one bounded pass per configured tenant and exit",
    )
    arguments = parser.parse_args()
    runtime = build_runtime_from_env(include_action_executor=False)
    worker = build_investigation_worker_from_env(runtime)
    action_reconciler = build_action_reconciler_from_env(runtime)
    tenants = configured_tenants()
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
            if arguments.once:
                break
            if not worked:
                stopped.wait(0.5)
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
