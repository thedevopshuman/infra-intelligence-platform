from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts import run_plugin_runner_conformance as conformance


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts" / "examples"


class PluginCompatibilityReportTests(unittest.TestCase):
    def test_report_binds_exact_host_artifacts_profiles_and_dirty_state(self) -> None:
        manifest = {
            "metadata": {"id": "kubernetes-observer", "version": "0.3.0"},
            "spec": {"protocolVersion": "1.0"},
        }
        offline_session = {
            "spec": {
                "manifestDigest": (
                    "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                    "aaaaaaaaaaaaaaaa"
                )
            }
        }
        mediated_session = {"spec": {"manifestDigest": "sha256:" + "d" * 64}}
        image_id = "sha256:" + "b" * 64
        bridge_image_id = "sha256:" + "c" * 64
        git_results = (
            SimpleNamespace(stdout="0123456789abcdef0123456789abcdef01234567\n"),
            SimpleNamespace(stdout=" M scripts/run_plugin_runner_conformance.py\n"),
        )

        with (
            patch.object(conformance.subprocess, "run", side_effect=git_results),
            patch.object(
                conformance,
                "docker",
                side_effect=("linux/amd64", "29.7.2"),
            ),
        ):
            report = conformance.compatibility_report(
                manifest=manifest,
                offline_session=offline_session,
                mediated_session=mediated_session,
                image_id=image_id,
                bridge_image_id=bridge_image_id,
            )

        self.assertTrue(report["metadata"]["sourceDirty"])
        self.assertEqual(report["spec"]["host"]["platform"], "linux/amd64")
        self.assertEqual(report["spec"]["plugin"]["artifactDigest"], image_id)
        self.assertEqual(
            report["spec"]["plugin"]["mediationBridgeDigest"], bridge_image_id
        )
        self.assertEqual(
            [profile["name"] for profile in report["spec"]["profiles"]],
            ["offline-fixture", "host-mediated-read"],
        )
        self.assertEqual(
            report["spec"]["profiles"][1]["manifestDigest"],
            mediated_session["spec"]["manifestDigest"],
        )
        self.assertEqual(report["spec"]["summary"]["overallStatus"], "compatible")
        conformance.validate_contract(
            "plugin-compatibility-report.schema.json",
            report,
            label="generated plugin compatibility report",
        )

    def test_result_validation_rejects_golden_output_drift(self) -> None:
        result = json.loads(
            (EXAMPLES / "plugin-invocation-result.json").read_text(encoding="utf-8")
        )

        with self.assertRaisesRegex(RuntimeError, "golden contract"):
            conformance.validate_result(
                result,
                {"unexpected": True},
                profile="offline-fixture",
            )

    def test_check_status_and_stable_error_code_are_a_closed_union(self) -> None:
        example = json.loads(
            (EXAMPLES / "plugin-compatibility-report.json").read_text(
                encoding="utf-8"
            )
        )
        passed_with_error = json.loads(json.dumps(example))
        passed_with_error["spec"]["profiles"][0]["checks"][0]["errorCode"] = (
            "plugin.signature.invalid"
        )
        failed_without_error = json.loads(json.dumps(example))
        failed_without_error["spec"]["profiles"][0]["checks"][0]["status"] = (
            "failed"
        )

        for label, report in (
            ("passed check with error", passed_with_error),
            ("failed check without error", failed_without_error),
        ):
            with self.subTest(label=label):
                with self.assertRaises(RuntimeError):
                    conformance.validate_contract(
                        "plugin-compatibility-report.schema.json",
                        report,
                        label=label,
                    )


if __name__ == "__main__":
    unittest.main()
