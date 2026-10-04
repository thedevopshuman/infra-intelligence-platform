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
        self.assertEqual(
            identities[0]["roles"],
            ["developer", "platform-admin"],
        )
        self.assertNotIn(tokens[0], environment)
        self.assertIn("tokenSha256", environment)
        self.assertNotIn("bearerToken", environment)
        catalog_line = next(
            line
            for line in environment.splitlines()
            if line.startswith("IIP_INVESTIGATION_SIGNAL_CATALOG_JSON=")
        )
        catalog = json.loads(catalog_line.partition("=")[2])
        self.assertEqual(catalog["profiles"][0]["tenantId"], "local")
        self.assertNotIn("credential", catalog_line.lower())
        self.assertNotIn("endpoint", catalog_line.lower())

    def test_existing_pair_is_preserved_and_partial_state_fails_closed(self) -> None:
        _, credentials_path, _ = local_stack.create_local_configuration()
        original = credentials_path.read_bytes()

        _, _, created = local_stack.create_local_configuration()

        self.assertFalse(created)
        self.assertEqual(credentials_path.read_bytes(), original)
        credentials_path.unlink()
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            local_stack.create_local_configuration()

    def test_existing_developer_operator_is_upgraded_without_rotating_token(self) -> None:
        env_path, credentials_path, _ = local_stack.create_local_configuration()
        credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
        token = credentials["identities"][0]["bearerToken"]
        credentials["identities"][0]["roles"] = ["developer"]
        credentials_path.write_text(json.dumps(credentials), encoding="utf-8")
        lines = env_path.read_text(encoding="utf-8").splitlines()
        index = next(
            index
            for index, line in enumerate(lines)
            if line.startswith("IIP_AUTH_IDENTITIES_JSON=")
        )
        verifiers = json.loads(lines[index].partition("=")[2])
        verifiers["identities"][0]["roles"] = ["developer"]
        lines[index] = "IIP_AUTH_IDENTITIES_JSON=" + json.dumps(
            verifiers, separators=(",", ":")
        )
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        _, _, created = local_stack.create_local_configuration()

        upgraded = json.loads(credentials_path.read_text(encoding="utf-8"))
        self.assertFalse(created)
        self.assertEqual(upgraded["identities"][0]["bearerToken"], token)
        self.assertEqual(
            upgraded["identities"][0]["roles"],
            ["developer", "platform-admin"],
        )

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

    def test_local_compose_keeps_ai_policies_worker_only_and_disabled_by_default(
        self,
    ) -> None:
        compose = local_stack.COMPOSE_PATH.read_text(encoding="utf-8")
        api, worker = compose.split("  workflow-worker:", maxsplit=1)

        self.assertIn("IIP_EVIDENCE_REDACTION_POLICIES_JSON", api)
        self.assertIn("IIP_EVIDENCE_REDACTION_POLICIES_JSON", worker)
        self.assertNotIn("IIP_AI_ATTRIBUTION_POLICIES_JSON", api)
        self.assertIn(
            "IIP_AI_ATTRIBUTION_ENABLED: ${IIP_AI_ATTRIBUTION_ENABLED:-false}",
            worker,
        )
        self.assertIn("IIP_AI_ATTRIBUTION_POLICIES_JSON", worker)
        self.assertIn("IIP_AI_ATTRIBUTION_ALLOW_TEST_FIXTURES", worker)
        self.assertNotIn("IIP_AI_PRICE_CATALOGS_JSON", api)
        self.assertIn(
            "IIP_AI_COST_ENGINE_ENABLED: ${IIP_AI_COST_ENGINE_ENABLED:-false}",
            worker,
        )
        self.assertIn("IIP_AI_PRICE_CATALOGS_JSON", worker)
        self.assertIn("IIP_AI_PRICE_CATALOG_ALLOW_TEST_FIXTURES", worker)
        self.assertNotIn("IIP_AI_SAVINGS_PROFILES_JSON", api)
        self.assertIn(
            "IIP_AI_SAVINGS_ENGINE_ENABLED: ${IIP_AI_SAVINGS_ENGINE_ENABLED:-false}",
            worker,
        )
        self.assertIn("IIP_AI_SAVINGS_PROFILES_JSON", worker)

    def test_every_plaintext_compose_database_client_opts_in_explicitly(self) -> None:
        for relative in (
            "deploy/docker-compose.yml",
            "deploy/docker-compose.ai-finops.yml",
            "deploy/docker-compose.otlp-receiver.yml",
        ):
            with self.subTest(compose=relative):
                compose = (ROOT / relative).read_text(encoding="utf-8")
                self.assertGreater(compose.count("IIP_DATABASE_URL:"), 0)
                self.assertEqual(
                    compose.count("IIP_DATABASE_URL:"),
                    compose.count(
                        "IIP_DATABASE_TRANSPORT_MODE: insecure-local"
                    ),
                )


if __name__ == "__main__":
    unittest.main()
