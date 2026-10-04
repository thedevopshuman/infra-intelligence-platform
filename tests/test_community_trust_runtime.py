"""Owned Docker rotation/rollback/recovery drill; no real provider invocation."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import ssl
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

from test_community_runtime import payload, preserved_test_directory
from test_community_stack import installation_inputs
import community_backup_crypto as crypto
import community_recovery as recovery
import community_stack as stack
import community_trust as trust


@unittest.skipUnless(os.environ.get("IIP_TEST_COMMUNITY_TRUST") == "true",
                     "explicit disposable Docker community trust gate")
class CommunityTrustRuntimeTests(unittest.TestCase):
    def test_rotation_rollback_and_backup_keep_data_credentials_and_verified_transport(self):
        with preserved_test_directory() as temporary:
            parent = Path(temporary).resolve()
            source, restored = parent / "source", parent / "restored"
            stack.initialize(source, installation_inputs(), image="iip-community:trust-test")
            credentials = stack.read_protected(source / "credentials.json")
            original_manifest = (source / "installation.json").read_bytes()
            original_credentials = (source / "credentials.json").read_bytes()
            current = source

            def opener(ca):
                return urllib.request.build_opener(urllib.request.ProxyHandler({}),
                    urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=str(ca))))

            old_client = opener(source / "transport/ca.crt")

            def compose(*arguments):
                return stack.run_compose(current, arguments,
                    validate_contracts=arguments[0] in ("up", "build"))

            def command(state, name, *, build=False):
                with stack.installation_lock(state):
                    stack.execute(argparse.Namespace(state=state, command=name, build=build))

            def transition(function, **kwargs):
                with stack.installation_lock(source):
                    function(source, **kwargs)

            def sql(query):
                return compose("exec", "-T", "postgres", "psql", "-U", "iip", "-d", "iip", "-tAc", query).strip()

            def counts(expected):
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    value = sql("SELECT (SELECT count(*) FROM iip.ai_usage_records), (SELECT count(*) FROM iip.ai_cost_records WHERE cost_status='priced'), (SELECT count(*) FROM iip.ai_usage_attributions)")
                    if value == f"{expected}|{expected}|{expected}":
                        return
                    time.sleep(1)
                self.fail("pipeline did not preserve exact usage/cost/attribution counts")

            def send(client, body):
                request = urllib.request.Request("https://localhost:14322/v1/traces", data=body,
                    headers={"Content-Type": "application/x-protobuf",
                             "Authorization": "Bearer " + credentials["collectorToken"]})
                with client.open(request, timeout=5) as response:
                    self.assertEqual(response.status, 200)

            def reject(client):
                with self.assertRaises(urllib.error.URLError) as denied:
                    send(client, payload(8999))
                self.assertIsInstance(denied.exception.reason, ssl.SSLCertVerificationError)

            def grafana(path, body=None):
                token = base64.b64encode(("admin:" + credentials["grafanaPassword"]).encode()).decode()
                request = urllib.request.Request("http://127.0.0.1:13001" + path,
                    data=json.dumps(body).encode() if body else None,
                    headers={"Authorization": "Basic " + token, "Content-Type": "application/json"})
                with old_client.open(request, timeout=5) as response:
                    return json.load(response)

            def metric(at=None):
                params = {"query": 'sum(iip_ai_allocation_requests{iip_ai_allocation_dimension="application",job="iip-ai-economics"})'}
                if at is not None:
                    params["time"] = str(at)
                with old_client.open("http://127.0.0.1:19092/api/v1/query?" + urllib.parse.urlencode(params), timeout=5) as response:
                    return json.load(response)["data"]["result"]

            def verify_internal_transport():
                probe = "import os,psycopg; from iip.adapters.postgres.connection import PostgresConnectionConfiguration; c=PostgresConnectionConfiguration.from_environment(os.environ['IIP_DATABASE_URL']); db=psycopg.connect(c.connection_string); print(db.execute('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()[0]); db.close()"
                for service in ("api", "workflow-worker", "ai-usage-receiver"):
                    self.assertEqual(compose("exec", "-T", service, "python", "-c", probe).strip(), "True")
                self.assertEqual(sql("SELECT count(*) FROM pg_stat_activity a JOIN pg_stat_ssl s USING(pid) WHERE a.datname='iip' AND a.client_addr IS NOT NULL AND NOT s.ssl"), "0")
                status = trust.status(current)
                docker = recovery.RecoveryDocker(current)
                runtime = stack.read_protected(current / "runtime-images.json")
                mounts = [f"type=volume,src={stack.project_name(current)}_{name},dst=/receipts/{name},readonly,volume-nocopy"
                          for name in ("database-secrets", "database-trust", "receiver-secrets", "collector-secrets")]
                observed = docker.helper(runtime["images"]["api"], mounts,
                    ["python", "-c", "from pathlib import Path; print(','.join(sorted(p.read_text().strip() for p in Path('/receipts').glob('*/.transport-generation'))))"])
                self.assertEqual(observed.strip().split(","), [status["activeGeneration"]] * 4)

            try:
                command(source, "up", build=True)
                startup_docker = recovery.RecoveryDocker(source)
                initializer = compose("ps", "--all", "--quiet", "initialize").strip()
                initialized_at = startup_docker.text("inspect", "--format", "{{.State.StartedAt}}", initializer)
                command(source, "up")
                self.assertEqual(startup_docker.text("inspect", "--format", "{{.State.StartedAt}}", initializer), initialized_at,
                                 "repeated up must not rerun a projector against live transport files")
                original = payload(8101)
                send(old_client, original)
                counts(1)
                verify_internal_transport()
                grafana("/api/folders", {"uid": "iip-trust-fixture", "title": "Trust lifecycle fixture"})
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    result = metric()
                    if result and float(result[0]["value"][1]) == 1:
                        historical_time = result[0]["value"][0]
                        break
                    time.sleep(1)
                else:
                    self.fail("source metric was not persisted")
                compose("stop", "ai-usage-receiver")
                send(old_client, payload(8102))
                time.sleep(3)
                command(source, "down")
                transition(trust.prepare)
                prepared = trust.status(source)
                old_generation = prepared["activeGeneration"]
                overlap_client = opener(prepared["overlapCaPath"])
                transition(trust.switch, action="activate", external_trust_staged=True)
                active = trust.status(source)
                self.assertNotEqual(active["activeGeneration"], old_generation)
                new_client = opener(active["caPath"])
                command(source, "up")
                counts(2)  # The queued record crossed the newly projected mTLS domain.
                reject(old_client)
                send(overlap_client, payload(8103))
                send(new_client, payload(8104))
                counts(4)
                verify_internal_transport()
                self.assertEqual((source / "credentials.json").read_bytes(), original_credentials)
                self.assertEqual((source / "installation.json").read_bytes(), original_manifest)
                command(source, "down")
                transition(trust.switch, action="rollback", external_trust_staged=True)
                command(source, "up")
                counts(4)
                reject(new_client)
                send(overlap_client, payload(8105))
                send(old_client, payload(8106))
                counts(6)
                send(old_client, original)
                time.sleep(3)
                counts(6)
                verify_internal_transport()
                transition(trust.finalize, new_trust_verified=True)
                self.assertEqual(trust.status(source)["phase"], "stable")
                self.assertEqual(trust.status(source)["activeGeneration"], old_generation)
                command(source, "down")
                key, archive = parent / "backup.key", parent / "backup.iip"
                crypto.create_key(key)
                with stack.installation_lock(source):
                    recovery.backup(source, archive, key)
                recovery.restore(restored, archive, key, source_fenced=True)
                self.assertEqual(trust._document(restored), trust._document(source))
                self.assertFalse((restored / trust.STARTED).exists())
                current = restored
                command(restored, "up")
                counts(6)
                verify_internal_transport()
                self.assertEqual((restored / "credentials.json").read_bytes(), original_credentials)
                self.assertEqual(grafana("/api/folders/iip-trust-fixture")["title"], "Trust lifecycle fixture")
                self.assertEqual(float(metric(historical_time)[0]["value"][1]), 1)
                self.assertNotIn("private-", sql("SELECT document::text FROM iip.ai_usage_records"))
                recovery.RecoveryDocker(source).require_stopped(stack.project_name(source))
            finally:
                # Only this drill's two exact allocated projects are destroyed.
                for state in (restored, source):
                    if (state / "installation.json").is_file() and (state / "daemon.json").is_file():
                        stack.run_compose(state, ["down", "--volumes"], validate_contracts=False)


if __name__ == "__main__":
    unittest.main()
