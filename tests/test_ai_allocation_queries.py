from __future__ import annotations

import copy
import json
import os
import unittest
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

try:
    import psycopg

    from iip.adapters.postgres import PostgresResourceStore
except ModuleNotFoundError:
    psycopg = None
    PostgresResourceStore = None

from infra_intelligence_sdk import AiAllocationReport, Client
from iip.adapters.memory import AllowTenantPolicy, InMemoryResourceStore
from iip.application.attribute_ai_usage import AiAttributionService
from iip.application.calculate_ai_cost import AiCostCalculationService
from iip.application.ports import (
    ActorContext,
    AiAllocationLedgerQuery,
    AiAllocationMeasurement,
)
from iip.application.query_ai_allocations import (
    AiAllocationAuthorizationError,
    AiAllocationQueryError,
    AiAllocationProjectionService,
    AiAllocationReportService,
)
from iip.bootstrap import _ai_allocation_report_configuration_from_env
from iip.domain.models import PlatformEvent
from iip.surfaces.http import ApiHandler


ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ.get("IIP_TEST_DATABASE_URL")


class FixedClock:
    def __init__(self, value: str = "2026-09-05T11:00:00Z") -> None:
        self.value = value

    def now(self) -> str:
        return self.value


def fixture(name: str) -> dict:
    return json.loads(
        (ROOT / "contracts" / "examples" / f"{name}.json").read_text(
            encoding="utf-8"
        )
    )


def usage_record(index: int, started_at: str, *, input_tokens: int = 2400) -> dict:
    document = copy.deepcopy(fixture("ai-usage-record"))
    document["metadata"].update(
        {
            "id": f"aiu_{index:032x}",
            "recordedAt": "2026-09-05T10:59:00Z",
        }
    )
    document["spec"]["invocation"].update(
        {
            "startedAt": started_at,
            "traceId": f"{index:032x}",
            "spanId": f"{index:016x}",
            "requestIdHash": f"sha256:{index:064x}",
        }
    )
    document["spec"]["usage"]["inputTokens"] = input_tokens
    document["spec"]["deduplicationKey"] = f"sha256:{index:064x}"
    return document


def usage_event(document: dict) -> PlatformEvent:
    metadata = document["metadata"]
    spec = document["spec"]
    invocation = spec["invocation"]
    observed = spec["attribution"]
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
            "serviceName": observed["serviceName"],
            "outcome": invocation["outcome"],
        },
    )


def seed_usage(store: InMemoryResourceStore, documents: list[dict]) -> None:
    actor = ActorContext(
        "ai-usage-channel:" + documents[0]["spec"]["source"]["channelId"],
        documents[0]["metadata"]["tenantId"],
        ("telemetry-ingest",),
    )
    store.commit_usage_batch(
        actor,
        tuple(documents),
        tuple(usage_event(item) for item in documents),
    )


class AiAllocationReportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryResourceStore()
        self.clock = FixedClock()
        self.policy = fixture("ai-attribution-policy")
        self.catalog = fixture("ai-price-catalog")
        self.actor = ActorContext("operator", "local", ("platform-admin",))

    def service(self, *, source_record_limit: int = 10_000, policy=None):
        return AiAllocationReportService(
            self.store,
            policy or AllowTenantPolicy(),
            self.clock,
            (self.policy,),
            (self.catalog,),
            allow_test_fixtures=True,
            source_record_limit=source_record_limit,
        )

    def run_engines(self) -> None:
        attribution = AiAttributionService(
            self.store,
            FixedClock("2026-09-05T10:59:01Z"),
            (self.policy,),
            allow_test_fixtures=True,
        )
        cost = AiCostCalculationService(
            self.store,
            FixedClock("2026-09-05T10:59:02Z"),
            (self.catalog,),
            allow_test_fixtures=True,
        )
        while attribution.run_once("local", "allocation-test").processed:
            pass
        while cost.run_once("local", "allocation-test").processed:
            pass

    def report(self, *, group_by: str = "application") -> dict:
        return dict(
            self.service().get(
                self.actor,
                start="2026-09-05T09:00:00Z",
                end="2026-09-05T11:00:00Z",
                group_by=group_by,
            )
        )

    def test_report_groups_exact_attribution_and_calculated_cost(self) -> None:
        seed_usage(self.store, [usage_record(1, "2026-09-05T10:00:00Z")])
        self.run_engines()

        report = self.report()
        spec = report["spec"]
        self.assertEqual(report["metadata"]["tenantId"], "local")
        self.assertEqual(
            spec["coverage"],
            {
                "usageRecords": 1,
                "allocatedRecords": 1,
                "unallocatedRecords": 0,
                "pendingAttributionRecords": 0,
                "pricedRecords": 1,
                "unpricedRecords": 0,
                "ambiguousRecords": 0,
                "pendingCostRecords": 0,
            },
        )
        self.assertEqual(spec["groups"][0]["dimension"]["id"], "support-experience")
        self.assertEqual(spec["groups"][0]["pricedCost"]["totalSubunits"], 15_735_000)
        self.assertEqual(spec["totals"]["inputTokens"], 2400)
        self.assertEqual(spec["scope"]["groupBy"], "application")

    def test_pending_is_explicit_and_not_zero_priced(self) -> None:
        seed_usage(self.store, [usage_record(1, "2026-09-05T10:00:00Z")])

        report = self.report()
        spec = report["spec"]
        self.assertEqual(spec["coverage"]["pendingAttributionRecords"], 1)
        self.assertEqual(spec["coverage"]["pendingCostRecords"], 1)
        self.assertEqual(spec["groups"][0]["allocationStatus"], "pending")
        self.assertNotIn("pricedCost", spec["totals"])

    def test_historical_rename_keeps_distinct_immutable_names(self) -> None:
        old_rule = self.policy["spec"]["rules"][0]
        old_rule["priority"] = 200
        old_rule["effectiveUntil"] = "2026-09-05T10:00:00Z"
        old_rule["allocation"]["application"]["name"] = "Support Classic"
        new_rule = copy.deepcopy(old_rule)
        new_rule["id"] = "support-production-renamed"
        new_rule["priority"] = 100
        new_rule["effectiveFrom"] = "2026-09-05T10:00:00Z"
        del new_rule["effectiveUntil"]
        new_rule["allocation"]["application"]["name"] = "Support Experience"
        self.policy["spec"]["rules"] = [old_rule, new_rule]
        seed_usage(
            self.store,
            [
                usage_record(1, "2026-09-05T09:30:00Z"),
                usage_record(2, "2026-09-05T10:30:00Z"),
            ],
        )
        self.run_engines()

        report = self.report()
        groups = report["spec"]["groups"]
        self.assertEqual(len(groups), 2)
        self.assertEqual(
            {item["dimension"]["name"] for item in groups},
            {"Support Classic", "Support Experience"},
        )
        self.assertEqual(
            {item["dimension"]["id"] for item in groups},
            {"support-experience"},
        )

    def test_source_ceiling_fails_instead_of_returning_partial_totals(self) -> None:
        seed_usage(
            self.store,
            [
                usage_record(1, "2026-09-05T09:30:00Z"),
                usage_record(2, "2026-09-05T10:30:00Z"),
            ],
        )
        with self.assertRaisesRegex(
            AiAllocationQueryError,
            "ai.allocation.source-limit-exceeded",
        ):
            self.service(source_record_limit=1).get(
                self.actor,
                start="2026-09-05T09:00:00Z",
                end="2026-09-05T11:00:00Z",
                group_by="team",
            )

    def test_query_bounds_and_policy_denial_fail_closed(self) -> None:
        for actor in (
            ActorContext("anonymous", "local", ("platform-admin",)),
            ActorContext("operator", "../local", ("platform-admin",)),
            ActorContext("operator", "local", ("INVALID ROLE",)),
            ActorContext("operator", "local", ("platform-admin", "platform-admin")),
        ):
            with self.subTest(actor=actor):
                with self.assertRaisesRegex(AiAllocationQueryError, "request.invalid"):
                    self.service().get(
                        actor,
                        start="2026-09-05T09:00:00Z",
                        end="2026-09-05T11:00:00Z",
                        group_by="application",
                    )

        with self.assertRaisesRegex(AiAllocationQueryError, "request.invalid"):
            self.store.list_ai_allocation_rows(
                ActorContext("anonymous", "local", ()),
                AiAllocationLedgerQuery(
                    start="2026-09-05T09:00:00Z",
                    end="2026-09-05T11:00:00Z",
                    policy_id=self.policy["metadata"]["id"],
                    attribution_engine_version="0.1.0",
                    catalog_id=self.catalog["metadata"]["id"],
                    cost_engine_version="0.1.0",
                    limit=101,
                ),
            )

        with self.assertRaisesRegex(AiAllocationQueryError, "request.invalid"):
            self.service().get(
                self.actor,
                start="2026-08-01T00:00:00Z",
                end="2026-09-05T00:00:00Z",
                group_by="application",
            )

        class DenyPolicy:
            def decide(self, actor, action, resource):
                from iip.application.ports import PolicyDecision

                return PolicyDecision(False, "denied")

        with self.assertRaises(AiAllocationAuthorizationError):
            self.service(policy=DenyPolicy()).get(
                self.actor,
                start="2026-09-05T09:00:00Z",
                end="2026-09-05T11:00:00Z",
                group_by="application",
            )

    def test_http_and_python_sdk_use_the_closed_query_contract(self) -> None:
        seed_usage(self.store, [usage_record(1, "2026-09-05T10:00:00Z")])
        self.run_engines()
        handler = object.__new__(ApiHandler)
        handler.runtime = SimpleNamespace(ai_allocation_reports=self.service())
        responses: list[tuple[HTTPStatus, dict]] = []
        handler._json = lambda status, payload: responses.append((status, payload))
        query = (
            "start=2026-09-05T09%3A00%3A00Z&"
            "end=2026-09-05T11%3A00%3A00Z&groupBy=team"
        )
        handler._query_ai_allocation(self.actor, query)
        self.assertEqual(responses[0][0], HTTPStatus.OK)
        self.assertEqual(responses[0][1]["spec"]["scope"]["groupBy"], "team")

        calls: list[str] = []
        client = Client("https://control.example", "allocation-test-token-0123456789")
        client._get = lambda path: (calls.append(path) or responses[0][1])  # type: ignore[method-assign]
        parsed = client.get_ai_allocation_report(
            start="2026-09-05T09:00:00Z",
            end="2026-09-05T11:00:00Z",
            group_by="team",
        )
        self.assertIsInstance(parsed, AiAllocationReport)
        self.assertEqual(parsed.groups[0]["dimension"]["id"], "customer-experience")
        self.assertIn("groupBy=team", calls[0])

    def test_environment_configuration_is_explicit_and_bounded(self) -> None:
        environment = {
            "IIP_AI_ALLOCATION_REPORTING_ENABLED": "true",
            "IIP_AI_ATTRIBUTION_POLICIES_JSON": json.dumps(
                {"policies": [self.policy]}
            ),
            "IIP_AI_PRICE_CATALOGS_JSON": json.dumps(
                {"catalogs": [self.catalog]}
            ),
            "IIP_AI_ATTRIBUTION_ALLOW_TEST_FIXTURES": "true",
            "IIP_AI_PRICE_CATALOG_ALLOW_TEST_FIXTURES": "true",
            "IIP_AI_ALLOCATION_SOURCE_RECORD_LIMIT": "500",
            "IIP_AI_ALLOCATION_WINDOW_SECONDS": "3600",
        }
        with patch.dict("os.environ", environment, clear=True):
            policies, catalogs, allow, limit, window = (
                _ai_allocation_report_configuration_from_env()
            )
        self.assertEqual(len(policies or ()), 1)
        self.assertEqual(len(catalogs or ()), 1)
        self.assertTrue(allow)
        self.assertEqual(limit, 500)
        self.assertEqual(window, 3600)

    def test_worker_projection_exports_only_bounded_stable_dimensions(self) -> None:
        seed_usage(self.store, [usage_record(1, "2026-09-05T10:00:00Z")])
        self.run_engines()

        class Sink:
            def __init__(self) -> None:
                self.snapshots: list[
                    tuple[str, tuple[AiAllocationMeasurement, ...]]
                ] = []

            def record_ai_allocation_snapshot(self, tenant_id, measurements):
                self.snapshots.append((tenant_id, measurements))

        sink = Sink()
        projection = AiAllocationProjectionService(
            self.store,
            self.clock,
            (self.policy,),
            (self.catalog,),
            sink,
            allow_test_fixtures=True,
        )
        result = projection.run_once("local", "worker-1")

        self.assertEqual((result.usage_records, result.groups), (1, 2))
        self.assertEqual(
            {(item.dimension, item.dimension_id) for item in sink.snapshots[0][1]},
            {
                ("application", "support-experience"),
                ("team", "customer-experience"),
            },
        )
        self.assertEqual(sink.snapshots[0][0], "local")
        serialized = repr(sink.snapshots)
        self.assertNotIn("Support Experience", serialized)
        self.assertNotIn("aiu_", serialized)


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and PostgresResourceStore is not None,
    "set IIP_TEST_DATABASE_URL to run PostgreSQL AI allocation tests",
)
class AiAllocationPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        assert DATABASE_URL is not None
        assert psycopg is not None
        assert PostgresResourceStore is not None
        self.store = PostgresResourceStore(DATABASE_URL)
        self.store.migrate()
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                TRUNCATE iip.ai_savings_findings,
                         iip.ai_usage_attributions,
                         iip.ai_attribution_policies,
                         iip.ai_cost_records,
                         iip.ai_price_catalogs,
                         iip.ai_usage_records,
                         iip.event_outbox,
                         iip.event_log
                RESTART IDENTITY CASCADE
                """
            )

    def test_report_join_is_durable_generation_bound_and_tenant_isolated(
        self,
    ) -> None:
        local = usage_record(1, "2026-09-05T10:00:00Z")
        other = usage_record(2, "2026-09-05T10:01:00Z")
        other["metadata"]["tenantId"] = "other"
        seed_usage(self.store, [local])
        seed_usage(self.store, [other])
        policy = fixture("ai-attribution-policy")
        catalog = fixture("ai-price-catalog")
        attribution = AiAttributionService(
            self.store,
            FixedClock("2026-09-05T10:59:01Z"),
            (policy,),
            allow_test_fixtures=True,
        )
        cost = AiCostCalculationService(
            self.store,
            FixedClock("2026-09-05T10:59:02Z"),
            (catalog,),
            allow_test_fixtures=True,
        )
        self.assertEqual(attribution.run_once("local", "postgres").processed, 1)
        self.assertEqual(cost.run_once("local", "postgres").processed, 1)
        service = AiAllocationReportService(
            self.store,
            AllowTenantPolicy(),
            FixedClock(),
            (policy,),
            (catalog,),
            allow_test_fixtures=True,
        )

        report = service.get(
            ActorContext("operator", "local", ("platform-admin",)),
            start="2026-09-05T09:00:00Z",
            end="2026-09-05T11:00:00Z",
            group_by="application",
        )

        self.assertEqual(report["spec"]["coverage"]["usageRecords"], 1)
        self.assertEqual(
            report["spec"]["groups"][0]["dimension"]["id"],
            "support-experience",
        )
        assert DATABASE_URL is not None
        with psycopg.connect(DATABASE_URL) as connection:
            tenants = connection.execute(
                "SELECT tenant_id, count(*) FROM iip.ai_usage_records "
                "GROUP BY tenant_id ORDER BY tenant_id"
            ).fetchall()
        self.assertEqual([tuple(row) for row in tenants], [("local", 1), ("other", 1)])


if __name__ == "__main__":
    unittest.main()
