from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import community_docker as docker_boundary


class CommunityDockerBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir="/tmp")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        self.socket_path = self.root / "docker.sock"
        self.endpoint = f"unix://{self.socket_path}"
        self.base_environment = {
            "PATH": "/trusted/bin",
            "HOME": "/trusted/home",
            "DOCKER_CONFIG": "/trusted/docker-config-a",
            "DOCKER_CONTEXT": "local-a",
            "DOCKER_TLS_VERIFY": "1",
            "DOCKER_CERT_PATH": "/unneeded-for-unix",
            "IIP_COMMUNITY_POSTGRES_PASSWORD": "database-secret",
            "COMPOSE_FILE": "/untrusted/compose.yaml",
            "PGPASSWORD": "database-secret",
            "SAFE_MARKER": "retained",
        }

    def runner(
        self,
        *,
        endpoint: str | None = None,
        daemon_id: str = "daemon-id-a",
        calls: list[tuple[list[str], dict[str, object]]] | None = None,
    ):
        selected_endpoint = self.endpoint if endpoint is None else endpoint

        def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            if calls is not None:
                calls.append((command, kwargs))
            if command[1:3] == ["context", "inspect"]:
                output = json.dumps(selected_endpoint) + "\n"
            elif command[1:3] == ["--host", selected_endpoint]:
                output = json.dumps(daemon_id) + "\n"
            else:  # pragma: no cover - protects the mock protocol itself
                raise AssertionError(command)
            return subprocess.CompletedProcess(command, 0, output, "")

        return run

    def bind(
        self,
        *,
        endpoint: str | None = None,
        daemon_id: str = "daemon-id-a",
        environment: dict[str, str] | None = None,
        calls: list[tuple[list[str], dict[str, object]]] | None = None,
    ) -> tuple[str, str, dict[str, str]]:
        with (
            patch.object(docker_boundary.shutil, "which", return_value="/trusted/bin/docker") as which,
            patch.object(
                docker_boundary.subprocess,
                "run",
                side_effect=self.runner(endpoint=endpoint, daemon_id=daemon_id, calls=calls),
            ),
            patch.object(docker_boundary, "_is_socket", return_value=True),
        ):
            result = docker_boundary.local_docker_binding(
                self.state,
                dict(self.base_environment if environment is None else environment),
            )
        which.assert_called_once_with("docker", path="/trusted/bin")
        return result

    def test_first_use_persists_protected_binding_and_returns_sanitized_environment(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []
        original = dict(self.base_environment)

        executable, endpoint, environment = self.bind(calls=calls)

        self.assertEqual(executable, "/trusted/bin/docker")
        self.assertEqual(endpoint, self.endpoint)
        self.assertEqual(self.base_environment, original)
        self.assertEqual(environment["SAFE_MARKER"], "retained")
        self.assertEqual(environment["DOCKER_CONFIG"], "/trusted/docker-config-a")
        for name in (
            "DOCKER_CONTEXT",
            "DOCKER_HOST",
            "DOCKER_TLS_VERIFY",
            "DOCKER_CERT_PATH",
            "IIP_COMMUNITY_POSTGRES_PASSWORD",
            "COMPOSE_FILE",
            "PGPASSWORD",
        ):
            self.assertNotIn(name, environment)

        binding_path = self.state / "daemon.json"
        metadata = binding_path.stat()
        self.assertEqual(stat.S_IMODE(metadata.st_mode), 0o600)
        self.assertEqual(metadata.st_nlink, 1)
        serialized = binding_path.read_bytes()
        self.assertTrue(serialized.endswith(b"\n"))
        self.assertEqual(
            json.loads(serialized),
            {"format": 1, "endpoint": self.endpoint, "daemonId": "daemon-id-a"},
        )

        self.assertEqual(
            calls[0][0],
            [
                "/trusted/bin/docker",
                "context",
                "inspect",
                "--format",
                "{{json .Endpoints.docker.Host}}",
            ],
        )
        self.assertEqual(
            calls[1][0],
            [
                "/trusted/bin/docker",
                "--host",
                self.endpoint,
                "info",
                "--format",
                "{{json .ID}}",
            ],
        )
        context_environment = calls[0][1]["env"]
        info_environment = calls[1][1]["env"]
        self.assertIsInstance(context_environment, dict)
        self.assertIsInstance(info_environment, dict)
        self.assertEqual(context_environment["DOCKER_CONTEXT"], "local-a")
        self.assertEqual(context_environment["DOCKER_CONFIG"], "/trusted/docker-config-a")
        self.assertNotIn("DOCKER_CONTEXT", info_environment)
        for child_environment in (context_environment, info_environment):
            self.assertNotIn("IIP_COMMUNITY_POSTGRES_PASSWORD", child_environment)
            self.assertNotIn("COMPOSE_FILE", child_environment)
            self.assertNotIn("PGPASSWORD", child_environment)
        self.assertEqual(calls[0][1]["timeout"], 15)
        self.assertEqual(calls[1][1]["timeout"], 15)

    def test_repeat_use_verifies_identity_without_rewriting_and_allows_config_change(self) -> None:
        self.bind()
        path = self.state / "daemon.json"
        before = (path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes())
        alternate = {
            **self.base_environment,
            "DOCKER_CONFIG": "/trusted/docker-config-b",
            "DOCKER_CONTEXT": "another-local-name",
        }
        calls: list[tuple[list[str], dict[str, object]]] = []

        self.bind(environment=alternate, calls=calls)

        self.assertEqual(before, (path.stat().st_ino, path.stat().st_mtime_ns, path.read_bytes()))
        self.assertEqual(calls[0][1]["env"]["DOCKER_CONFIG"], "/trusted/docker-config-b")
        self.assertEqual(calls[0][1]["env"]["DOCKER_CONTEXT"], "another-local-name")

    def test_endpoint_or_daemon_change_fails_without_rebinding(self) -> None:
        self.bind()
        original = (self.state / "daemon.json").read_bytes()
        second_path = self.root / "docker-b.sock"

        for endpoint, daemon_id in (
            (f"unix://{second_path}", "daemon-id-a"),
            (self.endpoint, "daemon-id-b"),
        ):
            with self.subTest(endpoint=endpoint, daemon_id=daemon_id):
                with self.assertRaisesRegex(
                    docker_boundary.CommunityDockerError,
                    "^community[.]docker[.]binding-mismatch$",
                ):
                    self.bind(endpoint=endpoint, daemon_id=daemon_id)
                self.assertEqual((self.state / "daemon.json").read_bytes(), original)

    def test_remote_context_and_host_override_are_rejected_before_binding(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []
        with self.assertRaisesRegex(
            docker_boundary.CommunityDockerError,
            "^community[.]docker[.]local-daemon-required$",
        ):
            self.bind(endpoint="tcp://remote.example:2376", calls=calls)
        self.assertEqual(len(calls), 1)
        self.assertFalse((self.state / "daemon.json").exists())

        overridden = {**self.base_environment, "DOCKER_HOST": "unix:///tmp/other.sock"}
        with (
            patch.object(docker_boundary.shutil, "which") as which,
            patch.object(docker_boundary.subprocess, "run") as run,
            self.assertRaisesRegex(
                docker_boundary.CommunityDockerError,
                "^community[.]docker[.]host-override-prohibited$",
            ),
        ):
            docker_boundary.local_docker_binding(self.state, overridden)
        which.assert_not_called()
        run.assert_not_called()

    def test_unix_endpoint_must_resolve_to_a_socket(self) -> None:
        regular = self.root / "not-a-socket"
        regular.write_text("not a socket", encoding="utf-8")
        calls: list[tuple[list[str], dict[str, object]]] = []
        with (
            patch.object(docker_boundary.shutil, "which", return_value="/trusted/bin/docker"),
            patch.object(
                docker_boundary.subprocess,
                "run",
                side_effect=self.runner(endpoint=f"unix://{regular}", calls=calls),
            ),
            self.assertRaisesRegex(
                docker_boundary.CommunityDockerError,
                "^community[.]docker[.]local-daemon-required$",
            ),
        ):
            docker_boundary.local_docker_binding(self.state, dict(self.base_environment))
        self.assertEqual(len(calls), 1)

    def test_socket_must_have_a_trusted_owner_and_not_be_world_writable(self) -> None:
        socket_mode = stat.S_IFSOCK | 0o660
        for owner, mode, trusted in (
            (os.getuid(), socket_mode, True),
            (0, socket_mode, True),
            (os.getuid() + 1, socket_mode, False),
            (os.getuid(), stat.S_IFSOCK | 0o662, False),
            (os.getuid(), stat.S_IFREG | 0o600, False),
        ):
            with self.subTest(owner=owner, mode=mode):
                metadata = SimpleNamespace(st_uid=owner, st_mode=mode)
                with patch.object(docker_boundary.os, "stat", return_value=metadata):
                    self.assertEqual(docker_boundary._is_socket("/docker.sock"), trusted)

    def test_relative_executable_is_pinned_to_one_absolute_path(self) -> None:
        calls: list[tuple[list[str], dict[str, object]]] = []
        with (
            patch.object(docker_boundary.shutil, "which", return_value="relative/bin/docker"),
            patch.object(
                docker_boundary.subprocess,
                "run",
                side_effect=self.runner(calls=calls),
            ),
            patch.object(docker_boundary, "_is_socket", return_value=True),
        ):
            executable, _, _ = docker_boundary.local_docker_binding(
                self.state, dict(self.base_environment)
            )
        expected = str(Path("relative/bin/docker").absolute())
        self.assertEqual(executable, expected)
        self.assertEqual(calls[0][0][0], expected)
        self.assertEqual(calls[1][0][0], expected)

    def test_invalid_protected_binding_fails_before_docker_inspection(self) -> None:
        path = self.state / "daemon.json"
        path.write_text('{"format":1}\n', encoding="utf-8")
        path.chmod(0o644)
        with (
            patch.object(docker_boundary.shutil, "which") as which,
            patch.object(docker_boundary.subprocess, "run") as run,
            self.assertRaisesRegex(
                docker_boundary.CommunityDockerError,
                "^community[.]docker[.]binding-invalid$",
            ),
        ):
            docker_boundary.local_docker_binding(self.state, dict(self.base_environment))
        which.assert_not_called()
        run.assert_not_called()

    def test_docker_failures_do_not_disclose_command_output(self) -> None:
        failure = subprocess.CalledProcessError(
            1,
            ["docker", "context", "inspect"],
            output="protected-value",
            stderr="another-protected-value",
        )
        with (
            patch.object(docker_boundary.shutil, "which", return_value="/trusted/bin/docker"),
            patch.object(docker_boundary.subprocess, "run", side_effect=failure),
            self.assertRaises(docker_boundary.CommunityDockerError) as raised,
        ):
            docker_boundary.local_docker_binding(self.state, dict(self.base_environment))
        self.assertEqual(str(raised.exception), "community.docker.command-failed")
        self.assertNotIn("protected-value", str(raised.exception))
        self.assertFalse((self.state / "daemon.json").exists())


if __name__ == "__main__":
    unittest.main()
