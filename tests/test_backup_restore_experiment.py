from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


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
            },
            source_manifest=self.manifest,
            backup_bytes=4096,
            recovery_point="2026-08-15T00:00:00Z",
            backup_duration_seconds=backup,
            recovery_point_age_seconds=backup,
            restore_command_duration_seconds=0.75,
            recovery_ready_seconds=recovery_ready,
            projection={
                "driftDetected": False,
                "resourceCount": 1,
                "relationshipCount": 1,
                "latestObservationOffset": 1,
                "projectionDigest": (
                    "sha256:11111111111111111111111111111111"
                    "11111111111111111111111111111111"
                ),
            },
            rpo_target_seconds=60,
            rto_target_seconds=120,
        )

    def test_report_counts_rows_and_marks_satisfied_objectives(self) -> None:
        report = self.report()

        recovery.validate_report(report)
        self.assertEqual(report["spec"]["status"], "qualified")
        measurements = report["spec"]["measurements"]
        self.assertTrue(measurements["integrity"]["matched"])
        self.assertEqual(measurements["integrity"]["rowCount"], 2)
        self.assertEqual(measurements["backup"]["committedRecordLoss"], 0)
        self.assertEqual(
            tuple(check["id"] for check in report["spec"]["checks"]),
            recovery.CHECK_IDS,
        )
        self.assertNotIn("tenantId", json.dumps(report))

    def test_report_marks_a_missed_experiment_guardrail(self) -> None:
        report = self.report(backup=61, recovery_ready=121)

        recovery.validate_report(report)
        self.assertEqual(report["spec"]["status"], "failed")
        self.assertEqual(
            report["spec"]["checks"][-2:],
            [
                {
                    "id": "recovery-point-age-objective",
                    "status": "failed",
                    "errorCode": (
                        "postgresql.recovery.recovery-point-age-objective.failed"
                    ),
                },
                {
                    "id": "recovery-ready-objective",
                    "status": "failed",
                    "errorCode": (
                        "postgresql.recovery.recovery-ready-objective.failed"
                    ),
                },
            ],
        )

    def test_verifier_rejects_derived_integrity_and_check_tampering(self) -> None:
        invalid_total = copy.deepcopy(self.report())
        invalid_total["spec"]["measurements"]["integrity"]["rowCount"] += 1
        with self.assertRaisesRegex(RuntimeError, "integrity_totals_invalid"):
            recovery.validate_report(invalid_total)

        invalid_check = copy.deepcopy(self.report())
        invalid_check["spec"]["checks"][-1] = {
            "id": "recovery-ready-objective",
            "status": "failed",
            "errorCode": "postgresql.recovery.recovery-ready-objective.failed",
        }
        invalid_check["spec"]["status"] = "failed"
        with self.assertRaisesRegex(RuntimeError, "status_invalid"):
            recovery.validate_report(invalid_check)

    def test_verifier_rejects_a_rewritten_source_bound_id(self) -> None:
        report = self.report()
        report["metadata"]["sourceRevision"] = (
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        )

        with self.assertRaisesRegex(RuntimeError, "report_id_invalid"):
            recovery.validate_report(report)

    def test_clean_verifier_binds_the_current_revision(self) -> None:
        report = self.report()
        revision = report["metadata"]["sourceRevision"]
        with patch.object(recovery, "source_identity", return_value=(revision, False)):
            recovery.validate_report(report, require_clean=True)

        report["metadata"]["sourceDirty"] = True
        with (
            patch.object(recovery, "source_identity", return_value=(revision, False)),
            self.assertRaisesRegex(RuntimeError, "clean_source_identity_invalid"),
        ):
            recovery.validate_report(report, require_clean=True)

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

    def test_disposable_database_image_is_digest_pinned(self) -> None:
        compose = (ROOT / "deploy" / "docker-compose.test.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn(f"image: {recovery.POSTGRES_IMAGE}", compose)


if __name__ == "__main__":
    unittest.main()
