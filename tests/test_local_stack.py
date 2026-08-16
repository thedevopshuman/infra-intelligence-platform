"""Protected local Docker-stack configuration tests."""

from __future__ import annotations

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import local_stack  # noqa: E402


class LocalStackConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.state = Path(self.temporary.name) / ".iip"
        self.paths = patch.multiple(
            local_stack,
            STATE_DIR=self.state,
            ENV_PATH=self.state / "local.env",
            CREDENTIALS_PATH=self.state / "local-credentials.json",
        )
        self.paths.start()

    def tearDown(self) -> None:
        self.paths.stop()
        self.temporary.cleanup()

    def test_configuration_has_distinct_hashed_identities_and_protected_tokens(self) -> None:
        env_path, credentials_path, created = local_stack.create_local_configuration()

        self.assertTrue(created)
        self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(credentials_path.stat().st_mode), 0o600)
        environment = env_path.read_text(encoding="utf-8")
        credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
        identities = credentials["identities"]
        tokens = [identity["bearerToken"] for identity in identities]
        self.assertEqual(len(tokens), len(set(tokens)))
        self.assertEqual(
            [identity["actorId"] for identity in identities],
            ["local-operator", "local-approver", "local-executor"],
        )
        self.assertNotIn(tokens[0], environment)
        self.assertIn("tokenSha256", environment)
        self.assertNotIn("bearerToken", environment)

    def test_existing_pair_is_preserved_and_partial_state_fails_closed(self) -> None:
        _, credentials_path, _ = local_stack.create_local_configuration()
        original = credentials_path.read_bytes()

        _, _, created = local_stack.create_local_configuration()

        self.assertFalse(created)
        self.assertEqual(credentials_path.read_bytes(), original)
        credentials_path.unlink()
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            local_stack.create_local_configuration()

    def test_compose_uses_explicit_project_env_and_file_without_shell(self) -> None:
        local_stack.create_local_configuration()
        with patch.dict(os.environ, {"IIP_DOCKER_BIN": "docker-test"}, clear=False):
            with patch("local_stack.subprocess.run") as run:
                local_stack.compose(("ps",))

        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["docker-test", "compose", "--project-name", "iip-local"])
        self.assertIn("--env-file", command)
        self.assertIn("--file", command)
        self.assertEqual(command[-1], "ps")
        self.assertTrue(run.call_args.kwargs["check"])


if __name__ == "__main__":
    unittest.main()
