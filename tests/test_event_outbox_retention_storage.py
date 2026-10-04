"""Storage proofs: retire delivery bookkeeping, never the immutable event log."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from threading import Barrier
import time
import unittest
from unittest.mock import patch
import uuid

from iip.adapters.memory import AllowTenantPolicy, InMemoryResourceStore
from iip.application.ingest_resource import IngestResourceCommand, ResourceIngestionService
from iip.application.ports import ActorContext, PersistenceError

try:
    import psycopg
    from iip.adapters.postgres import PostgresResourceStore
except ModuleNotFoundError:
    psycopg = PostgresResourceStore = None


ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ.get("IIP_TEST_DATABASE_URL")
PUBLISHED_SECONDS = 2_592_000
DIGEST = "sha256:" + "a" * 64


def stamp(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class RetentionStorageCases:
    def configure_fixture(self):
        self.tenant = "retention-" + uuid.uuid4().hex
        self.other_tenant = self.tenant + "-other"
        self.now = datetime.now(timezone.utc)
        self.cutoff = self.now - timedelta(seconds=PUBLISHED_SECONDS)
        self.old = self.cutoff - timedelta(days=1)
        self.service = ResourceIngestionService(self.store, AllowTenantPolicy())
        self.payloads = {}

    def seed(self, *, tenant=None, created=None, published="old", claimed_by=None,
             claim_expires_at=None, quarantined_at=None):
        tenant = tenant or self.tenant
        payload = json.loads((ROOT / "contracts/examples/resource.json").read_text())
        payload["metadata"]["tenantId"] = tenant
        payload["spec"]["externalId"] += "/" + uuid.uuid4().hex
        actor = ActorContext("collector", tenant)
        self.service.execute(IngestResourceCommand(actor, payload))
        event = tuple(self.store.list_events(tenant, limit=1000))[-1]
        message_id = self.message_id(tenant, event.offset)
        self.payloads[(tenant, message_id)] = payload
        self.change_entry(
            tenant, message_id,
            created_at=created or self.old,
            published_at=self.old if published == "old" else published,
            claimed_by=claimed_by, claim_expires_at=claim_expires_at,
            quarantined_at=quarantined_at,
            attempts=3 if quarantined_at else 0,
            last_error_code="event.publisher.unavailable" if quarantined_at else None,
        )
        return message_id

    def evaluate(self, *, tenant=None, **overrides):
        arguments = dict(
            published_seconds=PUBLISHED_SECONDS, limit=100, expire=False,
            policy_digest=DIGEST,
        )
        arguments.update(overrides)
        evaluated_at = arguments.pop("evaluated_at", stamp(self.now))
        return self.store.evaluate_event_outbox_retention(
            tenant or self.tenant, evaluated_at, **arguments,
        )

    def test_eligibility_is_strict_terminal_bounded_and_exact_tenant(self):
        first = self.seed(published=self.old - timedelta(hours=1))
        second = self.seed()
        protected = {
            self.seed(published=self.cutoff),
            self.seed(created=self.cutoff),
            self.seed(published=self.now),
            self.seed(created=self.now),
            self.seed(published=None),
            self.seed(claimed_by="worker", claim_expires_at=self.now + timedelta(hours=1)),
            self.seed(claim_expires_at=self.old),
            self.seed(claimed_by="worker"),
            self.seed(published=None, quarantined_at=self.old),
        }
        other = self.seed(tenant=self.other_tenant)
        events = tuple(self.store.list_events(self.tenant))
        observed = self.evaluate()
        self.assertEqual((observed.stored_rows, observed.published_rows), (11, 9))
        self.assertEqual((observed.eligible_rows, observed.expired_rows, observed.protected_rows), (2, 0, 9))
        self.assertEqual(observed.remaining_eligible_rows, 2)
        self.assertIsNone(observed.audit_ref)
        self.assertEqual(self.audit_documents(), [])
        expired = self.evaluate(expire=True, limit=1)
        self.assertEqual((expired.eligible_rows, expired.expired_rows, expired.remaining_eligible_rows), (2, 1, 1))
        self.assertRegex(expired.audit_ref, "^audit://" + self.tenant + "/")
        self.assertEqual(self.remaining_ids(self.tenant), protected | {second})
        self.assertNotIn(first, self.remaining_ids(self.tenant))
        self.assertEqual(self.remaining_ids(self.other_tenant), {other})
        self.assertEqual(tuple(self.store.list_events(self.tenant)), events)
        self.assertEqual(self.evaluate(expire=True).expired_rows, 1)
        self.assertEqual(self.evaluate(expire=True).expired_rows, 0)
        audits = self.audit_documents()
        self.assertEqual(len(audits), 2)
        for audit in audits:
            self.assertEqual(set(audit), {"apiVersion", "kind", "metadata", "spec"})
            self.assertEqual(set(audit["spec"]), {"policyDigest", "expiredRows", "remainingEligibleRows"})
            serialized = json.dumps(audit)
            self.assertNotIn("resourceUid", serialized)
            self.assertNotIn("outboxId", serialized)
            self.assertNotIn(events[0].event.event_id, serialized)

    def test_pruning_cannot_change_the_largest_supported_slo_cohort(self):
        self.seed()
        self.seed(created=self.cutoff, published=self.cutoff + timedelta(seconds=1))
        self.seed(created=self.now - timedelta(hours=1), published=self.now - timedelta(minutes=1))
        self.seed(created=self.now - timedelta(hours=2), published=None, quarantined_at=self.old)
        self.seed(created=self.now - timedelta(seconds=10), published=None)
        arguments = dict(
            window_start=stamp(self.cutoff), window_end=stamp(self.now),
            maturity_cutoff=stamp(self.now - timedelta(seconds=60)),
            latency_objective_seconds=60,
        )
        before = self.store.get_event_delivery_slo_state(self.tenant, **arguments)
        self.assertEqual(self.evaluate(expire=True).expired_rows, 1)
        after = self.store.get_event_delivery_slo_state(self.tenant, **arguments)
        self.assertEqual(after, before)
        self.assertEqual((after.created_events, after.eligible_events), (4, 3))
        self.assertEqual((after.within_objective_events, after.late_delivered_events, after.undelivered_events), (1, 1, 1))

    def test_quarantine_generation_replay_and_pending_claims_survive(self):
        self.seed()
        quarantine_id = self.seed(published=None, quarantined_at=self.old)
        pending_id = self.seed(published=None)
        quarantine = self.store.get_quarantined_outbox(self.tenant, quarantine_id)
        self.assertIsNotNone(quarantine)
        self.assertEqual(self.evaluate(expire=True).expired_rows, 1)
        self.assertEqual(self.store.get_quarantined_outbox(self.tenant, quarantine_id), quarantine)
        self.assertFalse(self.store.requeue_quarantined_outbox(
            self.other_tenant, quarantine_id, expected_event_id=quarantine.event_id,
            expected_quarantined_at=quarantine.quarantined_at, expected_attempts=quarantine.attempts,
        ))
        self.assertTrue(self.store.requeue_quarantined_outbox(
            self.tenant, quarantine_id, expected_event_id=quarantine.event_id,
            expected_quarantined_at=quarantine.quarantined_at, expected_attempts=quarantine.attempts,
        ))
        claims = tuple(self.store.claim_outbox(self.tenant, "publisher"))
        self.assertEqual({item.message_id for item in claims}, {quarantine_id, pending_id})
        replay = next(item for item in claims if item.message_id == quarantine_id)
        self.assertEqual(replay.event.event_id, quarantine.event_id)
        self.assertTrue(self.store.acknowledge_outbox(self.tenant, "publisher", quarantine_id))
        self.assertEqual(self.evaluate(expire=True).expired_rows, 0)

    def test_event_replay_dedup_and_offset_identity_survive_pruning(self):
        message_id = self.seed()
        events = tuple(self.store.list_events(self.tenant))
        self.assertEqual(self.evaluate(expire=True).expired_rows, 1)
        self.service.execute(IngestResourceCommand(
            ActorContext("collector", self.tenant), self.payloads[(self.tenant, message_id)],
        ))
        self.assertEqual(self.remaining_ids(self.tenant), set())
        self.assertEqual(tuple(self.store.list_events(self.tenant)), events)
        later = self.seed()
        self.assertGreater(later, message_id)
        later_events = tuple(self.store.list_events(self.tenant, after_offset=events[-1].offset))
        self.assertEqual(len(later_events), 1)
        self.assertGreater(later_events[0].offset, events[-1].offset)

    def test_two_retention_passes_delete_and_audit_one_row_once(self):
        self.seed()
        start = Barrier(2)

        def expire():
            start.wait(timeout=5)
            return self.evaluate(expire=True, limit=1)

        with ThreadPoolExecutor(max_workers=2) as executor:
            reports = tuple(executor.map(lambda _: expire(), range(2)))
        self.assertEqual(sorted(report.expired_rows for report in reports), [0, 1])
        self.assertEqual(sorted(report.eligible_rows for report in reports), [0, 1])
        self.assertEqual(len(self.audit_documents()), 1)
        self.assertEqual(len(tuple(self.store.list_events(self.tenant))), 1)

    def test_invalid_port_parameters_fail_before_mutation(self):
        message_id = self.seed()
        invalid = (
            {"published_seconds": PUBLISHED_SECONDS - 1},
            {"published_seconds": 315_360_001}, {"published_seconds": True},
            {"limit": 0}, {"limit": 1001}, {"limit": True},
            {"expire": 1}, {"policy_digest": "private-invalid-value"},
            {"evaluated_at": "2026-10-04T00:00:00"}, {"evaluated_at": None},
            {"evaluated_at": "0001-01-01T00:00:00Z"},
        )
        for arguments in invalid:
            with self.subTest(arguments=arguments), self.assertRaisesRegex(PersistenceError, "^storage.input-invalid$"):
                self.evaluate(**arguments)
        for tenant in ("../private", "", None, True):
            with self.subTest(tenant=tenant), self.assertRaisesRegex(PersistenceError, "^storage.input-invalid$"):
                self.store.evaluate_event_outbox_retention(
                    tenant, stamp(self.now), published_seconds=PUBLISHED_SECONDS,
                    limit=1, expire=True, policy_digest=DIGEST,
                )
        self.assertEqual(self.remaining_ids(self.tenant), {message_id})
        self.assertEqual(self.audit_documents(), [])


class EventOutboxRetentionMemoryTests(RetentionStorageCases, unittest.TestCase):
    def setUp(self):
        self.store = InMemoryResourceStore()
        self.configure_fixture()

    def message_id(self, tenant, event_offset):
        return event_offset

    def change_entry(self, tenant, message_id, **values):
        del tenant
        for name, value in values.items():
            setattr(self.store._outbox[message_id], name, stamp(value) if name == "created_at" else value)

    def remaining_ids(self, tenant):
        return {identity for identity, entry in self.store._outbox.items() if entry.event.tenant_id == tenant}

    def audit_documents(self):
        return list(self.store._outbox_retention_audit)

    def test_failed_audit_append_does_not_delete_rows(self):
        message_id = self.seed()

        class FailingAudit(list):
            def append(self, value):
                raise PersistenceError("storage.unavailable")

        self.store._outbox_retention_audit = FailingAudit()
        with self.assertRaisesRegex(PersistenceError, "^storage.unavailable$"):
            self.evaluate(expire=True)
        self.assertEqual(self.remaining_ids(self.tenant), {message_id})
        self.assertEqual(self.audit_documents(), [])


@unittest.skipUnless(DATABASE_URL and psycopg and PostgresResourceStore,
                     "IIP_TEST_DATABASE_URL and psycopg are required for real PostgreSQL retention tests")
class EventOutboxRetentionPostgresTests(RetentionStorageCases, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = PostgresResourceStore(DATABASE_URL)
        cls.store.migrate()

    def setUp(self):
        self.configure_fixture()
        self.addCleanup(self.cleanup_tenants)

    def cleanup_tenants(self):
        # Own only this test's random tenants; never truncate another fixture.
        with psycopg.connect(DATABASE_URL) as connection:
            for table in ("event_outbox", "event_log", "resource_relationships", "resource_observations", "resource_projections", "audit_records"):
                connection.execute(f"DELETE FROM iip.{table} WHERE tenant_id = ANY(%s)", ([self.tenant, self.other_tenant],))

    def message_id(self, tenant, event_offset):
        with psycopg.connect(DATABASE_URL) as connection:
            return connection.execute(
                "SELECT outbox_id FROM iip.event_outbox WHERE tenant_id = %s AND event_offset = %s",
                (tenant, event_offset),
            ).fetchone()[0]

    def change_entry(self, tenant, message_id, **values):
        assignments = ", ".join(f"{name} = %s" for name in values)
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                f"UPDATE iip.event_outbox SET {assignments} WHERE tenant_id = %s AND outbox_id = %s",
                (*values.values(), tenant, message_id),
            )

    def remaining_ids(self, tenant):
        with psycopg.connect(DATABASE_URL) as connection:
            return {row[0] for row in connection.execute("SELECT outbox_id FROM iip.event_outbox WHERE tenant_id = %s", (tenant,)).fetchall()}

    def audit_documents(self):
        with psycopg.connect(DATABASE_URL) as connection:
            return [row[0] for row in connection.execute(
                "SELECT document FROM iip.audit_records WHERE tenant_id = %s AND category = 'event-outbox-retention-expired' ORDER BY audit_offset",
                (self.tenant,),
            ).fetchall()]

    def test_audit_database_failure_rolls_back_the_actual_delete(self):
        message_id = self.seed()
        connect = self.store._connect

        class AuditFailure:
            def __init__(self, connection):
                self.connection = connection

            def execute(self, statement, *arguments):
                if "INSERT INTO iip.audit_records" in statement:
                    raise psycopg.OperationalError("private provider details")
                return self.connection.execute(statement, *arguments)

        @contextmanager
        def failing_connection():
            with connect() as connection:
                yield AuditFailure(connection)

        with patch.object(self.store, "_connect", side_effect=failing_connection):
            with self.assertRaisesRegex(PersistenceError, "^storage.unavailable$") as raised:
                self.evaluate(expire=True)
        self.assertIsNone(raised.exception.__cause__)
        self.assertEqual(self.remaining_ids(self.tenant), {message_id})
        self.assertEqual(self.audit_documents(), [])
        self.assertEqual(self.evaluate(expire=True).expired_rows, 1)

    def test_locked_candidate_is_skipped_without_losing_eligible_backlog(self):
        message_id = self.seed()
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute("SELECT outbox_id FROM iip.event_outbox WHERE tenant_id = %s AND outbox_id = %s FOR UPDATE", (self.tenant, message_id))
            observed = self.evaluate(expire=True)
            self.assertEqual((observed.eligible_rows, observed.expired_rows, observed.remaining_eligible_rows), (1, 0, 1))
            self.assertIsNone(observed.audit_ref)
        self.assertEqual(self.evaluate(expire=True).expired_rows, 1)

    def test_retention_lock_is_independent_of_other_tenants_and_evidence(self):
        self.seed()
        self.seed(tenant=self.other_tenant)
        connect = self.store._connect

        @contextmanager
        def bounded_connection():
            with connect() as connection:
                connection.execute("SET LOCAL statement_timeout = '1000ms'")
                yield connection

        with psycopg.connect(DATABASE_URL) as held:
            held.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"event-outbox-retention:{self.tenant}",))
            held.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"evidence-artifacts:{self.other_tenant}",))
            with patch.object(self.store, "_connect", side_effect=bounded_connection):
                self.assertEqual(self.evaluate(tenant=self.other_tenant, expire=True).expired_rows, 1)
        self.assertEqual(self.evaluate(expire=True).expired_rows, 1)

    def test_same_tenant_retention_lock_times_out_without_delete_or_audit(self):
        message_id = self.seed()
        with psycopg.connect(DATABASE_URL) as held:
            held.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (f"event-outbox-retention:{self.tenant}",))
            started = time.monotonic()
            with self.assertRaisesRegex(PersistenceError, "^storage.unavailable$") as raised:
                self.evaluate(expire=True)
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 8)
        self.assertIsNone(raised.exception.__cause__)
        self.assertEqual(self.remaining_ids(self.tenant), {message_id})
        self.assertEqual(self.audit_documents(), [])
        self.assertEqual(self.evaluate(expire=True).expired_rows, 1)

    def test_migration_is_repeatable_and_does_not_modify_existing_delivery_facts(self):
        message_id = self.seed()
        before = self.evaluate()
        self.store.migrate()
        self.store.migrate()
        self.assertEqual(self.evaluate(), before)
        self.assertEqual(self.remaining_ids(self.tenant), {message_id})
        with psycopg.connect(DATABASE_URL) as connection:
            index = connection.execute(
                "SELECT indexdef FROM pg_indexes WHERE schemaname = 'iip' AND indexname = 'event_outbox_published_retention_idx'",
            ).fetchone()
        self.assertIsNotNone(index)
        self.assertIn("published_at IS NOT NULL", index[0])
        self.assertIn("claim_expires_at IS NULL", index[0])

    def test_actual_once_worker_honors_enrollment_opt_in_and_idempotent_cleanup(self):
        published = self.seed()
        pending = self.seed(published=None)
        other = self.seed(tenant=self.other_tenant)
        events = tuple(self.store.list_events(self.tenant))

        def run_worker(*, enabled):
            # The worker's bounded health listener requires a nonzero port.
            # Reserve an available loopback port rather than a fixed test port.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
                reservation.bind(("127.0.0.1", 0))
                port = reservation.getsockname()[1]
            # Do not inherit operator IIP settings, proxies, provider credentials,
            # identity configuration, or telemetry destinations. Worker bootstrap
            # supplies DenyAllAuthenticator and never calls interactive auth.
            environment = {
                "PATH": os.defpath,
                "PYTHONPATH": os.pathsep.join((str(ROOT / "src"), str(ROOT / "sdks/python/src"))),
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUNBUFFERED": "1",
                "IIP_DATABASE_URL": DATABASE_URL,
                "IIP_DATABASE_TRANSPORT_MODE": "insecure-local",
                "IIP_DATABASE_AUTO_MIGRATE": "false",
                "IIP_WORKER_ID": "retention-process-fixture",
                "IIP_WORKER_TENANTS": self.tenant,
                "IIP_WORKER_HEALTH_HOST": "127.0.0.1",
                "IIP_WORKER_HEALTH_PORT": str(port),
                "IIP_POLICY_MODE": "local",
                "IIP_AUTH_MODE": "local-hashed",  # No identities: interactive composition must not occur.
                "IIP_EVENT_PUBLISHER_MODE": "disabled",
                "IIP_OTLP_RECEIVER_MODE": "disabled",
                "IIP_OTEL_METRICS_ENABLED": "false",
                "IIP_OTEL_TRACES_ENABLED": "false",
                "IIP_EVIDENCE_RETENTION_ENABLED": "false",
                "IIP_AI_ATTRIBUTION_ENABLED": "false",
                "IIP_AI_COST_ENGINE_ENABLED": "false",
                "IIP_AI_SAVINGS_ENGINE_ENABLED": "false",
                "IIP_AI_ALLOCATION_REPORTING_ENABLED": "false",
                "IIP_EVENT_OUTBOX_RETENTION_ENABLED": "true" if enabled else "false",
                "IIP_EVENT_OUTBOX_RETENTION_PUBLISHED_SECONDS": str(PUBLISHED_SECONDS),
                "IIP_EVENT_OUTBOX_RETENTION_BATCH_SIZE": "1",
                "IIP_EVENT_OUTBOX_RETENTION_INTERVAL_SECONDS": "3600",
            }
            completed = subprocess.run(
                [sys.executable, "-m", "iip.surfaces.worker", "--once"],
                cwd=ROOT, env=environment, capture_output=True, text=True,
                timeout=45, check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = completed.stdout + completed.stderr
            self.assertNotIn(self.tenant, output)
            self.assertNotIn(self.other_tenant, output)
            self.assertNotIn(DATABASE_URL, output)
            self.assertNotIn("resourceUid", output)
            self.assertNotIn("Traceback", output)
            for event in events:
                self.assertNotIn(event.event.event_id, output)
                self.assertNotIn(event.event.subject, output)
            return [document for line in completed.stdout.splitlines()
                    if (document := json.loads(line)).get("event") == "event.outbox-retention.completed"]

        self.assertEqual(run_worker(enabled=True), [{
            "event": "event.outbox-retention.completed", "tenants": 1,
            "expiredRows": 1, "remainingEligibleRows": 0, "failures": 0,
        }])
        self.assertNotIn(published, self.remaining_ids(self.tenant))
        self.assertEqual(self.remaining_ids(self.tenant), {pending})
        self.assertEqual(self.remaining_ids(self.other_tenant), {other})
        self.assertEqual(tuple(self.store.list_events(self.tenant)), events)
        self.assertEqual(len(self.audit_documents()), 1)
        self.assertEqual(run_worker(enabled=True), [{
            "event": "event.outbox-retention.completed", "tenants": 1,
            "expiredRows": 0, "remainingEligibleRows": 0, "failures": 0,
        }])
        self.assertEqual(len(self.audit_documents()), 1)
        retained = self.seed()
        self.assertEqual(run_worker(enabled=False), [])
        self.assertEqual(self.remaining_ids(self.tenant), {pending, retained})
        self.assertEqual(self.remaining_ids(self.other_tenant), {other})
        self.assertEqual(len(self.audit_documents()), 1)


if __name__ == "__main__":
    unittest.main()
