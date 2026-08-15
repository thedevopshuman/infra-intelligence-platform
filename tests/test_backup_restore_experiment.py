from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import backup_restore_experiment as recovery  # noqa: E402


class BackupRestoreManifestTests(unittest.TestCase):
    def test_manifest_is_stable_across_table_and_row_order(self) -> None:
        first = recovery.compile_manifest(
            {
                "resources": ({"id": 2}, {"id": 1}),
                "events": ({"offset": 1, "type": "created"},),
            },
            ({"name": "event_offset_seq", "lastValue": 1},),
        )
        second = recovery.compile_manifest(
            {
                "events": ({"type": "created", "offset": 1},),
                "resources": ({"id": 1}, {"id": 2}),
            },
            ({"lastValue": 1, "name": "event_offset_seq"},),
        )

        self.assertEqual(first, second)
        self.assertEqual(first["tables"]["resources"]["rowCount"], 2)

    def test_manifest_comparison_rejects_row_and_sequence_drift(self) -> None:
        source = recovery.compile_manifest(
            {"resources": ({"id": 1},)},
            ({"name": "resource_id_seq", "lastValue": 1},),
        )
        row_drift = recovery.compile_manifest(
            {"resources": ({"id": 2},)},
            ({"name": "resource_id_seq", "lastValue": 1},),
        )
        sequence_drift = recovery.compile_manifest(
            {"resources": ({"id": 1},)},
            ({"name": "resource_id_seq", "lastValue": 2},),
        )

        with self.assertRaisesRegex(RuntimeError, "integrity_mismatch"):
            recovery.assert_identical_manifests(source, row_drift)
        with self.assertRaisesRegex(RuntimeError, "integrity_mismatch"):
            recovery.assert_identical_manifests(source, sequence_drift)

    def test_database_targets_are_distinct_and_safe(self) -> None:
        recovery.validate_database_names("iip_test", "iip_restore")

        for source, restored in (
            ("iip_test", "iip_test"),
            ("iip_test", "postgres"),
            ("iip-test", "iip_restore"),
            ("iip_test", "template1"),
        ):
            with self.subTest(source=source, restored=restored):
                with self.assertRaisesRegex(ValueError, "database_name_invalid"):
                    recovery.validate_database_names(source, restored)


class BackupRestoreReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = recovery.compile_manifest(
            {
                "resource_projections": ({"tenant_id": "local"},),
                "resource_observations": ({"observation_offset": 1},),
            },
            ({"name": "resource_observations_observation_offset_seq", "lastValue": 1},),
        )

    def report(self, *, backup: float = 0.5, recovery_ready: float = 1.25) -> dict:
        return recovery.build_report(
            server_version="18.4",
            seed={"tenantId": "local", "resourceCount": 1},
            source_manifest=self.manifest,
            backup_bytes=4096,
            recovery_point="2026-08-15T00:00:00Z",
            backup_duration_seconds=backup,
            recovery_point_age_seconds=backup,
            restore_command_duration_seconds=0.75,
            recovery_ready_seconds=recovery_ready,
            projection={"driftDetected": False, "resourceCount": 1},
            rpo_target_seconds=60,
            rto_target_seconds=120,
        )

    def test_report_counts_rows_and_marks_satisfied_objectives(self) -> None:
        report = self.report()

        self.assertTrue(report["status"]["objectivesMet"])
        self.assertTrue(report["status"]["integrity"]["matched"])
        self.assertEqual(report["status"]["integrity"]["rowCount"], 2)
        self.assertEqual(report["status"]["backup"]["committedRecordLoss"], 0)

    def test_report_marks_a_missed_experiment_guardrail(self) -> None:
        report = self.report(backup=61, recovery_ready=121)

        self.assertFalse(report["status"]["objectivesMet"])

    def test_compose_commands_are_isolated_by_project_name(self) -> None:
        runner = recovery.DockerComposeRunner(
            "docker", Path("deploy/docker-compose.test.yml"), "iip-backup-restore"
        )

        command = runner.command("up", "--detach")

        self.assertEqual(
            command[:5],
            [
                "docker",
                "compose",
                "--project-name",
                "iip-backup-restore",
                "--file",
            ],
        )
        self.assertEqual(command[-2:], ["up", "--detach"])


if __name__ == "__main__":
    unittest.main()
