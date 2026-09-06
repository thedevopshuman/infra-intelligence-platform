from __future__ import annotations

import copy
import json
import os
import unittest
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace

try:
    import psycopg

    from iip.adapters.postgres import PostgresResourceStore
except ModuleNotFoundError:
    psycopg = None
    PostgresResourceStore = None

from infra_intelligence_sdk import AiSavingsFindingPage, Client
from iip.adapters.memory import AllowTenantPolicy, InMemoryResourceStore
from iip.application.ports import ActorContext, PersistenceError, PolicyDecision
from iip.application.query_ai_savings import (
    AiSavingsFindingAuthorizationError,
    AiSavingsFindingQueryError,
    AiSavingsFindingQueryService,
)
from iip.surfaces.http import ApiHandler


ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ.get("IIP_TEST_DATABASE_URL")


class FixedClock:
    def now(self) -> str:
        return "2026-09-05T11:05:00Z"


def fixture(name: str) -> dict:
    return json.loads(
        (ROOT / "contracts" / "examples" / f"{name}.json").read_text(
            encoding="utf-8"
        )
    )


def seed_findings(store: InMemoryResourceStore) -> tuple[dict, ...]:
    findings = tuple(
        fixture(name)
        for name in (
            "ai-savings-finding",
            "ai-retry-savings-finding",
            "ai-expensive-model-savings-finding",
        )
    )
    for finding in findings:
        metadata = finding["metadata"]
        store._ai_savings[(metadata["tenantId"], metadata["id"])] = (
            "0" * 64,
            copy.deepcopy(finding),
        )
    other = copy.deepcopy(findings[0])
    other["metadata"]["tenantId"] = "other"
    store._ai_savings[("other", other["metadata"]["id"])] = (
        "1" * 64,
        other,
    )
    return findings


class AiSavingsFindingQueryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryResourceStore()
        self.findings = seed_findings(self.store)
        self.actor = ActorContext("operator", "local", ("platform-admin",))
        self.service = AiSavingsFindingQueryService(
            self.store,
            AllowTenantPolicy(),
            FixedClock(),
        )

    def test_page_is_newest_first_bounded_and_tenant_scoped(self) -> None:
        first = self.service.list(
            self.actor,
            start="2026-09-05T00:00:00Z",
            end="2026-09-06T00:00:00Z",
            limit=1,
        )
        self.assertEqual(first["kind"], "AiSavingsFindingPage")
        self.assertEqual(first["metadata"]["tenantId"], "local")
        self.assertTrue(first["spec"]["page"]["hasMore"])
        cursor = first["spec"]["page"]["nextCursor"]
        second = self.service.list(
            self.actor,
            start="2026-09-05T00:00:00Z",
            end="2026-09-06T00:00:00Z",
            limit=1,
            cursor=cursor,
        )
        first_id = first["spec"]["items"][0]["metadata"]["id"]
        second_id = second["spec"]["items"][0]["metadata"]["id"]
        self.assertGreater(first_id, second_id)
        self.assertNotEqual(first_id, second_id)
        self.assertTrue(
            all(
                item["metadata"]["tenantId"] == "local"
                for item in first["spec"]["items"] + second["spec"]["items"]
            )
        )

    def test_cursor_is_bound_to_tenant_interval_and_canonical_encoding(self) -> None:
        page = self.service.list(
            self.actor,
            start="2026-09-05T00:00:00Z",
            end="2026-09-06T00:00:00Z",
            limit=1,
        )
        cursor = page["spec"]["page"]["nextCursor"]
        for changes in (
            {"end": "2026-09-05T23:00:00Z"},
            {"cursor": cursor[:-1] + ("A" if cursor[-1] != "A" else "B")},
        ):
            arguments = {
                "start": "2026-09-05T00:00:00Z",
                "end": "2026-09-06T00:00:00Z",
                "limit": 1,
                "cursor": cursor,
            }
            arguments.update(changes)
            with self.assertRaisesRegex(
                AiSavingsFindingQueryError, "pagination.cursor_invalid"
            ):
                self.service.list(self.actor, **arguments)
        with self.assertRaisesRegex(
            AiSavingsFindingQueryError, "pagination.cursor_invalid"
        ):
            self.service.list(
                ActorContext("operator", "other", ("platform-admin",)),
                start="2026-09-05T00:00:00Z",
                end="2026-09-06T00:00:00Z",
                limit=1,
                cursor=cursor,
            )

    def test_query_bounds_policy_and_corrupt_storage_fail_closed(self) -> None:
        invalid_queries = (
            {"start": "2026-09-05T00:00:00+00:00", "end": "2026-09-06T00:00:00Z"},
            {"start": "2026-09-05T00:00:00Z", "end": "2026-10-07T00:00:00Z"},
            {"start": "2026-09-06T00:00:00Z", "end": "2026-09-05T00:00:00Z"},
            {"start": "2026-09-05T00:00:00Z", "end": "2026-09-06T00:00:00Z", "limit": 101},
        )
        for query in invalid_queries:
            with self.subTest(query=query), self.assertRaisesRegex(
                AiSavingsFindingQueryError, "request.invalid"
            ):
                self.service.list(self.actor, **query)

        class Deny:
            def decide(self, actor, action, resource):
                return PolicyDecision(False, "policy.test-denied")

        with self.assertRaises(AiSavingsFindingAuthorizationError):
            AiSavingsFindingQueryService(
                self.store, Deny(), FixedClock()
            ).list(
                self.actor,
                start="2026-09-05T00:00:00Z",
                end="2026-09-06T00:00:00Z",
            )

        local_id = self.findings[0]["metadata"]["id"]
        self.store._ai_savings[("local", local_id)][1]["metadata"][
            "tenantId"
        ] = "other"
        with self.assertRaisesRegex(PersistenceError, "storage.state.invalid"):
            self.service.list(
                self.actor,
                start="2026-09-05T00:00:00Z",
                end="2026-09-06T00:00:00Z",
            )

    def test_http_and_python_sdk_use_the_closed_read_contract(self) -> None:
        handler = object.__new__(ApiHandler)
        handler.runtime = SimpleNamespace(ai_savings_findings=self.service)
        responses: list[tuple[HTTPStatus, dict]] = []
        handler._json = lambda status, payload: responses.append((status, payload))
        handler._query_ai_savings_findings(
            self.actor,
            "start=2026-09-05T00%3A00%3A00Z&"
            "end=2026-09-06T00%3A00%3A00Z&limit=2",
        )
        self.assertEqual(responses[0][0], HTTPStatus.OK)
        self.assertEqual(len(responses[0][1]["spec"]["items"]), 2)

        calls: list[str] = []
        client = Client("https://control.example", "savings-test-token-0123456789")
        client._get = lambda path: (calls.append(path) or responses[0][1])  # type: ignore[method-assign]
        parsed = client.list_ai_savings_findings(
            start="2026-09-05T00:00:00Z",
            end="2026-09-06T00:00:00Z",
            limit=2,
        )
        self.assertIsInstance(parsed, AiSavingsFindingPage)
        self.assertEqual(len(parsed.items), 2)
        self.assertIn("limit=2", calls[0])

        handler._query_ai_savings_findings(
            self.actor,
            "start=2026-09-05T00%3A00%3A00Z&"
            "end=2026-09-06T00%3A00%3A00Z&tenantId=other",
        )
        self.assertEqual(responses[-1], (HTTPStatus.BAD_REQUEST, {"error": {"code": "request.invalid"}}))

        openapi = json.loads(
            (ROOT / "api" / "openapi" / "control-plane.openapi.json").read_text(
                encoding="utf-8"
            )
        )
        operation = openapi["paths"]["/v1/ai/economics/savings-findings"]["get"]
        self.assertEqual(operation["operationId"], "listAiSavingsFindings")
        self.assertIn("403", operation["responses"])


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None,
    "set IIP_TEST_DATABASE_URL to run PostgreSQL AI savings query tests",
)
class AiSavingsFindingPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        assert DATABASE_URL is not None
        assert PostgresResourceStore is not None
        self.store = PostgresResourceStore(DATABASE_URL)
        self.store.migrate()
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                TRUNCATE iip.event_outbox, iip.ai_savings_findings,
                    iip.ai_model_suitability_reports,
                    iip.ai_usage_attributions, iip.ai_attribution_policies,
                    iip.ai_cost_records, iip.ai_price_catalogs,
                    iip.ai_usage_records, iip.event_log
                RESTART IDENTITY CASCADE
                """
            )

    def test_postgres_page_reads_only_the_authenticated_tenant(self) -> None:
        from tests.test_ai_savings_engine import (
            AiSavingsEvaluationService,
            MutableClock,
            profile,
            seed_standard_cohorts,
        )

        clock = MutableClock()
        seed_standard_cohorts(self.store, clock)
        result = AiSavingsEvaluationService(
            self.store, clock, (profile(),)
        ).run_once("local", "query-postgres")
        self.assertEqual(result.qualified, 1)
        page = AiSavingsFindingQueryService(
            self.store, AllowTenantPolicy(), FixedClock()
        ).list(
            ActorContext("operator", "local", ("platform-admin",)),
            start="2026-09-05T00:00:00Z",
            end="2026-09-06T00:00:00Z",
        )
        self.assertEqual(len(page["spec"]["items"]), 1)
        self.assertEqual(
            page["spec"]["items"][0]["metadata"]["tenantId"], "local"
        )


if __name__ == "__main__":
    unittest.main()
