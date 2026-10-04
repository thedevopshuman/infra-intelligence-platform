from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
import tempfile
import textwrap
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
        self.assertNotIn("packages: write", workflow)
        self.assertIn("id-token: write", workflow)
        self.assertIn("persist-credentials: false", workflow)

    def test_release_orders_closed_gates_before_external_release(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        ordered = (
            "validate-github-context",
            "Authenticate the publication boundary",
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

    def test_release_targets_canonical_docker_hub_repositories(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        for output, repository in (
            ("control_repository", "docker.io/thedevopshuman/iip"),
            ("bridge_repository", "docker.io/thedevopshuman/iip-bridge"),
        ):
            self.assertIn(f"{output}={repository}\\n", workflow)
        self.assertNotRegex(workflow, r"docker\s+login\s+ghcr\.io\b")
        self.assertNotIn("GHCR_PASSWORD", workflow)
        self.assertNotIn("control_repository=ghcr.io/", workflow)
        self.assertNotIn("bridge_repository=ghcr.io/", workflow)
        self.assertIn("github.repository == 'thedevopshuman/infra-intelligence-platform'", workflow)
        self.assertIn("DOCKER_CONFIG: ${{ runner.temp }}/iip-release-docker", workflow)
        cleanup = [step for step in re.split(r"(?m)^      - name: ", workflow)
                   if "docker logout docker.io" in step]
        self.assertEqual(len(cleanup), 1)
        self.assertRegex(cleanup[0], r"(?m)^\s+if:\s+(?:\$\{\{\s*)?always\(\)")
        self.assertGreater(workflow.index("docker logout docker.io"), workflow.index("gh release create"))

    def test_docker_hub_login_requires_both_secrets_and_uses_stdin(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        authentication = workflow.split(
            "      - name: Authenticate the publication boundary\n", 1,
        )[1].split("\n      - name:", 1)[0]
        self.assertIn("DOCKERHUB_USERNAME: ${{ secrets.DOCKERHUB_USERNAME }}", authentication)
        self.assertIn("DOCKERHUB_TOKEN: ${{ secrets.DOCKERHUB_TOKEN }}", authentication)
        self.assertNotIn("${{ github.token }}", authentication)
        script = textwrap.dedent(authentication.split("        run: |\n", 1)[1])
        token = "synthetic-test-token-not-a-registry-credential"
        with tempfile.TemporaryDirectory(prefix="iip-release-login-test-") as temporary:
            directory = Path(temporary)
            record = directory / "login.json"
            fake_docker = directory / "docker"
            fake_docker.write_text(
                f"#!{sys.executable}\n"
                "import hashlib,json,os,sys\n"
                "from pathlib import Path\n"
                "Path(os.environ['IIP_TEST_LOGIN_RECORD']).write_text(json.dumps({"
                "'arguments':sys.argv[1:],"
                "'stdinDigest':hashlib.sha256(sys.stdin.buffer.read()).hexdigest()}))\n",
                encoding="utf-8",
            )
            fake_docker.chmod(0o700)
            base_environment = {
                "PATH": f"{directory}:/usr/bin:/bin",
                "IIP_TEST_LOGIN_RECORD": str(record),
                "DOCKERHUB_USERNAME": "thedevopshuman",
                "DOCKERHUB_TOKEN": token,
                "DOCKER_CONFIG": str(directory / "docker-config"),
            }

            def run(environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
                # GitHub's bash failure settings; a harmless fake docker takes
                # precedence over system utilities used to create DOCKER_CONFIG.
                # No user environment, registry, or credentials are consulted,
                # and the fixture records only a hash of stdin.
                return subprocess.run(
                    ["/bin/bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", script],
                    env=environment, stdin=subprocess.DEVNULL, capture_output=True,
                    text=True, timeout=10,
                )

            completed = run(base_environment)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            observed = json.loads(record.read_text(encoding="utf-8"))
            self.assertEqual(observed["arguments"], [
                "login", "docker.io", "--username", "thedevopshuman", "--password-stdin",
            ])
            self.assertEqual(observed["stdinDigest"], hashlib.sha256(token.encode()).hexdigest())
            self.assertNotIn(token, completed.stdout + completed.stderr)
            record.unlink()
            for name in ("DOCKERHUB_USERNAME", "DOCKERHUB_TOKEN"):
                for missing in (False, True):
                    with self.subTest(secret=name, missing=missing):
                        environment = dict(base_environment)
                        if missing:
                            environment.pop(name)
                        else:
                            environment[name] = ""
                        failed = run(environment)
                        self.assertNotEqual(failed.returncode, 0)
                        self.assertFalse(record.exists(), "login ran without both required secrets")
                        self.assertNotIn(token, failed.stdout + failed.stderr)
            wrong_account = {**base_environment, "DOCKERHUB_USERNAME": "unapproved-fixture-account"}
            failed = run(wrong_account)
            self.assertNotEqual(failed.returncode, 0)
            self.assertFalse(record.exists(), "login accepted an account outside the selected namespace")
            self.assertNotIn(token, failed.stdout + failed.stderr)

    def test_community_kit_is_separately_signed_and_published_without_rebuilding(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        preparation = workflow.split(
            "      - name: Prepare signed customer release assets\n", 1,
        )[1].split("\n      - name:", 1)[0]
        publication = workflow.split("      - name: Publish verified GitHub release\n", 1)[1].split(
            "\n      - name:", 1,
        )[0]
        self.assertIn("infra-intelligence-community-", preparation)
        self.assertIn("--bundle dist/community-kit.sigstore.json", preparation)
        self.assertEqual(preparation.count("cosign_container.sh sign-blob --yes"), 2)
        self.assertNotIn("build_installation_kit", preparation)
        self.assertNotIn("installation_kit.py build", preparation)
        self.assertIn("community-kit.sigstore.json#", publication)
        self.assertIn("RELEASE_KIT: ${{ steps.assets.outputs.kit }}", publication)
        self.assertIn('"$RELEASE_KIT#Persistent Compose installation kit"', publication)
        self.assertIn("printf 'kit=%s\\n' \"$kit\" >> \"$GITHUB_OUTPUT\"", preparation)

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
