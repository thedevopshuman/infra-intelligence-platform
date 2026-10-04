from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests.test_community_stack import installation_inputs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import community_recovery as recovery
import community_stack as stack


class CommunityRecoveryStartupTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.state = Path(temporary.name) / "community"
        self.inputs = installation_inputs()
        stack.initialize(self.state, self.inputs, image="iip-community:mutable")
        selected = patch.object(recovery, "deployment_digest", return_value="a" * 64)
        selected.start()
        self.addCleanup(selected.stop)

    def restored(self, *, deployment: str | None = None) -> dict:
        images = {service: "sha256:" + "1" * 64 for service in recovery.SERVICES}
        images.update({service: "sha256:" + str(index) * 64 for index, service in enumerate(
            ("postgres", "otel-collector", "prometheus", "grafana"), start=2
        )})
        snapshot = {
            "format": 1,
            "deployment": deployment or recovery.deployment_digest(),
            "os": "linux",
            "architecture": "arm64",
            "images": images,
        }
        stack.write_protected(self.state / "recovery-images.json", snapshot)
        return snapshot

    def invoke(self, *arguments: str) -> tuple[int, str, str]:
        output = io.StringIO()
        error = io.StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            code = stack.main(["--state", str(self.state), *arguments])
        return code, output.getvalue(), error.getvalue()

    def test_restored_environment_pins_all_image_roles_without_mutating_manifest(self) -> None:
        manifest = (self.state / "installation.json").read_bytes()
        snapshot = self.restored()

        environment, _ = stack.installed_environment(self.state)

        for service, variable in (
            ("api", "IIP_COMMUNITY_IMAGE"),
            ("postgres", "IIP_COMMUNITY_POSTGRES_IMAGE"),
            ("otel-collector", "IIP_COMMUNITY_COLLECTOR_IMAGE"),
            ("prometheus", "IIP_COMMUNITY_PROMETHEUS_IMAGE"),
            ("grafana", "IIP_COMMUNITY_GRAFANA_IMAGE"),
        ):
            self.assertEqual(environment[variable], snapshot["images"][service])
        self.assertEqual((self.state / "installation.json").read_bytes(), manifest)

    def test_restored_up_checks_local_images_never_builds_or_pulls_and_records_after_health(self) -> None:
        snapshot = self.restored()
        events: list[str] = []
        with (
            patch.object(recovery, "RecoveryDocker") as docker,
            patch.object(stack, "run_compose", side_effect=lambda *_: events.append("up")) as compose,
            patch.object(stack, "wait_for_collector", side_effect=lambda *_: events.append("health")),
            patch.object(recovery, "record_runtime", side_effect=lambda *_: events.append("record")) as record,
        ):
            docker.return_value.require_images.side_effect = lambda *_: events.append("images")
            code, _, _ = self.invoke("up")

        self.assertEqual(code, 0)
        self.assertEqual(events, ["images", "up", "health", "record"])
        docker.assert_called_once_with(self.state)
        docker.return_value.require_images.assert_called_once_with(snapshot)
        compose.assert_called_once_with(self.state, [
            "up", "--detach", "--wait", "--wait-timeout", "240", "--no-build", "--pull", "never",
        ])
        record.assert_called_once_with(self.state)

    def test_restored_build_request_is_rejected_before_docker(self) -> None:
        self.restored()
        with patch.object(stack, "run_compose") as compose, patch.object(recovery, "RecoveryDocker") as docker:
            code, _, error = self.invoke("up", "--build")

        self.assertEqual(code, 2)
        self.assertIn("community.operation.failed", error)
        compose.assert_not_called()
        docker.assert_not_called()

    def test_unavailable_restored_images_prevent_start_without_build_fallback(self) -> None:
        self.restored()
        with patch.object(stack, "run_compose") as compose, patch.object(recovery, "RecoveryDocker") as docker:
            docker.return_value.require_images.side_effect = recovery.RecoveryError(
                "community.recovery.image-mismatch"
            )
            code, _, _ = self.invoke("up")

        self.assertEqual(code, 2)
        compose.assert_not_called()

    def test_changed_deployment_rejects_check_and_start_without_docker(self) -> None:
        self.restored(deployment="b" * 64)
        with patch.object(stack, "run_compose") as compose, patch.object(recovery, "RecoveryDocker") as docker:
            for command in ("check", "up"):
                with self.subTest(command=command):
                    self.assertEqual(self.invoke(command)[0], 2)
        compose.assert_not_called()
        docker.assert_not_called()

    def test_check_validates_pinned_snapshot_without_contacting_docker(self) -> None:
        self.restored()
        with patch.object(recovery, "RecoveryDocker") as docker, patch.object(stack.subprocess, "run") as run:
            self.assertEqual(self.invoke("check")[0], 0)
        docker.assert_not_called()
        run.assert_not_called()

    def test_incomplete_restore_blocks_check_up_and_configure_before_compose(self) -> None:
        marker = self.state / ".recovery-incomplete"
        for symlink in (False, True):
            with self.subTest(symlink=symlink):
                if symlink:
                    marker.symlink_to(self.state / "absent")
                else:
                    marker.write_text("incomplete", encoding="utf-8")
                try:
                    with patch.object(stack, "run_compose") as compose:
                        self.assertEqual(self.invoke("check")[0], 2)
                        self.assertEqual(self.invoke("up", "--build")[0], 2)
                        with self.assertRaisesRegex(stack.InstallationError, "recovery.incomplete"):
                            stack.configure(self.state, self.inputs)
                    compose.assert_not_called()
                finally:
                    marker.unlink()

    def test_rescue_environment_skips_incomplete_expired_or_old_deployment_validation(self) -> None:
        self.restored(deployment="b" * 64)
        (self.state / ".recovery-incomplete").write_text("incomplete", encoding="utf-8")
        with (
            patch.object(stack, "validate_inputs", side_effect=AssertionError("no price validation")),
            patch("community_transport.validate_transport", side_effect=AssertionError("no certificate expiry validation")),
            patch.object(recovery, "recovered_environment", side_effect=AssertionError("no recovery deployment validation")),
        ):
            environment, _ = stack.installed_environment(self.state, validate_contracts=False)
        self.assertEqual(environment["IIP_COMMUNITY_IMAGE"], "iip-community:mutable")

        for command, expected in (("status", ["ps"]), ("down", ["down"])):
            with self.subTest(command=command), patch.object(stack, "run_compose", return_value="") as compose:
                self.assertEqual(self.invoke(command)[0], 0)
                compose.assert_called_once_with(self.state, expected, validate_contracts=False)

    def test_normal_up_records_only_after_component_and_collector_health(self) -> None:
        events: list[str] = []
        with (
            patch.object(stack, "run_compose", side_effect=lambda _, args: events.append(args[0])),
            patch.object(stack, "wait_for_collector", side_effect=lambda *_: events.append("health")),
            patch.object(recovery, "record_runtime", side_effect=lambda *_: events.append("record")),
        ):
            self.assertEqual(self.invoke("up", "--build")[0], 0)
        self.assertEqual(events, ["build", "up", "health", "record"])

    def test_failed_start_or_health_does_not_record_successful_runtime(self) -> None:
        for target in ("run_compose", "wait_for_collector"):
            with (
                self.subTest(target=target),
                patch.object(stack, "run_compose", return_value=""),
                patch.object(stack, "wait_for_collector"),
                patch.object(stack, target, side_effect=stack.InstallationError("fixture.failure")),
                patch.object(recovery, "record_runtime") as record,
            ):
                self.assertEqual(self.invoke("up")[0], 2)
                record.assert_not_called()

    def test_snapshot_failure_does_not_claim_successful_start(self) -> None:
        with (
            patch.object(stack, "run_compose", return_value=""),
            patch.object(stack, "wait_for_collector"),
            patch.object(recovery, "record_runtime", side_effect=recovery.RecoveryError("fixture.failure")),
        ):
            code, output, _ = self.invoke("up")
        self.assertEqual(code, 2)
        self.assertNotIn("preview started", output)

    def test_compose_image_overrides_keep_digest_pinned_dependency_defaults(self) -> None:
        document = stack.COMPOSE.read_text(encoding="utf-8")
        for variable, default in (
            ("IIP_COMMUNITY_POSTGRES_IMAGE", "docker.io/library/postgres@sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15"),
            ("IIP_COMMUNITY_COLLECTOR_IMAGE", "docker.io/otel/opentelemetry-collector-contrib@sha256:c5918f78992ee73b0d6f0e599423ac5ec52dd5d9726733114d6eca53d5a32ed5"),
            ("IIP_COMMUNITY_PROMETHEUS_IMAGE", "docker.io/prom/prometheus@sha256:3c42b892cf723fa54d2f262c37a0e1f80aa8c8ddb1da7b9b0df9455a35a7f893"),
            ("IIP_COMMUNITY_GRAFANA_IMAGE", "docker.io/grafana/grafana@sha256:e932bd6ed0e026595b08483cd0141e5103e1ab7ff8604839ff899b8dc54cabcb"),
        ):
            self.assertIn("image: ${" + variable + ":-" + default + "}", document)


if __name__ == "__main__":
    unittest.main()
