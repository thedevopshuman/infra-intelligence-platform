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
        action_references = re.findall(r"^\s+(?:- )?uses: ([^\s#]+)", workflow, re.M)

        self.assertEqual(len(action_references), 6)
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
            "make verify-workflows",
            "validate-github-context",
            "Set up ARM64 emulation",
            "Set up release OCI builder",
            "Verify release builder platforms",
            "\n          make verify\n",
            "make release-bundle",
            "make qualify-release-vulnerabilities",
            "Authenticate the publication boundary",
            "release_publication.py publish",
            "cosign_container.sh sign --yes",
            "make qualify-release-signatures",
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

    def test_vulnerability_gate_precedes_registry_authority_and_mutation(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        step_names = re.findall(r"(?m)^      - name: (.+)$", workflow)
        build = step_names.index("Verify and build the clean candidate")
        self.assertEqual(step_names[build + 1], "Qualify release vulnerabilities")
        self.assertEqual(workflow.count("make qualify-release-vulnerabilities"), 1)
        vulnerability = workflow.index("make qualify-release-vulnerabilities")
        for later in (
            "DOCKERHUB_TOKEN: ${{ secrets.DOCKERHUB_TOKEN }}",
            "docker login docker.io",
            "release_publication.py publish",
            "cosign_container.sh sign --yes",
        ):
            with self.subTest(later=later):
                self.assertLess(vulnerability, workflow.index(later))
        self.assertGreater(vulnerability, workflow.index("make release-bundle"))
        self.assertIn(
            "IIP_RELEASE_VULNERABILITY_REPORT: "
            "dist/release-vulnerability-qualification-report.json",
            workflow,
        )

    def test_multiarch_builder_is_explicit_and_immutable(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        steps = {
            step.split("\n", 1)[0]: step
            for step in re.split(r"(?m)^      - name: ", workflow)[1:]
        }
        emulation = steps["Set up ARM64 emulation"]
        builder = steps["Set up release OCI builder"]
        validation = steps["Verify release builder platforms"]
        build = steps["Verify and build the clean candidate"]
        self.assertIn("docker/setup-qemu-action@99012661954931238ded8c8b007157a8430204e1", emulation)
        self.assertIn("platforms: arm64", emulation)
        self.assertIn(
            "image: docker.io/tonistiigi/binfmt@sha256:"
            "400a4873b838d1b89194d982c45e5fb3cda4593fbfd7e08a02e76b03b21166f0",
            emulation,
        )
        self.assertIn("docker/setup-buildx-action@f87e5991a6d7451dcb8d9637bfbc97413f497069", builder)
        self.assertIn("id: buildx", builder)
        self.assertIn("version: v0.37.2", builder)
        self.assertIn("driver: docker-container", builder)
        self.assertIn(
            "driver-opts: image=docker.io/moby/buildkit@sha256:"
            "cec9f139f45e93c5c69c60f8b07cfad9f43f4ef6b6a6cd917527fea5ff2e3dea",
            builder,
        )
        self.assertIn("use: true", builder)
        self.assertIn("cleanup: true", builder)
        # Do not override detected platforms: the next step verifies actual
        # builder output rather than a caller-asserted capability list.
        self.assertNotIn("platforms:", builder)
        self.assertIn("BUILDER_PLATFORMS: ${{ steps.buildx.outputs.platforms }}", validation)
        self.assertIn("docker buildx inspect --bootstrap", validation)
        self.assertIn("IIP_RELEASE_PLATFORMS: linux/amd64,linux/arm64", build)
        self.assertIn(
            "DOCKER_CONFIG: /tmp/iip-release-docker-${{ github.run_id }}-${{ github.run_attempt }}",
            workflow,
        )

    def test_builder_platform_check_rejects_missing_architectures(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        validation = workflow.split(
            "      - name: Verify release builder platforms\n", 1,
        )[1].split("\n      - name:", 1)[0]
        command = next(line.strip() for line in validation.splitlines() if "python -c " in line)
        # Execute only the stdlib capability check, never Docker or binfmt.
        expression = command.removeprefix("python -c '").removesuffix("'")
        for platforms, accepted in (
            ("linux/amd64,linux/arm64", True),
            ("linux/arm64, linux/amd64,linux/386", True),
            ("linux/amd64", False),
            ("linux/arm64", False),
            ("linux/amd64/v2,linux/arm/v7", False),
            ("", False),
        ):
            with self.subTest(platforms=platforms):
                completed = subprocess.run(
                    [sys.executable, "-c", expression],
                    env={"BUILDER_PLATFORMS": platforms},
                    stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(completed.returncode == 0, accepted, completed.stderr)

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
        self.assertIn(
            "DOCKER_CONFIG: /tmp/iip-release-docker-${{ github.run_id }}-${{ github.run_attempt }}",
            workflow,
        )
        self.assertNotIn("${{ runner.temp }}", workflow)
        cleanup = [step for step in re.split(r"(?m)^      - name: ", workflow)
                   if "docker logout docker.io" in step]
        self.assertEqual(len(cleanup), 1)
        self.assertRegex(cleanup[0], r"(?m)^\s+if:\s+(?:\$\{\{\s*)?always\(\)")
        self.assertGreater(workflow.index("docker logout docker.io"), workflow.index("gh release create"))

    def test_real_workflow_validation_runs_before_ci_verification_and_release_authentication(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        verify_job = ci.split("\n  verify:\n", 1)[1].split("\n  plugin-compatibility:\n", 1)[0]
        self.assertLess(verify_job.index("make verify-workflows"), verify_job.index("run: make verify\n"))
        self.assertLess(workflow.index("make verify-workflows"), workflow.index("Authenticate the publication boundary"))
        completed = subprocess.run(
            ["make", "--no-print-directory", "-n", "verify-workflows", "GO=go"],
            cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12", completed.stdout)
        self.assertIn("-shellcheck= -pyflakes= .github/workflows/*.yml", completed.stdout)
        self.assertNotIn("go install", completed.stdout)

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
            "TUF_ROOT=/tmp/sigstore",
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

    def test_cosign_wrapper_uses_private_ephemeral_tuf_cache_without_host_authority(self) -> None:
        with tempfile.TemporaryDirectory(prefix="iip-cosign-wrapper-") as temporary:
            directory = Path(temporary).resolve()
            scripts = directory / "scripts"
            scripts.mkdir()
            wrapper = scripts / "cosign_container.sh"
            wrapper.write_text(COSIGN_WRAPPER.read_text(encoding="utf-8"), encoding="utf-8")
            fake_docker = directory / "docker"
            fake_docker.write_text('#!/bin/sh\nprintf "%s\\0" "$@"\n', encoding="utf-8")
            fake_docker.chmod(0o700)
            config = directory / "anonymous-docker"
            for configured in (False, True):
                with self.subTest(configured=configured):
                    if configured:
                        config.mkdir(mode=0o700)
                    completed = subprocess.run(
                        ["/bin/sh", str(wrapper), "initialize", "--timeout=60s"],
                        env={
                            "PATH": "/usr/bin:/bin",
                            "IIP_COSIGN_DOCKER_BIN": str(fake_docker),
                            "DOCKER_CONFIG": str(config),
                            "TUF_ROOT": "/host-cache-must-not-be-used",
                            "TUF_MIRROR": "https://untrusted.example.invalid",
                            "GITHUB_TOKEN": "synthetic-token-must-not-be-forwarded",
                            "AWS_ACCESS_KEY_ID": "synthetic-key-must-not-be-forwarded",
                        },
                        check=True, capture_output=True, timeout=10,
                    )
                    arguments = completed.stdout.decode().rstrip("\0").split("\0")
                    self.assertEqual(arguments[:3], ["run", "--rm", "--read-only"])
                    self.assertEqual(arguments[-2:], ["initialize", "--timeout=60s"])
                    self.assertRegex(arguments[-3], r"^ghcr\.io/sigstore/cosign/cosign@sha256:[a-f0-9]{64}$")
                    self.assertEqual(arguments[arguments.index("--tmpfs") + 1],
                                     "/tmp:rw,noexec,nosuid,nodev,size=32m")
                    variables = [arguments[index + 1] for index, value in enumerate(arguments) if value == "-e"]
                    self.assertEqual(variables, [
                        "TUF_ROOT=/tmp/sigstore", "ACTIONS_ID_TOKEN_REQUEST_TOKEN",
                        "ACTIONS_ID_TOKEN_REQUEST_URL", "GITHUB_ACTIONS",
                        *(["DOCKER_CONFIG=/docker-config"] if configured else []),
                    ])
                    mounts = [arguments[index + 1] for index, value in enumerate(arguments) if value == "--volume"]
                    self.assertEqual(mounts, [
                        f"{directory}/dist:/workspace/dist:rw",
                        *([f"{config}:/docker-config:ro"] if configured else []),
                    ])
                    for forbidden in ("--privileged", "--env-file", "--insecure-ignore-tlog",
                                      "--insecure-ignore-sct", "--check-claims=false"):
                        self.assertNotIn(forbidden, arguments)
                    self.assertFalse(any("HOME=" in value or "must-not" in value for value in arguments))
                    self.assertFalse((directory / "sigstore").exists())


if __name__ == "__main__":
    unittest.main()
