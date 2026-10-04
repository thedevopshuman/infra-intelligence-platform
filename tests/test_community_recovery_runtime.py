"""Explicit local Docker gate; synthetic inputs, no provider or customer traffic."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import ssl
import time
import unittest
import urllib.parse
import urllib.request

from test_community_runtime import payload, preserved_test_directory
from test_community_stack import installation_inputs
import community_backup_crypto as crypto
import community_recovery as recovery
import community_stack as stack


@unittest.skipUnless(os.environ.get("IIP_TEST_COMMUNITY_RECOVERY") == "true",
                     "explicit disposable Docker community recovery gate")
class CommunityRecoveryRuntimeTests(unittest.TestCase):
    def test_encrypted_offline_point_restores_database_queue_and_dashboards(self):
        with preserved_test_directory() as temporary:
            parent = Path(temporary).resolve()
            source, restored = parent / "source", parent / "restored"
            archive, key = parent / "backup.iipbak", parent / "key.bin"
            stack.initialize(source, installation_inputs(), image="iip-community:recovery-test")
            credentials = stack.read_protected(source / "credentials.json")
            context = ssl.create_default_context(cafile=str(source / "transport/ca.crt"))
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                urllib.request.HTTPSHandler(context=context))
            current = source

            def compose(*arguments):
                return stack.run_compose(current, arguments,
                    validate_contracts=arguments[0] in ("up", "build"))

            def command(state, name, *, build=False):
                with stack.installation_lock(state):
                    stack.execute(argparse.Namespace(state=state, command=name, build=build))

            def sql(query):
                return compose("exec", "-T", "postgres", "psql", "-U", "iip", "-d", "iip", "-tAc", query).strip()

            def counts(expected):
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    observed = sql("SELECT (SELECT count(*) FROM iip.ai_usage_records), (SELECT count(*) FROM iip.ai_cost_records WHERE cost_status='priced'), (SELECT count(*) FROM iip.ai_usage_attributions)")
                    if observed == f"{expected}|{expected}|{expected}":
                        return
                    time.sleep(1)
                self.fail("restored pipeline did not reach exact usage/cost/attribution counts")

            def send(body):
                request = urllib.request.Request("https://localhost:14322/v1/traces", data=body,
                    headers={"Content-Type": "application/x-protobuf",
                             "Authorization": "Bearer " + credentials["collectorToken"]})
                with opener.open(request, timeout=5) as response:
                    self.assertEqual(response.status, 200)

            def grafana(path, body=None):
                token = base64.b64encode(("admin:" + credentials["grafanaPassword"]).encode()).decode()
                request = urllib.request.Request("http://127.0.0.1:13001" + path,
                    data=json.dumps(body).encode() if body else None,
                    headers={"Authorization": "Basic " + token, "Content-Type": "application/json"})
                with opener.open(request, timeout=5) as response:
                    return json.load(response)

            def metric(at=None):
                parameters = {"query": 'sum(iip_ai_allocation_requests{iip_ai_allocation_dimension="application",job="iip-ai-economics"})'}
                if at is not None:
                    parameters["time"] = str(at)
                with opener.open("http://127.0.0.1:19092/api/v1/query?" + urllib.parse.urlencode(parameters), timeout=5) as response:
                    return json.load(response)["data"]["result"]

            try:
                command(source, "up", build=True)
                runtime = stack.read_protected(source / "runtime-images.json")
                self.assertEqual(set(runtime["images"]), set(recovery.SERVICES))
                original = payload(7001)
                send(original)
                counts(1)
                grafana("/api/folders", {"uid": "iip-recovery-fixture", "title": "Recovery fixture"})
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    observed = metric()
                    if observed and float(observed[0]["value"][1]) == 1:
                        history_time = observed[0]["value"][0]
                        break
                    time.sleep(1)
                else:
                    self.fail("source metric was not scraped before backup")
                compose("stop", "ai-usage-receiver")
                send(payload(7002))
                time.sleep(3)
                command(source, "down")
                crypto.create_key(key)
                with stack.installation_lock(source):
                    recovery.backup(source, archive, key)
                self.assertGreater(archive.stat().st_size, 1024**2)
                recovery.restore(restored, archive, key, source_fenced=True)
                docker = recovery.RecoveryDocker(restored)
                docker.require_stopped(stack.project_name(source))
                docker.require_stopped(stack.project_name(restored))
                self.assertEqual(stack.read_protected(restored / "credentials.json"), credentials)
                self.assertEqual((source / "transport/ca.crt").read_bytes(), (restored / "transport/ca.crt").read_bytes())
                self.assertFalse((restored / ".recovery-incomplete").exists())
                current = restored
                command(restored, "up")
                self.assertEqual(stack.read_protected(restored / "runtime-images.json"), runtime)
                counts(2)
                send(original)
                time.sleep(3)
                counts(2)
                self.assertEqual(grafana("/api/folders/iip-recovery-fixture")["title"], "Recovery fixture")
                self.assertEqual(grafana("/api/dashboards/uid/iip-community-ai-finops")["dashboard"]["uid"], "iip-community-ai-finops")
                historical = metric(history_time)
                self.assertTrue(historical)
                self.assertEqual(float(historical[0]["value"][1]), 1)
                with opener.open("http://127.0.0.1:18083/readyz", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                self.assertNotIn("private-", sql("SELECT document::text FROM iip.ai_usage_records"))
                docker.require_stopped(stack.project_name(source))
            finally:
                # Only these two generated test projects may lose their volumes.
                # On cleanup failure the context manager keeps protected state.
                for state in (restored, source):
                    if (state / "installation.json").is_file() and (state / "daemon.json").is_file():
                        stack.run_compose(state, ["down", "--volumes"], validate_contracts=False)


if __name__ == "__main__":
    unittest.main()
