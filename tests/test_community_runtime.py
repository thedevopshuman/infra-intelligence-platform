"""Explicit Docker gate for a disposable instance of the persistent installer.

The synthetic inputs never enter the normal installation command. This proves
local transport/durability and rendering, not real prices or a live provider.
"""

from __future__ import annotations

import base64
from contextlib import contextmanager
import json
import os
import shutil
import ssl
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.trace.v1.trace_pb2 import Span

from test_community_stack import installation_inputs

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import community_stack as stack


@contextmanager
def preserved_test_directory():
    directory = tempfile.mkdtemp(prefix="iip-community-test-")
    try:
        yield directory
    except Exception:
        # In particular, never destroy the only recovery state if Docker
        # cleanup fails and a restart-enabled test container remains alive.
        print(f"Protected test state retained for diagnosis: {directory}")
        raise
    else:
        shutil.rmtree(directory)


def payload(identifier: int, *, content: bool = False, poison_key: str | None = None) -> bytes:
    request = ExportTraceServiceRequest()
    resources = request.resource_spans.add()
    resources.resource.entity_refs.add(schema_url="private-entity-schema", type="private-entity-type")
    for key, value in {
        "service.name": "support-assistant", "service.namespace": "customer-experience",
        "deployment.environment.name": "production", "cloud.region": "us-east-1",
    }.items():
        attribute = resources.resource.attributes.add(key=key)
        attribute.value.string_value = value
    scope = resources.scope_spans.add()
    scope.scope.name = stack.SCOPE
    # Arbitrary schema URLs are scrubbed before the persistent queue.
    resources.schema_url = "private-schema-resource"
    scope.schema_url = "private-schema-scope"
    span = scope.spans.add(
        trace_id=identifier.to_bytes(16, "big"), span_id=identifier.to_bytes(8, "big"),
        name="private-span-name", kind=Span.SPAN_KIND_CLIENT,
        start_time_unix_nano=time.time_ns() - 200_000_000,
        end_time_unix_nano=time.time_ns() - 100_000_000,
    )
    for message, marker in ((resources.resource, b"private-unknown-resource"), (scope.scope, b"private-unknown-scope"), (span, b"private-unknown-span")):
        # Unknown protobuf field 999, length-delimited. A pinned pipeline must
        # not preserve future/unknown raw fields in its durable queue either.
        message.MergeFromString(b"\xba\x3e" + bytes([len(marker)]) + marker)
    attributes = {
        "gen_ai.system": "aws.bedrock", "gen_ai.operation.name": "chat",
        "gen_ai.request.model": "example.foundation-model-v1:0",
        "gen_ai.usage.input_tokens": 100, "gen_ai.usage.output_tokens": 10,
        "gen_ai.usage.cache_read.input_tokens": 0,
        "gen_ai.usage.cache_creation.input_tokens": 0,
        "gen_ai.usage.reasoning.output_tokens": 0,
        "aws.retry_count": 0,
        "aws.request_id": "private-request-id-before-hashing",
        "error.type": "private-error-type-before-drop",
    }
    if content:
        attributes["gen_ai.prompt"] = "private-prompt-must-not-be-queued"
    if poison_key is not None:
        attributes[poison_key] = "private-allowed-key-must-not-be-queued"
    for key, value in attributes.items():
        attribute = span.attributes.add(key=key)
        if isinstance(value, int):
            attribute.value.int_value = value
        else:
            attribute.value.string_value = value
    return request.SerializeToString()


@unittest.skipUnless(os.environ.get("IIP_TEST_COMMUNITY_RUNTIME") == "true", "explicit disposable Docker community gate")
class CommunityRuntimeTests(unittest.TestCase):
    def test_empty_install_tls_pipeline_queue_and_restart_durability(self) -> None:
        with preserved_test_directory() as temporary:
            state = Path(temporary) / "installation"
            stack.initialize(state, installation_inputs(), image="iip-community:test")
            credentials = stack.read_protected(state / "credentials.json")
            context = ssl.create_default_context(cafile=str(state / "transport" / "ca.crt"))
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context))

            def compose(*arguments: str) -> str:
                return stack.run_compose(state, arguments, validate_contracts=arguments[0] in ("up", "build"))

            def sql(query: str) -> str:
                return compose("exec", "-T", "postgres", "psql", "-U", "iip", "-d", "iip", "-tAc", query).strip()

            def wait_counts(expected: int) -> None:
                deadline = time.monotonic() + 90
                while time.monotonic() < deadline:
                    observed = sql("SELECT (SELECT count(*) FROM iip.ai_usage_records), (SELECT count(*) FROM iip.ai_cost_records WHERE cost_status='priced'), (SELECT count(*) FROM iip.ai_usage_attributions)")
                    if observed == f"{expected}|{expected}|{expected}":
                        return
                    time.sleep(1)
                self.fail("community pipeline did not converge to exact usage/cost/attribution counts")

            def send(body: bytes, *, token: str | None = None) -> None:
                request = urllib.request.Request("https://localhost:14322/v1/traces", data=body, headers={
                    "Content-Type": "application/x-protobuf",
                    "Authorization": "Bearer " + (token or credentials["collectorToken"]),
                })
                with opener.open(request, timeout=5) as response:
                    self.assertEqual(response.status, 200)

            try:
                self.assertEqual(stack.main(["--state", str(state), "up", "--build"]), 0)
                stack.wait_for_collector(state)
                # Exercise the documented first login, not just container health.
                with opener.open("http://127.0.0.1:18083/console", timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(b"<html", response.read())
                session_url = "http://127.0.0.1:18083/v1/session"
                for token in (None, credentials["collectorToken"]):
                    request = urllib.request.Request(session_url)
                    if token:
                        request.add_header("Authorization", "Bearer " + token)
                    with self.assertRaises(urllib.error.HTTPError) as denied:
                        opener.open(request, timeout=5)
                    self.assertEqual(denied.exception.code, 401)
                    denied.exception.close()
                request = urllib.request.Request(session_url, headers={
                    "Authorization": "Bearer " + credentials["apiToken"],
                })
                with opener.open(request, timeout=5) as response:
                    session = json.load(response)
                self.assertEqual(session["metadata"], {
                    "tenantId": "local", "actorId": "community-operator",
                })
                self.assertEqual(sql("SELECT count(*) FROM iip.ai_usage_records"), "0")
                self.assertEqual(sql("SELECT count(*) FROM pg_stat_activity a JOIN pg_stat_ssl s USING(pid) WHERE a.datname='iip' AND a.client_addr IS NOT NULL AND NOT s.ssl"), "0")
                probe = "import os,psycopg; from iip.adapters.postgres.connection import PostgresConnectionConfiguration; c=PostgresConnectionConfiguration.from_environment(os.environ['IIP_DATABASE_URL']); db=psycopg.connect(c.connection_string); print(db.execute('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()[0]); db.close()"
                for component in ("api", "workflow-worker", "ai-usage-receiver"):
                    self.assertEqual(compose("exec", "-T", component, "python", "-c", probe).strip(), "True")
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    send(payload(1), token="incorrect-token")
                self.assertIn(denied.exception.code, (401, 403))
                denied.exception.close()
                original = payload(1)
                send(original)
                wait_counts(1)
                send(original)
                send(payload(3, content=True))
                time.sleep(3)
                wait_counts(1)
                # A stopped downstream receiver must not block application OTLP
                # acceptance; only the scrubbed span belongs in durable storage.
                compose("stop", "ai-usage-receiver")
                send(payload(2))
                send(payload(4, content=True))
                send(payload(5, poison_key="gen_ai.request.model"))
                time.sleep(3)
                queue_scan = compose("run", "--rm", "--no-deps", "--entrypoint", "python", "initialize", "-c",
                    "from pathlib import Path; data=b''.join(p.read_bytes() for p in Path('/volumes/queue').rglob('*') if p.is_file()); markers=(b'private-span-name',b'private-schema-resource',b'private-schema-scope',b'private-prompt-must-not-be-queued',b'private-request-id-before-hashing',b'private-error-type-before-drop',b'private-allowed-key-must-not-be-queued',b'private-entity-schema',b'private-entity-type',b'private-unknown-resource',b'private-unknown-scope',b'private-unknown-span'); print(','.join(v.decode() for v in markers if v in data) or ('clean' if data else 'empty'))")
                self.assertEqual(queue_scan.strip(), "clean")
                compose("down")
                self.assertEqual(stack.main(["--state", str(state), "up"]), 0)
                stack.wait_for_collector(state)
                wait_counts(2)
                expression = 'sum(iip_ai_allocation_requests{iip_ai_allocation_dimension="application",job="iip-ai-economics"})'
                endpoint = "http://127.0.0.1:19092/api/v1/query?" + urllib.parse.urlencode({"query": expression})
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    with opener.open(endpoint, timeout=5) as response:
                        results = json.load(response)["data"]["result"]
                    if results and float(results[0]["value"][1]) == 2:
                        break
                    time.sleep(1)
                else:
                    self.fail("rolling allocation did not reach Prometheus with savings disabled")
                request = urllib.request.Request("http://127.0.0.1:13001/api/dashboards/uid/iip-community-ai-finops")
                with self.assertRaises(urllib.error.HTTPError) as denied:
                    opener.open(request, timeout=5)
                self.assertEqual(denied.exception.code, 401)
                denied.exception.close()
                basic = base64.b64encode(("admin:" + credentials["grafanaPassword"]).encode()).decode()
                request.add_header("Authorization", "Basic " + basic)
                with opener.open(request, timeout=5) as response:
                    self.assertEqual(json.load(response)["dashboard"]["uid"], "iip-community-ai-finops")
                self.assertNotIn("private-", sql("SELECT document::text FROM iip.ai_usage_records"))
            finally:
                # This exact project was allocated above for this test alone.
                # Normal community-stack down never supplies --volumes.
                compose("down", "--volumes")


if __name__ == "__main__":
    unittest.main()
