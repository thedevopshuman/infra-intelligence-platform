from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from iip.application.qualify_ai_price_catalog import qualify_ai_price_catalog

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import community_stack as stack


def docker_result(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
    output = '"unix:///tmp/community-test.sock"' if command[1:3] == ["context", "inspect"] else '"community-test-daemon"' if "info" in command else ""
    return subprocess.CompletedProcess(command, 0, output)


def example(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / f"{name}.json").read_text())


def installation_inputs(*, age: timedelta = timedelta()) -> dict:
    """Synthetic in-memory inputs; not approved real prices or customer evidence."""
    now = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(seconds=5) - age
    stamp = now.isoformat().replace("+00:00", "Z")
    channel = json.loads((ROOT / "deploy" / "otlp" / "ai-usage-receiver-channels.example.json").read_text())
    channel["channels"][0].update({
        "instrumentationScopes": [stack.SCOPE],
        "usageAttributes": copy.deepcopy(stack.USAGE_ATTRIBUTES),
    })
    catalog = example("ai-price-catalog")
    catalog["metadata"]["publishedAt"] = stamp
    catalog["spec"]["source"].update({
        "kind": "operator-managed", "locator": "urn:local-test:prices",
        "retrievedAt": stamp,
    })
    policy = example("ai-price-catalog-qualification-policy")
    policy["spec"]["requiredScopes"][0]["effectiveAt"] = stamp
    identity = {"tenantId": "local", "version": policy["metadata"]["version"], "spec": policy["spec"]}
    policy["metadata"]["id"] = "apqp_" + hashlib.sha256(stack.compact(identity).encode()).hexdigest()[:32]
    report = qualify_ai_price_catalog(catalog, policy, generated_at=stamp, qualification_level="production-catalog")
    assert report["spec"]["status"] == "qualified"
    attribution = example("ai-attribution-policy")
    attribution["spec"]["source"].update({"kind": "operator-managed", "locator": "urn:local-test:ownership"})
    return {
        "channel": channel, "catalogs": {"catalogs": [catalog]},
        "qualifications": {"policies": [policy], "reports": [report]},
        "attribution": {"policies": [attribution]}, "savings": {"profiles": []},
    }


class CommunityStackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.parent = Path(self.temporary.name)
        self.state = self.parent / "community"
        self.inputs = installation_inputs()
        selected = patch("community_docker._is_socket", return_value=True)
        selected.start()
        self.addCleanup(selected.stop)

    def install(self) -> None:
        stack.initialize(self.state, self.inputs, image="iip-community:test")

    def test_existing_contracts_are_reused_without_mutating_operator_inputs(self) -> None:
        before = copy.deepcopy(self.inputs)
        normalized, tenant = stack.validate_inputs(self.inputs, "r" * 64)
        self.assertEqual(self.inputs, before)
        self.assertEqual(tenant, "local")
        self.assertEqual(normalized["channel"]["channels"][0]["channelId"], stack.CHANNEL)
        self.assertNotEqual(normalized["channel"], before["channel"])
        for name in ("catalogs", "attribution", "qualifications", "savings"):
            self.assertEqual(normalized[name], before[name])

    def test_fixture_prices_or_ownership_cannot_be_enabled(self) -> None:
        for name, key in (("catalogs", "catalogs"), ("attribution", "policies")):
            with self.subTest(name=name):
                inputs = copy.deepcopy(self.inputs)
                inputs[name][key][0]["spec"]["source"]["kind"] = "test-fixture"
                with self.assertRaisesRegex(stack.InstallationError, "fixture-prohibited"):
                    stack.validate_inputs(inputs, "r" * 64)

    def test_exact_real_scope_and_meter_semantics_required(self) -> None:
        mutations = (
            ("instrumentationScopes", ["opentelemetry.instrumentation.botocore"]),
            ("provider", "openai"),
            ("usageAttributes", {**stack.USAGE_ATTRIBUTES, "zeroWhenAbsent": ["reasoningOutputTokens"]}),
            ("invocationAttributes", {**stack.INVOCATION_ATTRIBUTES, "zeroWhenAbsent": ["retryCount"]}),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                inputs = copy.deepcopy(self.inputs)
                inputs["channel"]["channels"][0][field] = value
                with self.assertRaisesRegex(stack.InstallationError, "unsupported-profile"):
                    stack.validate_inputs(inputs, "r" * 64)

    def test_multiple_channels_and_cross_tenant_configuration_rejected(self) -> None:
        multiple = copy.deepcopy(self.inputs)
        multiple["channel"]["channels"].append(copy.deepcopy(multiple["channel"]["channels"][0]))
        mismatch = copy.deepcopy(self.inputs)
        mismatch["attribution"]["policies"][0]["metadata"]["tenantId"] = "another"
        for inputs in (multiple, mismatch):
            with self.assertRaises(stack.InstallationError):
                stack.validate_inputs(inputs, "r" * 64)

    def test_exact_current_qualified_price_report_required(self) -> None:
        changed = copy.deepcopy(self.inputs)
        changed["catalogs"]["catalogs"][0]["spec"]["entries"][0]["rates"]["uncachedInputTokens"]["priceSubunitsPerMillionTokens"] += 1
        missing = copy.deepcopy(self.inputs)
        missing["qualifications"]["reports"] = []
        expired = copy.deepcopy(self.inputs)
        expired["qualifications"]["reports"][0]["spec"]["validUntil"] = "2026-01-01T00:00:00Z"
        for inputs in (changed, missing, expired):
            with self.assertRaises(stack.InstallationError):
                stack.validate_inputs(inputs, "r" * 64)

    def test_invalid_inputs_do_not_create_installation(self) -> None:
        self.inputs["qualifications"]["reports"] = []
        with self.assertRaises(stack.InstallationError):
            self.install()
        self.assertFalse(self.state.exists())
        self.assertEqual(list(self.parent.iterdir()), [])

    def test_self_consistent_but_expired_qualification_is_rejected(self) -> None:
        with self.assertRaises(stack.InstallationError):
            stack.validate_inputs(installation_inputs(age=timedelta(days=2)), "r" * 64)

    def test_build_happens_before_start_only_when_explicit(self) -> None:
        self.install()
        with patch.object(stack, "run_compose", return_value="") as run:
            with redirect_stdout(io.StringIO()), patch.object(stack, "wait_for_collector"):
                self.assertEqual(stack.main(["--state", str(self.state), "up", "--build"]), 0)
            self.assertEqual(run.call_args_list[0].args[1], ["build", "initialize"])
            self.assertEqual(run.call_args_list[1].args[1][0], "up")
        self.assertIn("build:\n      context: ..\n      dockerfile: Dockerfile", stack.COMPOSE.read_text())
        ignored = (ROOT / ".dockerignore").read_text().splitlines()
        self.assertTrue({".iip", ".env", ".env.*", "*.log"}.issubset(ignored))

    def test_initialize_creates_private_unique_credentials_without_traffic(self) -> None:
        with patch.object(subprocess, "run") as run:
            self.install()
            run.assert_not_called()
        self.assertEqual(stat.S_IMODE(self.state.stat().st_mode), 0o700)
        credentials = stack.read_protected(self.state / "credentials.json")
        self.assertEqual(len(set(credentials.values())), 5)
        for value in credentials.values():
            self.assertEqual(len(value), 64)
        for path in self.state.rglob("*"):
            if path.is_file():
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600, path.name)
        values, project = stack.installed_environment(self.state)
        self.assertEqual(project, stack.project_name(self.state.absolute()))
        self.assertEqual(values["IIP_AI_SAVINGS_ENGINE_ENABLED"], "false")
        self.assertNotIn(credentials["apiToken"], values["IIP_AUTH_IDENTITIES_JSON"])
        self.assertNotIn(credentials["receiverToken"], values["IIP_AI_USAGE_RECEIVER_CHANNELS_JSON"])

    def test_initialize_refuses_overwrite_and_symlink(self) -> None:
        self.install()
        prior = (self.state / "credentials.json").read_bytes()
        with self.assertRaisesRegex(stack.InstallationError, "already-exists"):
            self.install()
        self.assertEqual((self.state / "credentials.json").read_bytes(), prior)
        link = self.parent / "link"
        link.symlink_to(self.state, target_is_directory=True)
        with self.assertRaises(stack.InstallationError):
            stack.initialize(link, self.inputs, image="iip:test")

    def test_nonignored_repository_state_cannot_enter_image_build_context(self) -> None:
        with self.assertRaisesRegex(stack.InstallationError, "build-context-prohibited"):
            stack.initialize(ROOT / "community-private-test-state", self.inputs, image="iip:test")

    def test_config_files_must_be_owner_only_regular_files(self) -> None:
        source = self.parent / "config.json"
        stack.write_protected(source, {"a": 1})
        self.assertEqual(stack.read_protected(source), {"a": 1})
        source.chmod(0o644)
        with self.assertRaises(stack.InstallationError):
            stack.read_protected(source)
        source.chmod(0o600)
        link = self.parent / "link"
        link.symlink_to(source)
        with self.assertRaises(stack.InstallationError):
            stack.read_protected(link)
        os.link(source, self.parent / "hardlink")
        with self.assertRaises(stack.InstallationError):
            stack.read_protected(source)

    def test_rejects_duplicate_json_keys(self) -> None:
        path = self.parent / "input.json"
        path.write_text('{"catalogs":[],"catalogs":[{}]}')
        path.chmod(0o600)
        with self.assertRaisesRegex(stack.InstallationError, "duplicate-key"):
            stack.read_protected(path)

    def test_secrets_and_dollar_signs_are_passed_as_environment_not_shell(self) -> None:
        self.install()
        with patch.dict(os.environ, {"IIP_COMMUNITY_IMAGE": "attacker", "COMPOSE_FILE": "/tmp/attacker", "PGSSLMODE": "disable"}):
            with patch.object(subprocess, "run", side_effect=docker_result) as run:
                self.assertEqual(stack.run_compose(self.state, ["ps"]), "")
                command = run.call_args.args[0]
                env = run.call_args.kwargs["env"]
                self.assertEqual(command[1:3], ["--host", "unix:///tmp/community-test.sock"])
                self.assertNotIn("IIP_COMMUNITY_POSTGRES_PASSWORD", run.call_args_list[0].kwargs["env"])
                self.assertNotIn("COMPOSE_FILE", env)
                self.assertNotIn("PGSSLMODE", env)
                self.assertEqual(env["IIP_COMMUNITY_IMAGE"], "iip-community:test")
                self.assertNotIn(env["IIP_COMMUNITY_POSTGRES_PASSWORD"], " ".join(command))
                self.assertNotIn("shell", run.call_args.kwargs)

    def test_down_does_not_revalidate_expired_prices_or_delete_volumes(self) -> None:
        self.install()
        with patch.object(stack, "validate_inputs", side_effect=AssertionError("not for stop")):
            with patch.object(subprocess, "run", side_effect=docker_result) as run:
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(stack.main(["--state", str(self.state), "down"]), 0)
                self.assertEqual(run.call_args.args[0][-1], "down")
                self.assertNotIn("--volumes", run.call_args.args[0])

    def test_docker_error_does_not_disclose_stderr_or_credentials(self) -> None:
        self.install()
        with patch.object(subprocess, "run", side_effect=subprocess.CalledProcessError(1, ["docker"], stderr="private-password")):
            output = io.StringIO()
            with redirect_stderr(output):
                self.assertEqual(stack.main(["--state", str(self.state), "up"]), 2)
            self.assertNotIn("private-password", output.getvalue())
            self.assertIn("community.operation.failed", output.getvalue())

    def test_remote_daemon_is_rejected_before_secrets_are_dispatched(self) -> None:
        self.install()
        with patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '"ssh://remote"')) as run:
            with self.assertRaises(stack.InstallationError):
                stack.run_compose(self.state, ["up"])
            self.assertEqual(run.call_count, 1)
            self.assertNotIn("IIP_DATABASE_URL", run.call_args.kwargs["env"])
        with patch.dict(os.environ, {"DOCKER_HOST": "tcp://remote:2375"}):
            with patch.object(subprocess, "run") as run:
                with self.assertRaises(stack.InstallationError):
                    stack.run_compose(self.state, ["up"])
                run.assert_not_called()

    def test_configure_switches_complete_generation_and_preserves_credentials(self) -> None:
        self.install()
        old = stack.read_protected(self.state / "installation.json")
        credentials = (self.state / "credentials.json").read_bytes()
        updated = copy.deepcopy(self.inputs)
        updated["attribution"]["policies"][0]["metadata"]["id"] = "aap_" + "3" * 32
        updated["attribution"]["policies"][0]["metadata"]["version"] = "2026-09-05.2"
        with patch.object(stack, "run_compose", return_value="") as run:
            stack.configure(self.state, updated)
            self.assertEqual(run.call_args.args[1], ["ps", "--all", "--quiet"])
        current = stack.read_protected(self.state / "installation.json")
        self.assertNotEqual(old["configurationGeneration"], current["configurationGeneration"])
        self.assertTrue((self.state / "config" / old["configurationGeneration"]).is_dir())
        self.assertEqual((self.state / "credentials.json").read_bytes(), credentials)
        stack.installed_environment(self.state)
        before = (self.state / "installation.json").read_bytes()
        with patch.object(stack, "run_compose", return_value="paused-container-id"):
            with self.assertRaisesRegex(stack.InstallationError, "stop-required"):
                stack.configure(self.state, self.inputs)
        with patch.object(stack, "run_compose", return_value=""):
            with self.assertRaises(stack.InstallationError):
                stack.configure(self.state, installation_inputs(age=timedelta(days=2)))
        self.assertEqual((self.state / "installation.json").read_bytes(), before)

    def test_generated_dashboard_integrity_and_concurrent_commands_fail_closed(self) -> None:
        self.install()
        environment, _ = stack.installed_environment(self.state)
        dashboard = Path(environment["IIP_COMMUNITY_DASHBOARD"])
        dashboard.write_text('{}')
        with self.assertRaises(stack.InstallationError):
            stack.installed_environment(self.state)
        with stack.installation_lock(self.state):
            with self.assertRaises(BlockingIOError):
                with stack.installation_lock(self.state):
                    self.fail("concurrent lock acquired")

    def test_template_changes_produce_a_fresh_generation(self) -> None:
        import community_dashboard
        self.install()
        prior = stack.read_protected(self.state / "installation.json")["configurationGeneration"]
        template = community_dashboard._load_template()
        template["description"] = "Updated release dashboard"
        with patch.object(community_dashboard, "_load_template", return_value=template):
            with patch.object(stack, "run_compose", return_value=""):
                stack.configure(self.state, self.inputs)
        current = stack.read_protected(self.state / "installation.json")["configurationGeneration"]
        self.assertNotEqual(prior, current)


if __name__ == "__main__":
    unittest.main()
