from __future__ import annotations

import copy
import json
import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Event
from unittest.mock import patch

try:
    import psycopg

    from iip.adapters.postgres import PostgresResourceStore
except ModuleNotFoundError:
    psycopg = None
    PostgresResourceStore = None

from iip.adapters.ai_history import ai_invocation_correlation_digest
from iip.adapters.memory import InMemoryResourceStore
from iip.application.attribute_ai_usage import ENGINE_VERSION as ATTRIBUTION_ENGINE_VERSION
from iip.application.calculate_ai_cost import ENGINE_VERSION as COST_ENGINE_VERSION
from iip.application.ports import (
    ActorContext,
    AiAllocationLedgerQuery,
    AiHistoryAvailabilityQuery,
    AiHistoryRetiredError,
    AiInvocationEconomicsQuery,
    AiSavingsCohortQuery,
    PersistenceError,
)


ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ.get("IIP_TEST_DATABASE_URL")
START = "2026-09-05T09:00:00Z"
END = "2026-09-05T11:00:00Z"
TRACE_ID = "1234567890abcdef1234567890abcdef"
SPAN_ID = "fedcba0987654321"
POLICY_ID = "aap_" + "1" * 32
CATALOG_ID = "apc_" + "1" * 32
ACTOR = ActorContext("operator", "local", ("platform-admin",))


def _fixture(name: str) -> dict:
    return json.loads(
        (ROOT / "contracts" / "examples" / f"{name}.json").read_text(
            encoding="utf-8"
        )
    )


def _usage(index: int, started_at: str) -> dict:
    document = copy.deepcopy(_fixture("ai-usage-record"))
    document["metadata"].update(
        {
            "id": f"aiu_{index:032x}",
            "tenantId": "local",
            "recordedAt": "2026-09-05T10:59:00Z",
        }
    )
    document["spec"]["deduplicationKey"] = f"sha256:{index:064x}"
    document["spec"]["invocation"].update(
        {
            "startedAt": started_at,
            "traceId": f"{index:032x}",
            "spanId": f"{index:016x}",
            "requestIdHash": f"sha256:{index + 100:064x}",
        }
    )
    return document


def _allocation_query() -> AiAllocationLedgerQuery:
    return AiAllocationLedgerQuery(
        start=START,
        end=END,
        policy_id=POLICY_ID,
        attribution_engine_version=ATTRIBUTION_ENGINE_VERSION,
        catalog_id=CATALOG_ID,
        cost_engine_version=COST_ENGINE_VERSION,
        limit=1,
    )


def _savings_query() -> AiSavingsCohortQuery:
    return AiSavingsCohortQuery(
        provider="aws.bedrock",
        model_id="example.foundation-model-v1:0",
        region="us-east-1",
        service_name="support-assistant",
        deployment_environment="production",
        start=START,
        end=END,
        catalog_id=CATALOG_ID,
        engine_version=COST_ENGINE_VERSION,
        limit=1,
    )


def _invocation_query() -> AiInvocationEconomicsQuery:
    return AiInvocationEconomicsQuery(
        trace_id=TRACE_ID,
        span_id=SPAN_ID,
        policy_id=POLICY_ID,
        attribution_engine_version=ATTRIBUTION_ENGINE_VERSION,
        catalog_id=CATALOG_ID,
        cost_engine_version=COST_ENGINE_VERSION,
    )


class InMemoryAiHistoryStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryResourceStore()

    def _seed_usage(self, document: dict) -> None:
        tenant_id = document["metadata"]["tenantId"]
        usage_id = document["metadata"]["id"]
        deduplication_key = document["spec"]["deduplicationKey"]
        self.store._ai_usage[(tenant_id, deduplication_key)] = (  # noqa: SLF001
            "a" * 64,
            document,
        )
        self.store._ai_usage_ids[(tenant_id, usage_id)] = deduplication_key  # noqa: SLF001

    def _seed_marker(
        self,
        usage_id: str,
        started_at: str = "2026-09-05T10:00:00Z",
        *,
        tenant_id: str = "local",
        trace_id: str = TRACE_ID,
        span_id: str = SPAN_ID,
    ) -> None:
        self.store._ai_retired_invocation_markers[(  # noqa: SLF001
            tenant_id,
            usage_id,
        )] = (
            ai_invocation_correlation_digest(tenant_id, trace_id, span_id),
            datetime.fromisoformat(started_at.replace("Z", "+00:00")),
        )

    def test_availability_counts_live_rows_excluding_authoritative_markers(self) -> None:
        retained = _usage(1, "2026-09-05T09:30:00Z")
        retired = _usage(2, "2026-09-05T10:00:00Z")
        self._seed_usage(retained)
        self._seed_usage(retired)
        self._seed_marker(retired["metadata"]["id"])
        self._seed_marker(
            "aiu_00000000000000000000000000000003",
            tenant_id="another",
        )

        state = self.store.get_ai_history_availability(
            ACTOR,
            AiHistoryAvailabilityQuery(START, END),
        )

        self.assertEqual(state.retained_usage_records, 1)
        self.assertEqual(state.retired_usage_records, 1)

    def test_interval_and_exact_reads_fail_whole_when_marker_overlaps(self) -> None:
        self._seed_marker("aiu_00000000000000000000000000000001")

        with self.assertRaises(AiHistoryRetiredError):
            self.store.list_ai_allocation_rows(ACTOR, _allocation_query())
        with self.assertRaises(AiHistoryRetiredError):
            self.store.list_ai_savings_cohort(
                ActorContext(
                    "ai-savings-worker:history-test",
                    "local",
                    ("ai-savings:evaluate",),
                ),
                _savings_query(),
            )
        with self.assertRaises(AiHistoryRetiredError):
            self.store.find_ai_invocation_economics(ACTOR, _invocation_query())

    def test_unrelated_marker_preserves_not_observed_and_available_interval(self) -> None:
        self._seed_marker(
            "aiu_00000000000000000000000000000001",
            started_at="2026-09-04T10:00:00Z",
            trace_id="0" * 32,
            span_id="0" * 16,
        )

        self.assertEqual(self.store.list_ai_allocation_rows(ACTOR, _allocation_query()), ())
        self.assertIsNone(
            self.store.find_ai_invocation_economics(ACTOR, _invocation_query())
        )

    def test_availability_preserves_canonical_microsecond_bounds(self) -> None:
        self._seed_marker(
            "aiu_00000000000000000000000000000001",
            started_at="2026-09-05T10:00:00.500000Z",
        )

        state = self.store.get_ai_history_availability(
            ACTOR,
            AiHistoryAvailabilityQuery(
                "2026-09-05T10:00:00.499999Z",
                "2026-09-05T10:00:00.500001Z",
            ),
        )

        self.assertEqual(state.retired_usage_records, 1)


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and PostgresResourceStore is not None,
    "IIP_TEST_DATABASE_URL and psycopg are required for PostgreSQL integration tests",
)
class PostgresAiHistoryStorageTests(unittest.TestCase):
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
                TRUNCATE iip.ai_retired_invocation_markers,
                         iip.ai_usage_records
                CASCADE
                """
            )

    def tearDown(self) -> None:
        self.setUp()

    def _insert_usage(self, connection, document: dict) -> None:
        invocation = document["spec"]["invocation"]
        attribution = document["spec"]["attribution"]
        connection.execute(
            """
            INSERT INTO iip.ai_usage_records (
                tenant_id, usage_record_id, deduplication_key,
                document_hash, provider, model_id, service_name,
                invocation_started_at, recorded_at, document
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                document["metadata"]["tenantId"],
                document["metadata"]["id"],
                document["spec"]["deduplicationKey"],
                "a" * 64,
                invocation["provider"],
                invocation.get("responseModel", invocation["requestModel"]),
                attribution["serviceName"],
                invocation["startedAt"],
                document["metadata"]["recordedAt"],
                json.dumps(document),
            ),
        )

    def _insert_marker(
        self,
        connection,
        *,
        usage_id: str = "aiu_00000000000000000000000000000002",
        deduplication_key: str = "sha256:" + "2" * 64,
        started_at: str = "2026-09-05T10:00:00Z",
        tenant_id: str = "local",
        trace_id: str = TRACE_ID,
        span_id: str = SPAN_ID,
    ) -> None:
        connection.execute(
            """
            INSERT INTO iip.ai_retired_invocation_markers (
                tenant_id, usage_record_id, deduplication_key,
                correlation_digest, document_hash, recorded_at,
                invocation_started_at, retired_at, policy_digest, audit_ref
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                tenant_id,
                usage_id,
                deduplication_key,
                ai_invocation_correlation_digest(tenant_id, trace_id, span_id),
                "a" * 64,
                "2026-09-05T11:30:00Z",
                started_at,
                "2026-09-06T00:00:00Z",
                "sha256:" + "c" * 64,
                f"audit://{tenant_id}/records/1",
            ),
        )

    def test_counts_exclude_marked_live_row_and_isolate_tenants(self) -> None:
        assert DATABASE_URL is not None and psycopg is not None
        retained = _usage(1, "2026-09-05T09:30:00Z")
        retired = _usage(2, "2026-09-05T10:00:00Z")
        with psycopg.connect(DATABASE_URL) as connection:
            self._insert_usage(connection, retained)
            self._insert_usage(connection, retired)
            self._insert_marker(connection)
            self._insert_marker(
                connection,
                tenant_id="another",
                usage_id="aiu_00000000000000000000000000000003",
                deduplication_key="sha256:" + "3" * 64,
            )

        state = self.store.get_ai_history_availability(
            ACTOR,
            AiHistoryAvailabilityQuery(START, END),
        )

        self.assertEqual(state.retained_usage_records, 1)
        self.assertEqual(state.retired_usage_records, 1)

    def test_marker_guards_aggregate_and_exact_reads_before_query_limit(self) -> None:
        assert DATABASE_URL is not None and psycopg is not None
        with psycopg.connect(DATABASE_URL) as connection:
            self._insert_usage(connection, _usage(1, "2026-09-05T09:15:00Z"))
            self._insert_marker(connection)

        with self.assertRaises(AiHistoryRetiredError):
            self.store.list_ai_allocation_rows(ACTOR, _allocation_query())
        with self.assertRaises(AiHistoryRetiredError):
            self.store.list_ai_savings_cohort(
                ActorContext(
                    "ai-savings-worker:history-test",
                    "local",
                    ("ai-savings:evaluate",),
                ),
                _savings_query(),
            )
        with self.assertRaises(AiHistoryRetiredError):
            self.store.find_ai_invocation_economics(ACTOR, _invocation_query())

    def test_half_open_end_and_other_tenant_markers_do_not_poison_reads(self) -> None:
        assert DATABASE_URL is not None and psycopg is not None
        with psycopg.connect(DATABASE_URL) as connection:
            self._insert_marker(
                connection,
                started_at=END,
                trace_id="0" * 32,
                span_id="0" * 16,
            )
            self._insert_marker(
                connection,
                tenant_id="another",
                usage_id="aiu_00000000000000000000000000000003",
                deduplication_key="sha256:" + "3" * 64,
            )

        state = self.store.get_ai_history_availability(
            ACTOR,
            AiHistoryAvailabilityQuery(START, END),
        )

        self.assertEqual(state.retired_usage_records, 0)
        self.assertEqual(self.store.list_ai_allocation_rows(ACTOR, _allocation_query()), ())
        self.assertIsNone(
            self.store.find_ai_invocation_economics(ACTOR, _invocation_query())
        )

    def test_repeatable_read_prevents_guard_to_rows_snapshot_race(self) -> None:
        assert DATABASE_URL is not None and psycopg is not None
        document = _usage(1, "2026-09-05T09:15:00Z")
        with psycopg.connect(DATABASE_URL) as connection:
            self._insert_usage(connection, document)

        guard_complete = Event()
        mutation_complete = Event()
        original_guard = self.store._raise_if_ai_history_retired

        def pause_after_guard(*args) -> None:
            original_guard(*args)
            guard_complete.set()
            if not mutation_complete.wait(5):
                raise AssertionError("test mutation did not complete")

        with patch.object(
            self.store,
            "_raise_if_ai_history_retired",
            side_effect=pause_after_guard,
        ), ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(
                self.store.list_ai_allocation_rows,
                ACTOR,
                _allocation_query(),
            )
            self.assertTrue(guard_complete.wait(5))
            try:
                with psycopg.connect(DATABASE_URL) as connection:
                    self._insert_marker(
                        connection,
                        usage_id=document["metadata"]["id"],
                        deduplication_key=document["spec"]["deduplicationKey"],
                        trace_id=document["spec"]["invocation"]["traceId"],
                        span_id=document["spec"]["invocation"]["spanId"],
                    )
                    connection.execute(
                        """
                        DELETE FROM iip.ai_usage_records
                        WHERE tenant_id = %s AND usage_record_id = %s
                        """,
                        ("local", document["metadata"]["id"]),
                    )
            finally:
                mutation_complete.set()
            rows = result.result(timeout=5)

        self.assertEqual(rows[0][0]["metadata"]["id"], document["metadata"]["id"])
        with self.assertRaises(AiHistoryRetiredError):
            self.store.list_ai_allocation_rows(ACTOR, _allocation_query())

    def test_shared_history_lock_is_bounded_and_fails_closed(self) -> None:
        assert DATABASE_URL is not None and psycopg is not None
        with psycopg.connect(DATABASE_URL) as blocker:
            blocker.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("iip.ai-history\x1flocal",),
            )
            started = time.monotonic()
            with self.assertRaisesRegex(PersistenceError, "^storage.unavailable$"):
                self.store.get_ai_history_availability(
                    ACTOR,
                    AiHistoryAvailabilityQuery(START, END),
                )
            self.assertLess(time.monotonic() - started, 4.0)

    def test_availability_preserves_canonical_microsecond_bounds(self) -> None:
        assert DATABASE_URL is not None and psycopg is not None
        with psycopg.connect(DATABASE_URL) as connection:
            self._insert_marker(
                connection,
                started_at="2026-09-05T10:00:00.500000Z",
            )

        state = self.store.get_ai_history_availability(
            ACTOR,
            AiHistoryAvailabilityQuery(
                "2026-09-05T10:00:00.499999Z",
                "2026-09-05T10:00:00.500001Z",
            ),
        )

        self.assertEqual(state.retired_usage_records, 1)


if __name__ == "__main__":
    unittest.main()
