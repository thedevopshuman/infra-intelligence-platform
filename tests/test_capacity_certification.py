from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from iip.adapters.operations import InMemoryOperationalStore
from scripts import run_capacity_certification as certification


ROOT = Path(__file__).resolve().parents[1]


class InvestigationCapacityCertificationTests(unittest.TestCase):
    def test_profile_is_closed_and_bounded(self) -> None:
        for profile in (
            certification.CapacityProfile(tenant_count=1),
            certification.CapacityProfile(jobs_per_tenant=1),
            certification.CapacityProfile(parallel_clients=65),
            certification.CapacityProfile(maximum_operation_p95_milliseconds=0),
            certification.CapacityProfile(maximum_suite_duration_milliseconds=999),
        ):
            with self.subTest(profile=profile), self.assertRaisesRegex(
                ValueError, "capacity.profile.invalid"
            ):
                profile.validate()

    def test_latency_uses_nearest_rank_and_integer_milliseconds(self) -> None:
        self.assertEqual(
            certification.latency_document([7, 1, 4, 3, 2]),
            {
                "p50Milliseconds": 3,
                "p95Milliseconds": 7,
                "maximumMilliseconds": 7,
            },
        )
        self.assertEqual(
            certification.latency_document([]),
            {
                "p50Milliseconds": 0,
                "p95Milliseconds": 0,
                "maximumMilliseconds": 0,
            },
        )

    def test_small_reference_run_proves_every_capacity_check(self) -> None:
        store = InMemoryOperationalStore()
        profile = certification.CapacityProfile(
            tenant_count=4,
            jobs_per_tenant=2,
            parallel_clients=4,
            maximum_operation_p95_milliseconds=5_000,
            maximum_suite_duration_milliseconds=120_000,
        )
        with (
            patch.object(certification, "PostgresOperationalStore", return_value=store),
            patch.object(certification, "database_version", return_value="18.4"),
        ):
            report = certification.certify(
                "postgresql://unused",
                profile,
                revision="a" * 40,
                dirty=False,
            )

        certification.validate_report(report)
        self.assertEqual(report["spec"]["status"], "certified")
        self.assertEqual(
            report["spec"]["measurements"]["admission"]["acceptedJobs"], 8
        )
        self.assertTrue(
            all(check["status"] == "passed" for check in report["spec"]["checks"])
        )
        serialized = json.dumps(report)
        self.assertNotIn("postgresql://unused", serialized)
        self.assertNotIn("tenantId", serialized)
        self.assertNotIn("question", serialized)


if __name__ == "__main__":
    unittest.main()
