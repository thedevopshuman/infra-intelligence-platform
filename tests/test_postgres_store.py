from __future__ import annotations

import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

try:
    import psycopg

    from iip.adapters.postgres import PostgresOperationalStore, PostgresResourceStore
except ModuleNotFoundError:
    psycopg = None
    PostgresOperationalStore = None
    PostgresResourceStore = None

from iip.adapters.evidence import (
    ResourceStateEvidenceProvider,
    StructuredTextRedactor,
    SystemClock,
    UuidEvidenceIdGenerator,
)
from iip.adapters.memory import AllowTenantPolicy
from iip.application.collect_evidence import CollectEvidenceCommand, EvidenceCollectionService
from iip.application.ingest_resource import (
    IngestResourceCommand,
    ResourceIngestionService,
    StaleObservationError,
)
from iip.application.ports import ActorContext, PersistenceError
from iip.domain.models import Resource


ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ.get("IIP_TEST_DATABASE_URL")


def resource_payload() -> dict:
    return json.loads((ROOT / "contracts/examples/resource.json").read_text(encoding="utf-8"))


@unittest.skipUnless(
    PostgresResourceStore is not None,
    "psycopg is required for PostgreSQL adapter tests",
)
class PostgresAdapterBoundaryTests(unittest.TestCase):
    def test_provider_error_text_is_replaced_by_a_stable_code(self) -> None:
        assert PostgresResourceStore is not None and psycopg is not None
        store = PostgresResourceStore("postgresql://unused")

        with patch.object(
            store,
            "_connect",
            side_effect=psycopg.OperationalError("provider detail must not escape"),
        ):
            with self.assertRaisesRegex(PersistenceError, "^storage.unavailable$") as raised:
                store.get("local", "res_00000000000000000000000000000000")

        self.assertIsNone(raised.exception.__cause__)


@unittest.skipUnless(
    DATABASE_URL and PostgresResourceStore is not None,
    "IIP_TEST_DATABASE_URL and psycopg are required for PostgreSQL integration tests",
)
class PostgresResourceStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        assert DATABASE_URL is not None and PostgresResourceStore is not None
        cls.store = PostgresResourceStore(DATABASE_URL)
        cls.store.migrate()

    def setUp(self) -> None:
        assert DATABASE_URL is not None and psycopg is not None
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                TRUNCATE iip.resource_relationships,
                         iip.source_checkpoints, iip.event_outbox,
                         iip.event_log, iip.resource_observations,
                         iip.resource_projections
                RESTART IDENTITY CASCADE
                """
            )
        self.service = ResourceIngestionService(self.store, AllowTenantPolicy())

    def test_projection_history_event_outbox_and_checkpoint_commit_together(self) -> None:
        command = IngestResourceCommand(
            actor=ActorContext("collector", "local"),
            payload=resource_payload(),
            correlation_id="collection-run-1",
            checkpoint_ready=True,
        )

        stored = self.service.execute(command)
        retry = self.service.execute(command)

        self.assertEqual(retry, stored)
        self.assertEqual(self.store.get("local", stored.identity.uid), stored)
        history = tuple(self.store.history("local", stored.identity.uid))
        self.assertEqual([item.disposition.value for item in history], ["accepted"])
        events = tuple(self.store.list_events("local"))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event.correlation_id, "collection-run-1")
        checkpoint = self.store.get_checkpoint("local", "kubernetes-local")
        self.assertIsNotNone(checkpoint)
        self.assertEqual(checkpoint.sequence, 42)
        self.assertEqual(len(tuple(self.store.claim_outbox("local", "worker-1"))), 1)

    def test_stale_observation_is_audited_without_side_effects(self) -> None:
        accepted = self.service.execute(
            IngestResourceCommand(ActorContext("collector", "local"), resource_payload())
        )
        stale = resource_payload()
        stale["metadata"]["observation"]["sequence"] = 41
        stale["spec"]["attributes"]["availableReplicas"] = 1

        with self.assertRaises(StaleObservationError):
            self.service.execute(
                IngestResourceCommand(
                    ActorContext("collector", "local"),
                    stale,
                    checkpoint_ready=True,
                )
            )

        self.assertEqual(self.store.get("local", accepted.identity.uid), accepted)
        history = tuple(self.store.history("local", accepted.identity.uid))
        self.assertEqual(
            [item.disposition.value for item in history],
            ["accepted", "stale"],
        )
        self.assertEqual(len(tuple(self.store.list_events("local"))), 1)
        self.assertIsNone(self.store.get_checkpoint("local", "kubernetes-local"))

    def test_event_replay_and_outbox_leases_are_tenant_scoped(self) -> None:
        first = self.service.execute(
            IngestResourceCommand(ActorContext("collector", "local"), resource_payload())
        )
        second_payload = resource_payload()
        second_payload["spec"]["externalId"] = "cluster-local/default/worker"
        second_payload["spec"]["displayName"] = "worker"
        second_payload["metadata"]["observation"]["sequence"] = 43
        second = self.service.execute(
            IngestResourceCommand(ActorContext("collector", "local"), second_payload)
        )

        first_page = tuple(self.store.list_events("local", limit=1))
        second_page = tuple(
            self.store.list_events("local", after_offset=first_page[0].offset, limit=1)
        )
        self.assertEqual(first_page[0].event.subject, first.identity.uid)
        self.assertEqual(second_page[0].event.subject, second.identity.uid)
        self.assertEqual(tuple(self.store.list_events("another-tenant")), ())
        self.assertEqual(
            tuple(self.store.claim_outbox("another-tenant", "worker-1")),
            (),
        )

        claimed = tuple(self.store.claim_outbox("local", "worker-1", limit=1))
        self.assertEqual(claimed[0].attempts, 1)
        self.assertFalse(
            self.store.release_outbox(
                "another-tenant",
                "worker-1",
                claimed[0].message_id,
                "transport.unavailable",
            )
        )
        self.assertTrue(
            self.store.release_outbox(
                "local",
                "worker-1",
                claimed[0].message_id,
                "transport.unavailable",
            )
        )
        retried = tuple(self.store.claim_outbox("local", "worker-2", limit=1))
        self.assertEqual(retried[0].message_id, claimed[0].message_id)
        self.assertEqual(retried[0].attempts, 2)
        self.assertTrue(
            self.store.acknowledge_outbox(
                "local", "worker-2", retried[0].message_id
            )
        )

    def test_relationship_index_and_timeline_pages_track_latest_projection(self) -> None:
        target_payload = resource_payload()
        target_payload["spec"]["type"] = "core/configmap"
        target_payload["spec"]["externalId"] = "cluster-local/default/settings"
        target_payload["spec"]["displayName"] = "settings"
        target_payload["spec"]["relationships"] = []
        target_payload["metadata"]["observation"]["sequence"] = 41
        target = self.service.execute(
            IngestResourceCommand(ActorContext("collector", "local"), target_payload)
        )
        source_payload = resource_payload()
        source_payload["spec"]["relationships"] = [
            {"type": "depends_on", "target": target.identity.uid}
        ]
        source = self.service.execute(
            IngestResourceCommand(ActorContext("collector", "local"), source_payload)
        )

        outgoing = tuple(
            self.store.relationships("local", source.identity.uid, direction="outgoing")
        )
        incoming = tuple(
            self.store.relationships("local", target.identity.uid, direction="incoming")
        )
        resolved = tuple(
            self.store.get_many("local", (source.identity.uid, target.identity.uid))
        )

        self.assertEqual(outgoing, incoming)
        self.assertEqual(outgoing[0].relationship_type, "depends_on")
        self.assertEqual(
            {item.identity.uid for item in resolved},
            {source.identity.uid, target.identity.uid},
        )
        self.assertEqual(tuple(self.store.relationships("another-tenant", source.identity.uid)), ())

        replacement = resource_payload()
        replacement["metadata"]["observation"]["sequence"] = 43
        replacement["spec"]["relationships"] = []
        self.service.execute(
            IngestResourceCommand(ActorContext("collector", "local"), replacement)
        )

        self.assertEqual(
            tuple(self.store.relationships("local", source.identity.uid, direction="outgoing")),
            (),
        )
        first = tuple(self.store.history("local", source.identity.uid, limit=1))
        second = tuple(
            self.store.history(
                "local",
                source.identity.uid,
                after_offset=first[0].offset,
                limit=1,
            )
        )
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)
        self.assertGreater(second[0].offset, first[0].offset)

    def test_concurrent_duplicate_delivery_emits_one_event(self) -> None:
        barrier = Barrier(2)

        def ingest() -> Resource:
            barrier.wait()
            return self.service.execute(
                IngestResourceCommand(
                    ActorContext("collector", "local"),
                    resource_payload(),
                )
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(lambda _: ingest(), range(2)))

        self.assertEqual(results[0], results[1])
        self.assertEqual(len(tuple(self.store.list_events("local"))), 1)
        self.assertEqual(
            len(tuple(self.store.history("local", results[0].identity.uid))),
            1,
        )

    def test_outbox_failure_rolls_back_every_accepted_observation_effect(self) -> None:
        assert DATABASE_URL is not None and psycopg is not None
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                CREATE FUNCTION iip.reject_outbox_insert()
                RETURNS trigger
                LANGUAGE plpgsql
                AS $$
                BEGIN
                    RAISE EXCEPTION 'injected outbox failure';
                END;
                $$
                """
            )
            connection.execute(
                """
                CREATE TRIGGER reject_outbox_insert
                BEFORE INSERT ON iip.event_outbox
                FOR EACH ROW EXECUTE FUNCTION iip.reject_outbox_insert()
                """
            )

        resource = Resource.from_dict(resource_payload())
        try:
            with self.assertRaisesRegex(PersistenceError, "storage.unavailable"):
                self.service.execute(
                    IngestResourceCommand(
                        ActorContext("collector", "local"),
                        resource_payload(),
                        checkpoint_ready=True,
                    )
                )
        finally:
            with psycopg.connect(DATABASE_URL) as connection:
                connection.execute(
                    "DROP TRIGGER reject_outbox_insert ON iip.event_outbox"
                )
                connection.execute("DROP FUNCTION iip.reject_outbox_insert()")

        self.assertIsNone(self.store.get("local", resource.identity.uid))
        self.assertEqual(
            tuple(self.store.relationships("local", resource.identity.uid)),
            (),
        )
        self.assertEqual(tuple(self.store.history("local", resource.identity.uid)), ())
        self.assertEqual(tuple(self.store.list_events("local")), ())
        self.assertIsNone(self.store.get_checkpoint("local", "kubernetes-local"))


@unittest.skipUnless(
    DATABASE_URL
    and PostgresResourceStore is not None
    and PostgresOperationalStore is not None,
    "IIP_TEST_DATABASE_URL and psycopg are required for PostgreSQL integration tests",
)
class PostgresOperationalStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        assert DATABASE_URL is not None
        cls.resources = PostgresResourceStore(DATABASE_URL)
        cls.resources.migrate()
        cls.operations = PostgresOperationalStore(DATABASE_URL)

    def setUp(self) -> None:
        assert DATABASE_URL is not None and psycopg is not None
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                TRUNCATE iip.audit_records, iip.plugin_sessions,
                         iip.action_results, iip.action_approvals,
                         iip.action_proposals, iip.investigations,
                         iip.evidence_artifacts, iip.resource_relationships,
                         iip.source_checkpoints, iip.event_outbox,
                         iip.event_log, iip.resource_observations,
                         iip.resource_projections
                RESTART IDENTITY CASCADE
                """
            )

    def test_evidence_metadata_and_artifact_are_durable_and_tenant_scoped(self) -> None:
        actor = ActorContext("collector", "local")
        resource = ResourceIngestionService(
            self.resources, AllowTenantPolicy()
        ).execute(IngestResourceCommand(actor, resource_payload()))
        service = EvidenceCollectionService(
            self.resources,
            {"resource-state": ResourceStateEvidenceProvider(self.resources)},
            self.operations,
            StructuredTextRedactor(),
            AllowTenantPolicy(),
            UuidEvidenceIdGenerator(),
            SystemClock(),
        )
        evidence = service.execute(
            CollectEvidenceCommand(
                actor=actor,
                provider="resource-state",
                integration_id="platform-resource-state",
                evidence_type="kubernetes.resource-status",
                resource_uids=(resource.identity.uid,),
                locator="resource://current",
                deadline="2099-08-14T13:30:00Z",
            )
        )
        evidence_id = evidence["metadata"]["id"]

        reconnected = PostgresOperationalStore(DATABASE_URL)
        self.assertEqual(reconnected.get(actor, evidence_id), evidence)
        self.assertTrue(reconnected.read_artifact(actor, evidence_id))
        self.assertEqual(len(tuple(reconnected.list(actor))), 1)
        self.assertIsNone(
            reconnected.get(ActorContext("other", "another-tenant"), evidence_id)
        )


if __name__ == "__main__":
    unittest.main()
