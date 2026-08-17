from __future__ import annotations

import os
import unittest
from threading import Event, Lock
from unittest.mock import patch

from iip.application.investigation_worker import InvestigationWorkerResult
from iip.bootstrap import (
    build_investigation_worker_from_env,
    build_local_runtime,
)
from iip.surfaces.worker import (
    TenantFairInvestigationScheduler,
    investigation_concurrency,
)


class BlockingWorker:
    def __init__(self) -> None:
        self.slow_started = Event()
        self.fast_started = Event()
        self.release_slow = Event()
        self._lock = Lock()
        self.calls: list[str] = []

    def run_once(self, tenant_id: str) -> InvestigationWorkerResult | None:
        with self._lock:
            self.calls.append(tenant_id)
        if tenant_id == "tenant-a":
            self.slow_started.set()
            self.release_slow.wait(5)
        else:
            self.fast_started.set()
        return InvestigationWorkerResult(f"inv_{'a' * 32}", "completed")


class TenantFairInvestigationSchedulerTests(unittest.TestCase):
    def test_slow_tenant_does_not_block_another_tenant(self) -> None:
        worker = BlockingWorker()
        scheduler = TenantFairInvestigationScheduler(
            worker,  # type: ignore[arg-type]
            ("tenant-a", "tenant-b"),
            concurrency=2,
        )
        try:
            scheduler.poll()
            self.assertTrue(worker.slow_started.wait(1))
            self.assertTrue(worker.fast_started.wait(1))
            scheduler.poll()
            self.assertEqual(worker.calls.count("tenant-a"), 1)
        finally:
            worker.release_slow.set()
            scheduler.close()

    def test_bounded_pass_visits_every_enrolled_tenant(self) -> None:
        worker = BlockingWorker()
        worker.release_slow.set()
        scheduler = TenantFairInvestigationScheduler(
            worker,  # type: ignore[arg-type]
            ("tenant-a", "tenant-b", "tenant-c"),
            concurrency=2,
        )
        try:
            result = scheduler.run_bounded_pass()
        finally:
            scheduler.close()

        self.assertEqual(result.failures, 0)
        self.assertEqual(len(result.results), 3)
        self.assertCountEqual(worker.calls, ["tenant-a", "tenant-b", "tenant-c"])

    def test_process_concurrency_configuration_is_closed_and_bounded(self) -> None:
        with patch.dict(
            os.environ,
            {"IIP_WORKER_INVESTIGATION_CONCURRENCY": "8"},
            clear=True,
        ):
            self.assertEqual(investigation_concurrency(), 8)
        for value in ("bad", "0", "65"):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {"IIP_WORKER_INVESTIGATION_CONCURRENCY": value},
                clear=True,
            ):
                with self.assertRaisesRegex(
                    ValueError, "workflow.worker.scheduling.invalid"
                ):
                    investigation_concurrency()

    def test_deployment_tenant_cap_configuration_is_closed_and_bounded(self) -> None:
        runtime = build_local_runtime()
        self.addCleanup(runtime.close)
        with patch.dict(
            os.environ,
            {
                "IIP_WORKER_ID": "worker-a",
                "IIP_WORKER_MAX_TENANT_CONCURRENCY": "2",
            },
            clear=True,
        ):
            self.assertIsNotNone(build_investigation_worker_from_env(runtime))
        for value in ("bad", "0", "65"):
            with self.subTest(value=value), patch.dict(
                os.environ,
                {
                    "IIP_WORKER_ID": "worker-a",
                    "IIP_WORKER_MAX_TENANT_CONCURRENCY": value,
                },
                clear=True,
            ):
                with self.assertRaisesRegex(
                    ValueError, "investigation.worker.configuration.invalid"
                ):
                    build_investigation_worker_from_env(runtime)


if __name__ == "__main__":
    unittest.main()
