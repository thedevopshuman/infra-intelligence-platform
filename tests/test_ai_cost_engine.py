from __future__ import annotations

import copy
import json
import os
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

try:
    import psycopg

    from iip.adapters.postgres import PostgresResourceStore
except ModuleNotFoundError:
    psycopg = None
    PostgresResourceStore = None

from iip.adapters.ai_price_catalogs import ai_price_catalogs_from_json
from iip.adapters.memory import InMemoryResourceStore
from iip.application.calculate_ai_cost import (
    ENGINE_VERSION,
    AiCostCalculationService,
    AiCostConfigurationError,
    InvalidAiCostInputError,
    calculate_ai_cost_record,
    validate_ai_price_catalog,
)
from iip.application.ports import ActorContext, PersistenceError
from iip.bootstrap import _ai_cost_engine_configuration_from_env, build_local_runtime
from iip.domain.models import PlatformEvent
from iip.surfaces.worker import run_ai_cost_pass


ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ.get("IIP_TEST_DATABASE_URL")
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


class MutableClock:
    def __init__(self, value: str = "2026-09-05T10:00:03Z") -> None:
        self.value = value

    def now(self) -> str:
        return self.value


def fixture(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / f"{name}.json").read_text())


def usage_event(record: dict) -> PlatformEvent:
    metadata = record["metadata"]
    spec = record["spec"]
    invocation = spec["invocation"]
    attribution = spec["attribution"]
    model_id = invocation.get("responseModel", invocation["requestModel"])
    digest = spec["deduplicationKey"].removeprefix("sha256:")
    return PlatformEvent(
        event_id="ai-usage-" + digest,
        event_type="io.iip.ai.usage-recorded.v1",
        source="urn:iip:ai-usage:" + spec["source"]["integrationId"],
        time=metadata["recordedAt"],
        subject=metadata["id"],
        tenant_id=metadata["tenantId"],
        correlation_id=invocation["traceId"],
        data={
            "usageRecordId": metadata["id"],
            "deduplicationKey": spec["deduplicationKey"],
            "provider": invocation["provider"],
            "modelId": model_id,
            "serviceName": attribution["serviceName"],
            "outcome": invocation["outcome"],
        },
    )


def seed_usage(store: InMemoryResourceStore, record: dict | None = None) -> dict:
    document = record or fixture("ai-usage-record")
    actor = ActorContext(
        "ai-usage-channel:" + document["spec"]["source"]["channelId"],
        document["metadata"]["tenantId"],
        ("telemetry-ingest",),
    )
    store.commit_usage_batch(actor, (document,), (usage_event(document),))
    return document


class AiCostCalculationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog_document = fixture("ai-price-catalog")
        self.catalog = validate_ai_price_catalog(self.catalog_document)
        self.usage = fixture("ai-usage-record")

    def test_priced_result_uses_non_overlapping_integer_lines(self) -> None:
        record, event = calculate_ai_cost_record(
            self.catalog,
            self.usage,
            calculated_at="2026-09-05T10:00:03Z",
        )
        result = record["spec"]["result"]
        self.assertEqual(result["costStatus"], "priced")
        self.assertEqual(result["totalSubunits"], 15_735_000)
        self.assertEqual(
            [line["billableQuantity"] for line in result["lines"]],
            [2100, 200, 100, 450, 150],
        )
        self.assertEqual(
            result["warnings"],
            ["calculated-cost-not-invoice", "test-fixture-pricing"],
        )
        self.assertEqual(event.event_type, "io.iip.ai.cost-calculated.v1")
        self.assertEqual(event.causation_id, self.usage["metadata"]["id"])
        schema = json.loads(
            (ROOT / "contracts" / "schemas" / "ai-cost-record.schema.json").read_text()
        )
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                schema,
                record,
                label="calculated cost",
            ),
            [],
        )
        self.assertEqual(record, fixture("ai-cost-record"))
        self.assertEqual(event.to_dict(), fixture("ai-cost-calculated-event"))

    def test_half_up_rounding_and_response_model_selection(self) -> None:
        usage = copy.deepcopy(self.usage)
        usage["spec"]["invocation"]["responseModel"] = "response-model"
        usage["spec"]["usage"].update(
            {
                "inputTokens": 1,
                "outputTokens": 0,
                "cacheReadInputTokens": 0,
                "cacheWriteInputTokens": 0,
                "reasoningOutputTokens": 0,
            }
        )
        catalog = copy.deepcopy(self.catalog_document)
        entry = catalog["spec"]["entries"][0]
        entry["modelId"] = "response-model"
        for rate in entry["rates"].values():
            rate["priceSubunitsPerMillionTokens"] = 0
        entry["rates"]["uncachedInputTokens"][
            "priceSubunitsPerMillionTokens"
        ] = 500_000
        record, _ = calculate_ai_cost_record(
            validate_ai_price_catalog(catalog),
            usage,
            calculated_at="2026-09-05T10:00:03Z",
        )
        self.assertEqual(record["spec"]["result"]["totalSubunits"], 1)

    def test_no_match_overlap_partial_breakdown_and_overflow_are_explicit(self) -> None:
        no_match = copy.deepcopy(self.usage)
        no_match["spec"]["invocation"]["region"] = "eu-west-1"
        record, _ = calculate_ai_cost_record(
            self.catalog,
            no_match,
            calculated_at="2026-09-05T10:00:03Z",
        )
        self.assertEqual(record["spec"]["result"]["reasonCode"], "no-catalog-match")
        self.assertNotIn("totalSubunits", record["spec"]["result"])

        overlap = copy.deepcopy(self.catalog_document)
        second = copy.deepcopy(overlap["spec"]["entries"][0])
        second["id"] = "aws-bedrock.overlapping-price"
        overlap["spec"]["entries"].append(second)
        record, _ = calculate_ai_cost_record(
            validate_ai_price_catalog(overlap),
            self.usage,
            calculated_at="2026-09-05T10:00:03Z",
        )
        self.assertEqual(record["spec"]["result"]["costStatus"], "ambiguous")

        partial = copy.deepcopy(self.usage)
        del partial["spec"]["usage"]["reasoningOutputTokens"]
        partial["spec"]["usage"]["completeness"] = "partial"
        partial["spec"]["usage"]["missingFields"] = ["reasoningOutputTokens"]
        record, _ = calculate_ai_cost_record(
            self.catalog,
            partial,
            calculated_at="2026-09-05T10:00:03Z",
        )
        self.assertEqual(record["spec"]["result"]["reasonCode"], "missing-usage")
        self.assertEqual(record["spec"]["result"]["coverage"], "partial")

        invalid = copy.deepcopy(self.usage)
        invalid["spec"]["usage"]["inputTokens"] = 1
        record, _ = calculate_ai_cost_record(
            self.catalog,
            invalid,
            calculated_at="2026-09-05T10:00:03Z",
        )
        self.assertEqual(record["spec"]["result"]["reasonCode"], "invalid-breakdown")

        overflow_catalog = copy.deepcopy(self.catalog_document)
        overflow_catalog["spec"]["entries"][0]["rates"]["uncachedInputTokens"][
            "priceSubunitsPerMillionTokens"
        ] = 9_007_199_254_740_991
        overflow_usage = copy.deepcopy(self.usage)
        overflow_usage["spec"]["usage"].update(
            {
                "inputTokens": 9_007_199_254_740_991,
                "cacheReadInputTokens": 0,
                "cacheWriteInputTokens": 0,
            }
        )
        record, _ = calculate_ai_cost_record(
            validate_ai_price_catalog(overflow_catalog),
            overflow_usage,
            calculated_at="2026-09-05T10:00:03Z",
        )
        self.assertEqual(record["spec"]["result"]["reasonCode"], "unsupported-meter")
        self.assertIn("cost-overflow", record["spec"]["result"]["warnings"])

    def test_catalog_and_stored_usage_privacy_fields_are_defensively_validated(
        self,
    ) -> None:
        credentialed = copy.deepcopy(self.catalog_document)
        credentialed["spec"]["source"]["locator"] = (
            "https://operator:credential@pricing.example.test/catalog.json"
        )
        with self.assertRaisesRegex(
            AiCostConfigurationError,
            "ai.cost.catalog.invalid",
        ):
            validate_ai_price_catalog(credentialed)

        for value in (True, 100_001):
            with self.subTest(value=value):
                usage = copy.deepcopy(self.usage)
                usage["spec"]["privacy"]["droppedAttributeCount"] = value
                with self.assertRaisesRegex(
                    InvalidAiCostInputError,
                    "ai.cost.usage.invalid",
                ):
                    calculate_ai_cost_record(
                        self.catalog,
                        usage,
                        calculated_at="2026-09-05T10:00:03Z",
                    )

        invalid_trace = copy.deepcopy(self.usage)
        invalid_trace["spec"]["invocation"]["traceId"] = "not-a-trace-id"
        with self.assertRaisesRegex(
            InvalidAiCostInputError,
            "ai.cost.usage.invalid",
        ):
            calculate_ai_cost_record(
                self.catalog,
                invalid_trace,
                calculated_at="2026-09-05T10:00:03Z",
            )

        with self.assertRaisesRegex(
            InvalidAiCostInputError,
            "ai.cost.time.invalid",
        ):
            calculate_ai_cost_record(
                self.catalog,
                self.usage,
                calculated_at="2026-09-05T09:59:59Z",
            )

        inconsistent = copy.deepcopy(self.usage)
        inconsistent["spec"]["usage"]["completeness"] = "partial"
        with self.assertRaisesRegex(
            InvalidAiCostInputError,
            "ai.cost.usage.invalid",
        ):
            calculate_ai_cost_record(
                self.catalog,
                inconsistent,
                calculated_at="2026-09-05T10:00:03Z",
            )


class AiCostLedgerAndWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryResourceStore()
        self.usage = seed_usage(self.store)
        self.catalog = fixture("ai-price-catalog")
        self.clock = MutableClock()

    def test_worker_registers_catalog_commits_event_and_is_idempotent(self) -> None:
        service = AiCostCalculationService(
            self.store,
            self.clock,
            (self.catalog,),
            allow_test_fixtures=True,
        )
        result = service.run_once("local", "worker-1")
        self.assertEqual(
            (result.processed, result.priced, result.unpriced, result.ambiguous),
            (1, 1, 0, 0),
        )
        self.assertEqual(len(self.store.events), 2)
        replay = service.run_once("local", "worker-2")
        self.assertEqual(replay.processed, 0)
        self.assertEqual(len(self.store.events), 2)

    def test_fixture_gate_tenant_scope_and_value_minimized_worker_pass(self) -> None:
        with self.assertRaisesRegex(
            AiCostConfigurationError, "ai.cost.test-fixture.prohibited"
        ):
            AiCostCalculationService(self.store, self.clock, (self.catalog,))
        service = AiCostCalculationService(
            self.store,
            self.clock,
            (self.catalog,),
            allow_test_fixtures=True,
        )
        summary = run_ai_cost_pass(service, ("local", "other"), "worker-1")
        self.assertEqual(summary.processed, 1)
        self.assertEqual(summary.failures, 1)

        actor = ActorContext("ai-cost-worker:worker-1", "other", ("ai-cost:calculate",))
        with self.assertRaisesRegex(PersistenceError, "storage.request.invalid"):
            self.store.register_price_catalog(actor, self.catalog)

    def test_changed_catalog_or_cost_identity_conflicts(self) -> None:
        actor = ActorContext("ai-cost-worker:worker-1", "local", ("ai-cost:calculate",))
        self.store.register_price_catalog(actor, self.catalog)
        changed_catalog = copy.deepcopy(self.catalog)
        changed_catalog["spec"]["entries"][0]["rates"]["uncachedInputTokens"][
            "priceSubunitsPerMillionTokens"
        ] += 1
        with self.assertRaisesRegex(PersistenceError, "storage.conflict"):
            self.store.register_price_catalog(actor, changed_catalog)

        same_version = copy.deepcopy(self.catalog)
        same_version["metadata"]["id"] = "apc_99999999999999999999999999999999"
        with self.assertRaisesRegex(PersistenceError, "storage.conflict"):
            self.store.register_price_catalog(actor, same_version)

        record, event = calculate_ai_cost_record(
            validate_ai_price_catalog(self.catalog),
            self.usage,
            calculated_at=self.clock.now(),
        )
        self.store.commit_cost_batch(actor, (record,), (event,))
        replay = self.store.commit_cost_batch(actor, (record,), (event,))
        self.assertEqual(replay[0]["metadata"]["id"], record["metadata"]["id"])
        changed = copy.deepcopy(record)
        changed["spec"]["result"]["warnings"].append("unexpected-change")
        changed["spec"]["result"]["warnings"].sort()
        with self.assertRaisesRegex(PersistenceError, "storage.conflict"):
            self.store.commit_cost_batch(actor, (changed,), (event,))

    def test_cost_event_must_remain_bound_to_its_stored_usage(self) -> None:
        actor = ActorContext("ai-cost-worker:worker-1", "local", ("ai-cost:calculate",))
        self.store.register_price_catalog(actor, self.catalog)
        record, event = calculate_ai_cost_record(
            validate_ai_price_catalog(self.catalog),
            self.usage,
            calculated_at=self.clock.now(),
        )
        wrong_currency = copy.deepcopy(record)
        wrong_currency["spec"]["result"]["currency"] = "EUR"
        with self.assertRaisesRegex(PersistenceError, "storage.request.invalid"):
            self.store.commit_cost_batch(actor, (wrong_currency,), (event,))

        changed_event = replace(
            event,
            data={**event.data, "serviceName": "unrelated-service"},
        )
        with self.assertRaisesRegex(PersistenceError, "storage.request.invalid"):
            self.store.commit_cost_batch(actor, (record,), (changed_event,))


class AiCostCompositionTests(unittest.TestCase):
    def test_closed_configuration_and_worker_only_composition(self) -> None:
        catalogs = {"catalogs": [fixture("ai-price-catalog")]}
        self.assertEqual(
            len(ai_price_catalogs_from_json(json.dumps(catalogs))),
            1,
        )
        with self.assertRaisesRegex(
            AiCostConfigurationError, "ai.cost.configuration.invalid"
        ):
            ai_price_catalogs_from_json(json.dumps({"catalogs": [], "extra": True}))
        environment = {
            "IIP_AI_COST_ENGINE_ENABLED": "true",
            "IIP_AI_PRICE_CATALOGS_JSON": json.dumps(catalogs),
            "IIP_AI_PRICE_CATALOG_ALLOW_TEST_FIXTURES": "true",
            "IIP_AI_COST_BATCH_SIZE": "25",
            "IIP_WORKER_TENANTS": "local",
        }
        with patch.dict("os.environ", environment, clear=True):
            parsed, allow, batch_size = _ai_cost_engine_configuration_from_env()
        self.assertEqual(len(parsed or ()), 1)
        self.assertTrue(allow)
        self.assertEqual(batch_size, 25)

        runtime = build_local_runtime(
            ai_cost_catalogs=(fixture("ai-price-catalog"),),
            ai_cost_allow_test_fixtures=True,
        )
        self.assertIsNotNone(runtime.ai_cost_calculation)
        runtime.close()
        disabled = build_local_runtime()
        self.assertIsNone(disabled.ai_cost_calculation)
        disabled.close()

    def test_catalog_tenants_must_exactly_match_worker_enrollment(self) -> None:
        environment = {
            "IIP_AI_COST_ENGINE_ENABLED": "true",
            "IIP_AI_PRICE_CATALOGS_JSON": json.dumps(
                {"catalogs": [fixture("ai-price-catalog")]}
            ),
            "IIP_AI_PRICE_CATALOG_ALLOW_TEST_FIXTURES": "true",
            "IIP_WORKER_TENANTS": "local,other",
        }
        with patch.dict("os.environ", environment, clear=True):
            with self.assertRaisesRegex(
                AiCostConfigurationError, "ai.cost.tenants.invalid"
            ):
                _ai_cost_engine_configuration_from_env()


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and PostgresResourceStore is not None,
    "set IIP_TEST_DATABASE_URL to run PostgreSQL AI cost tests",
)
class AiCostPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        assert DATABASE_URL is not None
        assert psycopg is not None
        assert PostgresResourceStore is not None
        self.store = PostgresResourceStore(DATABASE_URL)
        self.store.migrate()
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                TRUNCATE iip.ai_cost_records, iip.ai_price_catalogs,
                         iip.ai_usage_records, iip.event_outbox, iip.event_log
                RESTART IDENTITY CASCADE
                """
            )

    def test_catalog_usage_cost_and_event_commit_are_durable(self) -> None:
        usage = seed_usage(self.store)  # type: ignore[arg-type]
        service = AiCostCalculationService(
            self.store,
            MutableClock(),
            (fixture("ai-price-catalog"),),
            allow_test_fixtures=True,
        )
        result = service.run_once("local", "postgres-worker")
        self.assertEqual((result.processed, result.priced), (1, 1))
        self.assertEqual(service.run_once("local", "postgres-worker").processed, 0)
        assert psycopg is not None
        assert DATABASE_URL is not None
        with psycopg.connect(DATABASE_URL) as connection:
            row = connection.execute(
                """
                SELECT cost.cost_status, cost.total_subunits,
                       cost.document, catalog.source_hash,
                       event.event_type, outbox.event_offset
                FROM iip.ai_cost_records AS cost
                JOIN iip.ai_price_catalogs AS catalog
                  ON catalog.tenant_id = cost.tenant_id
                 AND catalog.catalog_id = cost.catalog_id
                JOIN iip.event_log AS event
                  ON event.tenant_id = cost.tenant_id
                 AND event.document->>'subject' = cost.cost_record_id
                JOIN iip.event_outbox AS outbox
                  ON outbox.tenant_id = event.tenant_id
                 AND outbox.event_offset = event.event_offset
                WHERE cost.tenant_id = 'local'
                  AND cost.usage_record_id = %s
                """,
                (usage["metadata"]["id"],),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "priced")
        self.assertEqual(row[1], 15_735_000)
        self.assertEqual(row[2]["spec"]["result"]["costStatus"], "priced")
        self.assertEqual(row[3], fixture("ai-price-catalog")["spec"]["source"]["contentHash"])
        self.assertEqual(row[4], "io.iip.ai.cost-calculated.v1")
        self.assertIsInstance(row[5], int)

    def test_concurrent_catalog_version_collision_is_a_stable_conflict(self) -> None:
        actor = ActorContext(
            "ai-cost-worker:postgres-worker",
            "local",
            ("ai-cost:calculate",),
        )
        first = fixture("ai-price-catalog")
        second = copy.deepcopy(first)
        second["metadata"]["id"] = "apc_99999999999999999999999999999999"
        barrier = Barrier(2)

        def register(document: dict) -> str:
            barrier.wait()
            try:
                self.store.register_price_catalog(actor, document)
            except PersistenceError as exc:
                return str(exc)
            return "stored"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(executor.map(register, (first, second)))

        self.assertEqual(sorted(outcomes), ["storage.conflict", "stored"])


if __name__ == "__main__":
    unittest.main()
