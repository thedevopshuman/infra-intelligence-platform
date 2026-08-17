from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from infra_intelligence_sdk import Client, EventDeliveryReplayCommand
from iip.adapters.actions import (
    ActionExecutorRouter,
    EventDeliveryReplayExecutor,
    KubernetesRestartDryRunExecutor,
)
from iip.adapters.evidence import SystemClock
from iip.adapters.memory import AllowTenantPolicy, InMemoryResourceStore
from iip.adapters.operations import InMemoryOperationalStore
from iip.application.actions import (
    ActionWorkflowError,
    DecideActionCommand,
    ExecuteActionCommand,
    GovernedActionService,
    ProposeActionCommand,
)
from iip.application.ingest_resource import IngestResourceCommand, ResourceIngestionService
from iip.application.ports import ActorContext


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


def example(name: str) -> dict:
    return json.loads(
        (ROOT / "contracts" / "examples" / name).read_text(encoding="utf-8")
    )


def assert_schema(test: unittest.TestCase, name: str, document: dict) -> None:
    errors: list[str] = []
    schema = validate_schemas.load_json(
        ROOT / "contracts" / "schemas" / name,
        errors,
    )
    test.assertEqual(errors, [])
    test.assertEqual(
        validate_schemas.instance_validation_errors(
            schema,
            document,
            label=name,
        ),
        [],
    )


class GovernedEventDeliveryReplayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.resources = InMemoryResourceStore()
        self.policy = AllowTenantPolicy()
        stored = ResourceIngestionService(self.resources, self.policy).execute(
            IngestResourceCommand(
                ActorContext("collector", "local"),
                example("resource.json"),
            )
        )
        self.target_uid = stored.identity.uid
        claimed = tuple(self.resources.claim_outbox("local", "publisher"))[0]
        self.assertTrue(
            self.resources.quarantine_outbox(
                "local",
                "publisher",
                claimed.message_id,
                "event.publisher.unavailable",
            )
        )
        self.quarantine = self.resources.get_quarantined_outbox(
            "local",
            claimed.message_id,
        )
        assert self.quarantine is not None

        self.operations = InMemoryOperationalStore()
        request = example("investigation-request.json")
        report = example("investigation-report.json")
        status = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "InvestigationStatus",
            "metadata": {
                "id": request["metadata"]["id"],
                "tenantId": "local",
                "updatedAt": report["spec"]["completedAt"],
            },
            "spec": {
                "requestDigest": report["spec"]["requestDigest"],
                "state": "completed",
                "startedAt": report["spec"]["startedAt"],
                "completedAt": report["spec"]["completedAt"],
                "reportRef": f"investigation://local/{request['metadata']['id']}/report",
            },
        }
        self.proposer = ActorContext(
            "incident-operator",
            "local",
            ("platform-admin",),
        )
        self.operations.commit_investigation(
            self.proposer,
            request["metadata"]["id"],
            request,
            report,
            status,
        )
        self.service = GovernedActionService(
            self.resources,
            self.policy,
            self.operations,
            ActionExecutorRouter(
                KubernetesRestartDryRunExecutor(),
                EventDeliveryReplayExecutor(self.resources),
            ),
            self.operations,
            SystemClock(),
            self.operations,
            self.resources,
        )

    def proposal(self, *, dry_run: bool = False) -> dict:
        return self.service.propose(
            ProposeActionCommand(
                actor=self.proposer,
                investigation_id="inv_71c9e4a2b8d04f65a3c7e9b102d4f608",
                action_type="event-delivery.requeue",
                target_resource_uid=self.target_uid,
                parameters={
                    "outboxId": self.quarantine.message_id,
                    "eventId": self.quarantine.event_id,
                    "quarantinedAt": self.quarantine.quarantined_at,
                    "attempts": self.quarantine.attempts,
                },
                idempotency_key=(
                    "incident-71c9-requeue-dry-run"
                    if dry_run
                    else "incident-71c9-requeue-live"
                ),
                expires_at="2099-08-17T13:30:00Z",
                dry_run=dry_run,
            )
        )

    def approve(self, proposal: dict) -> None:
        self.service.decide(
            DecideActionCommand(
                ActorContext("on-call-approver", "local", ("approver",)),
                proposal["metadata"]["id"],
                "approved",
                "The exact quarantine generation and receiver recovery were reviewed.",
            )
        )

    def test_live_requeue_is_high_risk_one_shot_and_resets_retry_budget(self) -> None:
        proposal = self.proposal()
        assert_schema(self, "action-proposal.schema.json", proposal)
        self.assertEqual(proposal["spec"]["risk"], "high")
        self.assertFalse(proposal["spec"]["reversible"])
        self.assertNotIn("integrationId", proposal["spec"])
        self.approve(proposal)
        executor = ActorContext("workflow-executor", "local", ("executor",))

        result = self.service.execute(
            ExecuteActionCommand(executor, proposal["metadata"]["id"])
        )

        assert_schema(self, "action-result.schema.json", result)
        self.assertEqual(result["spec"]["outcome"], "succeeded")
        self.assertEqual(
            result["spec"]["execution"]["provider"],
            "iip-event-outbox",
        )
        self.assertIsNone(
            self.resources.get_quarantined_outbox(
                "local",
                self.quarantine.message_id,
            )
        )
        replayed = tuple(self.resources.claim_outbox("local", "publisher-2"))
        self.assertEqual(len(replayed), 1)
        self.assertEqual(replayed[0].attempts, 1)

        duplicate = self.service.execute(
            ExecuteActionCommand(executor, proposal["metadata"]["id"])
        )
        self.assertEqual(duplicate, result)
        self.assertTrue(
            self.resources.acknowledge_outbox(
                "local",
                "publisher-2",
                replayed[0].message_id,
            )
        )

    def test_dry_run_preserves_quarantine_and_live_replay_requires_admin(self) -> None:
        proposal = self.proposal(dry_run=True)
        self.approve(proposal)
        result = self.service.execute(
            ExecuteActionCommand(
                ActorContext("workflow-executor", "local", ("executor",)),
                proposal["metadata"]["id"],
            )
        )

        self.assertEqual(result["spec"]["outcome"], "dry-run")
        self.assertIsNotNone(
            self.resources.get_quarantined_outbox(
                "local",
                self.quarantine.message_id,
            )
        )
        with self.assertRaisesRegex(ActionWorkflowError, "replay.role-required"):
            self.service.propose(
                ProposeActionCommand(
                    actor=ActorContext("developer", "local"),
                    investigation_id="inv_71c9e4a2b8d04f65a3c7e9b102d4f608",
                    action_type="event-delivery.requeue",
                    target_resource_uid=self.target_uid,
                    parameters=proposal["spec"]["parameters"],
                    idempotency_key="incident-71c9-requeue-denied",
                    expires_at="2099-08-17T13:30:00Z",
                    dry_run=False,
                )
            )

    def test_generation_and_tenant_preconditions_fail_closed(self) -> None:
        parameters = {
            "outboxId": self.quarantine.message_id,
            "eventId": "a-different-event",
            "quarantinedAt": self.quarantine.quarantined_at,
            "attempts": self.quarantine.attempts,
        }
        with self.assertRaisesRegex(ActionWorkflowError, "target-mismatch"):
            self.service.propose(
                ProposeActionCommand(
                    actor=self.proposer,
                    investigation_id="inv_71c9e4a2b8d04f65a3c7e9b102d4f608",
                    action_type="event-delivery.requeue",
                    target_resource_uid=self.target_uid,
                    parameters=parameters,
                    idempotency_key="incident-71c9-requeue-stale",
                    expires_at="2099-08-17T13:30:00Z",
                    dry_run=False,
                )
            )
        self.assertIsNone(
            self.resources.get_quarantined_outbox(
                "another-tenant",
                self.quarantine.message_id,
            )
        )
        self.assertFalse(
            self.resources.requeue_quarantined_outbox(
                "another-tenant",
                self.quarantine.message_id,
                expected_event_id=self.quarantine.event_id,
                expected_quarantined_at=self.quarantine.quarantined_at,
                expected_attempts=self.quarantine.attempts,
            )
        )


class EventDeliveryReplaySdkTests(unittest.TestCase):
    def test_python_sdk_posts_typed_replay_command(self) -> None:
        payload = example("action-proposal-event-delivery-replay.json")

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(payload).encode("utf-8")

        command = EventDeliveryReplayCommand(
            investigation_id="inv_71c9e4a2b8d04f65a3c7e9b102d4f608",
            target_resource_uid="res_e0ae9225a316fce4c97df5c23057b97a",
            outbox_id=1042,
            event_id="evt_3b03ac5bb4d94fd3b574ebee8ed5c14f",
            quarantined_at="2026-08-17T12:58:45Z",
            attempts=8,
            idempotency_key="incident-71c9-requeue-event-1042",
            expires_at="2026-08-17T13:35:00Z",
            dry_run=False,
        )
        client = Client(
            "https://control.example",
            "replay-sdk-token-0123456789abcdef0123456789abcdef",
        )
        with patch(
            "infra_intelligence_sdk.client.urlopen",
            return_value=Response(),
        ) as send:
            proposal = client.propose_event_delivery_replay(command)

        self.assertEqual(proposal.to_dict(), payload)
        request = send.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://control.example/v1/actions/proposals",
        )
        sent = json.loads(request.data)
        self.assertEqual(sent["actionType"], "event-delivery.requeue")
        self.assertEqual(sent["parameters"]["quarantinedAt"], command.quarantined_at)


if __name__ == "__main__":
    unittest.main()
