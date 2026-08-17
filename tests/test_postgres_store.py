from __future__ import annotations

import copy
import hashlib
import json
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

try:
    import psycopg

    from iip.adapters.postgres import (
        PostgresOperationalStore,
        PostgresReadinessProbe,
        PostgresResourceStore,
    )
except ModuleNotFoundError:
    psycopg = None
    PostgresOperationalStore = None
    PostgresReadinessProbe = None
    PostgresResourceStore = None

from iip.adapters.evidence import (
    ResourceStateEvidenceProvider,
    StructuredTextRedactor,
    SystemClock,
    UuidEvidenceIdGenerator,
)
from iip.adapters.memory import AllowTenantPolicy
from iip.application.action_reconciliation import ActionReconciliationService
from iip.application.collect_evidence import CollectEvidenceCommand, EvidenceCollectionService
from iip.application.ingest_collection import (
    IngestCollectionCommand,
    ResourceCollectionIngestionService,
)
from iip.application.ingest_resource import (
    IngestResourceCommand,
    ResourceIngestionService,
    StaleObservationError,
)
from iip.application.investigate import canonical_digest
from iip.application.plugin_invocations import (
    CancelPluginInvocationCommand,
    PluginInvocationLifecycleService,
    ReconcilePluginInvocationCommand,
)
from iip.application.ports import ActorContext, PersistenceError, SourceCheckpoint
from iip.application.investigation_dispatch import InvestigationDispatchService
from iip.application.rebuild_projections import (
    ProjectionRebuildService,
    RebuildProjectionsCommand,
)
from iip.domain.models import Resource


ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ.get("IIP_TEST_DATABASE_URL")


def resource_payload() -> dict:
    return json.loads((ROOT / "contracts/examples/resource.json").read_text(encoding="utf-8"))


def reconciliation_pair(
    *,
    request_id: str,
    snapshot_id: str,
    start_sequence: int,
    observations: list[dict],
    checkpoint: str,
    provider_cursors: dict[str, str] | None = None,
) -> tuple[dict, dict]:
    request = json.loads(
        (ROOT / "contracts/examples/resource-collection-request.json").read_text(
            encoding="utf-8"
        )
    )
    result = json.loads(
        (ROOT / "contracts/examples/resource-collection-result.json").read_text(
            encoding="utf-8"
        )
    )
    request["metadata"].update(
        {"requestId": request_id, "actorId": "collector"}
    )
    request["spec"].update(
        {
            "mode": "reconciliation",
            "snapshotId": snapshot_id,
            "startSequence": start_sequence,
        }
    )
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
        "scopeDigest": result["spec"]["completion"]["scopeDigest"],
    }
    if provider_cursors is not None:
        result["spec"]["completion"]["providerCursors"] = provider_cursors
    return request, result


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
                         iip.source_reconciliations, iip.source_checkpoints,
                         iip.event_outbox,
                         iip.event_log, iip.resource_observations,
                         iip.resource_projections
                RESTART IDENTITY CASCADE
                """
            )
        self.service = ResourceIngestionService(self.store, AllowTenantPolicy())

    def test_readiness_requires_the_applied_schema(self) -> None:
        assert DATABASE_URL is not None and PostgresReadinessProbe is not None
        PostgresReadinessProbe(DATABASE_URL).check()

    def test_reconciliation_membership_and_tombstone_are_durable(self) -> None:
        actor = ActorContext("collector", "local")
        collection = ResourceCollectionIngestionService(
            self.service,
            self.store,
            self.store,
            self.store,
            SystemClock(),
        )
        public_result = json.loads(
            (ROOT / "contracts/examples/resource-collection-result.json").read_text(
                encoding="utf-8"
            )
        )
        first_request, first_result = reconciliation_pair(
            request_id="col_11111111111111111111111111111111",
            snapshot_id="snap_11111111111111111111111111111111",
            start_sequence=42,
            observations=public_result["spec"]["observations"],
            checkpoint="kubernetes:cluster-local:snapshot:first",
            provider_cursors={"/api/v1/namespaces/iip-demo/configmaps": "5000"},
        )
        active = collection.execute(
            IngestCollectionCommand(actor, first_request, first_result)
        )[0]
        second_request, second_result = reconciliation_pair(
            request_id="col_22222222222222222222222222222222",
            snapshot_id="snap_22222222222222222222222222222222",
            start_sequence=43,
            observations=[],
            checkpoint="kubernetes:cluster-local:snapshot:second",
            provider_cursors={"/api/v1/namespaces/iip-demo/configmaps": "5001"},
        )

        tombstone = collection.execute(
            IngestCollectionCommand(actor, second_request, second_result)
        )[0]
        replay = collection.execute(
            IngestCollectionCommand(actor, second_request, second_result)
        )[0]

        self.assertEqual(tombstone, replay)
        self.assertEqual(tombstone.lifecycle, "deleted")
        reconnected = PostgresResourceStore(DATABASE_URL)
        self.assertEqual(
            reconnected.get("local", active.identity.uid).lifecycle,
            "deleted",
        )
        snapshot = reconnected.get_reconciliation("local", "kubernetes-local")
        self.assertEqual(snapshot.tombstoned_uids, (active.identity.uid,))
        self.assertEqual(
            reconnected.get_checkpoint("local", "kubernetes-local").sequence,
            43,
        )
        self.assertEqual(
            dict(
                reconnected.get_checkpoint(
                    "local", "kubernetes-local"
                ).provider_cursors
            ),
            {"/api/v1/namespaces/iip-demo/configmaps": "5001"},
        )
        self.assertEqual(len(tuple(reconnected.list_events("local"))), 2)

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

    def test_ingestion_telemetry_state_is_source_and_tenant_scoped(self) -> None:
        stored = self.service.execute(
            IngestResourceCommand(
                actor=ActorContext("collector", "local"),
                payload=resource_payload(),
                checkpoint_ready=True,
            )
        )

        state = self.store.get_source_ingestion_state(
            "local", "kubernetes-local"
        )

        self.assertIsNotNone(state)
        self.assertEqual(state.tenant_id, "local")
        self.assertEqual(state.source_id, "kubernetes-local")
        self.assertEqual(state.stream_id, stored.observation.stream_id)
        self.assertEqual(state.checkpoint_sequence, 42)
        self.assertEqual(state.latest_observed_at, stored.observed_at)
        self.assertEqual(state.accepted_observation_count, 1)
        self.assertEqual(state.pending_event_count, 1)
        self.assertIsNotNone(state.oldest_pending_event_recorded_at)
        self.assertIsNone(
            self.store.get_source_ingestion_state(
                "another-tenant", "kubernetes-local"
            )
        )

        original_committed_at = state.checkpoint_committed_at
        checkpoint = self.store.get_checkpoint("local", "kubernetes-local")
        self.store.commit_checkpoint(
            SourceCheckpoint(
                tenant_id=checkpoint.tenant_id,
                source_id=checkpoint.source_id,
                stream_id=checkpoint.stream_id,
                sequence=checkpoint.sequence,
                checkpoint=checkpoint.checkpoint,
                committed_at="2099-01-01T00:00:00Z",
                provider_cursors=checkpoint.provider_cursors,
            ),
            mode="incremental",
        )
        self.assertEqual(
            self.store.get_source_ingestion_state(
                "local", "kubernetes-local"
            ).checkpoint_committed_at,
            original_committed_at,
        )

        message = tuple(self.store.claim_outbox("local", "telemetry-worker"))[0]
        self.assertTrue(
            self.store.acknowledge_outbox(
                "local", "telemetry-worker", message.message_id
            )
        )
        delivered = self.store.get_source_ingestion_state(
            "local", "kubernetes-local"
        )
        self.assertEqual(delivered.pending_event_count, 0)
        self.assertIsNone(delivered.oldest_pending_event_recorded_at)

    def test_projection_drift_is_detected_and_rebuilt_from_accepted_history(self) -> None:
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
        maintenance = ProjectionRebuildService(self.store, AllowTenantPolicy())
        command = RebuildProjectionsCommand(
            ActorContext("operator", "local", ("platform-admin",)),
            dry_run=True,
            max_resources=100,
        )

        healthy = maintenance.execute(command)
        self.assertFalse(healthy.drift_detected)
        self.assertFalse(healthy.rebuild_performed)
        self.assertEqual(healthy.resource_count, 2)
        self.assertEqual(healthy.relationship_count, 1)
        event_count = len(tuple(self.store.list_events("local")))
        observation_count = len(tuple(self.store.history("local", source.identity.uid)))

        assert DATABASE_URL is not None and psycopg is not None
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                UPDATE iip.resource_projections
                SET document = jsonb_set(
                    document,
                    '{status,health}',
                    to_jsonb('unhealthy'::text)
                )
                WHERE tenant_id = %s AND resource_uid = %s
                """,
                ("local", source.identity.uid),
            )
            connection.execute(
                "DELETE FROM iip.resource_relationships WHERE tenant_id = %s",
                ("local",),
            )

        dry_run = maintenance.execute(command)
        self.assertTrue(dry_run.drift_detected)
        self.assertFalse(dry_run.rebuild_performed)
        self.assertEqual(dry_run.after_digest, dry_run.before_digest)
        self.assertNotEqual(self.store.get("local", source.identity.uid), source)

        rebuilt = maintenance.execute(
            RebuildProjectionsCommand(
                command.actor,
                dry_run=False,
                max_resources=100,
            )
        )
        self.assertTrue(rebuilt.drift_detected)
        self.assertTrue(rebuilt.rebuild_performed)
        self.assertEqual(rebuilt.after_digest, rebuilt.expected_digest)
        self.assertEqual(self.store.get("local", source.identity.uid), source)
        self.assertEqual(
            len(tuple(self.store.relationships("local", source.identity.uid))),
            1,
        )
        self.assertEqual(len(tuple(self.store.list_events("local"))), event_count)
        self.assertEqual(
            len(tuple(self.store.history("local", source.identity.uid))),
            observation_count,
        )

        verified = maintenance.execute(command)
        self.assertFalse(verified.drift_detected)

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
        window_end = datetime.now(timezone.utc) + timedelta(seconds=3)
        cutoff = window_end - timedelta(seconds=1)
        start = window_end - timedelta(minutes=5)
        slo = self.store.get_event_delivery_slo_state(
            "local",
            window_start=start.isoformat().replace("+00:00", "Z"),
            window_end=window_end.isoformat().replace("+00:00", "Z"),
            maturity_cutoff=cutoff.isoformat().replace("+00:00", "Z"),
            latency_objective_seconds=1,
        )
        self.assertEqual(slo.created_events, 2)
        self.assertEqual(slo.eligible_events, 2)
        self.assertEqual(slo.within_objective_events, 1)
        self.assertEqual(slo.late_delivered_events, 0)
        self.assertEqual(slo.undelivered_events, 1)
        self.assertEqual(slo.quarantined_events, 0)
        other = self.store.get_event_delivery_slo_state(
            "another-tenant",
            window_start=slo.window_start,
            window_end=slo.window_end,
            maturity_cutoff=slo.maturity_cutoff,
            latency_objective_seconds=1,
        )
        self.assertEqual(other.created_events, 0)

    def test_outbox_quarantine_is_terminal_tenant_scoped_and_value_minimized(self) -> None:
        stored = self.service.execute(
            IngestResourceCommand(
                ActorContext("collector", "local"),
                resource_payload(),
                checkpoint_ready=True,
            )
        )
        message = tuple(self.store.claim_outbox("local", "worker-1"))[0]

        self.assertFalse(
            self.store.quarantine_outbox(
                "another-tenant",
                "worker-1",
                message.message_id,
                "event.publisher.unavailable",
            )
        )
        self.assertTrue(
            self.store.quarantine_outbox(
                "local",
                "worker-1",
                message.message_id,
                "event.publisher.unavailable",
            )
        )
        self.assertEqual(tuple(self.store.claim_outbox("local", "worker-2")), ())

        state = self.store.get_event_delivery_state("local")
        self.assertEqual(state.pending_events, 0)
        self.assertEqual(state.quarantined_events, 1)
        self.assertEqual(state.quarantined[0].subject, stored.identity.uid)
        self.assertFalse(hasattr(state.quarantined[0], "event"))
        self.assertEqual(state.quarantined[0].attempts, 1)
        self.assertEqual(
            state.quarantined[0].last_error_code,
            "event.publisher.unavailable",
        )
        source = self.store.get_source_ingestion_state(
            "local", "kubernetes-local"
        )
        self.assertEqual(source.pending_event_count, 0)
        self.assertIsNone(source.oldest_pending_event_recorded_at)
        self.assertEqual(
            self.store.get_event_delivery_state("another-tenant").quarantined_events,
            0,
        )
        quarantine = self.store.get_quarantined_outbox(
            "local",
            message.message_id,
        )
        self.assertIsNotNone(quarantine)
        assert quarantine is not None
        self.assertEqual(quarantine.event_id, message.event.event_id)
        self.assertIsNone(
            self.store.get_quarantined_outbox(
                "another-tenant",
                message.message_id,
            )
        )
        self.assertFalse(
            self.store.requeue_quarantined_outbox(
                "local",
                message.message_id,
                expected_event_id="stale-event-generation",
                expected_quarantined_at=quarantine.quarantined_at,
                expected_attempts=quarantine.attempts,
            )
        )
        self.assertTrue(
            self.store.requeue_quarantined_outbox(
                "local",
                message.message_id,
                expected_event_id=quarantine.event_id,
                expected_quarantined_at=quarantine.quarantined_at,
                expected_attempts=quarantine.attempts,
            )
        )
        self.assertIsNone(
            self.store.get_quarantined_outbox("local", message.message_id)
        )
        replayed = tuple(self.store.claim_outbox("local", "worker-2"))
        self.assertEqual(len(replayed), 1)
        self.assertEqual(replayed[0].attempts, 1)

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
                TRUNCATE iip.audit_records, iip.plugin_invocations,
                         iip.plugin_sessions,
                         iip.action_results, iip.action_executions,
                         iip.action_approvals,
                         iip.action_proposals, iip.investigation_jobs,
                         iip.investigations,
                         iip.evidence_artifacts, iip.resource_relationships,
                         iip.source_reconciliations, iip.source_checkpoints,
                         iip.event_outbox,
                         iip.event_log, iip.resource_observations,
                         iip.resource_projections
                RESTART IDENTITY CASCADE
                """
            )

    def test_plugin_claim_survives_reconnect_and_replays_only_terminal_result(self) -> None:
        actor = ActorContext("plugin-host", "local")
        session = json.loads(
            (ROOT / "contracts/examples/plugin-session.json").read_text()
        )
        invocation = json.loads(
            (ROOT / "contracts/examples/plugin-invocation.json").read_text()
        )
        result = json.loads(
            (ROOT / "contracts/examples/plugin-invocation-result.json").read_text()
        )
        digest = canonical_digest(invocation)
        self.operations.commit_plugin_session(actor, session)

        claimed = self.operations.claim_plugin_invocation(
            actor,
            session,
            invocation,
            digest,
            "2026-08-14T12:44:31Z",
        )
        reconnected = PostgresOperationalStore(DATABASE_URL)
        ambiguous = reconnected.claim_plugin_invocation(
            actor,
            session,
            invocation,
            digest,
            "2026-08-14T12:44:32Z",
        )

        self.assertEqual(claimed.state, "claimed")
        self.assertEqual(ambiguous.state, "in-progress")
        self.assertIsNone(ambiguous.result)

        reconnected.commit_plugin_invocation_result(actor, digest, result)
        replayed = self.operations.claim_plugin_invocation(
            actor,
            session,
            invocation,
            digest,
            "2026-08-14T13:00:00Z",
        )
        self.assertEqual(replayed.state, "completed")
        self.assertEqual(replayed.result, result)
        reconnected.commit_plugin_invocation_result(actor, digest, result)

    def test_plugin_claim_binds_content_tenant_session_and_atomic_request_limit(self) -> None:
        actor = ActorContext("plugin-host", "local")
        session = json.loads(
            (ROOT / "contracts/examples/plugin-session.json").read_text()
        )
        invocation = json.loads(
            (ROOT / "contracts/examples/plugin-invocation.json").read_text()
        )
        self.operations.commit_plugin_session(actor, session)
        digest = canonical_digest(invocation)

        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = tuple(
                pool.map(
                    lambda store: store.claim_plugin_invocation(
                        actor,
                        session,
                        invocation,
                        digest,
                        "2026-08-14T12:44:31Z",
                    ),
                    (
                        self.operations,
                        PostgresOperationalStore(DATABASE_URL),
                    ),
                )
            )
        self.assertEqual(sorted(claim.state for claim in claims), ["claimed", "in-progress"])

        conflicting = copy.deepcopy(invocation)
        conflicting["spec"]["input"]["spec"]["startSequence"] = 43
        with self.assertRaisesRegex(PersistenceError, "storage.conflict"):
            self.operations.claim_plugin_invocation(
                actor,
                session,
                conflicting,
                canonical_digest(conflicting),
                "2026-08-14T12:44:32Z",
            )

        next_invocation = copy.deepcopy(invocation)
        next_invocation["metadata"]["id"] = "pin_88888888888888888888888888888888"
        with self.assertRaisesRegex(PersistenceError, "plugin.request.limit-exceeded"):
            self.operations.claim_plugin_invocation(
                actor,
                session,
                next_invocation,
                canonical_digest(next_invocation),
                "2026-08-14T12:44:33Z",
            )

        cross_tenant = copy.deepcopy(invocation)
        cross_tenant["metadata"]["tenantId"] = "another-tenant"
        with self.assertRaisesRegex(PersistenceError, "storage.input-invalid"):
            self.operations.claim_plugin_invocation(
                actor,
                session,
                cross_tenant,
                canonical_digest(cross_tenant),
                "2026-08-14T12:44:34Z",
            )

    def test_plugin_cancellation_is_durable_audited_and_wins_normal_output(self) -> None:
        actor = ActorContext("plugin-host", "local", ("developer",))
        session = json.loads(
            (ROOT / "contracts/examples/plugin-session.json").read_text()
        )
        invocation = json.loads(
            (ROOT / "contracts/examples/plugin-invocation.json").read_text()
        )
        digest = canonical_digest(invocation)
        self.operations.commit_plugin_session(actor, session)
        self.operations.claim_plugin_invocation(
            actor, session, invocation, digest, "2026-08-14T12:44:31Z"
        )

        class Clock:
            def now(self) -> str:
                return "2026-08-14T12:44:45Z"

        service = PluginInvocationLifecycleService(
            PostgresOperationalStore(DATABASE_URL), AllowTenantPolicy(), Clock()
        )
        cancellation = json.loads(
            (
                ROOT
                / "contracts/examples/plugin-invocation-cancellation-request.json"
            ).read_text()
        )
        pending = service.cancel(
            CancelPluginInvocationCommand(actor, cancellation)
        )
        reconnected = PostgresOperationalStore(DATABASE_URL)
        self.assertEqual(pending["spec"]["state"], "cancellation-requested")
        self.assertTrue(
            reconnected.plugin_invocation_cancellation_requested(
                actor, invocation["metadata"]["id"], digest
            )
        )

        succeeded = json.loads(
            (ROOT / "contracts/examples/plugin-invocation-result.json").read_text()
        )
        with self.assertRaisesRegex(
            PersistenceError, "plugin.request.cancellation-pending"
        ):
            reconnected.commit_plugin_invocation_result(actor, digest, succeeded)

        cancelled = json.loads(
            (
                ROOT / "contracts/examples/plugin-invocation-result-failed.json"
            ).read_text()
        )
        cancelled["metadata"] = copy.deepcopy(succeeded["metadata"])
        cancelled["spec"]["status"] = "cancelled"
        cancelled["spec"]["error"]["code"] = "plugin.runtime.cancelled"
        reconnected.commit_plugin_invocation_result(actor, digest, cancelled)
        terminal = self.operations.get_plugin_invocation_status(
            actor, invocation["metadata"]["id"]
        )
        self.assertEqual(terminal["spec"]["state"], "cancelled")
        with psycopg.connect(DATABASE_URL) as connection:
            count = connection.execute(
                "SELECT count(*) AS count FROM iip.audit_records WHERE tenant_id = %s AND category = %s",
                (actor.tenant_id, "plugin-invocation-cancellation"),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_plugin_reconciliation_closes_post_deadline_claim_without_replay(self) -> None:
        runner = ActorContext("plugin-host", "local", ("developer",))
        admin = ActorContext("platform-operator", "local", ("platform-admin",))
        session = json.loads(
            (ROOT / "contracts/examples/plugin-session.json").read_text()
        )
        invocation = json.loads(
            (ROOT / "contracts/examples/plugin-invocation.json").read_text()
        )
        digest = canonical_digest(invocation)
        self.operations.commit_plugin_session(runner, session)
        self.operations.claim_plugin_invocation(
            runner, session, invocation, digest, "2026-08-14T12:44:31Z"
        )

        class Clock:
            def now(self) -> str:
                return "2026-08-14T12:46:00Z"

        service = PluginInvocationLifecycleService(
            PostgresOperationalStore(DATABASE_URL), AllowTenantPolicy(), Clock()
        )
        request = json.loads(
            (
                ROOT
                / "contracts/examples/plugin-invocation-reconciliation-request.json"
            ).read_text()
        )
        status = service.reconcile(
            ReconcilePluginInvocationCommand(admin, request)
        )
        replayed = self.operations.claim_plugin_invocation(
            runner, session, invocation, digest, "2026-08-14T13:00:00Z"
        )

        self.assertEqual(status["spec"]["state"], "failed")
        self.assertEqual(replayed.state, "completed")
        self.assertEqual(
            replayed.result["spec"]["error"]["code"],
            "plugin.execution.outcome-unknown",
        )
        with psycopg.connect(DATABASE_URL) as connection:
            count = connection.execute(
                "SELECT count(*) AS count FROM iip.audit_records WHERE tenant_id = %s AND category = %s",
                (runner.tenant_id, "plugin-invocation-reconciliation"),
            ).fetchone()[0]
        self.assertEqual(count, 1)

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

    def test_evidence_retention_expires_bytes_but_preserves_metadata_and_legal_hold(self) -> None:
        def commit(
            actor: ActorContext,
            digit: str,
            retention_class: str,
            recorded_at: str,
            *,
            expires_at: str | None = None,
        ) -> str:
            evidence_id = f"evd_{digit * 32}"
            content = f"retention-artifact-{digit}".encode()
            handling = {
                "redaction": {"status": "not-required", "methods": []},
                "sensitivity": "internal",
                "retentionClass": retention_class,
            }
            if expires_at is not None:
                handling["expiresAt"] = expires_at
            document = {
                "apiVersion": "iip.platform/v1alpha1",
                "kind": "Evidence",
                "metadata": {
                    "id": evidence_id,
                    "tenantId": actor.tenant_id,
                    "recordedAt": recorded_at,
                },
                "spec": {
                    "handling": handling,
                    "artifact": {
                        "contentHash": (
                            "sha256:" + hashlib.sha256(content).hexdigest()
                        ),
                        "sizeBytes": len(content),
                        "storageRef": (
                            f"evidence://{actor.tenant_id}/{evidence_id}/artifact"
                        ),
                    },
                },
            }
            self.operations.commit(actor, evidence_id, document, content)
            return evidence_id

        actor = ActorContext("operator", "local", ("platform-admin",))
        other = ActorContext("operator", "another-tenant", ("platform-admin",))
        expired_id = commit(
            actor,
            "a",
            "ephemeral",
            "2026-08-15T00:00:00Z",
        )
        future_id = commit(
            actor,
            "b",
            "standard",
            "2026-08-17T15:00:00Z",
        )
        hold_id = commit(
            actor,
            "c",
            "legal-hold",
            "2026-01-01T00:00:00Z",
            expires_at="2026-01-02T00:00:00Z",
        )
        other_id = commit(
            other,
            "d",
            "ephemeral",
            "2026-01-01T00:00:00Z",
        )
        digest = "sha256:" + ("e" * 64)

        observed = self.operations.evaluate_evidence_retention(
            "local",
            "2026-08-17T16:00:00Z",
            ephemeral_seconds=86_400,
            standard_seconds=2_592_000,
            extended_seconds=31_536_000,
            limit=1,
            expire=False,
            policy_digest=digest,
        )
        self.assertEqual(observed.stored_artifacts, 3)
        self.assertEqual(observed.eligible_artifacts, 1)
        self.assertEqual(observed.legal_hold_artifacts, 1)
        self.assertIsNone(observed.audit_ref)

        expired = self.operations.evaluate_evidence_retention(
            "local",
            "2026-08-17T16:00:00Z",
            ephemeral_seconds=86_400,
            standard_seconds=2_592_000,
            extended_seconds=31_536_000,
            limit=1,
            expire=True,
            policy_digest=digest,
        )
        self.assertEqual(expired.expired_artifacts, 1)
        self.assertEqual(expired.remaining_eligible_artifacts, 0)
        self.assertRegex(expired.audit_ref or "", r"^audit://local/records/[0-9]+$")
        self.assertIsNone(self.operations.read_artifact(actor, expired_id))
        self.assertIsNotNone(self.operations.get(actor, expired_id))
        self.assertIsNotNone(self.operations.read_artifact(actor, future_id))
        self.assertIsNotNone(self.operations.read_artifact(actor, hold_id))
        self.assertIsNotNone(self.operations.read_artifact(other, other_id))

        assert DATABASE_URL is not None and psycopg is not None
        with psycopg.connect(DATABASE_URL) as connection:
            row = connection.execute(
                "SELECT artifact_deleted_at FROM iip.evidence_artifacts "
                "WHERE tenant_id = 'local' AND evidence_id = %s",
                (expired_id,),
            ).fetchone()
            audit = connection.execute(
                "SELECT document FROM iip.audit_records "
                "WHERE tenant_id = 'local' "
                "AND category = 'evidence-retention-expired'"
            ).fetchone()
        self.assertIsNotNone(row[0])
        self.assertNotIn("evd_", json.dumps(audit[0]))
        self.assertNotIn("retention-artifact", json.dumps(audit[0]))

    def test_concurrent_evidence_retention_expires_and_audits_one_batch_once(self) -> None:
        actor = ActorContext("operator", "local", ("platform-admin",))
        evidence_id = "evd_" + ("e" * 32)
        content = b"one-retention-race-artifact"
        document = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "Evidence",
            "metadata": {
                "id": evidence_id,
                "tenantId": "local",
                "recordedAt": "2026-01-01T00:00:00Z",
            },
            "spec": {
                "handling": {
                    "redaction": {"status": "not-required", "methods": []},
                    "sensitivity": "internal",
                    "retentionClass": "ephemeral",
                },
                "artifact": {
                    "contentHash": "sha256:" + hashlib.sha256(content).hexdigest(),
                    "sizeBytes": len(content),
                    "storageRef": f"evidence://local/{evidence_id}/artifact",
                },
            },
        }
        self.operations.commit(actor, evidence_id, document, content)
        barrier = Barrier(3)

        def expire():
            barrier.wait()
            return PostgresOperationalStore(
                DATABASE_URL
            ).evaluate_evidence_retention(
                "local",
                "2026-08-17T16:00:00Z",
                ephemeral_seconds=86_400,
                standard_seconds=2_592_000,
                extended_seconds=31_536_000,
                limit=1,
                expire=True,
                policy_digest="sha256:" + ("f" * 64),
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (pool.submit(expire), pool.submit(expire))
            barrier.wait()
            results = tuple(future.result() for future in futures)

        self.assertEqual(sum(result.expired_artifacts for result in results), 1)
        self.assertEqual(
            sum(result.audit_ref is not None for result in results),
            1,
        )
        assert DATABASE_URL is not None and psycopg is not None
        with psycopg.connect(DATABASE_URL) as connection:
            count = connection.execute(
                "SELECT count(*) FROM iip.audit_records "
                "WHERE tenant_id = 'local' "
                "AND category = 'evidence-retention-expired'"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_action_execution_claim_and_terminal_result_are_atomic(self) -> None:
        actor = ActorContext("workflow-executor", "local", ("executor",))
        proposal = json.loads(
            (ROOT / "contracts/examples/action-proposal.json").read_text()
        )
        approval = json.loads(
            (ROOT / "contracts/examples/action-approval.json").read_text()
        )
        terminal_status = json.loads(
            (ROOT / "contracts/examples/action-execution-status.json").read_text()
        )
        result = json.loads(
            (ROOT / "contracts/examples/action-result.json").read_text()
        )
        running_status = copy.deepcopy(terminal_status)
        running_status["metadata"]["updatedAt"] = running_status["spec"]["startedAt"]
        running_status["spec"]["state"] = "executing"
        running_status["spec"]["leaseExpiresAt"] = "2026-08-14T13:07:59Z"
        for field in ("completedAt", "operationRef", "summary"):
            del running_status["spec"][field]

        self.operations.commit_proposal(actor, proposal)
        self.operations.commit_approval(actor, approval)
        self.assertTrue(self.operations.claim_action_execution(actor, running_status))
        self.assertFalse(self.operations.claim_action_execution(actor, running_status))
        self.operations.commit_action_result(actor, result, terminal_status)

        reconnected = PostgresOperationalStore(DATABASE_URL)
        self.assertEqual(
            reconnected.get_action_execution_status(
                actor, proposal["metadata"]["id"]
            ),
            terminal_status,
        )
        self.assertEqual(
            reconnected.get_action_result(actor, proposal["metadata"]["id"]),
            result,
        )
        workflow = reconnected.get_action_workflow(
            actor, proposal["metadata"]["id"]
        )
        self.assertIsNotNone(workflow)
        self.assertEqual(workflow.proposal, proposal)
        self.assertEqual(workflow.approval, approval)
        self.assertEqual(workflow.execution_status, terminal_status)
        self.assertEqual(workflow.result, result)
        page = reconnected.list_action_workflows(
            actor,
            before_created_at=None,
            before_proposal_id=None,
            limit=2,
        )
        self.assertEqual(page, (workflow,))
        self.assertEqual(
            reconnected.list_action_workflows(
                ActorContext("other", "another-tenant"),
                before_created_at=None,
                before_proposal_id=None,
                limit=2,
            ),
            (),
        )

    def test_expired_action_reconciliation_and_audit_are_atomic_and_idempotent(self) -> None:
        actor = ActorContext("workflow-executor", "local", ("executor",))
        proposal = json.loads(
            (ROOT / "contracts/examples/action-proposal.json").read_text()
        )
        approval = json.loads(
            (ROOT / "contracts/examples/action-approval.json").read_text()
        )
        running = json.loads(
            (ROOT / "contracts/examples/action-execution-status.json").read_text()
        )
        running["metadata"]["updatedAt"] = running["spec"]["startedAt"]
        running["spec"]["state"] = "executing"
        running["spec"]["leaseExpiresAt"] = "2026-08-14T13:07:59Z"
        for field in ("completedAt", "operationRef", "summary"):
            running["spec"].pop(field, None)
        self.operations.commit_proposal(actor, proposal)
        self.operations.commit_approval(actor, approval)
        self.assertTrue(self.operations.claim_action_execution(actor, running))
        clock = type(
            "FixedClock",
            (),
            {"now": lambda _self: "2026-08-14T13:08:00Z"},
        )()
        service = ActionReconciliationService(
            self.operations,
            clock,
            worker_id="postgres-reconciler",
        )

        first = service.run_once("local")
        second = service.run_once("local")

        self.assertEqual((first.scanned, first.transitioned), (1, 1))
        self.assertEqual((second.scanned, second.transitioned), (0, 0))
        status = self.operations.get_action_execution_status(
            actor, proposal["metadata"]["id"]
        )
        self.assertEqual(
            status["spec"]["state"], "manual-reconciliation-required"
        )
        assert DATABASE_URL is not None and psycopg is not None
        with psycopg.connect(DATABASE_URL) as connection:
            count = connection.execute(
                """
                SELECT count(*)
                FROM iip.audit_records
                WHERE tenant_id = %s
                  AND category = 'action-execution-reconciliation-required'
                """,
                (actor.tenant_id,),
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_investigation_lifecycle_is_durable_and_transitions_atomically(self) -> None:
        actor = ActorContext("developer", "local")
        request = json.loads(
            (ROOT / "contracts/examples/investigation-request.json").read_text()
        )
        cancellation_status = json.loads(
            (ROOT / "contracts/examples/investigation-status.json").read_text()
        )
        running_status = copy.deepcopy(cancellation_status)
        running_status["metadata"]["updatedAt"] = running_status["spec"]["startedAt"]
        running_status["spec"]["state"] = "running"
        del running_status["spec"]["cancellation"]
        investigation_id = request["metadata"]["id"]

        self.operations.start_investigation(
            actor, investigation_id, request, running_status
        )
        self.assertEqual(
            self.operations.get_investigation_status(actor, investigation_id),
            running_status,
        )
        self.assertIsNone(
            self.operations.get_investigation(actor, investigation_id)
        )
        self.assertEqual(
            self.operations.request_investigation_cancellation(
                actor, investigation_id, cancellation_status
            ),
            cancellation_status,
        )

        report = json.loads(
            (ROOT / "contracts/examples/investigation-report.json").read_text()
        )
        report["spec"].update(
            {
                "outcome": "cancelled",
                "terminalReason": "cancelled",
                "summary": "The investigation stopped after a cooperative cancellation request.",
                "hypotheses": [],
                "recommendations": [],
            }
        )
        terminal_status = copy.deepcopy(cancellation_status)
        terminal_status["metadata"]["updatedAt"] = report["spec"]["completedAt"]
        terminal_status["spec"].update(
            {
                "state": "cancelled",
                "completedAt": report["spec"]["completedAt"],
                "reportRef": (
                    f"investigation://local/{investigation_id}/report"
                ),
            }
        )
        del terminal_status["spec"]["leaseExpiresAt"]
        self.operations.commit_investigation(
            actor, investigation_id, request, report, terminal_status
        )

        reconnected = PostgresOperationalStore(DATABASE_URL)
        self.assertEqual(reconnected.get_investigation(actor, investigation_id), report)
        self.assertEqual(
            reconnected.get_investigation_status(actor, investigation_id),
            terminal_status,
        )
        self.assertIsNone(
            reconnected.get_investigation(
                ActorContext("other", "another-tenant"), investigation_id
            )
        )

    def test_investigation_job_claim_heartbeat_and_completion_are_atomic(self) -> None:
        actor = ActorContext("developer", "local", ("developer",))
        request = json.loads(
            (ROOT / "contracts/examples/investigation-request.json").read_text()
        )
        investigation_id = request["metadata"]["id"]
        queued = InvestigationDispatchService.queued_status(
            actor,
            investigation_id,
            request,
            "2026-08-17T12:00:00Z",
            attempts=0,
        )
        self.assertEqual(
            self.operations.enqueue_investigation_job(
                actor, investigation_id, request, queued
            ),
            queued,
        )
        self.assertIsNone(
            self.operations.claim_investigation_job(
                "another-tenant",
                "worker-a",
                "2026-08-17T12:00:01Z",
                "2026-08-17T12:00:31Z",
            )
        )
        claim = self.operations.claim_investigation_job(
            "local",
            "worker-a",
            "2026-08-17T12:00:01Z",
            "2026-08-17T12:00:31Z",
        )
        self.assertIsNotNone(claim)
        assert claim is not None
        running = self.operations.get_investigation_job(actor, investigation_id)
        self.assertEqual(running["spec"]["state"], "running")
        heartbeat = copy.deepcopy(running)
        heartbeat["metadata"]["updatedAt"] = "2026-08-17T12:00:11Z"
        heartbeat["spec"]["heartbeatAt"] = "2026-08-17T12:00:11Z"
        heartbeat["spec"]["leaseExpiresAt"] = "2026-08-17T12:00:41Z"
        self.assertFalse(
            self.operations.heartbeat_investigation_job(
                "local", investigation_id, "worker-b", claim.claim_token, heartbeat
            )
        )
        self.assertTrue(
            self.operations.heartbeat_investigation_job(
                "local", investigation_id, "worker-a", claim.claim_token, heartbeat
            )
        )
        terminal = InvestigationDispatchService.terminal_status(
            heartbeat,
            state="completed",
            completed_at="2026-08-17T12:00:12Z",
            report_ref=f"investigation://local/{investigation_id}/report",
        )
        self.assertTrue(
            self.operations.finish_investigation_job(
                "local", investigation_id, "worker-a", claim.claim_token, terminal
            )
        )
        self.assertFalse(
            self.operations.finish_investigation_job(
                "local", investigation_id, "worker-a", claim.claim_token, terminal
            )
        )
        self.assertEqual(
            PostgresOperationalStore(DATABASE_URL).get_investigation_job(
                actor, investigation_id
            ),
            terminal,
        )
        slo = self.operations.get_investigation_completion_slo_state(
            "local",
            window_start="2026-08-17T11:59:00Z",
            window_end="2026-08-17T12:10:00Z",
            maturity_cutoff="2026-08-17T12:05:00Z",
            completion_objective_seconds=300,
        )
        self.assertEqual(slo.accepted_jobs, 1)
        self.assertEqual(slo.eligible_jobs, 1)
        self.assertEqual(slo.within_objective_jobs, 1)
        self.assertEqual(slo.late_completed_jobs, 0)
        self.assertEqual(slo.failed_jobs, 0)
        self.assertEqual(slo.cancelled_jobs, 0)
        self.assertEqual(slo.unfinished_jobs, 0)
        historical = self.operations.get_investigation_completion_slo_state(
            "local",
            window_start="2026-08-17T11:59:00Z",
            window_end="2026-08-17T12:00:10Z",
            maturity_cutoff="2026-08-17T12:00:05Z",
            completion_objective_seconds=5,
        )
        self.assertEqual(historical.accepted_jobs, 1)
        self.assertEqual(historical.eligible_jobs, 1)
        self.assertEqual(historical.unfinished_jobs, 1)
        self.assertEqual(historical.within_objective_jobs, 0)
        self.assertEqual(
            self.operations.get_investigation_completion_slo_state(
                "another-tenant",
                window_start="2026-08-17T11:59:00Z",
                window_end="2026-08-17T12:10:00Z",
                maturity_cutoff="2026-08-17T12:05:00Z",
                completion_objective_seconds=300,
            ).accepted_jobs,
            0,
        )

    def test_concurrent_job_claims_enforce_the_tenant_live_lease_cap(self) -> None:
        actor = ActorContext("developer", "local", ("developer",))
        for digit in ("a", "b"):
            request = json.loads(
                (ROOT / "contracts/examples/investigation-request.json").read_text()
            )
            investigation_id = f"inv_{digit * 32}"
            request["metadata"]["id"] = investigation_id
            queued = InvestigationDispatchService.queued_status(
                actor,
                investigation_id,
                request,
                "2026-08-17T12:00:00Z",
                attempts=0,
            )
            self.operations.enqueue_investigation_job(
                actor, investigation_id, request, queued
            )

        barrier = Barrier(3)

        def claim(worker_id: str):
            barrier.wait()
            return self.operations.claim_investigation_job(
                "local",
                worker_id,
                "2026-08-17T12:00:01Z",
                "2026-08-17T12:00:31Z",
                max_tenant_concurrency=1,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = tuple(
                pool.submit(claim, worker_id)
                for worker_id in ("worker-a", "worker-b")
            )
            barrier.wait()
            claims = tuple(future.result() for future in futures)

        self.assertEqual(sum(claim is not None for claim in claims), 1)

        other_actor = ActorContext("developer", "another-tenant", ("developer",))
        other_request = json.loads(
            (ROOT / "contracts/examples/investigation-request.json").read_text()
        )
        other_id = f"inv_{'c' * 32}"
        other_request["metadata"].update(
            {
                "id": other_id,
                "tenantId": other_actor.tenant_id,
                "actorId": other_actor.actor_id,
            }
        )
        other_queued = InvestigationDispatchService.queued_status(
            other_actor,
            other_id,
            other_request,
            "2026-08-17T12:00:00Z",
            attempts=0,
        )
        self.operations.enqueue_investigation_job(
            other_actor, other_id, other_request, other_queued
        )
        self.assertIsNotNone(
            self.operations.claim_investigation_job(
                "another-tenant",
                "worker-c",
                "2026-08-17T12:00:01Z",
                "2026-08-17T12:00:31Z",
                max_tenant_concurrency=1,
            )
        )

    def test_concurrent_job_admission_cannot_overfill_one_tenant(self) -> None:
        actor = ActorContext("developer", "local", ("developer",))
        barrier = Barrier(3)

        def enqueue(digit: str):
            request = json.loads(
                (ROOT / "contracts/examples/investigation-request.json").read_text()
            )
            investigation_id = f"inv_{digit * 32}"
            request["metadata"]["id"] = investigation_id
            queued = InvestigationDispatchService.queued_status(
                actor,
                investigation_id,
                request,
                "2026-08-17T12:00:00Z",
                attempts=0,
            )
            barrier.wait()
            try:
                result = self.operations.enqueue_investigation_job(
                    actor,
                    investigation_id,
                    request,
                    queued,
                    max_outstanding_jobs_per_tenant=1,
                )
                return "accepted", request, result
            except PersistenceError as exc:
                return str(exc), request, queued

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = tuple(pool.submit(enqueue, digit) for digit in ("d", "e"))
            barrier.wait()
            results = tuple(future.result() for future in futures)

        self.assertCountEqual(
            (result[0] for result in results),
            ("accepted", "storage.capacity-exceeded"),
        )
        accepted = next(result for result in results if result[0] == "accepted")
        accepted_request = accepted[1]
        accepted_status = accepted[2]
        self.assertEqual(
            self.operations.enqueue_investigation_job(
                actor,
                accepted_status["metadata"]["id"],
                accepted_request,
                accepted_status,
                max_outstanding_jobs_per_tenant=1,
            ),
            accepted_status,
        )


if __name__ == "__main__":
    unittest.main()
