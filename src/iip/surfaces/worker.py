"""Process surface for tenant-explicit investigation job workers."""

from __future__ import annotations

import argparse
import os
import signal
from threading import Event

from iip.bootstrap import (
    build_investigation_worker_from_env,
    build_runtime_from_env,
)


def configured_tenants() -> tuple[str, ...]:
    raw = os.environ.get("IIP_WORKER_TENANTS", "")
    tenants = tuple(value.strip() for value in raw.split(",") if value.strip())
    if not tenants or len(tenants) != len(set(tenants)) or any(
        len(value) > 128 for value in tenants
    ):
        raise ValueError("investigation.worker.tenants.required")
    return tenants


def main() -> None:
    parser = argparse.ArgumentParser(description="Run durable investigation jobs")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Claim at most one job per configured tenant and exit",
    )
    arguments = parser.parse_args()
    runtime = build_runtime_from_env()
    worker = build_investigation_worker_from_env(runtime)
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
            if arguments.once:
                break
            if not worked:
                stopped.wait(0.5)
    finally:
        runtime.close()


if __name__ == "__main__":
    main()
