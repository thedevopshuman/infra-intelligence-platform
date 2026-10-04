from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import recovery_docker_readonly as adapter


class RecoveryDockerReadonlyTests(unittest.TestCase):
    def test_only_fixed_read_operations_are_permitted(self):
        self.assertIsNone(adapter.validate_arguments(["buildx", "version"]))
        for repository, digest in adapter.REFERENCES.items():
            for reference in (repository + ":v0.84.2", repository + "@" + digest):
                self.assertEqual(adapter.validate_arguments(
                    ["buildx", "imagetools", "inspect", reference, "--format", "{{json .Manifest}}"]), digest)
        for arguments in (["buildx", "imagetools", "create"], ["login"], ["push", "image"],
                          ["build"], ["tag", "image", "other"], ["run", "image"],
                          ["--config", "/private", "buildx", "version"],
                          ["buildx", "imagetools", "inspect", "docker.io/thedevopshuman/iip:latest",
                           "--format", "{{json .Manifest}}"]):
            with self.subTest(arguments=arguments), patch.object(adapter.subprocess, "run") as command:
                with self.assertRaises(adapter.RecoveryDockerError):
                    adapter.execute(arguments)
                command.assert_not_called()

    def test_preserves_actual_buildx_version_and_discards_ambient_auth(self):
        result = subprocess.CompletedProcess([], 0, "github.com/docker/buildx v0.37.2 fixture\n", "")
        with patch.dict(os.environ, {"DOCKER_AUTH_CONFIG": "credential", "DOCKER_CONFIG": "/private",
                                     "DOCKER_CLI_PLUGIN_EXTRA_DIRS": "/untrusted", "DOCKER_HOST": "ssh://host"}):
            with patch.object(adapter.subprocess, "run", return_value=result) as command:
                self.assertEqual(adapter.execute(["buildx", "version"]), result.stdout)
        environment = command.call_args.kwargs["env"]
        self.assertNotEqual(environment["DOCKER_CONFIG"], "/private")
        self.assertNotIn("DOCKER_AUTH_CONFIG", environment)
        self.assertNotIn("DOCKER_HOST", environment)
        self.assertNotIn("DOCKER_CLI_PLUGIN_EXTRA_DIRS", environment)

    def test_inspection_is_exact_and_minimized(self):
        repository, digest = next(iter(adapter.REFERENCES.items()))
        arguments = ["buildx", "imagetools", "inspect", repository + ":v0.84.2", "--format", "{{json .Manifest}}"]
        result = subprocess.CompletedProcess([], 0, json.dumps({"digest": digest, "extra": "ignored"}), "")
        with patch.object(adapter.subprocess, "run", return_value=result):
            self.assertEqual(json.loads(adapter.execute(arguments)), {"digest": digest})
        for output in ("not JSON", "[]", json.dumps({"digest": "sha256:" + "0" * 64})):
            with self.subTest(output=output), patch.object(adapter.subprocess, "run", return_value=
                    subprocess.CompletedProcess([], 0, output, "private provider error")):
                with self.assertRaises(adapter.RecoveryDockerError):
                    adapter.execute(arguments)

    def test_failure_is_closed_without_provider_output_or_mutation(self):
        result = subprocess.CompletedProcess([], 1, "secret", "private endpoint")
        with patch.object(adapter.subprocess, "run", return_value=result) as command:
            with self.assertRaisesRegex(adapter.RecoveryDockerError, "^release-recovery.docker.read-failed$"):
                adapter.execute(["buildx", "version"])
            command.assert_called_once()

    def test_explicit_buildx_binary_does_not_change_command_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "buildx"
            executable.write_text("fixture")
            executable.chmod(0o700)
            with patch.dict(os.environ, {"IIP_RECOVERY_BUILDX": str(executable)}), \
                    patch.object(adapter.subprocess, "run", return_value=subprocess.CompletedProcess(
                        [], 0, "github.com/docker/buildx v0.37.2 fixture\n", "")) as command:
                adapter.execute(["buildx", "version"])
                self.assertEqual(command.call_args.args[0], [str(executable), "version"])
                with self.assertRaises(adapter.RecoveryDockerError):
                    adapter.execute(["buildx", "imagetools", "create"])
                command.assert_called_once()
        with patch.dict(os.environ, {"IIP_RECOVERY_BUILDX": "relative/buildx"}), \
                patch.object(adapter.subprocess, "run") as command:
            with self.assertRaisesRegex(adapter.RecoveryDockerError, "buildx-invalid"):
                adapter.execute(["buildx", "version"])
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
