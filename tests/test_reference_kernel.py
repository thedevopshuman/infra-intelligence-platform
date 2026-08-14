from __future__ import annotations

import json
import unittest
from pathlib import Path

from iip.adapters.memory import (
    AllowTenantPolicy,
    InMemoryResourceStore,
)
from iip.application.ingest_resource import (
    AuthorizationError,
    IngestResourceCommand,
    InvalidInputError,
    ObservationConflictError,
    ResourceIngestionService,
    StaleObservationError,
)
from iip.application.ports import ActorContext
from iip.domain.models import (
    ContractError,
    ObservationCursor,
    PlatformEvent,
    Resource,
    ResourceIdentity,
)
from infra_intelligence_sdk import ResourceObservation, ResourceObservationCursor


ROOT = Path(__file__).resolve().parents[1]


def resource_payload() -> dict:
    return json.loads((ROOT / "contracts/examples/resource.json").read_text(encoding="utf-8"))


class ResourceIdentityTests(unittest.TestCase):
    def test_identity_is_stable_and_tenant_scoped(self) -> None:
        first = ResourceIdentity("tenant-a", "kubernetes", "apps/deployment", "cluster/ns/api")
        retry = ResourceIdentity("tenant-a", "kubernetes", "apps/deployment", "cluster/ns/api")
        other_tenant = ResourceIdentity("tenant-b", "kubernetes", "apps/deployment", "cluster/ns/api")

        self.assertEqual(first.uid, retry.uid)
        self.assertNotEqual(first.uid, other_tenant.uid)
        self.assertRegex(first.uid, r"^res_[a-f0-9]{32}$")

    def test_resource_round_trip_adds_canonical_uid(self) -> None:
        resource = Resource.from_dict(resource_payload())
        serialized = resource.to_dict()
        self.assertEqual(serialized["metadata"]["uid"], resource.identity.uid)
        self.assertEqual(serialized["metadata"]["observation"]["sequence"], 42)
        self.assertEqual(serialized["status"]["health"], "degraded")

    def test_reconciliation_cursor_requires_snapshot_identity(self) -> None:
        with self.assertRaisesRegex(ContractError, "snapshotId is required"):
            ObservationCursor(
                source_id="kubernetes-local",
                stream_id="obs_0f4e8c2a6b1d4975a3c9e7f102d468ab",
                sequence=1,
                mode="reconciliation",
            )

    def test_platform_event_round_trips_structured_cloudevent(self) -> None:
        payload = json.loads(
            (ROOT / "contracts/examples/event.json").read_text(encoding="utf-8")
        )

        event = PlatformEvent.from_dict(payload)

        self.assertEqual(event.to_dict(), payload)


class ResourceIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryResourceStore()
        self.events = self.repository
        self.service = ResourceIngestionService(
            self.repository,
            AllowTenantPolicy(),
        )

    def test_ingestion_persists_and_publishes_correlated_event(self) -> None:
        stored = self.service.execute(
            IngestResourceCommand(
                actor=ActorContext("developer", "local"),
                payload=resource_payload(),
                correlation_id="incident-123",
            )
        )

        self.assertEqual(self.repository.get("local", stored.identity.uid), stored)
        self.assertEqual(len(self.events.events), 1)
        event = self.events.events[0]
        self.assertEqual(event.event_type, "io.iip.resource.observed.v1")
        self.assertEqual(event.tenant_id, "local")
        self.assertEqual(event.correlation_id, "incident-123")
        self.assertEqual(event.subject, stored.identity.uid)
        self.assertEqual(event.data["resourceUid"], stored.identity.uid)
        self.assertEqual(event.data["observation"], stored.observation.to_dict())

    def test_actor_cannot_write_to_another_tenant(self) -> None:
        with self.assertRaises(InvalidInputError):
            self.service.execute(
                IngestResourceCommand(
                    actor=ActorContext("developer", "another-tenant"),
                    payload=resource_payload(),
                )
            )
        self.assertEqual(list(self.repository.list("local")), [])
        self.assertEqual(self.events.events, ())

    def test_anonymous_actor_is_denied(self) -> None:
        with self.assertRaises(AuthorizationError):
            self.service.execute(
                IngestResourceCommand(
                    actor=ActorContext("anonymous", "local"),
                    payload=resource_payload(),
                )
            )

    def test_repository_never_crosses_tenant_boundary(self) -> None:
        stored = self.service.execute(
            IngestResourceCommand(
                actor=ActorContext("developer", "local"),
                payload=resource_payload(),
            )
        )
        self.assertIsNone(self.repository.get("another-tenant", stored.identity.uid))
        self.assertEqual(list(self.repository.list("another-tenant")), [])

    def test_duplicate_retry_is_idempotent(self) -> None:
        command = IngestResourceCommand(
            actor=ActorContext("developer", "local"),
            payload=resource_payload(),
        )

        first = self.service.execute(command)
        retry = self.service.execute(command)

        self.assertEqual(retry, first)
        self.assertEqual(len(self.events.events), 1)
        self.assertEqual(len(tuple(self.repository.history("local", first.identity.uid))), 1)

    def test_checkpoint_advances_only_at_an_explicit_safe_boundary(self) -> None:
        command = IngestResourceCommand(
            actor=ActorContext("developer", "local"),
            payload=resource_payload(),
            checkpoint_ready=True,
        )

        stored = self.service.execute(command)
        checkpoint = self.repository.get_checkpoint("local", "kubernetes-local")

        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint.sequence, stored.observation.sequence)
        self.assertEqual(checkpoint.checkpoint, stored.observation.checkpoint)

    def test_ordinary_ingestion_does_not_infer_checkpoint_completion(self) -> None:
        self.service.execute(
            IngestResourceCommand(
                actor=ActorContext("developer", "local"),
                payload=resource_payload(),
            )
        )

        self.assertIsNone(
            self.repository.get_checkpoint("local", "kubernetes-local")
        )

    def test_outbox_claims_and_acknowledgements_are_tenant_scoped(self) -> None:
        self.service.execute(
            IngestResourceCommand(
                actor=ActorContext("developer", "local"),
                payload=resource_payload(),
            )
        )

        self.assertEqual(
            tuple(self.repository.claim_outbox("another-tenant", "worker-1")),
            (),
        )
        claimed = tuple(self.repository.claim_outbox("local", "worker-1"))
        self.assertEqual(len(claimed), 1)
        self.assertFalse(
            self.repository.acknowledge_outbox(
                "another-tenant", "worker-1", claimed[0].message_id
            )
        )
        self.assertTrue(
            self.repository.acknowledge_outbox(
                "local", "worker-1", claimed[0].message_id
            )
        )

    def test_stale_sequence_cannot_replace_latest_projection(self) -> None:
        latest = resource_payload()
        self.service.execute(
            IngestResourceCommand(ActorContext("developer", "local"), latest)
        )
        stale = resource_payload()
        stale["metadata"]["observation"]["sequence"] = 41
        stale["spec"]["attributes"]["availableReplicas"] = 1

        with self.assertRaises(StaleObservationError) as raised:
            self.service.execute(
                IngestResourceCommand(ActorContext("developer", "local"), stale)
            )

        self.assertEqual(str(raised.exception), "resource.observation.stale")
        stored = self.repository.get(
            "local", Resource.from_dict(latest).identity.uid
        )
        self.assertIsNotNone(stored)
        self.assertEqual(stored.attributes["availableReplicas"], 2)
        self.assertEqual(len(self.events.events), 1)
        history = tuple(self.repository.history("local", stored.identity.uid))
        self.assertEqual(
            [item.disposition.value for item in history],
            ["accepted", "stale"],
        )

    def test_same_sequence_with_different_content_is_conflict(self) -> None:
        current = resource_payload()
        self.service.execute(
            IngestResourceCommand(ActorContext("developer", "local"), current)
        )
        conflict = resource_payload()
        conflict["spec"]["attributes"]["availableReplicas"] = 3

        with self.assertRaises(ObservationConflictError) as raised:
            self.service.execute(
                IngestResourceCommand(ActorContext("developer", "local"), conflict)
            )

        self.assertEqual(str(raised.exception), "resource.observation.conflict")
        self.assertEqual(len(self.events.events), 1)

    def test_higher_sequence_replaces_projection(self) -> None:
        self.service.execute(
            IngestResourceCommand(
                ActorContext("developer", "local"), resource_payload()
            )
        )
        newer = resource_payload()
        newer["metadata"]["observation"]["sequence"] = 43
        newer["metadata"]["observation"]["resourceVersion"] = "398713"
        newer["spec"]["attributes"]["availableReplicas"] = 3

        stored = self.service.execute(
            IngestResourceCommand(ActorContext("developer", "local"), newer)
        )

        self.assertEqual(stored.observation.sequence, 43)
        self.assertEqual(stored.attributes["availableReplicas"], 3)
        self.assertEqual(len(self.events.events), 2)

    def test_new_stream_requires_reconciliation(self) -> None:
        self.service.execute(
            IngestResourceCommand(
                ActorContext("developer", "local"), resource_payload()
            )
        )
        next_stream = resource_payload()
        next_stream["metadata"]["observation"]["streamId"] = (
            "obs_1234567890abcdef1234567890abcdef"
        )
        next_stream["metadata"]["observation"]["sequence"] = 0

        with self.assertRaises(ObservationConflictError):
            self.service.execute(
                IngestResourceCommand(
                    ActorContext("developer", "local"), next_stream
                )
            )

        next_stream["metadata"]["observation"]["mode"] = "reconciliation"
        next_stream["metadata"]["observation"]["snapshotId"] = (
            "snap_1234567890abcdef1234567890abcdef"
        )
        stored = self.service.execute(
            IngestResourceCommand(ActorContext("developer", "local"), next_stream)
        )
        self.assertEqual(
            stored.observation.stream_id,
            next_stream["metadata"]["observation"]["streamId"],
        )

    def test_tombstone_remains_as_latest_projection(self) -> None:
        self.service.execute(
            IngestResourceCommand(
                ActorContext("developer", "local"), resource_payload()
            )
        )
        tombstone = resource_payload()
        tombstone["metadata"]["observation"]["sequence"] = 43
        tombstone["spec"]["attributes"] = {}
        tombstone["spec"]["relationships"] = []
        tombstone["status"] = {"health": "unknown", "lifecycle": "deleted"}

        stored = self.service.execute(
            IngestResourceCommand(ActorContext("developer", "local"), tombstone)
        )

        self.assertEqual(stored.lifecycle, "deleted")
        self.assertEqual(stored.attributes, {})
        self.assertEqual(stored.relationships, ())

    def test_different_source_cannot_take_over_projection(self) -> None:
        self.service.execute(
            IngestResourceCommand(
                ActorContext("developer", "local"), resource_payload()
            )
        )
        takeover = resource_payload()
        takeover["metadata"]["observation"].update(
            {
                "sourceId": "kubernetes-other",
                "streamId": "obs_1234567890abcdef1234567890abcdef",
                "sequence": 0,
                "mode": "reconciliation",
                "snapshotId": "snap_1234567890abcdef1234567890abcdef",
            }
        )

        with self.assertRaises(ObservationConflictError):
            self.service.execute(
                IngestResourceCommand(ActorContext("developer", "local"), takeover)
            )

    def test_legacy_unsequenced_observation_remains_compatible(self) -> None:
        legacy = resource_payload()
        del legacy["metadata"]["observation"]

        stored = self.service.execute(
            IngestResourceCommand(ActorContext("developer", "local"), legacy)
        )

        self.assertIsNone(stored.observation)

    def test_unsequenced_write_cannot_downgrade_sequenced_projection(self) -> None:
        self.service.execute(
            IngestResourceCommand(
                ActorContext("developer", "local"), resource_payload()
            )
        )
        legacy = resource_payload()
        del legacy["metadata"]["observation"]
        legacy["metadata"]["observedAt"] = "2026-08-14T10:31:00Z"

        with self.assertRaises(StaleObservationError):
            self.service.execute(
                IngestResourceCommand(ActorContext("developer", "local"), legacy)
            )


class PythonSdkBoundaryTests(unittest.TestCase):
    def test_sdk_model_accepts_public_resource_without_server_imports(self) -> None:
        model = ResourceObservation.from_dict(resource_payload())
        self.assertEqual(model.to_dict()["kind"], "Resource")

    def test_sdk_exposes_observation_cursor_without_server_imports(self) -> None:
        payload = resource_payload()["metadata"]["observation"]
        cursor = ResourceObservationCursor.from_dict(payload)

        self.assertEqual(cursor.sequence, 42)
        self.assertEqual(cursor.to_dict(), payload)

    def test_sdk_cursor_rejects_unbounded_reconciliation(self) -> None:
        payload = resource_payload()["metadata"]["observation"]
        payload["mode"] = "reconciliation"

        with self.assertRaisesRegex(ValueError, "requires snapshotId"):
            ResourceObservationCursor.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
