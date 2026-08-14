from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from iip.adapters.memory import AllowTenantPolicy, InMemoryResourceStore
from iip.application.ingest_collection import (
    IngestCollectionCommand,
    InvalidCollectionError,
    ResourceCollectionIngestionService,
)
from iip.application.ingest_resource import ResourceIngestionService
from iip.application.ports import ActorContext
from iip.application.ports import SourceCheckpoint


ROOT = Path(__file__).resolve().parents[1]


def example(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text())


class ResourceCollectionIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.actor = ActorContext("collector-local", "local")
        self.store = InMemoryResourceStore()
        self.service = ResourceCollectionIngestionService(
            ResourceIngestionService(self.store, AllowTenantPolicy()),
            self.store,
        )
        self.request = example("resource-collection-request.json")
        self.result = example("resource-collection-result.json")
        self.request["metadata"]["actorId"] = self.actor.actor_id

    def execute(self) -> tuple:
        return self.service.execute(
            IngestCollectionCommand(self.actor, self.request, self.result)
        )

    def test_complete_batch_ingests_resources_then_advances_checkpoint(self) -> None:
        resources = self.execute()

        self.assertEqual(len(resources), len(self.result["spec"]["observations"]))
        checkpoint = self.store.get_checkpoint("local", self.request["spec"]["sourceId"])
        self.assertIsNotNone(checkpoint)
        self.assertEqual(
            checkpoint.checkpoint,
            self.result["spec"]["completion"]["checkpoint"],
        )
        self.assertEqual(
            checkpoint.sequence,
            self.result["spec"]["completion"]["nextSequence"] - 1,
        )

    def test_incomplete_batch_is_safe_to_ingest_but_cannot_advance_checkpoint(self) -> None:
        self.result["spec"]["completion"].update(
            {"status": "partial", "reasonCode": "collector.deadline-exceeded"}
        )
        del self.result["spec"]["completion"]["checkpoint"]

        self.execute()

        self.assertIsNone(
            self.store.get_checkpoint("local", self.request["spec"]["sourceId"])
        )

    def test_scope_mismatch_rejects_before_any_mutation(self) -> None:
        invalid = copy.deepcopy(self.result)
        invalid["metadata"]["tenantId"] = "another-tenant"

        with self.assertRaisesRegex(InvalidCollectionError, "collection.scope.mismatch"):
            self.service.execute(
                IngestCollectionCommand(self.actor, self.request, invalid)
            )

        self.assertEqual(tuple(self.store.list("local")), ())

    def test_checkpoint_cannot_move_backwards(self) -> None:
        self.execute()
        current = self.store.get_checkpoint("local", self.request["spec"]["sourceId"])
        self.assertIsNotNone(current)

        with self.assertRaisesRegex(ValueError, "checkpoint sequence cannot move backwards"):
            self.store.commit_checkpoint(
                SourceCheckpoint(
                    tenant_id=current.tenant_id,
                    source_id=current.source_id,
                    stream_id=current.stream_id,
                    sequence=current.sequence - 1,
                    checkpoint=current.checkpoint,
                    committed_at=current.committed_at,
                ),
                mode="reconciliation",
            )
