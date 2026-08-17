from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from http import HTTPStatus
from pathlib import Path

from iip.adapters.memory import AllowTenantPolicy
from iip.adapters.auth import HashedBearerAuthenticator
from iip.adapters.operations import InMemoryOperationalStore
from iip.bootstrap import build_local_runtime
from iip.application.ports import ActionWorkflowRecord, ActorContext, PolicyDecision
from iip.application.query_actions import (
    ActionQueryAuthorizationError,
    ActionQueryError,
    ActionWorkflowQueryService,
)
from iip.application.investigate import canonical_digest
from iip.surfaces.http import ApiHandler
from infra_intelligence_sdk import ActionWorkflow, ActionWorkflowPage, Client


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


def example(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text())


def assert_schema(test: unittest.TestCase, name: str, document: dict) -> None:
    schema = validate_schemas.load_json(ROOT / "contracts" / "schemas" / name, [])
    test.assertEqual(
        validate_schemas.instance_validation_errors(schema, document, label=name), []
    )


class ActionWorkflowQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryOperationalStore()
        self.actor = ActorContext("operator", "local", ("developer",))
        self.clock = type(
            "FixedClock",
            (),
            {"now": lambda _self: "2026-08-14T13:15:00Z"},
        )()
        self.service = ActionWorkflowQueryService(
            self.store, AllowTenantPolicy(), self.clock
        )

    def proposal(self, digit: str, created_at: str) -> dict:
        proposal = copy.deepcopy(example("action-proposal.json"))
        proposal_id = "act_" + digit * 32
        proposal["metadata"].update(
            {"id": proposal_id, "actorId": f"proposer-{digit}", "createdAt": created_at}
        )
        proposal["spec"]["idempotencyKey"] = f"action-query-{digit}-0001"
        return proposal

    def test_tenant_bound_page_is_newest_first_and_cursor_stable(self) -> None:
        for digit, created_at in (
            ("1", "2026-08-17T10:00:00Z"),
            ("2", "2026-08-17T10:01:00Z"),
            ("3", "2026-08-17T10:02:00Z"),
        ):
            self.store.commit_proposal(self.actor, self.proposal(digit, created_at))
        other = ActorContext("other", "other")
        foreign = self.proposal("4", "2026-08-17T10:03:00Z")
        foreign["metadata"]["tenantId"] = "other"
        self.store.commit_proposal(other, foreign)

        first = self.service.list(self.actor, limit=2)
        cursor = first["spec"]["page"]["nextCursor"]
        second = self.service.list(self.actor, limit=2, cursor=cursor)

        self.assertEqual(
            [item["metadata"]["id"] for item in first["spec"]["items"]],
            ["act_" + "3" * 32, "act_" + "2" * 32],
        )
        self.assertTrue(first["spec"]["page"]["hasMore"])
        self.assertEqual(
            [item["metadata"]["id"] for item in second["spec"]["items"]],
            ["act_" + "1" * 32],
        )
        self.assertFalse(second["spec"]["page"]["hasMore"])
        assert_schema(self, "action-workflow-page.schema.json", first)
        assert_schema(self, "action-workflow-page.schema.json", second)

        with self.assertRaisesRegex(ActionQueryError, "cursor_invalid"):
            self.service.list(other, limit=2, cursor=cursor)

    def test_workflow_reconstructs_approval_state(self) -> None:
        proposal = self.proposal("5", "2026-08-17T10:05:00Z")
        self.store.commit_proposal(self.actor, proposal)
        approval = copy.deepcopy(example("action-approval.json"))
        approval["metadata"]["id"] = "apr_" + "6" * 32
        approval["spec"]["proposalId"] = proposal["metadata"]["id"]
        approval["spec"]["proposalDigest"] = canonical_digest(proposal)
        self.store.commit_approval(self.actor, approval)

        workflow = self.service.get(self.actor, proposal["metadata"]["id"])

        self.assertEqual(workflow["spec"]["state"], "approved")
        self.assertEqual(workflow["spec"]["approval"], approval)
        assert_schema(self, "action-workflow.schema.json", workflow)

    def test_workflow_derives_expiry_without_mutating_the_proposal(self) -> None:
        pending = self.proposal("7", "2026-08-14T13:00:00Z")
        self.store.commit_proposal(self.actor, pending)
        approved = self.proposal("8", "2026-08-14T13:01:00Z")
        self.store.commit_proposal(self.actor, approved)
        approval = copy.deepcopy(example("action-approval.json"))
        approval["metadata"]["id"] = "apr_" + "8" * 32
        approval["spec"]["proposalId"] = approved["metadata"]["id"]
        approval["spec"]["proposalDigest"] = canonical_digest(approved)
        self.store.commit_approval(self.actor, approval)
        self.clock.now = lambda: "2026-08-14T13:31:00Z"  # type: ignore[method-assign]

        pending_workflow = self.service.get(self.actor, pending["metadata"]["id"])
        approved_workflow = self.service.get(self.actor, approved["metadata"]["id"])

        self.assertEqual(pending_workflow["spec"]["state"], "expired")
        self.assertEqual(approved_workflow["spec"]["state"], "expired")
        self.assertEqual(pending_workflow["spec"]["proposal"]["status"], "pending-approval")
        assert_schema(self, "action-workflow.schema.json", pending_workflow)
        assert_schema(self, "action-workflow.schema.json", approved_workflow)

    def test_terminal_state_prefers_result_and_rejects_corrupt_relationships(self) -> None:
        record = ActionWorkflowRecord(
            proposal=example("action-proposal.json"),
            approval=example("action-approval.json"),
            execution_status=example("action-execution-status.json"),
            result=example("action-result.json"),
        )

        workflow = self.service._workflow(self.actor, record)

        self.assertEqual(workflow["spec"]["state"], "dry-run")
        assert_schema(self, "action-workflow.schema.json", workflow)

        corrupt = copy.deepcopy(example("action-approval.json"))
        corrupt["spec"]["proposalId"] = "act_" + "9" * 32
        with self.assertRaisesRegex(RuntimeError, "storage.corrupt"):
            self.service._workflow(
                self.actor,
                ActionWorkflowRecord(
                    proposal=example("action-proposal.json"), approval=corrupt
                ),
            )

        corrupt_result = copy.deepcopy(example("action-result.json"))
        corrupt_result["spec"]["proposalDigest"] = "sha256:" + "0" * 64
        with self.assertRaisesRegex(RuntimeError, "storage.corrupt"):
            self.service._workflow(
                self.actor,
                ActionWorkflowRecord(
                    proposal=example("action-proposal.json"),
                    approval=example("action-approval.json"),
                    execution_status=example("action-execution-status.json"),
                    result=corrupt_result,
                ),
            )

        with self.assertRaisesRegex(RuntimeError, "storage.corrupt"):
            self.service._workflow(
                self.actor,
                ActionWorkflowRecord(
                    proposal=example("action-proposal.json"),
                    execution_status=example("action-execution-status.json"),
                ),
            )

    def test_policy_denial_happens_before_storage(self) -> None:
        class DenyPolicy:
            def decide(self, actor, action, resource):
                del actor, action, resource
                return PolicyDecision(False, "test.denied")

        service = ActionWorkflowQueryService(self.store, DenyPolicy(), self.clock)

        with self.assertRaises(ActionQueryAuthorizationError):
            service.list(self.actor)


class ActionWorkflowHttpAndSdkTests(unittest.TestCase):
    token = "action-workflow-console-token-0123456789abcdef"

    def runtime(self):
        digest = hashlib.sha256(self.token.encode()).hexdigest()
        authenticator = HashedBearerAuthenticator.from_json(
            json.dumps(
                {
                    "identities": [
                        {
                            "tokenSha256": f"sha256:{digest}",
                            "actorId": "workflow-reader",
                            "tenantId": "local",
                            "roles": ["developer"],
                        }
                    ]
                }
            )
        )
        runtime = build_local_runtime(authenticator)
        actor = ActorContext("workflow-reader", "local", ("developer",))
        runtime.operational_store.commit_proposal(
            actor, example("action-proposal.json")
        )
        return runtime

    def test_http_lists_and_gets_consistent_workflows(self) -> None:
        handler = object.__new__(ApiHandler)
        handler.runtime = self.runtime()
        handler.headers = {"authorization": f"Bearer {self.token}"}
        responses: list[tuple[HTTPStatus, dict]] = []
        handler._json = lambda status, payload: responses.append((status, payload))

        handler.path = "/v1/actions?limit=10"
        handler.do_GET()
        self.assertEqual(responses[-1][0], HTTPStatus.OK)
        self.assertEqual(responses[-1][1]["kind"], "ActionWorkflowPage")
        assert_schema(
            self, "action-workflow-page.schema.json", responses[-1][1]
        )

        handler.path = "/v1/actions/act_55555555555555555555555555555555/workflow"
        handler.do_GET()
        self.assertEqual(responses[-1][0], HTTPStatus.OK)
        self.assertEqual(responses[-1][1]["kind"], "ActionWorkflow")
        assert_schema(self, "action-workflow.schema.json", responses[-1][1])

        handler.path = "/v1/actions?limit=101"
        handler.do_GET()
        self.assertEqual(responses[-1][0], HTTPStatus.BAD_REQUEST)

    def test_python_sdk_parses_workflow_and_page_contracts(self) -> None:
        client = Client("https://control-plane.example", self.token)
        workflow = example("action-workflow.json")
        page = example("action-workflow-page.json")
        calls: list[str] = []

        def get(path: str):
            calls.append(path)
            return page if path.startswith("/v1/actions?") else workflow

        client._get = get  # type: ignore[method-assign]

        parsed_page = client.list_action_workflows(limit=25, cursor="p1.cursor")
        parsed_workflow = client.get_action_workflow(workflow["metadata"]["id"])

        self.assertIsInstance(parsed_page, ActionWorkflowPage)
        self.assertIsInstance(parsed_workflow, ActionWorkflow)
        self.assertEqual(calls[0], "/v1/actions?limit=25&cursor=p1.cursor")


if __name__ == "__main__":
    unittest.main()
