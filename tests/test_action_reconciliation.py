from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

from iip.adapters.operations import InMemoryOperationalStore
from iip.application.action_reconciliation import ActionReconciliationService
from iip.application.ports import ActorContext


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


def example(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text())


class MutableClock:
    value = "2026-08-17T10:06:00Z"

    def now(self) -> str:
        return self.value


class ActionReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryOperationalStore()
        self.clock = MutableClock()
        self.service = ActionReconciliationService(
            self.store,
            self.clock,
            worker_id="reconciler-a",
            batch_size=10,
        )

    @staticmethod
    def executing(tenant_id: str, digit: str, lease_expires_at: str) -> dict:
        document = copy.deepcopy(example("action-execution-status.json"))
        document["metadata"] = {
            "id": "act_" + digit * 32,
            "tenantId": tenant_id,
            "updatedAt": "2026-08-17T10:00:00Z",
        }
        document["spec"].update(
            {
                "state": "executing",
                "startedAt": "2026-08-17T10:00:00Z",
                "leaseExpiresAt": lease_expires_at,
            }
        )
        for field in ("completedAt", "operationRef", "summary"):
            document["spec"].pop(field, None)
        return document

    def claim(self, tenant_id: str, digit: str, lease_expires_at: str) -> dict:
        actor = ActorContext("executor", tenant_id, ("executor",))
        document = self.executing(tenant_id, digit, lease_expires_at)
        self.assertTrue(self.store.claim_action_execution(actor, document))
        return document

    def test_exact_tenant_expired_execution_is_closed_once_without_replay(self) -> None:
        expired = self.claim("local", "1", "2026-08-17T10:05:00Z")
        future = self.claim("local", "2", "2026-08-17T10:07:00Z")
        foreign = self.claim("other", "3", "2026-08-17T10:05:00Z")

        first = self.service.run_once("local")
        second = self.service.run_once("local")

        self.assertEqual((first.scanned, first.transitioned), (1, 1))
        self.assertEqual((second.scanned, second.transitioned), (0, 0))
        actor = ActorContext("reader", "local")
        resolved = self.store.get_action_execution_status(
            actor, expired["metadata"]["id"]
        )
        self.assertEqual(
            resolved["spec"]["state"], "manual-reconciliation-required"
        )
        self.assertNotIn("leaseExpiresAt", resolved["spec"])
        self.assertEqual(
            self.store.get_action_execution_status(actor, future["metadata"]["id"])[
                "spec"
            ]["state"],
            "executing",
        )
        self.assertEqual(
            self.store.get_action_execution_status(
                ActorContext("reader", "other"), foreign["metadata"]["id"]
            )["spec"]["state"],
            "executing",
        )
        self.assertEqual(len(self.store._audit), 1)
        self.assertEqual(
            self.store._audit[0][1],
            "action-execution-reconciliation-required",
        )
        schema = validate_schemas.load_json(
            ROOT / "contracts" / "schemas" / "action-execution-status.schema.json",
            [],
        )
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                schema, resolved, label="action-execution-status.schema.json"
            ),
            [],
        )

    def test_tenant_wildcard_and_invalid_worker_configuration_fail_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "tenant.invalid"):
            self.service.run_once("*")
        with self.assertRaisesRegex(ValueError, "configuration.invalid"):
            ActionReconciliationService(
                self.store,
                self.clock,
                worker_id="bad worker id",
            )


if __name__ == "__main__":
    unittest.main()
