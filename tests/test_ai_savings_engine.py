from __future__ import annotations

import copy
import json
import os
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

try:
    import psycopg

    from iip.adapters.postgres import PostgresResourceStore
except ModuleNotFoundError:
    psycopg = None
    PostgresResourceStore = None

from iip.adapters.memory import InMemoryResourceStore
from iip.application.calculate_ai_cost import AiCostCalculationService
from iip.application.evaluate_ai_savings import (
    AiSavingsConfigurationError,
    AiSavingsEvaluationService,
    InvalidAiSavingsInputError,
    validate_ai_savings_finding,
    validate_ai_savings_source_binding,
    validate_context_growth_profile,
    validate_context_growth_source_binding,
    validate_retry_amplification_profile,
)
from iip.application.ports import (
    ActorContext,
    AiEconomicsMeasurement,
    AiRetryMeasurement,
    PersistenceError,
)
from iip.bootstrap import _ai_savings_engine_configuration_from_env
from iip.domain.models import PlatformEvent


ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ.get("IIP_TEST_DATABASE_URL")
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


class MutableClock:
    def __init__(self, value: str = "2026-09-05T11:00:00Z") -> None:
        self.value = value

    def now(self) -> str:
        return self.value


class RecordingAiEconomicsSink:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.measurements: list[AiEconomicsMeasurement] = []
        self.retry_measurements: list[AiRetryMeasurement] = []

    def record_ai_economics(self, measurement: AiEconomicsMeasurement) -> None:
        if self.fail:
            raise RuntimeError("export unavailable")
        self.measurements.append(measurement)

    def record_ai_retry(self, measurement: AiRetryMeasurement) -> None:
        if self.fail:
            raise RuntimeError("export unavailable")
        self.retry_measurements.append(measurement)


def fixture(name: str) -> dict:
    return json.loads(
        (ROOT / "contracts" / "examples" / f"{name}.json").read_text()
    )


def profile(**changes: object) -> dict:
    document: dict[str, object] = {
        "profileId": "support-assistant-context",
        "tenantId": "local",
        "catalogId": "apc_11111111111111111111111111111111",
        "costEngineVersion": "0.1.0",
        "scope": {
            "provider": "aws.bedrock",
            "modelId": "example.foundation-model-v1:0",
            "region": "us-east-1",
            "serviceName": "support-assistant",
            "deploymentEnvironment": "production",
        },
        "baselineWindow": {
            "start": "2026-09-03T10:00:00Z",
            "end": "2026-09-04T10:00:00Z",
        },
        "currentWindow": {
            "start": "2026-09-04T10:00:00Z",
            "end": "2026-09-05T10:00:00Z",
        },
        "minimumRequestsPerWindow": 2,
        "growthThresholdBasisPoints": 2500,
        "maxRecordsPerWindow": 100,
        "evaluationGraceSeconds": 300,
    }
    document.update(changes)
    return document


def retry_profile(**changes: object) -> dict:
    document = profile(
        profileId="support-assistant-retries",
        ruleId="retry-amplification",
    )
    document.pop("growthThresholdBasisPoints")
    document.update(
        {
            "retryRateIncreaseThresholdBasisPoints": 2500,
            "minimumCurrentRetryRateBasisPoints": 2500,
        }
    )
    document.update(changes)
    return document


def usage_record(
    index: int,
    started_at: str,
    input_tokens: int,
    *,
    cache_read: int = 0,
    retry_count: int | None = 0,
    tenant_id: str = "local",
) -> dict:
    document = copy.deepcopy(fixture("ai-usage-record"))
    document["metadata"].update(
        {
            "id": f"aiu_{index:032x}",
            "tenantId": tenant_id,
            "recordedAt": "2026-09-05T10:00:01Z",
        }
    )
    document["spec"]["invocation"].update(
        {
            "startedAt": started_at,
            "traceId": f"{index:032x}",
            "spanId": f"{index:016x}",
            "requestIdHash": f"sha256:{index:064x}",
            "outcome": "success",
        }
    )
    if retry_count is None:
        document["spec"]["invocation"].pop("retryCount", None)
    else:
        document["spec"]["invocation"]["retryCount"] = retry_count
    document["spec"]["attribution"]["deploymentEnvironment"] = "production"
    document["spec"]["usage"].update(
        {
            "inputTokens": input_tokens,
            "outputTokens": 100,
            "cacheReadInputTokens": cache_read,
            "cacheWriteInputTokens": 0,
            "reasoningOutputTokens": 0,
            "completeness": "complete",
            "missingFields": [],
        }
    )
    document["spec"]["deduplicationKey"] = f"sha256:{index:064x}"
    return document


def usage_event(document: dict) -> PlatformEvent:
    metadata = document["metadata"]
    spec = document["spec"]
    invocation = spec["invocation"]
    attribution = spec["attribution"]
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
            "modelId": invocation.get("responseModel", invocation["requestModel"]),
            "serviceName": attribution["serviceName"],
            "outcome": invocation["outcome"],
        },
    )


def seed_usage(store: object, documents: list[dict]) -> None:
    channel_id = documents[0]["spec"]["source"]["channelId"]
    actor = ActorContext(
        "ai-usage-channel:" + channel_id,
        documents[0]["metadata"]["tenantId"],
        ("telemetry-ingest",),
    )
    store.commit_usage_batch(  # type: ignore[attr-defined]
        actor,
        tuple(documents),
        tuple(usage_event(item) for item in documents),
    )


def seed_standard_cohorts(
    store: object,
    clock: MutableClock,
    *,
    baseline_tokens: tuple[int, ...] = (1200, 1200),
    current_tokens: tuple[int, ...] = (2400, 2400),
    cache_read: int = 0,
    calculate_cost: bool = True,
) -> list[dict]:
    documents = [
        usage_record(index + 1, f"2026-09-03T{12 + index}:00:00Z", tokens, cache_read=cache_read)
        for index, tokens in enumerate(baseline_tokens)
    ] + [
        usage_record(index + 101, f"2026-09-04T{12 + index}:00:00Z", tokens, cache_read=cache_read)
        for index, tokens in enumerate(current_tokens)
    ]
    seed_usage(store, documents)
    if calculate_cost:
        service = AiCostCalculationService(
            store,  # type: ignore[arg-type]
            clock,
            (fixture("ai-price-catalog"),),
            allow_test_fixtures=True,
        )
        result = service.run_once("local", "cost-test")
        if result.processed != len(documents):
            raise AssertionError("cost fixture did not fully calculate")
    return documents


class AiSavingsRuleTests(unittest.TestCase):
    def test_qualified_context_growth_is_source_bound_and_idempotent(self) -> None:
        store = InMemoryResourceStore()
        clock = MutableClock()
        documents = seed_standard_cohorts(store, clock)
        telemetry = RecordingAiEconomicsSink()
        service = AiSavingsEvaluationService(
            store,
            clock,
            (profile(),),
            telemetry_sink=telemetry,
        )

        first = service.run_once("local", "savings-test")
        clock.value = "2026-09-05T11:05:00Z"
        second = service.run_once("local", "savings-test")

        self.assertEqual(first.qualified, 1)
        self.assertEqual(first.failures, 0)
        self.assertEqual(second.qualified, 1)
        self.assertEqual(len(telemetry.measurements), 2)
        measurement = telemetry.measurements[0]
        self.assertEqual(measurement.request_count, 2)
        self.assertEqual(measurement.input_tokens, 4800)
        self.assertEqual(measurement.output_tokens, 200)
        self.assertEqual(measurement.priced_requests, 2)
        self.assertEqual(measurement.calculated_cost_subunits, 17_400_000)
        self.assertEqual(measurement.baseline_input_tokens_per_request, 1200)
        self.assertEqual(measurement.current_input_tokens_per_request, 2400)
        self.assertEqual(measurement.context_growth_change_basis_points, 10_000)
        self.assertEqual(measurement.evaluation_status, "qualified")
        self.assertEqual(measurement.finding_count, 1)
        self.assertEqual(measurement.potential_savings_subunits, 7_200_000)
        self.assertEqual(len(store.ai_savings_findings), 1)
        finding = store.ai_savings_findings[0]
        observation = finding["spec"]["observations"][0]
        savings = finding["spec"]["potentialSavings"]
        self.assertEqual(observation["baseline"], {"value": 1200, "sampleCount": 2})
        self.assertEqual(observation["current"], {"value": 2400, "sampleCount": 2})
        self.assertEqual(observation["changeBasisPoints"], 10_000)
        self.assertEqual(finding["spec"]["finding"]["severity"], "medium")
        self.assertEqual(finding["spec"]["finding"]["confidenceBasisPoints"], 6000)
        self.assertEqual(savings["calculation"]["excessQuantity"], 2400)
        self.assertEqual(savings["amountSubunits"], 7_200_000)
        self.assertEqual(len(savings["costRecordRefs"]), 4)
        self.assertEqual(len(finding["spec"]["evidenceRefs"]), 5)
        self.assertEqual(
            len(
                [
                    event
                    for event in store.events
                    if event.event_type
                    == "io.iip.ai.savings-finding-recorded.v1"
                ]
            ),
            1,
        )
        encoded_event = json.dumps(store.events[-1].to_dict())
        for excluded in ("7200000", "2400", "priceSubunits"):
            self.assertNotIn(excluded, encoded_event)
        validate_ai_savings_finding(finding)
        costs = tuple(value[1] for value in store._ai_costs.values())
        validate_context_growth_source_binding(finding, tuple(documents), costs)
        schema = json.loads(
            (ROOT / "contracts/schemas/ai-savings-finding.schema.json").read_text()
        )
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                schema, finding, label="calculated AI saving"
            ),
            [],
        )

    def test_telemetry_failure_never_changes_committed_rule_outcome(self) -> None:
        store = InMemoryResourceStore()
        clock = MutableClock()
        seed_standard_cohorts(store, clock)
        result = AiSavingsEvaluationService(
            store,
            clock,
            (profile(),),
            telemetry_sink=RecordingAiEconomicsSink(fail=True),
        ).run_once("local", "savings-test")

        self.assertEqual(result.qualified, 1)
        self.assertEqual(result.failures, 0)
        self.assertEqual(len(store.ai_savings_findings), 1)

    def test_retry_amplification_is_source_bound_without_invented_money(self) -> None:
        store = InMemoryResourceStore()
        clock = MutableClock()
        documents = [
            usage_record(1, "2026-09-03T12:00:00Z", 1200, retry_count=0),
            usage_record(2, "2026-09-03T13:00:00Z", 1200, retry_count=0),
            usage_record(101, "2026-09-04T12:00:00Z", 1200, retry_count=2),
            usage_record(102, "2026-09-04T13:00:00Z", 1200, retry_count=1),
        ]
        seed_usage(store, documents)
        telemetry = RecordingAiEconomicsSink()
        service = AiSavingsEvaluationService(
            store,
            clock,
            (retry_profile(),),
            telemetry_sink=telemetry,
        )

        first = service.run_once("local", "retry-test")
        clock.value = "2026-09-05T11:05:00Z"
        second = service.run_once("local", "retry-test")

        self.assertEqual((first.qualified, first.failures), (1, 0))
        self.assertEqual((second.qualified, second.failures), (1, 0))
        self.assertEqual(len(store.ai_savings_findings), 1)
        finding = store.ai_savings_findings[0]
        self.assertEqual(finding["spec"]["rule"]["id"], "retry-amplification")
        self.assertEqual(
            finding["spec"]["potentialSavings"],
            {
                "status": "unresolved",
                "reasonCode": "retry-billing-unproven",
                "period": finding["spec"]["scope"]["currentWindow"],
            },
        )
        observation = finding["spec"]["observations"][0]
        self.assertEqual(observation["baseline"]["value"], 0)
        self.assertEqual(observation["current"]["value"], 10_000)
        self.assertEqual(observation["changeBasisPoints"], 10_000)
        self.assertNotIn("amountSubunits", json.dumps(finding))
        self.assertFalse(
            any(
                reference["type"] == "ai-cost-record"
                for reference in finding["spec"]["evidenceRefs"]
            )
        )
        validate_ai_savings_source_binding(finding, tuple(documents), ())
        self.assertEqual(len(telemetry.retry_measurements), 2)
        measurement = telemetry.retry_measurements[0]
        self.assertEqual(measurement.current_operations, 2)
        self.assertEqual(measurement.current_retry_fact_operations, 2)
        self.assertEqual(measurement.current_retrying_operations, 2)
        self.assertEqual(measurement.current_excess_attempts, 3)
        self.assertEqual(measurement.retry_rate_increase_basis_points, 10_000)
        self.assertEqual(measurement.finding_count, 1)

    def test_retry_rule_requires_complete_retry_facts_and_thresholds(self) -> None:
        for name, retry_counts, expected in (
            ("missing", (0, 0, 1, None), "unsupported"),
            ("below", (0, 0, 0, 0), "below_threshold"),
        ):
            with self.subTest(name=name):
                store = InMemoryResourceStore()
                clock = MutableClock()
                documents = [
                    usage_record(1, "2026-09-03T12:00:00Z", 1200, retry_count=retry_counts[0]),
                    usage_record(2, "2026-09-03T13:00:00Z", 1200, retry_count=retry_counts[1]),
                    usage_record(101, "2026-09-04T12:00:00Z", 1200, retry_count=retry_counts[2]),
                    usage_record(102, "2026-09-04T13:00:00Z", 1200, retry_count=retry_counts[3]),
                ]
                seed_usage(store, documents)
                telemetry = RecordingAiEconomicsSink()
                result = AiSavingsEvaluationService(
                    store,
                    clock,
                    (retry_profile(),),
                    telemetry_sink=telemetry,
                ).run_once("local", "retry-test")
                self.assertEqual(getattr(result, expected), 1)
                self.assertEqual(result.qualified, 0)
                self.assertEqual(store.ai_savings_findings, ())
                self.assertEqual(len(telemetry.retry_measurements), 1)

    def test_unresolved_cost_exports_coverage_without_inventing_zero(self) -> None:
        store = InMemoryResourceStore()
        clock = MutableClock()
        seed_standard_cohorts(store, clock, calculate_cost=False)
        telemetry = RecordingAiEconomicsSink()

        result = AiSavingsEvaluationService(
            store,
            clock,
            (profile(),),
            telemetry_sink=telemetry,
        ).run_once("local", "savings-test")

        self.assertEqual(result.unresolved, 1)
        self.assertEqual(len(telemetry.measurements), 1)
        measurement = telemetry.measurements[0]
        self.assertEqual(measurement.pending_cost_requests, 2)
        self.assertEqual(measurement.priced_requests, 0)
        self.assertIsNone(measurement.calculated_cost_subunits)
        self.assertIsNone(measurement.currency)
        self.assertEqual(measurement.finding_count, 0)
        self.assertIsNone(measurement.potential_savings_subunits)

    def test_rule_does_not_emit_without_complete_eligible_evidence(self) -> None:
        cases = (
            ("pending", {}, "2026-09-05T10:04:59Z", "pending"),
            (
                "insufficient",
                {"baseline_tokens": (1200,), "current_tokens": (2400,)},
                None,
                "insufficient",
            ),
            ("unresolved", {"calculate_cost": False}, None, "unresolved"),
            ("unsupported", {"cache_read": 100}, None, "unsupported"),
            ("below_threshold", {"current_tokens": (1300, 1300)}, None, "below_threshold"),
        )
        for name, seed_options, now, expected_field in cases:
            with self.subTest(name=name):
                store = InMemoryResourceStore()
                clock = MutableClock(now or "2026-09-05T11:00:00Z")
                seed_standard_cohorts(store, clock, **seed_options)
                result = AiSavingsEvaluationService(
                    store, clock, (profile(),)
                ).run_once("local", "savings-test")
                self.assertEqual(getattr(result, expected_field), 1)
                self.assertEqual(result.qualified, 0)
                self.assertEqual(store.ai_savings_findings, ())

    def test_unpriced_cost_is_unresolved_and_reports_coverage(self) -> None:
        store = InMemoryResourceStore()
        clock = MutableClock()
        documents = seed_standard_cohorts(store, clock, calculate_cost=False)
        catalog = copy.deepcopy(fixture("ai-price-catalog"))
        catalog["spec"]["entries"][0]["modelId"] = "other.foundation-model-v1:0"
        cost = AiCostCalculationService(
            store,
            clock,
            (catalog,),
            allow_test_fixtures=True,
        ).run_once("local", "cost-test")
        self.assertEqual(cost.unpriced, len(documents))
        telemetry = RecordingAiEconomicsSink()

        result = AiSavingsEvaluationService(
            store,
            clock,
            (profile(),),
            telemetry_sink=telemetry,
        ).run_once("local", "savings-test")

        self.assertEqual(result.unresolved, 1)
        self.assertEqual(result.failures, 0)
        self.assertEqual(len(telemetry.measurements), 1)
        measurement = telemetry.measurements[0]
        self.assertEqual(measurement.unpriced_requests, 2)
        self.assertEqual(measurement.priced_requests, 0)
        self.assertIsNone(measurement.calculated_cost_subunits)
        self.assertIsNone(measurement.currency)
        self.assertIsNone(measurement.currency_scale)

    def test_profile_is_closed_adjacent_equal_duration_and_bounded(self) -> None:
        valid = validate_context_growth_profile(profile())
        self.assertEqual(valid.minimum_requests, 2)
        for mutation in (
            {"unknown": True},
            {"minimumRequestsPerWindow": 1},
            {"maxRecordsPerWindow": 101},
            {"costEngineVersion": "9.9.9"},
            {
                "currentWindow": {
                    "start": "2026-09-04T11:00:00Z",
                    "end": "2026-09-05T10:00:00Z",
                }
            },
        ):
            with self.subTest(mutation=mutation):
                with self.assertRaisesRegex(
                    AiSavingsConfigurationError, "ai.savings.profile.invalid"
                ):
                    validate_context_growth_profile(profile(**mutation))
        validated_retry = validate_retry_amplification_profile(retry_profile())
        self.assertEqual(validated_retry.minimum_current_retry_rate_basis_points, 2500)
        for mutation in (
            {"minimumCurrentRetryRateBasisPoints": 0},
            {"retryRateIncreaseThresholdBasisPoints": 10_001},
            {"ruleId": "context-growth"},
        ):
            with self.subTest(retry_mutation=mutation), self.assertRaisesRegex(
                AiSavingsConfigurationError,
                "ai.savings.profile.invalid",
            ):
                validate_retry_amplification_profile(retry_profile(**mutation))

    def test_storage_rejects_cross_tenant_and_forged_finding(self) -> None:
        store = InMemoryResourceStore()
        clock = MutableClock()
        seed_standard_cohorts(store, clock)
        service = AiSavingsEvaluationService(store, clock, (profile(),))
        self.assertEqual(service.run_once("local", "savings-test").qualified, 1)
        finding = copy.deepcopy(store.ai_savings_findings[0])
        event = next(
            event
            for event in store.events
            if event.event_type == "io.iip.ai.savings-finding-recorded.v1"
        )
        finding["spec"]["potentialSavings"]["amountSubunits"] += 1
        with self.assertRaisesRegex(
            (InvalidAiSavingsInputError, PersistenceError),
            "ai.savings|storage.request.invalid",
        ):
            store.commit_ai_savings_batch(
                ActorContext(
                    "ai-savings-worker:savings-test",
                    "local",
                    ("ai-savings:evaluate",),
                ),
                (finding,),
                (event,),
            )
        with self.assertRaisesRegex(
            AiSavingsConfigurationError, "ai.savings.profile.missing"
        ):
            service.run_once("other-tenant", "savings-test")


class AiSavingsCompositionTests(unittest.TestCase):
    def test_enabled_profiles_require_cost_catalog_and_exact_worker_tenants(self) -> None:
        catalogs = (fixture("ai-price-catalog"),)
        wrapper = json.dumps({"profiles": [profile()]})
        environment = {
            "IIP_AI_SAVINGS_ENGINE_ENABLED": "true",
            "IIP_AI_SAVINGS_PROFILES_JSON": wrapper,
            "IIP_WORKER_TENANTS": "local",
        }
        with patch.dict(os.environ, environment, clear=False):
            configured = _ai_savings_engine_configuration_from_env(catalogs)
            self.assertEqual(configured, (profile(),))
            with self.assertRaisesRegex(
                AiSavingsConfigurationError,
                "ai.savings.configuration.required",
            ):
                _ai_savings_engine_configuration_from_env(None)
        with patch.dict(
            os.environ,
            {**environment, "IIP_WORKER_TENANTS": "other-tenant"},
            clear=False,
        ):
            with self.assertRaisesRegex(
                AiSavingsConfigurationError, "ai.savings.tenants.invalid"
            ):
                _ai_savings_engine_configuration_from_env(catalogs)


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None,
    "set IIP_TEST_DATABASE_URL to run PostgreSQL AI savings tests",
)
class AiSavingsPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        assert DATABASE_URL is not None
        assert PostgresResourceStore is not None
        self.store = PostgresResourceStore(DATABASE_URL)
        self.store.migrate()
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                TRUNCATE iip.event_outbox, iip.ai_savings_findings,
                    iip.ai_usage_attributions, iip.ai_attribution_policies,
                    iip.ai_cost_records, iip.ai_price_catalogs,
                    iip.ai_usage_records, iip.event_log
                RESTART IDENTITY CASCADE
                """
            )
        self.clock = MutableClock()

    def test_finding_event_and_outbox_are_durable_and_idempotent(self) -> None:
        seed_standard_cohorts(self.store, self.clock)
        service = AiSavingsEvaluationService(self.store, self.clock, (profile(),))
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                executor.map(
                    lambda worker: service.run_once("local", worker),
                    ("savings-a", "savings-b"),
                )
            )
        self.assertTrue(all(item.qualified == 1 for item in results))
        self.assertTrue(all(item.failures == 0 for item in results))
        with psycopg.connect(DATABASE_URL) as connection:
            finding_count = connection.execute(
                "SELECT count(*) FROM iip.ai_savings_findings WHERE tenant_id = 'local'"
            ).fetchone()[0]
            event_count = connection.execute(
                """
                SELECT count(*)
                FROM iip.event_log
                WHERE tenant_id = 'local'
                  AND event_type = 'io.iip.ai.savings-finding-recorded.v1'
                """
            ).fetchone()[0]
            outbox_count = connection.execute(
                """
                SELECT count(*)
                FROM iip.event_outbox AS outbox
                JOIN iip.event_log AS event
                  ON event.tenant_id = outbox.tenant_id
                 AND event.event_offset = outbox.event_offset
                WHERE event.event_type = 'io.iip.ai.savings-finding-recorded.v1'
                """
            ).fetchone()[0]
        self.assertEqual((finding_count, event_count, outbox_count), (1, 1, 1))

    def test_retry_finding_is_durable_source_bound_and_has_no_cost_refs(self) -> None:
        documents = [
            usage_record(1, "2026-09-03T12:00:00Z", 1200, retry_count=0),
            usage_record(2, "2026-09-03T13:00:00Z", 1200, retry_count=0),
            usage_record(101, "2026-09-04T12:00:00Z", 1200, retry_count=2),
            usage_record(102, "2026-09-04T13:00:00Z", 1200, retry_count=1),
        ]
        seed_usage(self.store, documents)
        service = AiSavingsEvaluationService(
            self.store,
            self.clock,
            (retry_profile(),),
        )

        result = service.run_once("local", "retry-postgres")

        self.assertEqual((result.qualified, result.failures), (1, 0))
        with psycopg.connect(DATABASE_URL) as connection:
            row = connection.execute(
                """
                SELECT rule_id, document
                FROM iip.ai_savings_findings
                WHERE tenant_id = 'local'
                """
            ).fetchone()
        self.assertEqual(row[0], "retry-amplification")
        self.assertEqual(row[1]["spec"]["potentialSavings"]["status"], "unresolved")
        self.assertNotIn("costRecordRefs", row[1]["spec"]["potentialSavings"])


if __name__ == "__main__":
    unittest.main()
