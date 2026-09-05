from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import postgresql_continuity_experiment as continuity  # noqa: E402
from backup_restore_experiment import compile_manifest  # noqa: E402


class PostgreSQLContinuityReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.target_manifest = compile_manifest(
            {
                "resource_projections": ({"resource_uid": "res_before"},),
                "resource_observations": ({"observation_offset": 1},),
            },
            (
                {
                    "name": "resource_observations_observation_offset_seq",
                    "lastValue": 1,
                },
            ),
        )
        self.source_manifest = compile_manifest(
            {
                "resource_projections": (
                    {"resource_uid": "res_before"},
                    {"resource_uid": "res_after"},
                ),
                "resource_observations": (
                    {"observation_offset": 1},
                    {"observation_offset": 2},
                ),
            },
            (
                {
                    "name": "resource_observations_observation_offset_seq",
                    "lastValue": 2,
                },
            ),
        )
        self.source_projection = {
            "driftDetected": False,
            "resourceCount": 3,
            "relationshipCount": 1,
            "latestObservationOffset": 3,
            "projectionDigest": "sha256:" + "1" * 64,
        }
        self.pitr_projection = {
            "driftDetected": False,
            "resourceCount": 2,
            "relationshipCount": 1,
            "latestObservationOffset": 2,
            "projectionDigest": "sha256:" + "2" * 64,
        }

    def report(
        self,
        *,
        catchup_seconds: float = 0.25,
        failover_seconds: float = 0.5,
        pitr_seconds: float = 0.75,
    ) -> dict:
        return continuity.build_report(
            source_revision="0123456789abcdef0123456789abcdef01234567",
            source_dirty=False,
            container_runtime_version="29.7.2",
            server_version="18.4",
            fixture={
                "tenantCount": 1,
                "resourceCount": 1,
                "relationshipCount": 1,
                "investigationCount": 1,
                "governedActionCount": 1,
                "pluginSessionCount": 1,
                "pluginInvocationCount": 1,
                "beforeTargetResourceCount": 1,
                "afterTargetResourceCount": 1,
            },
            backup_bytes=131072,
            backup_duration_seconds=0.4,
            primary_timeline=1,
            primary_flush_lsn_value="0/3000128",
            standby_replay_lsn="0/3000128",
            replay_lag_bytes=0,
            catchup_seconds=catchup_seconds,
            source_manifest=self.source_manifest,
            standby_manifest=self.source_manifest,
            promoted_timeline=2,
            failover_ready_seconds=failover_seconds,
            failover_manifest=self.source_manifest,
            failover_projection=self.source_projection,
            archived_wal_file_count=3,
            pitr_ready_seconds=pitr_seconds,
            target_manifest=self.target_manifest,
            pitr_manifest=self.target_manifest,
            pitr_projection=self.pitr_projection,
            boundary={
                "baselinePresent": True,
                "beforeTargetPresent": True,
                "afterTargetAbsent": True,
            },
            catchup_target_seconds=60,
            failover_target_seconds=120,
            pitr_target_seconds=180,
        )

    def test_report_is_closed_minimized_and_qualified(self) -> None:
        report = self.report()

        continuity.validate_report(report)

        self.assertEqual(report["spec"]["status"], "qualified")
        self.assertEqual(
            tuple(item["id"] for item in report["spec"]["checks"]),
            continuity.CHECK_IDS,
        )
        serialized = json.dumps(report)
        for forbidden in (
            "tenantId",
            "databaseUrl",
            "password",
            "continuity-primary",
            "res_before",
            "res_after",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_committed_example_passes_the_semantic_verifier(self) -> None:
        example = json.loads(
            (
                ROOT
                / "contracts"
                / "examples"
                / "postgresql-continuity-qualification-report.json"
            ).read_text(encoding="utf-8")
        )

        continuity.validate_report(example)

    def test_report_marks_only_missed_local_objectives_failed(self) -> None:
        report = self.report(
            catchup_seconds=61,
            failover_seconds=121,
            pitr_seconds=181,
        )

        continuity.validate_report(report)

        self.assertEqual(report["spec"]["status"], "failed")
        self.assertEqual(
            [item["id"] for item in report["spec"]["checks"] if item["status"] == "failed"],
            [
                "catchup-objective",
                "failover-ready-objective",
                "pitr-ready-objective",
            ],
        )

    def test_verifier_rejects_integrity_and_boundary_tampering(self) -> None:
        invalid_total = copy.deepcopy(self.report())
        invalid_total["spec"]["measurements"]["failover"]["integrity"]["rowCount"] += 1
        with self.assertRaisesRegex(RuntimeError, "failover_integrity_totals_invalid"):
            continuity.validate_report(invalid_total)

        invalid_boundary = copy.deepcopy(self.report())
        invalid_boundary["spec"]["measurements"]["pitr"]["boundary"]["afterTargetAbsent"] = False
        with self.assertRaisesRegex(RuntimeError, "PostgreSQL continuity qualification report"):
            continuity.validate_report(invalid_boundary)

    def test_verifier_rejects_digest_and_check_tampering(self) -> None:
        invalid_digest = copy.deepcopy(self.report())
        invalid_digest["spec"]["measurements"]["pitr"]["expectedTargetRowDigest"] = "sha256:" + "a" * 64
        with self.assertRaisesRegex(RuntimeError, "pitr-row-integrity.status_invalid"):
            continuity.validate_report(invalid_digest)

        invalid_check = copy.deepcopy(self.report())
        invalid_check["spec"]["checks"][-1] = {
            "id": "pitr-ready-objective",
            "status": "failed",
            "errorCode": "postgresql.continuity.pitr-ready-objective.failed",
        }
        invalid_check["spec"]["status"] = "failed"
        with self.assertRaisesRegex(RuntimeError, "pitr-ready-objective.status_invalid"):
            continuity.validate_report(invalid_check)

    def test_clean_verifier_binds_revision_and_application_version(self) -> None:
        report = self.report()
        revision = report["metadata"]["sourceRevision"]
        with patch.object(continuity, "source_identity", return_value=(revision, False)):
            continuity.validate_report(report, require_clean=True)

        report["metadata"]["sourceDirty"] = True
        with (
            patch.object(continuity, "source_identity", return_value=(revision, False)),
            self.assertRaisesRegex(RuntimeError, "clean_source_identity_invalid"),
        ):
            continuity.validate_report(report, require_clean=True)


class PostgreSQLContinuityBoundaryTests(unittest.TestCase):
    def test_sequence_floor_accepts_expected_wal_preallocation_but_not_regression(self) -> None:
        expected = compile_manifest(
            {}, ({"name": "event_offset_seq", "incrementBy": 1, "lastValue": 12},)
        )
        preallocated = compile_manifest(
            {}, ({"name": "event_offset_seq", "incrementBy": 1, "lastValue": 32},)
        )
        regressed = compile_manifest(
            {}, ({"name": "event_offset_seq", "incrementBy": 1, "lastValue": 11},)
        )

        self.assertTrue(continuity.sequence_floor_satisfied(expected, preallocated))
        self.assertFalse(continuity.sequence_floor_satisfied(expected, regressed))

    def test_names_are_narrowly_constrained(self) -> None:
        continuity.validate_names("iip-pg-continuity", "iip_continuity")
        continuity.validate_names("iip-pg-continuity-a1", "iip_continuity_2")

        for project, database in (
            ("customer-db", "iip_continuity"),
            ("iip-pg-continuity", "postgres"),
            ("iip-pg-continuity", "iip-continuity"),
        ):
            with self.subTest(project=project, database=database):
                with self.assertRaises(ValueError):
                    continuity.validate_names(project, database)

    def test_resource_names_and_label_are_exact(self) -> None:
        runner = continuity.DockerRunner("docker", "iip-pg-continuity")

        self.assertEqual(runner.name("primary"), "iip-pg-continuity-primary")
        self.assertEqual(
            runner.label, "iip.continuity.project=iip-pg-continuity"
        )

    def test_runtime_credentials_are_not_embedded_in_source_commands(self) -> None:
        source = (ROOT / "scripts" / "postgresql_continuity_experiment.py").read_text(
            encoding="utf-8"
        )

        self.assertIn('"--env",\n            "POSTGRES_PASSWORD"', source)
        self.assertIn('"--env",\n            "PGPASSWORD"', source)
        self.assertNotIn("POSTGRES_PASSWORD=", source)
        self.assertNotIn("PGPASSWORD=", source)
        self.assertIn("POSTGRES_IMAGE,", source)
        self.assertEqual(
            continuity.POSTGRES_IMAGE,
            "postgres:18.4-alpine@sha256:"
            "9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15",
        )


if __name__ == "__main__":
    unittest.main()
