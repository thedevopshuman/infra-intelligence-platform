"""Image preparation/start ordering at the installer composition boundary."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_community_stack import docker_result, installation_inputs
import community_stack as stack
import community_images as images
import community_recovery as recovery


REFERENCE = "ghcr.io/thedevopshuman/infra-intelligence-platform@sha256:" + "1" * 64
IDENTITIES = {name: "sha256:" + str(index) * 64 for index, name in enumerate(
    ("IIP_COMMUNITY_IMAGE", *images.DEFAULT_IMAGES), start=1)}


class CommunityDigestStartTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "installation"
        stack.initialize(self.state, installation_inputs(), image=REFERENCE)
        for context in (
            patch("community_docker._is_socket", return_value=True),
            patch("community_docker.shutil.which", return_value="/trusted/docker"),
        ):
            context.start()
            self.addCleanup(context.stop)

    def cli(self, *arguments: str) -> tuple[int, str]:
        output = io.StringIO()
        with redirect_stdout(output), redirect_stderr(output):
            result = stack.main(["--state", str(self.state), *arguments])
        return result, output.getvalue()

    def test_digest_up_resolves_after_guard_without_secrets_and_runs_only_local_ids(self) -> None:
        order = []

        def guard(state, arguments, values):
            order.append("guard")
            self.assertEqual(values["IIP_COMMUNITY_IMAGE"], REFERENCE)
            return True

        def resolve(image, **kwargs):
            order.append("resolve")
            self.assertEqual(image, REFERENCE)
            self.assertEqual(kwargs["executable"], "/trusted/docker")
            self.assertEqual(kwargs["endpoint"], "unix:///tmp/community-test.sock")
            self.assertNotIn("pull", kwargs)
            self.assertFalse(any(key.startswith(("IIP_", "COMPOSE_", "PG")) for key in kwargs["environment"]))
            self.assertNotIn("DOCKER_DEFAULT_PLATFORM", kwargs["environment"])
            return dict(IDENTITIES)

        with (
            patch.dict(os.environ, {"IIP_COMMUNITY_IMAGE": "attacker", "PGPASSWORD": "secret", "DOCKER_DEFAULT_PLATFORM": "linux/other"}),
            patch.object(subprocess, "run", side_effect=docker_result) as run,
            patch("community_trust.guard_start", side_effect=guard),
            patch.object(images, "resolve_images", side_effect=resolve),
            stack.installation_lock(self.state),
        ):
            stack.run_compose(self.state, ["up", "--detach"])
        self.assertEqual(order, ["guard", "resolve"])
        command = run.call_args.args[0]
        self.assertEqual(command[command.index("--env-file") + 1], os.devnull)
        self.assertEqual(command[-5:], ["up", "--detach", "--no-build", "--pull", "never"])
        for variable, identifier in IDENTITIES.items():
            self.assertEqual(run.call_args.kwargs["env"][variable], identifier)
        self.assertNotIn("DOCKER_DEFAULT_PLATFORM", run.call_args.kwargs["env"])

    def test_compose_ignores_implicit_dotenv_lifecycle_overrides(self) -> None:
        # Use an owned working directory, never create or read a user's .env.
        directory = self.state.parent / "working"
        directory.mkdir()
        (directory / ".env").write_text("COMPOSE_REMOVE_ORPHANS=true\nCOMPOSE_PROFILES=unexpected\nDOCKER_DEFAULT_PLATFORM=linux/other\n")
        with (
            patch.object(stack, "ROOT", directory),
            patch.dict(os.environ, {"COMPOSE_REMOVE_ORPHANS": "true", "COMPOSE_PROFILES": "unexpected"}),
            patch.object(subprocess, "run", side_effect=docker_result) as run,
        ):
            stack.run_compose(self.state, ["ps"], validate_contracts=False)
        command = run.call_args.args[0]
        self.assertEqual(run.call_args.kwargs["cwd"], directory)
        self.assertEqual(command[command.index("--env-file") + 1], os.devnull)
        self.assertNotIn("COMPOSE_REMOVE_ORPHANS", run.call_args.kwargs["env"])
        self.assertNotIn("COMPOSE_PROFILES", run.call_args.kwargs["env"])

    def test_digest_start_missing_image_prevents_compose_and_runtime_receipts(self) -> None:
        with (
            patch.object(subprocess, "run", side_effect=docker_result) as run,
            patch("community_trust.guard_start", return_value=True),
            patch.object(images, "resolve_images", side_effect=images.CommunityImageError("community.image.unavailable")),
            patch("community_recovery.record_runtime") as record,
        ):
            result, output = self.cli("up")
        self.assertEqual(result, 2)
        self.assertIn("community.operation.failed", output)
        self.assertFalse(any("compose" in call.args[0] for call in run.call_args_list))
        record.assert_not_called()
        self.assertFalse((self.state / "runtime-images.json").exists())

    def test_healthy_repeat_up_never_resolves_or_runs_compose(self) -> None:
        with (
            patch.object(subprocess, "run", side_effect=docker_result) as run,
            patch("community_trust.guard_start", return_value=False),
            patch.object(images, "resolve_images", side_effect=AssertionError("cache not consulted")) as resolve,
            stack.installation_lock(self.state),
        ):
            self.assertEqual(stack.run_compose(self.state, ["up"]), "")
        resolve.assert_not_called()
        self.assertFalse(any("compose" in call.args[0] for call in run.call_args_list))

    def test_digest_build_is_rejected_before_docker(self) -> None:
        with patch.object(subprocess, "run") as run:
            result, _ = self.cli("up", "--build")
            self.assertEqual(result, 2)
            for arguments in (["build", "initialize"], ["up", "--build"]):
                with self.assertRaisesRegex(stack.InstallationError, "digest-build-prohibited"):
                    stack.run_compose(self.state, arguments)
        run.assert_not_called()

    def test_images_reads_only_non_secret_metadata_without_configuration_or_health(self) -> None:
        original = stack.read_protected

        def read(path):
            self.assertEqual(path.name, "installation.json")
            return original(path)

        for pull in (False, True):
            with (
                self.subTest(pull=pull),
                patch.object(stack, "read_protected", side_effect=read),
                patch.object(stack, "installed_environment", side_effect=AssertionError("no secrets/config")),
                patch.object(subprocess, "run", side_effect=docker_result) as run,
                patch.object(images, "resolve_images", return_value=dict(IDENTITIES)) as resolve,
            ):
                result, output = self.cli("images", *(["--pull"] if pull else []))
                self.assertEqual(result, 0)
                self.assertIn("Publisher/signature verification is separate", output)
                self.assertEqual(resolve.call_args.kwargs["pull"], pull)
                self.assertFalse(any(key.startswith(("IIP_", "PG", "COMPOSE_")) for key in resolve.call_args.kwargs["environment"]))
                self.assertFalse(any("compose" in call.args[0] for call in run.call_args_list))
                self.assertNotIn(REFERENCE, output)
        self.assertFalse((self.state / "runtime-images.json").exists())

    def test_successful_image_preparation_is_rechecked_before_start(self) -> None:
        with (
            patch.object(subprocess, "run", side_effect=docker_result) as run,
            patch("community_trust.guard_start", return_value=True),
            patch.object(images, "resolve_images", side_effect=[dict(IDENTITIES), images.CommunityImageError("community.image.unavailable")]) as resolve,
        ):
            self.assertEqual(self.cli("images")[0], 0)
            self.assertEqual(self.cli("up")[0], 2)
        self.assertEqual(resolve.call_count, 2)
        self.assertFalse(any("compose" in call.args[0] for call in run.call_args_list))

    def test_recovered_digest_selection_uses_only_historical_local_ids(self) -> None:
        snapshot = {
            "format": 1, "deployment": recovery.deployment_digest(), "os": "linux", "architecture": "arm64",
            "images": {service: "sha256:" + "a" * 64 for service in recovery.SERVICES},
        }
        stack.write_protected(self.state / "recovery-images.json", snapshot)
        with (
            patch.object(subprocess, "run", side_effect=docker_result) as run,
            patch("community_trust.guard_start", return_value=True),
            patch.object(images, "resolve_images", side_effect=AssertionError("recovery is offline")) as resolve,
            patch.object(recovery, "RecoveryDocker") as docker,
            patch.object(recovery, "record_runtime"),
            patch.object(stack, "wait_for_collector"),
            patch("community_trust.record_started"),
        ):
            result, _ = self.cli("up")
        self.assertEqual(result, 0)
        docker.return_value.require_images.assert_called_once_with(snapshot)
        resolve.assert_not_called()
        command = run.call_args.args[0]
        self.assertEqual(command[-2:], ["--pull", "never"])
        self.assertIn("--no-build", command)
        for variable in IDENTITIES:
            self.assertEqual(run.call_args.kwargs["env"][variable], "sha256:" + "a" * 64)
        with patch.object(subprocess, "run") as run:
            self.assertEqual(self.cli("images", "--pull")[0], 2)
            self.assertEqual(self.cli("up", "--build")[0], 2)
        run.assert_not_called()

    def test_check_status_down_do_not_resolve_images(self) -> None:
        with (
            patch.object(subprocess, "run", side_effect=docker_result),
            patch.object(images, "resolve_images", side_effect=AssertionError("not startup")) as resolve,
        ):
            for command in ("check", "status", "down"):
                self.assertEqual(self.cli(command)[0], 0, command)
        resolve.assert_not_called()

    def test_source_profile_rejects_image_preparation_without_daemon_use(self) -> None:
        manifest = stack.read_protected(self.state / "installation.json")
        manifest["image"] = "iip-community:test"
        recovery.atomic_document(self.state / "installation.json", manifest)
        with patch.object(subprocess, "run") as run:
            self.assertEqual(self.cli("images", "--pull")[0], 2)
        run.assert_not_called()

    def test_malformed_digest_selection_never_falls_back_to_source_mode(self) -> None:
        for image in ("iip@sha256:" + "a" * 64, "ghcr.io/team/iip:latest@sha256:" + "a" * 64, "ghcr.io/team/iip@sha256:bad"):
            with self.subTest(image=image), self.assertRaises(ValueError):
                stack.initialize(self.state.parent / "invalid", installation_inputs(), image=image)
            self.assertFalse((self.state.parent / "invalid").exists())

    def test_incomplete_recovery_and_lock_contention_block_image_preparation(self) -> None:
        with patch.object(subprocess, "run") as run:
            with stack.installation_lock(self.state):
                self.assertEqual(self.cli("images", "--pull")[0], 2)
            stack.write_protected(self.state / ".recovery-incomplete", {})
            self.assertEqual(self.cli("images", "--pull")[0], 2)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
