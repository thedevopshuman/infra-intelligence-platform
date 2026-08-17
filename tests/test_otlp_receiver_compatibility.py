from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts import validate_repo
from scripts import write_otlp_receiver_compatibility_report as compatibility


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts" / "examples"


class OtlpReceiverCompatibilityTests(unittest.TestCase):
    def test_report_is_closed_source_bound_and_schema_valid(self) -> None:
        report = compatibility.compatibility_report(
            revision="0123456789abcdef0123456789abcdef01234567",
            source_dirty=True,
            docker_platform="linux/arm64",
            docker_version="28.3.3",
        )

        compatibility.validate_report(report)
        self.assertTrue(report["metadata"]["sourceDirty"])
        self.assertEqual(
            [check["id"] for check in report["spec"]["checks"]],
            list(compatibility.CHECK_IDS),
        )
        encoded = json.dumps(report, sort_keys=True)
        for protected in (
            "tenant-a",
            "spiffe://",
            "postgresql://",
            "Bearer ",
            "BEGIN CERTIFICATE",
        ):
            with self.subTest(protected=protected):
                self.assertNotIn(protected, encoded)

    def test_semantic_validation_rejects_inconsistent_summary(self) -> None:
        report = json.loads(
            (EXAMPLES / "otlp-receiver-compatibility-report.json").read_text(
                encoding="utf-8"
            )
        )
        report["spec"]["checks"][0] = {
            "id": "isolated-route-surface",
            "status": "failed",
            "errorCode": "otlp.compatibility.failed",
        }

        errors: list[str] = []
        validate_repo.validate_otlp_receiver_compatibility_document(report, errors)

        self.assertIn("OTLP receiver compatibility status must match its checks", errors)
        self.assertIn("OTLP receiver compatibility summary must match its checks", errors)

    def test_collector_profile_is_pinned_and_persistent(self) -> None:
        config = (ROOT / "deploy/otel/collector-to-iip.example.yaml").read_text(
            encoding="utf-8"
        )

        for required in (
            "file_storage/iip:",
            "storage: file_storage/iip",
            "retry_on_failure:",
            "max_elapsed_time: 0s",
            "cert_file:",
            "key_file:",
            "ca_file:",
        ):
            with self.subTest(required=required):
                self.assertIn(required, config)


if __name__ == "__main__":
    unittest.main()
