from __future__ import annotations

import json
import unittest
from pathlib import Path

from iip.adapters.memory import (
    AllowTenantPolicy,
    InMemoryEventPublisher,
    InMemoryResourceRepository,
)
from iip.application.ingest_resource import (
    AuthorizationError,
    IngestResourceCommand,
    InvalidInputError,
    ResourceIngestionService,
)
from iip.application.ports import ActorContext
from iip.domain.models import Resource, ResourceIdentity
from infra_intelligence_sdk import ResourceObservation


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
        self.assertEqual(serialized["status"]["health"], "degraded")


class ResourceIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryResourceRepository()
        self.events = InMemoryEventPublisher()
        self.service = ResourceIngestionService(
            self.repository,
            self.events,
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

    def test_actor_cannot_write_to_another_tenant(self) -> None:
        with self.assertRaises(InvalidInputError):
            self.service.execute(
                IngestResourceCommand(
                    actor=ActorContext("developer", "another-tenant"),
                    payload=resource_payload(),
                )
            )
        self.assertEqual(list(self.repository.list("local")), [])
        self.assertEqual(self.events.events, [])

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


class PythonSdkBoundaryTests(unittest.TestCase):
    def test_sdk_model_accepts_public_resource_without_server_imports(self) -> None:
        model = ResourceObservation.from_dict(resource_payload())
        self.assertEqual(model.to_dict()["kind"], "Resource")


if __name__ == "__main__":
    unittest.main()

