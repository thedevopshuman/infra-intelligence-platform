from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from scripts import run_github_context_compatibility as compatibility
from scripts import validate_repo


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = (
    ROOT
    / "contracts"
    / "examples"
    / "github-context-compatibility-report.json"
)


class GithubContextCompatibilityTests(unittest.TestCase):
    def test_real_tls_profile_covers_the_closed_checks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            checks = compatibility.run_profile(Path(temporary))

        self.assertEqual(
            tuple(check["id"] for check in checks),
            compatibility.CHECK_IDS[:-1],
        )
        self.assertTrue(all(check["status"] == "passed" for check in checks))

    def test_report_is_closed_source_bound_minimized_and_schema_valid(self) -> None:
        checks = tuple(
            {"id": identifier, "status": "passed"}
            for identifier in compatibility.CHECK_IDS[:-1]
        )
        report = compatibility.compatibility_report(
            revision="0123456789abcdef0123456789abcdef01234567",
            source_dirty=True,
            generated_at="2026-09-06T12:00:00Z",
            platform_name="darwin/arm64",
            python_version="3.14.3",
            checks=checks,
        )

        compatibility.validate_report(report)
        self.assertEqual(report["metadata"]["id"], compatibility.report_id(report))
        self.assertEqual(
            tuple(check["id"] for check in report["spec"]["checks"]),
            compatibility.CHECK_IDS,
        )
        encoded = json.dumps(report, sort_keys=True)
        self.assertNotIn(compatibility.TOKEN, encoded)
        self.assertNotIn(compatibility.TENANT, encoded)
        self.assertNotIn("127.0.0.1", encoded)

    def test_semantic_validation_rejects_tampering(self) -> None:
        report = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        report["spec"]["checks"][0] = {
            "id": "ca-verified-tls",
            "status": "failed",
            "errorCode": "context.document.unavailable",
        }

        errors: list[str] = []
        validate_repo.validate_github_context_compatibility_document(report, errors)

        self.assertIn(
            "GitHub context compatibility status must match its checks", errors
        )
        self.assertIn(
            "GitHub context compatibility summary must match its checks", errors
        )
        self.assertIn(
            "GitHub context compatibility report ID must be content-derived", errors
        )

        reordered = copy.deepcopy(report)
        reordered["spec"]["checks"].reverse()
        errors = []
        validate_repo.validate_github_context_compatibility_document(
            reordered, errors
        )
        self.assertIn(
            "GitHub context compatibility checks must match the closed profile", errors
        )


if __name__ == "__main__":
    unittest.main()
