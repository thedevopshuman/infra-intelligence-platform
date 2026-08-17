"""Proposal-only plugin action mediation and SDK boundary tests."""

from __future__ import annotations

import copy
import json
import socket
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

from infra_intelligence_sdk import (
    PluginActionMediationRequest,
    PluginMediationClient,
)
from iip.adapters.plugin_mediation import StaticPluginMediationBindingRegistry
from iip.application.investigate import canonical_digest
from iip.application.plugin_mediation import PluginMediationError, PluginMediationService
from iip.application.ports import ActorContext, PolicyDecision
from scripts import validate_schemas


ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 8, 14, 12, 44, 32, tzinfo=timezone.utc)


def example(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text())


def action_documents() -> tuple[dict, dict, dict, dict]:
    manifest = example("plugin-manifest.json")
    manifest["spec"]["permissions"]["network"] = []
    manifest["spec"]["permissions"]["secrets"] = []
    manifest["spec"]["permissions"]["actions"] = [
        "kubernetes.restart-workload"
    ]
    invocation = example("plugin-invocation.json")
    grant = example("plugin-action-mediation-grant.json")
    request = example("plugin-action-mediation-request.json")
    invocation["metadata"]["id"] = grant["metadata"]["invocationId"]
    invocation["metadata"]["createdAt"] = grant["metadata"]["issuedAt"]
    invocation["metadata"]["deadline"] = grant["metadata"]["expiresAt"]
    invocation["spec"]["manifestDigest"] = canonical_digest(manifest)
    invocation["spec"]["actionMediationGrants"] = [grant]
    return manifest, invocation, grant, request


class RecordingPolicy:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls = []

    def decide(self, actor, action, resource):
        self.calls.append((actor, action, resource))
        return PolicyDecision(
            self.allowed,
            "policy.plugin.action-allowed" if self.allowed else "policy.plugin.action-denied",
            "policy://local/snapshots/plugin-action-v1",
        )


class RecordingAudit:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.documents = []

    def append_audit(self, actor, category, document):
        del actor
        if self.fail:
            raise RuntimeError("unavailable")
        self.documents.append((category, copy.deepcopy(document)))
        return "audit://local/plugin-actions/1"


class UnusedReadGateway:
    def fetch_plugin_json(self, *args, **kwargs):
        del args, kwargs
        raise AssertionError("action-only mediation cannot use the read gateway")


class RecordingActionGateway:
    def __init__(self) -> None:
        self.calls = []

    def propose_plugin_action(self, actor, **arguments):
        self.calls.append({"actor": actor, **copy.deepcopy(arguments)})
        proposal = example("action-proposal.json")
        proposal["metadata"]["actorId"] = actor.actor_id
        proposal["metadata"]["tenantId"] = actor.tenant_id
        proposal["metadata"]["createdAt"] = "2026-08-14T12:44:32Z"
        proposal["spec"]["investigationId"] = arguments["investigation_id"]
        proposal["spec"]["actionType"] = arguments["action_type"]
        proposal["spec"]["targetResourceUid"] = arguments["target_resource_uid"]
        proposal["spec"]["parameters"] = dict(arguments["parameters"])
        proposal["spec"]["idempotencyKey"] = arguments["idempotency_key"]
        proposal["spec"]["expiresAt"] = arguments["expires_at"]
        proposal["spec"]["dryRun"] = arguments["dry_run"]
        return proposal


class PluginActionMediationServiceTests(unittest.TestCase):
    actor = ActorContext("plugin-host", "local", ("developer",))

    def service(self, *, policy=None, audit=None, action_gateway=None):
        selected_policy = policy or RecordingPolicy()
        selected_audit = audit or RecordingAudit()
        selected_action_gateway = action_gateway or RecordingActionGateway()
        service = PluginMediationService(
            StaticPluginMediationBindingRegistry(()),
            selected_policy,
            selected_audit,
            UnusedReadGateway(),
            action_gateway=selected_action_gateway,
            now=lambda: NOW,
        )
        return service, selected_policy, selected_audit, selected_action_gateway

    def test_proposal_is_policy_checked_audited_derived_and_idempotent(self) -> None:
        manifest, invocation, _, request = action_documents()
        service, policy, audit, gateway = self.service()
        mediator = service.bind(self.actor, manifest, invocation)

        first = mediator.handle(request)
        replay = mediator.handle(copy.deepcopy(request))

        self.assertEqual(replay, first)
        self.assertEqual(first["spec"]["status"], "proposed")
        self.assertEqual(first["spec"]["dryRun"], True)
        self.assertEqual(len(gateway.calls), 1)
        self.assertEqual(policy.calls[0][1], "plugin:propose-action")
        self.assertIn("parametersDigest", policy.calls[0][2])
        self.assertNotIn("parameters", policy.calls[0][2])
        self.assertEqual(audit.documents[0][0], "plugin-action-proposal-intent")
        self.assertTrue(gateway.calls[0]["idempotency_key"].startswith("plugin-action-"))
        self.assertEqual(gateway.calls[0]["expires_at"], "2026-08-14T12:45:00Z")
        schema = json.loads(
            (
                ROOT
                / "contracts"
                / "schemas"
                / "plugin-action-mediation-response.schema.json"
            ).read_text()
        )
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                schema, first, label="action mediation response"
            ),
            [],
        )

    def test_grant_dry_run_target_limit_policy_and_audit_fail_closed(self) -> None:
        manifest, invocation, _, request = action_documents()
        service, _, _, gateway = self.service()
        mediator = service.bind(self.actor, manifest, invocation)

        live = copy.deepcopy(request)
        live["spec"]["dryRun"] = False
        self.assertEqual(
            mediator.handle(live)["spec"]["error"]["code"],
            "plugin.action.denied",
        )
        wrong_target = copy.deepcopy(request)
        wrong_target["metadata"]["id"] = "par_" + "c" * 32
        wrong_target["spec"]["targetResourceUid"] = "res_" + "d" * 32
        self.assertEqual(
            mediator.handle(wrong_target)["spec"]["error"]["code"],
            "plugin.action.denied",
        )
        approval_smuggling = copy.deepcopy(request)
        approval_smuggling["metadata"]["id"] = "par_" + "9" * 32
        approval_smuggling["spec"]["approvalId"] = "apr_" + "1" * 32
        self.assertEqual(
            mediator.handle(approval_smuggling)["spec"]["error"]["code"],
            "plugin.action.request-invalid",
        )
        valid = copy.deepcopy(request)
        valid["metadata"]["id"] = "par_" + "e" * 32
        self.assertEqual(mediator.handle(valid)["spec"]["status"], "proposed")
        over_limit = copy.deepcopy(request)
        over_limit["metadata"]["id"] = "par_" + "f" * 32
        self.assertEqual(
            mediator.handle(over_limit)["spec"]["error"]["code"],
            "plugin.action.limit-exceeded",
        )
        self.assertEqual(len(gateway.calls), 1)

        service, _, _, gateway = self.service(audit=RecordingAudit(fail=True))
        response = service.bind(self.actor, manifest, invocation).handle(request)
        self.assertEqual(
            response["spec"]["error"]["code"],
            "plugin.action.audit-unavailable",
        )
        self.assertEqual(gateway.calls, [])

    def test_grant_cannot_widen_manifest_or_run_without_action_gateway(self) -> None:
        manifest, invocation, _, _ = action_documents()
        manifest["spec"]["permissions"]["actions"] = []
        invocation["spec"]["manifestDigest"] = canonical_digest(manifest)
        service, _, _, _ = self.service()
        with self.assertRaisesRegex(PluginMediationError, "binding-invalid"):
            service.bind(self.actor, manifest, invocation)

        manifest, invocation, _, _ = action_documents()
        service = PluginMediationService(
            StaticPluginMediationBindingRegistry(()),
            RecordingPolicy(),
            RecordingAudit(),
            UnusedReadGateway(),
            now=lambda: NOW,
        )
        with self.assertRaisesRegex(PluginMediationError, "binding-invalid"):
            service.bind(self.actor, manifest, invocation)

    def test_policy_denial_and_untrusted_gateway_output_never_create_a_receipt(
        self,
    ) -> None:
        manifest, invocation, _, request = action_documents()
        policy = RecordingPolicy(allowed=False)
        audit = RecordingAudit()
        service, _, _, gateway = self.service(policy=policy, audit=audit)

        denied = service.bind(self.actor, manifest, invocation).handle(request)

        self.assertEqual(denied["spec"]["error"]["code"], "plugin.action.denied")
        self.assertEqual(audit.documents, [])
        self.assertEqual(gateway.calls, [])

        class InvalidGateway:
            def propose_plugin_action(self, actor, **arguments):
                del actor, arguments
                return {
                    "kind": "ActionApproval",
                    "secret": "provider detail must not cross the socket",
                }

        service, _, _, _ = self.service(action_gateway=InvalidGateway())
        rejected = service.bind(self.actor, manifest, invocation).handle(request)
        self.assertEqual(
            rejected["spec"]["error"]["code"],
            "plugin.action.proposal-rejected",
        )
        self.assertNotIn("secret", json.dumps(rejected))


class PluginActionMediationSdkTests(unittest.TestCase):
    def test_sdk_uses_same_bounded_socket_without_confusing_response_kind(self) -> None:
        request_document = example("plugin-action-mediation-request.json")
        response_document = example("plugin-action-mediation-response.json")
        with tempfile.TemporaryDirectory(prefix="iip-action-sdk-", dir="/tmp") as tmp:
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
            response = PluginMediationClient(socket_path).propose_action(
                PluginActionMediationRequest.from_dict(request_document)
            )
            thread.join(2)

        self.assertTrue(response.proposed)
        self.assertEqual(response.proposal_id, "act_" + "5" * 32)
        self.assertIsNone(response.error_code)


if __name__ == "__main__":
    unittest.main()
