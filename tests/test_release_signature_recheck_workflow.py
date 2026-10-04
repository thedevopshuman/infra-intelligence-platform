from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ReleaseSignatureRecheckWorkflowTests(unittest.TestCase):
    def test_recheck_is_manual_read_only_and_never_rebuilds_or_promotes(self) -> None:
        workflow = (ROOT / ".github/workflows/release-signature-recheck.yml").read_text()
        self.assertIn("  workflow_dispatch:", workflow)
        self.assertIn("  contents: read", workflow)
        self.assertIn("timeout-minutes: 10", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn("scripts/recheck_release_signatures.py", workflow)
        for forbidden in ("  push:", "  schedule:", "  pull_request:", "secrets.",
                          "id-token:", "contents: write", "environment: release",
                          "make verify", "make release-bundle", "docker login",
                          "sign --yes", "sign-blob", "gh release create"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, workflow)
        actions = re.findall(r"^\s+- uses: (\S+)", workflow, re.M)
        self.assertEqual(len(actions), 2)
        for action in actions:
            self.assertRegex(action, r"^[\w/-]+@[a-f0-9]{40}$")
        # Dispatch inputs cross environment variables, never shell source text.
        run_body = workflow.split("        run: |", 1)[1]
        self.assertNotIn("${{", run_body)
        for variable in ("RELEASE_TAG", "CONTROL_DIGEST", "BRIDGE_DIGEST"):
            self.assertIn(f'"${variable}"', run_body)


if __name__ == "__main__":
    unittest.main()
