from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from iip.application.ports import PolicyDecision
from scripts import run_policy_engine_compatibility as compatibility
from scripts import validate_repo


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts" / "examples"


class PolicyEngineCompatibilityTests(unittest.TestCase):
    def test_fixture_credentials_are_distinct_and_client_starts_at_generation_a(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            tokens = compatibility.write_fixture(directory)

            self.assertNotEqual(tokens["generation-a"], tokens["generation-b"])
            self.assertGreaterEqual(len(tokens["generation-a"]), 32)
            self.assertEqual(
                (directory / "client-token").read_text(encoding="ascii"),
                tokens["generation-a"],
            )
            self.assertNotIn(
                tokens["generation-a"],
                (directory / "audit.jsonl").read_text(encoding="utf-8"),
            )

    def test_stable_unavailable_decision_never_grants_authority(self) -> None:
        decision = PolicyDecision(
            False,
            "policy.unavailable",
            "policy://tenant-a/snapshots/unavailable",
        )

        compatibility.expect_unavailable(decision)

        with self.assertRaises(RuntimeError):
            compatibility.expect_unavailable(
                PolicyDecision(
                    True,
                    "policy.fixture-allowed",
                    "policy://tenant-a/snapshots/bundle-generation-1",
                )
            )

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
        self.assertNotIn("tenant-a", encoded)
        self.assertNotIn(compatibility.ENDPOINT, encoded)

    def test_semantic_validation_rejects_inconsistent_summary(self) -> None:
        report = json.loads(
            (EXAMPLES / "policy-engine-compatibility-report.json").read_text(
                encoding="utf-8"
            )
        )
        report["spec"]["checks"][0] = {
            "id": "ca-verified-tls",
            "status": "failed",
            "errorCode": "policy.unavailable",
        }

        errors: list[str] = []
        validate_repo.validate_policy_engine_compatibility_document(report, errors)

        self.assertIn("policy engine compatibility status must match its checks", errors)
        self.assertIn("policy engine compatibility summary must match its checks", errors)


if __name__ == "__main__":
    unittest.main()
