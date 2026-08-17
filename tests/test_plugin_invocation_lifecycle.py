from __future__ import annotations

import copy
import json
import sys
import unittest
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path

from iip.adapters.auth import HashedBearerAuthenticator
from iip.adapters.memory import AllowTenantPolicy
from iip.adapters.operations import InMemoryOperationalStore
from iip.application.investigate import canonical_digest
from iip.application.plugin_invocations import (
    CancelPluginInvocationCommand,
    GetPluginInvocationStatusCommand,
    PluginInvocationAuthorizationError,
    PluginInvocationConflictError,
    PluginInvocationLifecycleService,
    PluginInvocationNotFoundError,
    ReconcilePluginInvocationCommand,
)
from iip.application.ports import ActorContext
from iip.bootstrap import build_local_runtime
from iip.surfaces.http import ApiHandler
from infra_intelligence_sdk import (
    Client,
    PluginInvocationCancellationRequest,
    PluginInvocationReconciliationRequest,
    PluginInvocationStatus,
)


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


def example(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text())


class FixedClock:
    def __init__(self, value: str) -> None:
        self.value = value

    def now(self) -> str:
        return self.value


def claim(store: InMemoryOperationalStore, actor: ActorContext) -> tuple[dict, dict]:
    session = example("plugin-session.json")
    invocation = example("plugin-invocation.json")
    store.commit_plugin_session(actor, session)
    outcome = store.claim_plugin_invocation(
        actor,
        session,
        invocation,
        canonical_digest(invocation),
        "2026-08-14T12:44:40Z",
    )
    if outcome.state != "claimed":
        raise AssertionError(outcome)
    return session, invocation


class PluginInvocationLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.actor = ActorContext("plugin-host", "local", ("developer",))
        self.store = InMemoryOperationalStore()
        self.session, self.invocation = claim(self.store, self.actor)
        self.clock = FixedClock("2026-08-14T12:46:00Z")
        self.service = PluginInvocationLifecycleService(
            self.store, AllowTenantPolicy(), self.clock
        )

    def assert_contract(self, name: str, document: dict) -> None:
        schema = json.loads(
            (ROOT / "contracts" / "schemas" / name).read_text()
        )
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                schema, document, label=name
            ),
            [],
        )

    def test_status_and_first_cancellation_are_tenant_scoped_and_immutable(self) -> None:
        invocation_id = self.invocation["metadata"]["id"]
        initial = self.service.get(
            GetPluginInvocationStatusCommand(self.actor, invocation_id)
        )
        self.assertEqual(initial["spec"]["state"], "claimed")

        request = example("plugin-invocation-cancellation-request.json")
        cancelled = self.service.cancel(
            CancelPluginInvocationCommand(self.actor, request)
        )
        changed = copy.deepcopy(request)
        changed["spec"]["reasonCode"] = "shutdown"
        repeated = self.service.cancel(
            CancelPluginInvocationCommand(self.actor, changed)
        )

        self.assertEqual(cancelled, repeated)
        self.assertEqual(
            cancelled["spec"]["cancellation"]["reasonCode"],
            "operator-requested",
        )
        self.assert_contract("plugin-invocation-status.schema.json", cancelled)
        with self.assertRaises(PluginInvocationNotFoundError):
            self.service.get(
                GetPluginInvocationStatusCommand(
                    ActorContext("reader", "another-tenant"), invocation_id
                )
            )

    def test_reconciliation_requires_admin_and_deadline_then_never_replays(self) -> None:
        request = example("plugin-invocation-reconciliation-request.json")
        non_admin_request = copy.deepcopy(request)
        non_admin_request["metadata"]["actorId"] = self.actor.actor_id
        with self.assertRaises(PluginInvocationAuthorizationError):
            self.service.reconcile(
                ReconcilePluginInvocationCommand(self.actor, non_admin_request)
            )

        admin = ActorContext("platform-operator", "local", ("platform-admin",))
        self.clock.value = "2026-08-14T12:44:50Z"
        early = copy.deepcopy(request)
        early["metadata"]["requestedAt"] = self.clock.now()
        with self.assertRaises(PluginInvocationConflictError):
            self.service.reconcile(
                ReconcilePluginInvocationCommand(admin, early)
            )

        self.clock.value = "2026-08-14T12:46:00Z"
        terminal = self.service.reconcile(
            ReconcilePluginInvocationCommand(admin, request)
        )
        self.assertEqual(terminal["spec"]["state"], "failed")
        self.assert_contract("plugin-invocation-status.schema.json", terminal)
        replay = self.store.claim_plugin_invocation(
            self.actor,
            self.session,
            self.invocation,
            canonical_digest(self.invocation),
            self.clock.now(),
        )
        self.assertEqual(replay.state, "completed")
        self.assertEqual(
            replay.result["spec"]["error"]["code"],
            "plugin.execution.outcome-unknown",
        )

    def test_cancellation_racing_reconciliation_is_closed_as_cancelled(self) -> None:
        invocation_id = self.invocation["metadata"]["id"]
        original = self.store.reconcile_plugin_invocation
        injected = False

        def reconcile_with_cancellation(
            actor, request_id, observed_at, result, status, audit_document
        ):
            nonlocal injected
            if not injected:
                injected = True
                current = self.store.get_plugin_invocation_status(
                    self.actor, invocation_id
                )
                pending = copy.deepcopy(current)
                pending["metadata"]["updatedAt"] = "2026-08-14T12:45:30Z"
                pending["spec"]["state"] = "cancellation-requested"
                pending["spec"]["cancellation"] = {
                    "requestedBy": self.actor.actor_id,
                    "requestedAt": "2026-08-14T12:45:30Z",
                    "reasonCode": "shutdown",
                }
                self.store.request_plugin_invocation_cancellation(
                    self.actor,
                    invocation_id,
                    pending,
                    {"metadata": {"tenantId": self.actor.tenant_id}},
                )
            return original(
                actor,
                request_id,
                observed_at,
                result,
                status,
                audit_document,
            )

        self.store.reconcile_plugin_invocation = reconcile_with_cancellation
        admin = ActorContext("platform-operator", "local", ("platform-admin",))
        status = self.service.reconcile(
            ReconcilePluginInvocationCommand(
                admin, example("plugin-invocation-reconciliation-request.json")
            )
        )

        self.assertEqual(status["spec"]["state"], "cancelled")
        self.assertEqual(
            status["spec"]["cancellation"]["reasonCode"], "shutdown"
        )


class PluginInvocationLifecycleHttpAndSdkTests(unittest.TestCase):
    token = "plugin-lifecycle-token-0123456789abcdef"

    def runtime(self):
        authenticator = HashedBearerAuthenticator.from_json(
            json.dumps(
                {
                    "identities": [
                        {
                            "tokenSha256": HashedBearerAuthenticator.token_sha256(
                                self.token
                            ),
                            "actorId": "plugin-host",
                            "tenantId": "local",
                            "roles": ["developer"],
                        }
                    ]
                }
            )
        )
        runtime = build_local_runtime(authenticator)
        actor = ActorContext("plugin-host", "local", ("developer",))
        claim(runtime.operational_store, actor)
        return runtime

    def test_http_status_and_cancel_paths_enforce_body_identity(self) -> None:
        runtime = self.runtime()
        invocation_id = example("plugin-invocation.json")["metadata"]["id"]
        handler = object.__new__(ApiHandler)
        handler.runtime = runtime
        handler.headers = {"authorization": f"Bearer {self.token}"}
        handler.responses = []
        handler._json = lambda status, body: handler.responses.append((status, body))

        handler.path = f"/v1/plugin-invocations/{invocation_id}/status"
        handler.do_GET()
        self.assertEqual(handler.responses[-1][0], HTTPStatus.OK)

        cancellation = example("plugin-invocation-cancellation-request.json")
        handler.path = f"/v1/plugin-invocations/{invocation_id}/cancel"
        handler._read_json = lambda: cancellation
        handler.do_POST()
        self.assertEqual(handler.responses[-1][0], HTTPStatus.ACCEPTED)
        self.assertEqual(
            handler.responses[-1][1]["spec"]["state"],
            "cancellation-requested",
        )

        mismatched = copy.deepcopy(cancellation)
        mismatched["spec"]["invocationId"] = "pin_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        handler._read_json = lambda: mismatched
        handler.do_POST()
        self.assertEqual(handler.responses[-1][0], HTTPStatus.BAD_REQUEST)

    def test_python_sdk_parses_all_lifecycle_contracts_and_paths(self) -> None:
        cancellation = PluginInvocationCancellationRequest.from_dict(
            example("plugin-invocation-cancellation-request.json")
        )
        reconciliation = PluginInvocationReconciliationRequest.from_dict(
            example("plugin-invocation-reconciliation-request.json")
        )
        status_payload = example("plugin-invocation-status.json")
        invocation_id = cancellation.to_dict()["spec"]["invocationId"]
        client = Client("https://platform.example", "valid-token")
        calls: list[tuple[str, str]] = []
        client._get = lambda path: (
            calls.append(("GET", path)) or status_payload
        )
        client._post = lambda path, payload, *args: (
            calls.append(("POST", path)) or status_payload
        )

        status = client.get_plugin_invocation_status(invocation_id)
        cancelled = client.cancel_plugin_invocation(cancellation)
        reconciled = client.reconcile_plugin_invocation(reconciliation)

        self.assertIsInstance(status, PluginInvocationStatus)
        self.assertIsInstance(cancelled, PluginInvocationStatus)
        self.assertIsInstance(reconciled, PluginInvocationStatus)
        self.assertEqual(
            calls,
            [
                ("GET", f"/v1/plugin-invocations/{invocation_id}/status"),
                ("POST", f"/v1/plugin-invocations/{invocation_id}/cancel"),
                ("POST", f"/v1/plugin-invocations/{invocation_id}/reconcile"),
            ],
        )

    def test_openapi_describes_closed_lifecycle_routes_and_conflict(self) -> None:
        openapi = json.loads(
            (ROOT / "api" / "openapi" / "control-plane.openapi.json").read_text()
        )
        paths = openapi["paths"]
        status = paths["/v1/plugin-invocations/{invocationId}/status"]["get"]
        cancellation = paths["/v1/plugin-invocations/{invocationId}/cancel"]["post"]
        reconciliation = paths["/v1/plugin-invocations/{invocationId}/reconcile"]["post"]

        self.assertEqual(status["operationId"], "getPluginInvocationStatus")
        self.assertIn("400", status["responses"])
        self.assertEqual(cancellation["operationId"], "cancelPluginInvocation")
        self.assertEqual(
            reconciliation["responses"]["409"]["$ref"],
            "#/components/responses/PluginInvocationLifecycleConflict",
        )
        self.assertIn(
            "plugin.reconciliation.deadline-live",
            json.dumps(
                openapi["components"]["responses"][
                    "PluginInvocationLifecycleConflict"
                ]
            ),
        )


if __name__ == "__main__":
    unittest.main()
