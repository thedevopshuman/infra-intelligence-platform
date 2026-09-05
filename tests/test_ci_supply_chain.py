from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"


class CiSupplyChainTests(unittest.TestCase):
    def test_actions_and_service_image_are_immutable(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        action_references = re.findall(r"^\s*- uses: ([^\s#]+)", workflow, re.M)

        self.assertEqual(len(action_references), 11)
        for reference in action_references:
            with self.subTest(reference=reference):
                self.assertRegex(reference, r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[a-f0-9]{40}$")
        self.assertNotRegex(workflow, r"uses:\s+[^\s]+@v[0-9]")
        self.assertRegex(
            workflow,
            r"image: postgres:18\.4-alpine@sha256:[a-f0-9]{64}",
        )

    def test_workflow_has_bounded_read_only_execution(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn("contents: read", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("runs-on: ubuntu-24.04", workflow)
        self.assertIn("timeout-minutes: 15", workflow)
        self.assertIn("cancel-in-progress: true", workflow)
        self.assertIn("version: v4.1.3", workflow)
        self.assertIn("make test-postgres-continuity", workflow)
        self.assertIn("make test-release-signatures", workflow)

    def test_dependency_updates_remain_reviewed_pull_requests(self) -> None:
        configuration = (ROOT / ".github" / "dependabot.yml").read_text(
            encoding="utf-8"
        )

        for ecosystem in ("github-actions", "pip", "npm"):
            with self.subTest(ecosystem=ecosystem):
                self.assertIn(f"package-ecosystem: {ecosystem}", configuration)
        self.assertEqual(configuration.count("interval: weekly"), 3)


if __name__ == "__main__":
    unittest.main()
