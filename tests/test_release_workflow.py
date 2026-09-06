from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/release.yml"
COSIGN_WRAPPER = ROOT / "scripts/cosign_container.sh"


class ReleaseWorkflowTests(unittest.TestCase):
    def test_release_workflow_has_a_bounded_protected_identity(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        action_references = re.findall(r"^\s*- uses: ([^\s#]+)", workflow, re.M)

        self.assertEqual(len(action_references), 4)
        for reference in action_references:
            with self.subTest(reference=reference):
                self.assertRegex(
                    reference,
                    r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[a-f0-9]{40}$",
                )
        self.assertIn('      - "v*"', workflow)
        self.assertNotIn("workflow_dispatch", workflow)
        self.assertIn("environment: release", workflow)
        self.assertIn("timeout-minutes: 90", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("packages: write", workflow)
        self.assertIn("id-token: write", workflow)
        self.assertIn("persist-credentials: false", workflow)

    def test_release_orders_closed_gates_before_external_release(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        ordered = (
            "validate-github-context",
            "make verify",
            "make release-bundle",
            "release_publication.py publish",
            "cosign_container.sh sign --yes",
            "make qualify-release-signatures",
            "make qualify-release-vulnerabilities",
            "cosign_container.sh sign-blob --yes",
            "gh release create",
        )
        offsets = [workflow.index(item) for item in ordered]
        self.assertEqual(offsets, sorted(offsets))
        self.assertNotIn(":latest", workflow)
        self.assertIn("--verify-tag", workflow)
        self.assertIn("release-publication-report.json", workflow)
        self.assertIn("release-signature-verification-report.json", workflow)
        self.assertIn("release-vulnerability-qualification-report.json", workflow)

    def test_cosign_wrapper_is_digest_pinned_and_least_authority(self) -> None:
        wrapper = COSIGN_WRAPPER.read_text(encoding="utf-8")

        self.assertRegex(
            wrapper,
            r"IIP_COSIGN_IMAGE=ghcr\.io/sigstore/cosign/cosign@sha256:[a-f0-9]{64}",
        )
        for control in (
            "--read-only",
            "--cap-drop ALL",
            "--security-opt no-new-privileges:true",
            "--memory 256m",
            "--memory-swap 256m",
            "--pids-limit 128",
            'IIP_COSIGN_ROOT/dist:/workspace/dist:rw',
            'IIP_COSIGN_CONFIG:/docker-config:ro',
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
            "ACTIONS_ID_TOKEN_REQUEST_URL",
        ):
            with self.subTest(control=control):
                self.assertIn(control, wrapper)
        self.assertNotIn("--privileged", wrapper)
        self.assertNotIn("/var/run/docker.sock", wrapper)
        self.assertNotIn('IIP_COSIGN_ROOT:$IIP_COSIGN_ROOT', wrapper)


if __name__ == "__main__":
    unittest.main()
