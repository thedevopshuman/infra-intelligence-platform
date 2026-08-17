"""Host-mediated plugin connectivity, adapter, and SDK boundary tests."""

from __future__ import annotations

import copy
import json
import socket
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from infra_intelligence_sdk import PluginMediationClient, PluginMediationRequest
from iip.adapters.plugin_mediation import (
    HttpsJsonPluginMediationGateway,
    NoPluginMediationRedirectHandler,
    StaticPluginMediationBindingRegistry,
)
from iip.application.plugin_mediation import PluginMediationError, PluginMediationService
from iip.application.ports import (
    ActorContext,
    CredentialLease,
    PluginMediationBinding,
    PolicyDecision,
)


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 14, 12, 44, 32, tzinfo=timezone.utc)


def example(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text())


def binding(**changes: object) -> PluginMediationBinding:
    values = {
        "tenant_id": "local",
        "plugin_id": "kubernetes-observer",
        "plugin_version": "0.3.0",
        "grant_id": "pmg_5f2ab8de42a7498ba9b23b331101acdf",
        "integration_id": "kubernetes-local",
        "provider": "kubernetes",
        "destination": "kubernetes.default.svc:443",
        "credential_name": "kubernetes.projected-service-account-token",
        "endpoint": "https://kubernetes.default.svc",
        "credential_ref": "credential://local/integrations/kubernetes-local/token",
        "ca_bundle_path": None,
        "path_templates": ("/api/v1/namespaces/{namespace}/pods",),
        "query_keys": ("continue", "fieldSelector", "labelSelector", "limit"),
        "scopes": ("kubernetes:read",),
        "max_requests": 8,
        "max_response_bytes": 1_048_576,
    }
    values.update(changes)
    return PluginMediationBinding(**values)


class RecordingPolicy:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[ActorContext, str, object]] = []

    def decide(self, actor, action, resource):
        self.calls.append((actor, action, resource))
        return PolicyDecision(self.allowed, "test.allow" if self.allowed else "test.deny")


class RecordingAudit:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.documents: list[dict] = []

    def append_audit(self, actor, category, document):
        del actor, category
        if self.fail:
            raise RuntimeError("unavailable")
        self.documents.append(dict(document))
        return "audit://local/plugin-mediation/1"


class RecordingGateway:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def fetch_plugin_json(self, actor, selected, **arguments):
        self.calls.append({"actor": actor, "binding": selected, **arguments})
        return {
            "apiVersion": "v1",
            "items": [],
            "kind": "PodList",
            "metadata": {"continue": "", "resourceVersion": "12345"},
        }


class PluginMediationServiceTests(unittest.TestCase):
    actor = ActorContext("plugin-host", "local", ("developer",))

    def service(self, *, audit=None, gateway=None, selected=None, policy=None):
        selected_binding = selected or binding()
        selected_gateway = gateway or RecordingGateway()
        return (
            PluginMediationService(
                StaticPluginMediationBindingRegistry((selected_binding,)),
                policy or RecordingPolicy(),
                audit or RecordingAudit(),
                selected_gateway,
                now=lambda: NOW,
            ),
            selected_gateway,
        )

    def test_valid_grant_is_policy_checked_audited_and_returns_bounded_json(self):
        manifest = example("plugin-manifest.json")
        invocation = example("plugin-invocation-mediated.json")
        request = example("plugin-mediation-request.json")
        service, gateway = self.service()

        response = service.bind(self.actor, manifest, invocation).handle(request)

        self.assertEqual(response, example("plugin-mediation-response.json"))
        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(gateway.calls[0]["path"], request["spec"]["path"])
        self.assertEqual(gateway.calls[0]["query"]["limit"], ("100",))

    def test_path_query_replay_and_count_limits_fail_before_egress(self):
        manifest = example("plugin-manifest.json")
        invocation = example("plugin-invocation-mediated.json")
        grant = invocation["spec"]["mediationGrants"][0]
        grant["spec"]["limits"]["maxRequests"] = 1
        selected = binding(max_requests=1)
        service, gateway = self.service(selected=selected)
        mediator = service.bind(self.actor, manifest, invocation)

        traversal = example("plugin-mediation-request.json")
        traversal["spec"]["path"] = "/api/v1/namespaces/../pods"
        self.assertEqual(
            mediator.handle(traversal)["spec"]["error"]["code"],
            "plugin.mediation.denied",
        )
        disallowed_query = example("plugin-mediation-request.json")
        disallowed_query["metadata"]["id"] = "pmr_" + "1" * 32
        disallowed_query["spec"]["query"] = {"watch": "true"}
        self.assertEqual(
            mediator.handle(disallowed_query)["spec"]["error"]["code"],
            "plugin.mediation.denied",
        )
        valid = example("plugin-mediation-request.json")
        self.assertEqual(mediator.handle(valid)["spec"]["status"], "succeeded")
        repeated = copy.deepcopy(valid)
        repeated["metadata"]["id"] = "pmr_" + "2" * 32
        self.assertEqual(
            mediator.handle(repeated)["spec"]["error"]["code"],
            "plugin.mediation.limit-exceeded",
        )
        self.assertEqual(len(gateway.calls), 1)

    def test_audit_failure_prevents_provider_call(self):
        service, gateway = self.service(audit=RecordingAudit(fail=True))
        response = service.bind(
            self.actor,
            example("plugin-manifest.json"),
            example("plugin-invocation-mediated.json"),
        ).handle(example("plugin-mediation-request.json"))
        self.assertEqual(
            response["spec"]["error"]["code"],
            "plugin.mediation.audit-unavailable",
        )
        self.assertEqual(gateway.calls, [])

    def test_binding_mismatch_fails_before_socket_or_provider(self):
        service, _ = self.service(selected=binding(integration_id="kubernetes-other"))
        with self.assertRaisesRegex(PluginMediationError, "binding-mismatch"):
            service.bind(
                self.actor,
                example("plugin-manifest.json"),
                example("plugin-invocation-mediated.json"),
            )


class RecordingCredentialBroker:
    def __init__(self) -> None:
        self.requests = []

    def resolve(self, request):
        self.requests.append(request)
        return CredentialLease("bearer", "provider-secret-token-123456")


class RecordingHttpTransport:
    def __init__(self) -> None:
        self.calls = []

    def get(self, url, headers, **options):
        self.calls.append({"url": url, "headers": dict(headers), **options})
        return b'{"apiVersion":"v1","items":[],"kind":"PodList"}'


class PluginMediationAdapterTests(unittest.TestCase):
    def test_gateway_uses_exact_lease_direct_destination_and_encoded_query(self):
        broker = RecordingCredentialBroker()
        transport = RecordingHttpTransport()
        gateway = HttpsJsonPluginMediationGateway(
            broker, transport=transport, now=lambda: NOW
        )
        document = gateway.fetch_plugin_json(
            ActorContext("plugin-host", "local"),
            binding(),
            path="/api/v1/namespaces/default/pods",
            query={"fieldSelector": ("status.phase!=Succeeded",), "limit": ("100",)},
            deadline="2026-08-14T12:45:00Z",
            max_response_bytes=1_048_576,
        )

        self.assertEqual(document["kind"], "PodList")
        self.assertEqual(broker.requests[0].credential_ref, binding().credential_ref)
        self.assertEqual(
            transport.calls[0]["url"],
            "https://kubernetes.default.svc/api/v1/namespaces/default/pods?"
            "fieldSelector=status.phase%21%3DSucceeded&limit=100",
        )
        self.assertEqual(
            transport.calls[0]["headers"]["Authorization"],
            "Bearer provider-secret-token-123456",
        )
        self.assertNotIn("provider-secret-token", repr(gateway))

    def test_redirects_are_disabled(self):
        self.assertIsNone(NoPluginMediationRedirectHandler().redirect_request())


class PluginMediationSdkTests(unittest.TestCase):
    def test_sdk_sends_one_framed_request_and_correlates_response(self):
        request_document = example("plugin-mediation-request.json")
        response_document = example("plugin-mediation-response.json")
        with tempfile.TemporaryDirectory(prefix="iip-mediation-sdk-", dir="/tmp") as tmp:
            socket_path = str(Path(tmp) / "request.sock")
            ready = threading.Event()

            def serve() -> None:
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                    server.bind(socket_path)
                    server.listen(1)
                    ready.set()
                    connection, _ = server.accept()
                    with connection:
                        raw = b""
                        while not raw.endswith(b"\n"):
                            raw += connection.recv(8192)
                        self.assertEqual(json.loads(raw), request_document)
                        connection.sendall(
                            json.dumps(response_document, separators=(",", ":")).encode()
                            + b"\n"
                        )

            thread = threading.Thread(target=serve, daemon=True)
            thread.start()
            self.assertTrue(ready.wait(2))
            response = PluginMediationClient(socket_path).request(
                PluginMediationRequest.from_dict(request_document)
            )
            thread.join(2)

        self.assertTrue(response.succeeded)
        self.assertEqual(response.body["kind"], "PodList")
        self.assertIsNone(response.error_code)


if __name__ == "__main__":
    unittest.main()
