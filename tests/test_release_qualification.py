from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from release_qualification import (  # noqa: E402
    INSTALL_CHECKS,
    UPGRADE_CHECKS,
    ReleaseQualificationError,
    record_install,
    verify_report,
)
from release_bundle import sha256_file  # noqa: E402
from infra_intelligence_sdk import ReleaseQualificationReport  # noqa: E402


def repository_document(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


class ReleaseQualificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        self.manifest = repository_document(
            "contracts/examples/release-manifest.json"
        )
        self.manifest_path = self.bundle / "release-manifest.json"
        self.manifest_path.write_text(
            json.dumps(self.manifest, sort_keys=True), encoding="utf-8"
        )
        self.report_path = self.root / "release-qualification-report.json"
        self.report = repository_document(
            "contracts/examples/release-qualification-report.json"
        )
        self.report["spec"]["candidate"] = {
            "version": self.manifest["metadata"]["version"],
            "chartVersion": self.manifest["metadata"]["chartVersion"],
            "revision": self.manifest["metadata"]["revision"],
            "releaseManifestDigest": f"sha256:{sha256_file(self.manifest_path)}",
            "controlPlaneImageDigest": self.manifest["spec"]["image"][
                "indexDigest"
            ],
            "platforms": [
                item["name"]
                for item in self.manifest["spec"]["image"]["platforms"]
            ],
            "signatureStatus": self.manifest["metadata"]["signatureStatus"],
        }
        self.report["metadata"]["sourceRevision"] = self.manifest["metadata"][
            "revision"
        ]
        runtime = self.report["spec"]["profiles"][0]["measurement"]["runtime"]
        runtime.update(
            {
                "version": self.manifest["metadata"]["version"],
                "chartVersion": self.manifest["metadata"]["chartVersion"],
                "revision": self.manifest["metadata"]["revision"],
                "imageDigest": self.manifest["spec"]["image"]["indexDigest"],
            }
        )
        target = self.report["spec"]["profiles"][1]["measurement"]["target"]
        target.update(
            {
                "version": self.manifest["metadata"]["version"],
                "chartVersion": self.manifest["metadata"]["chartVersion"],
                "revision": self.manifest["metadata"]["revision"],
                "imageDigest": self.manifest["spec"]["image"]["indexDigest"],
            }
        )
        self.report_path.write_text(
            json.dumps(self.report, sort_keys=True), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def verify(self, *, require_complete: bool = True) -> dict:
        with patch("release_qualification.verify_bundle", return_value=self.manifest):
            return dict(
                verify_report(
                    self.report_path,
                    self.bundle,
                    require_complete=require_complete,
                )
            )

    def write(self, report: dict) -> None:
        self.report_path.write_text(
            json.dumps(report, sort_keys=True), encoding="utf-8"
        )

    def test_complete_report_is_manifest_bound_and_sdk_readable(self) -> None:
        verified = self.verify()

        model = ReleaseQualificationReport.from_dict(verified)
        self.assertEqual(model.to_dict()["spec"]["status"], "qualified")
        self.assertEqual(
            tuple(item["id"] for item in verified["spec"]["profiles"][0]["checks"]),
            INSTALL_CHECKS,
        )
        self.assertEqual(
            tuple(item["id"] for item in verified["spec"]["profiles"][1]["checks"]),
            UPGRADE_CHECKS,
        )

    def test_rewritten_manifest_binding_is_rejected(self) -> None:
        report = copy.deepcopy(self.report)
        report["spec"]["candidate"]["releaseManifestDigest"] = (
            "sha256:" + "f" * 64
        )
        self.write(report)

        with self.assertRaisesRegex(
            ReleaseQualificationError, "qualification.candidate.mismatch"
        ):
            self.verify()

    def test_caller_cannot_invent_or_reorder_closed_checks(self) -> None:
        report = copy.deepcopy(self.report)
        checks = report["spec"]["profiles"][1]["checks"]
        checks[0], checks[1] = checks[1], checks[0]
        self.write(report)

        with self.assertRaisesRegex(
            ReleaseQualificationError, "qualification.profile.checks.invalid"
        ):
            self.verify()

    def test_availability_totals_are_derived_and_zero_failure(self) -> None:
        report = copy.deepcopy(self.report)
        availability = report["spec"]["profiles"][1]["measurement"][
            "availability"
        ]
        availability["requestCount"] += 1
        self.write(report)

        with self.assertRaisesRegex(
            ReleaseQualificationError, "qualification.availability.invalid"
        ):
            self.verify()

    def test_incomplete_report_cannot_be_promoted(self) -> None:
        report = copy.deepcopy(self.report)
        report["spec"]["profiles"] = report["spec"]["profiles"][:1]
        report["spec"]["status"] = "incomplete"
        report["spec"]["summary"] = {
            "requiredProfiles": 2,
            "passedProfiles": 1,
            "overallStatus": "incomplete",
        }
        self.write(report)

        self.assertEqual(self.verify(require_complete=False)["spec"]["status"], "incomplete")
        with self.assertRaisesRegex(
            ReleaseQualificationError,
            "qualification.required-profiles.incomplete",
        ):
            self.verify(require_complete=True)

    def test_report_must_remain_outside_the_immutable_bundle(self) -> None:
        inside = self.bundle / "qualification.json"
        inside.write_text(json.dumps(self.report), encoding="utf-8")

        with self.assertRaisesRegex(
            ReleaseQualificationError, "qualification.output.inside-bundle"
        ):
            with patch(
                "release_qualification.verify_bundle", return_value=self.manifest
            ):
                verify_report(inside, self.bundle)

    def test_install_recorder_rejects_runtime_identity_claims(self) -> None:
        runtime_path = self.root / "runtime.json"
        runtime_path.write_text(
            json.dumps(
                {
                    "apiVersion": "iip.platform/v1alpha1",
                    "kind": "RuntimeVersionReport",
                    "metadata": {
                        "tenantId": "not-retained",
                        "evaluatedAt": "2026-09-05T14:20:00Z",
                    },
                    "spec": {
                        "application": {"version": "9.9.9"},
                        "storage": {
                            "requiredMigration": "0022_ai_retry_attempt_attribution.sql"
                        },
                        "build": {
                            "mode": "release",
                            "revision": self.manifest["metadata"]["revision"],
                        },
                        "deployment": {
                            "helmChartVersion": self.manifest["metadata"][
                                "chartVersion"
                            ],
                            "imageDigest": self.manifest["spec"]["image"][
                                "indexDigest"
                            ],
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        candidate = self.report["spec"]["candidate"]

        with patch(
            "release_qualification._candidate", return_value=(self.manifest, candidate)
        ), patch("release_qualification._require_clean_source"):
            with self.assertRaisesRegex(
                ReleaseQualificationError,
                "qualification.runtime-report.identity-mismatch",
            ):
                record_install(
                    bundle=self.bundle,
                    output=self.report_path,
                    runtime_report=runtime_path,
                    required_migration="0022_ai_retry_attempt_attribution.sql",
                    applied_migration_count=22,
                    final_helm_revision=2,
                    platform="linux/arm64",
                    kubernetes_version="v1.36.1",
                    container_runtime_version="29.1.5",
                )

    def test_packaged_gates_record_their_measured_profiles(self) -> None:
        install = (ROOT / "scripts" / "test_helm_install.sh").read_text(
            encoding="utf-8"
        )
        upgrade = (ROOT / "scripts" / "test_release_upgrade.sh").read_text(
            encoding="utf-8"
        )

        self.assertIn("release_qualification.py record-install", install)
        self.assertIn('runtime-version-report.json"', install)
        self.assertIn("release_qualification.py record-upgrade", upgrade)
        self.assertIn('availability-state.json"', upgrade)
        self.assertIn("--base-revision", upgrade)
        self.assertIn("--target-migration", upgrade)


if __name__ == "__main__":
    unittest.main()
