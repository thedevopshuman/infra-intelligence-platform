from __future__ import annotations

import copy
import hashlib
import json
import unittest
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace

from iip.adapters.auth import HashedBearerAuthenticator
from iip.adapters.memory import AllowTenantPolicy, InMemoryResourceStore
from iip.application.ingest_collection import (
    CollectionConflictError,
    IngestCollectionCommand,
    InvalidCollectionError,
    ResourceCollectionIngestionService,
)
from iip.application.ingest_resource import ResourceIngestionService
from iip.application.ports import ActorContext
from iip.application.ports import SourceCheckpoint
from iip.surfaces.http import ApiHandler


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
            self.store,
            self.store,
        )
        self.request = example("resource-collection-request.json")
        self.result = example("resource-collection-result.json")
        self.request["metadata"]["actorId"] = self.actor.actor_id

    def execute(self) -> tuple:
        return self.service.execute(
            IngestCollectionCommand(self.actor, self.request, self.result)
        )

    def reconciliation(
        self,
        *,
        request_id: str,
        snapshot_id: str,
        start_sequence: int,
        observations: list[dict],
        checkpoint: str,
    ) -> tuple[dict, dict]:
        request = copy.deepcopy(self.request)
        request["metadata"]["requestId"] = request_id
        request["spec"].update(
            {
                "mode": "reconciliation",
                "snapshotId": snapshot_id,
                "startSequence": start_sequence,
            }
        )
        result = copy.deepcopy(self.result)
        result["metadata"]["requestId"] = request_id
        result["spec"]["observations"] = copy.deepcopy(observations)
        for index, observation in enumerate(result["spec"]["observations"]):
            observation["metadata"]["observation"].update(
                {
                    "mode": "reconciliation",
                    "snapshotId": snapshot_id,
                    "sequence": start_sequence + index,
                }
            )
        result["spec"]["completion"] = {
            "status": "complete",
            "resourceCount": len(observations),
            "nextSequence": start_sequence + len(observations),
            "snapshotId": snapshot_id,
            "checkpoint": checkpoint,
            "scopeDigest": self.result["spec"]["completion"]["scopeDigest"],
        }
        return request, result

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
        self.assertEqual(
            dict(checkpoint.provider_cursors),
            self.result["spec"]["completion"]["providerCursors"],
        )

    def test_resume_state_must_match_the_atomically_committed_cursor_set(self) -> None:
        self.execute()
        current = self.store.get_checkpoint("local", "kubernetes-local")
        self.assertIsNotNone(current)
        assert current is not None
        request, result = self.reconciliation(
            request_id="col_10101010101010101010101010101010",
            snapshot_id="snap_10101010101010101010101010101010",
            start_sequence=current.sequence + 1,
            observations=[],
            checkpoint="kubernetes:cursor-set:v1:sha256:" + "1" * 64,
        )
        request["spec"]["resume"] = {
            "checkpoint": current.checkpoint,
            "providerCursors": dict(current.provider_cursors),
        }
        result["spec"]["completion"]["providerCursors"] = {
            "/apis/apps/v1/namespaces/default/deployments": "398713"
        }

        self.service.execute(IngestCollectionCommand(self.actor, request, result))

        advanced = self.store.get_checkpoint("local", "kubernetes-local")
        self.assertIsNotNone(advanced)
        assert advanced is not None
        self.assertEqual(
            dict(advanced.provider_cursors),
            result["spec"]["completion"]["providerCursors"],
        )
        stale_request = copy.deepcopy(request)
        stale_request["metadata"]["requestId"] = "col_20202020202020202020202020202020"
        stale_request["spec"]["snapshotId"] = "snap_20202020202020202020202020202020"
        stale_request["spec"]["startSequence"] = advanced.sequence + 1
        stale_result = copy.deepcopy(result)
        stale_result["metadata"]["requestId"] = stale_request["metadata"]["requestId"]
        stale_result["spec"]["completion"]["snapshotId"] = stale_request["spec"][
            "snapshotId"
        ]
        stale_result["spec"]["completion"]["nextSequence"] = advanced.sequence + 1

        with self.assertRaisesRegex(CollectionConflictError, "collection.resume.stale"):
            self.service.execute(
                IngestCollectionCommand(self.actor, stale_request, stale_result)
            )

    def test_incomplete_batch_is_safe_to_ingest_but_cannot_advance_checkpoint(self) -> None:
        self.result["spec"]["completion"].update(
            {"status": "partial", "reasonCode": "collector.deadline-exceeded"}
        )
        del self.result["spec"]["completion"]["checkpoint"]
        del self.result["spec"]["completion"]["providerCursors"]

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

    def test_complete_reconciliation_tombstones_missing_resource_and_replays(self) -> None:
        first_request, first_result = self.reconciliation(
            request_id="col_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            snapshot_id="snap_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            start_sequence=42,
            observations=self.result["spec"]["observations"],
            checkpoint="kubernetes:cluster-local:snapshot:first",
        )
        first_result["spec"]["observations"][0]["spec"]["relationships"] = [
            {"type": "runs_on", "target": "cluster-local"}
        ]
        active = self.service.execute(
            IngestCollectionCommand(self.actor, first_request, first_result)
        )[0]
        self.assertEqual(
            len(
                tuple(
                    self.store.relationships(
                        "local", active.identity.uid, direction="outgoing"
                    )
                )
            ),
            1,
        )
        second_request, second_result = self.reconciliation(
            request_id="col_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            snapshot_id="snap_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            start_sequence=43,
            observations=[],
            checkpoint="kubernetes:cluster-local:snapshot:second",
        )

        tombstones = self.service.execute(
            IngestCollectionCommand(self.actor, second_request, second_result)
        )
        replay = self.service.execute(
            IngestCollectionCommand(self.actor, second_request, second_result)
        )

        self.assertEqual(tombstones, replay)
        self.assertEqual(len(tombstones), 1)
        self.assertEqual(tombstones[0].identity.uid, active.identity.uid)
        self.assertEqual(tombstones[0].lifecycle, "deleted")
        self.assertEqual(tombstones[0].attributes, {})
        self.assertEqual(tombstones[0].relationships, ())
        self.assertEqual(tombstones[0].observation.sequence, 43)
        self.assertEqual(
            tuple(
                self.store.relationships(
                    "local", active.identity.uid, direction="outgoing"
                )
            ),
            (),
        )
        self.assertEqual(len(self.store.events), 2)
        checkpoint = self.store.get_checkpoint("local", "kubernetes-local")
        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint.sequence, 43)
        snapshot = self.store.get_reconciliation("local", "kubernetes-local")
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.resource_uids, ())
        self.assertEqual(snapshot.tombstoned_uids, (active.identity.uid,))

    def test_partial_reconciliation_never_implies_deletion(self) -> None:
        first_request, first_result = self.reconciliation(
            request_id="col_cccccccccccccccccccccccccccccccc",
            snapshot_id="snap_cccccccccccccccccccccccccccccccc",
            start_sequence=42,
            observations=self.result["spec"]["observations"],
            checkpoint="kubernetes:cluster-local:snapshot:first",
        )
        active = self.service.execute(
            IngestCollectionCommand(self.actor, first_request, first_result)
        )[0]
        second_request, second_result = self.reconciliation(
            request_id="col_dddddddddddddddddddddddddddddddd",
            snapshot_id="snap_dddddddddddddddddddddddddddddddd",
            start_sequence=43,
            observations=[],
            checkpoint="unused",
        )
        second_result["spec"]["completion"].update(
            {"status": "partial", "reasonCode": "collector.deadline-exceeded"}
        )
        del second_result["spec"]["completion"]["checkpoint"]

        self.service.execute(
            IngestCollectionCommand(self.actor, second_request, second_result)
        )

        self.assertEqual(self.store.get("local", active.identity.uid).lifecycle, "active")
        snapshot = self.store.get_reconciliation("local", "kubernetes-local")
        self.assertEqual(snapshot.snapshot_id, first_request["spec"]["snapshotId"])

    def test_source_scope_change_is_rejected_before_mutation(self) -> None:
        first_request, first_result = self.reconciliation(
            request_id="col_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
            snapshot_id="snap_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
            start_sequence=42,
            observations=self.result["spec"]["observations"],
            checkpoint="kubernetes:cluster-local:snapshot:first",
        )
        self.service.execute(IngestCollectionCommand(self.actor, first_request, first_result))
        second_request, second_result = self.reconciliation(
            request_id="col_ffffffffffffffffffffffffffffffff",
            snapshot_id="snap_ffffffffffffffffffffffffffffffff",
            start_sequence=43,
            observations=[],
            checkpoint="kubernetes:cluster-local:snapshot:second",
        )
        second_request["spec"]["scope"]["parameters"]["namespaces"] = ["other"]
        encoded = json.dumps(
            second_request["spec"]["scope"],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        second_result["spec"]["completion"]["scopeDigest"] = (
            "sha256:" + hashlib.sha256(encoded).hexdigest()
        )

        with self.assertRaisesRegex(CollectionConflictError, "collection.scope.changed"):
            self.service.execute(
                IngestCollectionCommand(self.actor, second_request, second_result)
            )

        self.assertEqual(len(self.store.events), 1)

    def test_snapshot_id_reuse_with_different_content_fails_closed(self) -> None:
        request, result = self.reconciliation(
            request_id="col_12341234123412341234123412341234",
            snapshot_id="snap_12341234123412341234123412341234",
            start_sequence=42,
            observations=self.result["spec"]["observations"],
            checkpoint="kubernetes:cluster-local:snapshot:first",
        )
        self.service.execute(IngestCollectionCommand(self.actor, request, result))
        changed = copy.deepcopy(result)
        changed["spec"]["observations"][0]["spec"]["attributes"][
            "availableReplicas"
        ] = 1

        with self.assertRaisesRegex(
            CollectionConflictError, "collection.snapshot.conflict"
        ):
            self.service.execute(IngestCollectionCommand(self.actor, request, changed))

        self.assertEqual(len(self.store.events), 1)

    def test_old_snapshot_retry_is_rejected_after_incremental_checkpoint(self) -> None:
        request, result = self.reconciliation(
            request_id="col_56785678567856785678567856785678",
            snapshot_id="snap_56785678567856785678567856785678",
            start_sequence=42,
            observations=self.result["spec"]["observations"],
            checkpoint="kubernetes:cluster-local:snapshot:first",
        )
        self.service.execute(IngestCollectionCommand(self.actor, request, result))
        incremental_request = copy.deepcopy(self.request)
        incremental_request["metadata"]["requestId"] = (
            "col_98769876987698769876987698769876"
        )
        incremental_request["spec"]["startSequence"] = 43
        incremental_result = copy.deepcopy(self.result)
        incremental_result["metadata"]["requestId"] = incremental_request["metadata"][
            "requestId"
        ]
        incremental_result["spec"]["observations"][0]["metadata"]["observation"][
            "sequence"
        ] = 43
        incremental_result["spec"]["observations"][0]["spec"]["attributes"][
            "availableReplicas"
        ] = 1
        incremental_result["spec"]["completion"].update(
            {
                "nextSequence": 44,
                "checkpoint": "kubernetes:cluster-local:resource-version:398713",
            }
        )
        self.service.execute(
            IngestCollectionCommand(
                self.actor, incremental_request, incremental_result
            )
        )

        with self.assertRaisesRegex(CollectionConflictError, "collection.snapshot.stale"):
            self.service.execute(IngestCollectionCommand(self.actor, request, result))

        self.assertEqual(len(self.store.events), 2)


class ResourceCollectionHttpTests(unittest.TestCase):
    def test_committed_source_conflict_returns_http_409(self) -> None:
        token = "collection-conflict-token-0123456789abcdef"
        authenticator = HashedBearerAuthenticator.from_json(
            json.dumps(
                {
                    "identities": [
                        {
                            "tokenSha256": HashedBearerAuthenticator.token_sha256(token),
                            "actorId": "collector-local",
                            "tenantId": "local",
                            "roles": [],
                        }
                    ]
                }
            )
        )

        class ConflictingCollectionService:
            def execute(self, command: IngestCollectionCommand) -> None:
                raise CollectionConflictError("collection.scope.changed")

        handler = object.__new__(ApiHandler)
        handler.runtime = SimpleNamespace(
            authenticator=authenticator,
            collection_ingestion=ConflictingCollectionService(),
        )
        handler.path = "/v1/collections/ingest"
        handler.headers = {"authorization": f"Bearer {token}"}
        handler._read_json = lambda: {"request": {}, "result": {}}
        responses: list[tuple[HTTPStatus, dict]] = []
        handler._json = lambda status, payload: responses.append((status, payload))

        handler.do_POST()

        self.assertEqual(
            responses,
            [
                (
                    HTTPStatus.CONFLICT,
                    {"error": {"code": "collection.scope.changed"}},
                )
            ],
        )
