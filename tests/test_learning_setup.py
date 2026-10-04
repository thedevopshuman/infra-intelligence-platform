"""The learning launcher composes fixtures, never production authority."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ai_finops_fixture
import learning_fixture


class LearningFixtureTests(unittest.TestCase):
    def test_configuration_reuses_existing_fixture_contracts(self):
        anchor = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)
        values = learning_fixture.configuration(anchor)
        import json
        self.assertEqual(values["IIP_AI_FINOPS_ANCHOR"], "2026-10-04T12:00:00Z")
        self.assertEqual(json.loads(values["IIP_AI_PRICE_CATALOGS_JSON"]),
                         ai_finops_fixture.price_catalog_configuration(anchor))
        self.assertEqual(json.loads(values["IIP_AI_ATTRIBUTION_POLICIES_JSON"]),
                         ai_finops_fixture.attribution_policy_configuration(anchor))
        self.assertEqual(values["IIP_AI_USAGE_CHANNEL_TOKEN"], ai_finops_fixture.CHANNEL_TOKEN)
        self.assertNotIn("AWS_ACCESS_KEY_ID", values)
        self.assertEqual(len(values), 8)

    def test_seed_calls_real_fixture_validation_at_internal_endpoints(self):
        with patch.dict(os.environ, {"IIP_AI_FINOPS_ANCHOR": "2026-10-04T12:00:00Z"}), \
             patch.object(learning_fixture.urllib.request, "urlopen") as health, \
             patch.object(ai_finops_fixture, "send_fixture") as send, \
             patch.object(ai_finops_fixture, "verify_fixture", return_value={"usageRecordCount": 15}) as verify:
            health.return_value.__enter__.return_value.status = 200
            self.assertEqual(learning_fixture.seed(), {"usageRecordCount": 15})
            self.assertEqual(send.call_args.args[1:], ("http://otel-collector:4318",
                "http://otel-collector:4320", "http://ai-usage-receiver:4318"))
            self.assertEqual(verify.call_args.kwargs["api_endpoint"], "http://api:8080")
            self.assertEqual(verify.call_args.kwargs["database_url"], "postgresql://iip@postgres:5432/iip")

    def test_bad_anchor_rejected_before_network(self):
        with patch.dict(os.environ, {"IIP_AI_FINOPS_ANCHOR": "invalid"}), \
             patch.object(ai_finops_fixture, "send_fixture") as send:
            with self.assertRaises(ValueError):
                learning_fixture.seed()
            send.assert_not_called()

    def test_launcher_syntax_and_closed_commands(self):
        script = ROOT / "scripts/learning.sh"
        subprocess.run(["sh", "-n", str(script)], check=True)
        for args in ([], ["reset"], ["down", "--all"]):
            result = subprocess.run(["sh", str(script), *args], capture_output=True)
            self.assertEqual(result.returncode, 2)

    def test_launcher_remote_override_rejected_before_docker(self):
        for name in ("DOCKER_HOST", "DOCKER_CONTEXT"):
            result = subprocess.run(["sh", str(ROOT / "scripts/learning.sh"), "up"],
                env={**os.environ, "IIP_DOCKER_BIN": "true", name: "remote"}, capture_output=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn(b"Unset DOCKER_HOST/DOCKER_CONTEXT", result.stderr)

    def test_default_image_excludes_fixture_layer(self):
        dockerfile = (ROOT / "Dockerfile").read_text()
        self.assertIn("FROM runtime AS learning", dockerfile)
        self.assertTrue(dockerfile.rstrip().endswith("FROM runtime AS final"))
        self.assertNotIn("learning_fixture", dockerfile.split("FROM runtime AS learning")[0])


class LearningLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "scripts").mkdir()
        self.script = self.root / "scripts/learning.sh"
        self.script.write_text((ROOT / "scripts/learning.sh").read_text())
        self.fake = self.root / "docker"
        self.fake.write_text(f"#!{sys.executable}\n" + '''
import json, os, pathlib, sys
root = pathlib.Path(__file__).parent
args = sys.argv[1:]
if args[:1] == ['--host']:
    args = args[2:]
with (root/'calls').open('a') as handle:
    handle.write(json.dumps({'args': args, 'ambient': os.getenv('IIP_AI_FINOPS_API_PORT')})+'\\n')
mode = (root/'mode').read_text()
if args[0] == 'context':
    print('tcp://remote:2375' if mode == 'remote' else 'unix:///local.sock')
elif args[0] == 'info': print('daemon-a')
elif args[0] == 'create':
    if mode == 'locked': sys.exit(1)
    print('owned-lock-id')
elif args[0] == 'ps':
    if mode in ('existing', 'foreign'): print('container-a')
elif args[0] == 'inspect':
    print(str(root/'deploy') if mode == 'existing' else '/another/checkout/deploy')
elif args[0] == 'build':
    if mode == 'build-failed': sys.exit(1)
elif args[0] == 'run': print("IIP_AI_FINOPS_IMAGE='iip-learning:0.84.2'")
''')
        self.fake.chmod(0o700)

    def run_launcher(self, mode, command="up"):
        import json
        (self.root / "mode").write_text(mode)
        environment = {key: value for key, value in os.environ.items()
                       if key not in ("DOCKER_HOST", "DOCKER_CONTEXT")}
        environment.update(IIP_DOCKER_BIN=str(self.fake), IIP_AI_FINOPS_API_PORT="9999")
        result = subprocess.run(["sh", str(self.script), command],
                                env=environment, capture_output=True, text=True)
        calls = [json.loads(line) for line in (self.root / "calls").read_text().splitlines()]
        return result, calls

    def test_locked_daemon_cannot_build_or_adopt_project(self):
        result, calls = self.run_launcher("locked")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("build", [call["args"][0] for call in calls])
        self.assertNotIn("rm", [call["args"][0] for call in calls])
        self.assertIn("No project changes", result.stderr)

    def test_lock_precedes_ownership_check_and_is_released_on_existing_project(self):
        result, calls = self.run_launcher("existing")
        commands = [call["args"][0] for call in calls]
        self.assertEqual(result.returncode, 2)
        self.assertLess(commands.index("create"), commands.index("ps"))
        self.assertNotIn("build", commands)
        self.assertEqual(calls[-1]["args"], ["rm", "owned-lock-id"])

    def test_foreign_checkout_not_removed(self):
        result, calls = self.run_launcher("foreign", "down")
        self.assertEqual(result.returncode, 2)
        self.assertIn("another checkout", result.stderr)
        self.assertFalse(any("down" in call["args"] for call in calls))
        self.assertEqual(calls[-1]["args"], ["rm", "owned-lock-id"])

    def test_failed_build_releases_only_acquired_lock(self):
        result, calls = self.run_launcher("build-failed")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls[-1]["args"], ["rm", "owned-lock-id"])

    def test_remote_context_is_refused_before_any_mutation(self):
        result, calls = self.run_launcher("remote")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(len(calls), 1)

    def test_success_holds_lock_through_seed_and_clears_shell_settings(self):
        result, calls = self.run_launcher("new")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls[-1]["args"], ["rm", "owned-lock-id"])
        self.assertIn("learning-tools", calls[-2]["args"])
        for call in calls[1:]:
            self.assertIsNone(call["ambient"])
        for call in calls:
            if call["args"][0] in ("create", "run"):
                self.assertIn("HTTP_PROXY=", call["args"])
                self.assertIn("no_proxy=*", call["args"])

    def test_different_daemon_is_refused_before_project_changes(self):
        state = self.root / ".iip/learning"
        state.mkdir(parents=True)
        (state / "daemon").write_text("unix:///different.sock daemon-b")
        result, calls = self.run_launcher("new", "down")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(any(call["args"][0] == "ps" for call in calls))
        self.assertIn("differs", result.stderr)


if __name__ == "__main__":
    unittest.main()
