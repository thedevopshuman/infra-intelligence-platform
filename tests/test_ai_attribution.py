from __future__ import annotations

import copy
import json
import os
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

try:
    import psycopg

    from iip.adapters.postgres import PostgresResourceStore
except ModuleNotFoundError:
    psycopg = None
    PostgresResourceStore = None

from iip.adapters.ai_attribution_policies import (
    ai_attribution_policies_from_json,
)
from iip.adapters.memory import InMemoryResourceStore
from iip.application.attribute_ai_usage import (
    AiAttributionConfigurationError,
    AiAttributionService,
    InvalidAiAttributionInputError,
    resolve_ai_usage_attribution,
    validate_ai_attribution_policy,
    validate_ai_attribution_source_binding,
    validate_ai_usage_attribution_record,
)
from iip.application.ports import ActorContext, PersistenceError
from iip.bootstrap import (
    _ai_attribution_engine_configuration_from_env,
    build_local_runtime,
)
from iip.domain.models import PlatformEvent
from iip.surfaces.worker import run_ai_attribution_pass


ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ.get("IIP_TEST_DATABASE_URL")


class MutableClock:
    def __init__(self, value: str = "2026-09-05T10:00:04Z") -> None:
        self.value = value

    def now(self) -> str:
        return self.value


def fixture(name: str) -> dict:
    return json.loads(
        (ROOT / "contracts" / "examples" / f"{name}.json").read_text(
            encoding="utf-8"
        )
    )


def usage_event(record: dict) -> PlatformEvent:
    metadata = record["metadata"]
    spec = record["spec"]
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


def seed_usage(store: object, record: dict | None = None) -> dict:
    document = record or fixture("ai-usage-record")
    actor = ActorContext(
        "ai-usage-channel:" + document["spec"]["source"]["channelId"],
        document["metadata"]["tenantId"],
        ("telemetry-ingest",),
    )
    store.commit_usage_batch(  # type: ignore[attr-defined]
        actor,
        (document,),
        (usage_event(document),),
    )
    return document


class AiAttributionResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy_document = fixture("ai-attribution-policy")
        self.policy = validate_ai_attribution_policy(self.policy_document)
        self.usage = fixture("ai-usage-record")

    def test_example_resolves_exactly_and_event_is_value_minimized(self) -> None:
        record, event = resolve_ai_usage_attribution(
            self.policy,
            self.usage,
            resolved_at="2026-09-05T10:00:04Z",
        )
        self.assertEqual(record, fixture("ai-usage-attribution-record"))
        self.assertEqual(event.to_dict(), fixture("ai-usage-attributed-event"))
        self.assertEqual(validate_ai_usage_attribution_record(record), record)
        self.assertEqual(
            set(event.data),
            {
                "attributionRecordId",
                "usageRecordId",
                "policyId",
                "policyVersion",
                "status",
                "applicationId",
                "teamId",
            },
        )
        self.assertNotIn("token", json.dumps(event.data).lower())

    def test_unmatched_usage_is_explicitly_unallocated(self) -> None:
        usage = copy.deepcopy(self.usage)
        usage["spec"]["attribution"]["serviceName"] = "research-assistant"
        record, event = resolve_ai_usage_attribution(
            self.policy,
            usage,
            resolved_at="2026-09-05T10:00:04Z",
        )
        self.assertEqual(
            record["spec"]["resolution"],
            {"status": "unallocated", "reasonCode": "no-matching-rule"},
        )
        self.assertEqual(event.data["status"], "unallocated")
        self.assertNotIn("applicationId", event.data)
        self.assertNotIn("teamId", event.data)

    def test_invocation_time_selects_the_effective_rule_at_boundary(self) -> None:
        document = copy.deepcopy(self.policy_document)
        current = document["spec"]["rules"][0]
        current["priority"] = 100
        current["effectiveFrom"] = "2026-09-05T10:00:00Z"
        prior = copy.deepcopy(current)
        prior["id"] = "support-prior-owner"
        prior["priority"] = 200
        prior["allocation"]["team"] = {
            "id": "platform-foundations",
            "name": "Platform Foundations",
        }
        prior["effectiveFrom"] = "2026-01-01T00:00:00Z"
        prior["effectiveUntil"] = "2026-09-05T10:00:00Z"
        document["spec"]["rules"] = [prior, current]

        record, _ = resolve_ai_usage_attribution(
            validate_ai_attribution_policy(document),
            self.usage,
            resolved_at="2026-09-05T10:00:04Z",
        )
        self.assertEqual(
            record["spec"]["resolution"]["team"]["id"],
            "customer-experience",
        )

    def test_policy_rejects_ambiguous_order_time_and_credentials(self) -> None:
        duplicate_priority = copy.deepcopy(self.policy_document)
        extra = copy.deepcopy(duplicate_priority["spec"]["rules"][0])
        extra["id"] = "another-rule"
        duplicate_priority["spec"]["rules"].append(extra)

        unsorted = copy.deepcopy(self.policy_document)
        high = copy.deepcopy(unsorted["spec"]["rules"][0])
        high["id"] = "higher-rule"
        high["priority"] = 200
        unsorted["spec"]["rules"].append(high)

        reversed_time = copy.deepcopy(self.policy_document)
        reversed_time["spec"]["rules"][0]["effectiveUntil"] = (
            "2025-12-31T23:59:59Z"
        )

        credentialed = copy.deepcopy(self.policy_document)
        credentialed["spec"]["source"]["locator"] = (
            "https://user:secret@example.test/catalog.json"
        )

        for invalid in (duplicate_priority, unsorted, reversed_time, credentialed):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                AiAttributionConfigurationError,
                "ai.attribution.policy.invalid",
            ):
                validate_ai_attribution_policy(invalid)

    def test_persistence_binding_rejects_forged_allocation(self) -> None:
        record, _ = resolve_ai_usage_attribution(
            self.policy,
            self.usage,
            resolved_at="2026-09-05T10:00:04Z",
        )
        forged = copy.deepcopy(record)
        forged["spec"]["resolution"]["team"] = {
            "id": "attacker-team",
            "name": "Attacker Team",
        }
        validate_ai_usage_attribution_record(forged)
        with self.assertRaisesRegex(
            InvalidAiAttributionInputError,
            "ai.attribution.binding.invalid",
        ):
            validate_ai_attribution_source_binding(
                forged,
                self.policy_document,
                self.usage,
            )


class AiAttributionLedgerAndWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryResourceStore()
        self.usage = seed_usage(self.store)
        self.policy = fixture("ai-attribution-policy")
        self.clock = MutableClock()

    def test_worker_commits_record_event_and_replay_is_idempotent(self) -> None:
        service = AiAttributionService(
            self.store,
            self.clock,
            (self.policy,),
            allow_test_fixtures=True,
        )
        result = service.run_once("local", "worker-1")
        self.assertEqual((result.processed, result.allocated, result.unallocated), (1, 1, 0))
        self.assertEqual(len(self.store.ai_usage_attributions), 1)
        self.assertEqual(len(self.store.events), 2)

        replay = service.run_once("local", "worker-2")
        self.assertEqual(replay.processed, 0)
        self.assertEqual(len(self.store.events), 2)

    def test_fixture_gate_tenant_scope_and_value_minimized_pass(self) -> None:
        with self.assertRaisesRegex(
            AiAttributionConfigurationError,
            "ai.attribution.test-fixture.prohibited",
        ):
            AiAttributionService(self.store, self.clock, (self.policy,))

        service = AiAttributionService(
            self.store,
            self.clock,
            (self.policy,),
            allow_test_fixtures=True,
        )
        summary = run_ai_attribution_pass(service, ("local", "other"), "worker-1")
        self.assertEqual(
            (summary.processed, summary.allocated, summary.unallocated, summary.failures),
            (1, 1, 0, 1),
        )

        wrong_actor = ActorContext(
            "ai-attribution-worker:worker-1",
            "other",
            ("ai-attribution:resolve",),
        )
        with self.assertRaisesRegex(PersistenceError, "storage.request.invalid"):
            self.store.register_attribution_policy(wrong_actor, self.policy)

    def test_changed_policy_and_event_or_result_forgery_fail_closed(self) -> None:
        actor = ActorContext(
            "ai-attribution-worker:worker-1",
            "local",
            ("ai-attribution:resolve",),
        )
        self.store.register_attribution_policy(actor, self.policy)
        changed_policy = copy.deepcopy(self.policy)
        changed_policy["spec"]["rules"][0]["allocation"]["team"]["name"] = (
            "Changed Team"
        )
        with self.assertRaisesRegex(PersistenceError, "storage.conflict"):
            self.store.register_attribution_policy(actor, changed_policy)

        record, event = resolve_ai_usage_attribution(
            validate_ai_attribution_policy(self.policy),
            self.usage,
            resolved_at=self.clock.now(),
        )
        forged = copy.deepcopy(record)
        forged["spec"]["resolution"]["application"]["id"] = "forged-app"
        with self.assertRaisesRegex(PersistenceError, "storage.request.invalid"):
            self.store.commit_usage_attribution_batch(actor, (forged,), (event,))

        wrong_event = replace(event, data={**event.data, "teamId": "forged-team"})
        with self.assertRaisesRegex(PersistenceError, "storage.request.invalid"):
            self.store.commit_usage_attribution_batch(actor, (record,), (wrong_event,))


class AiAttributionCompositionTests(unittest.TestCase):
    def test_closed_configuration_and_worker_only_composition(self) -> None:
        wrapper = {"policies": [fixture("ai-attribution-policy")]}
        self.assertEqual(
            len(ai_attribution_policies_from_json(json.dumps(wrapper))),
            1,
        )
        with self.assertRaisesRegex(
            AiAttributionConfigurationError,
            "ai.attribution.configuration.invalid",
        ):
            ai_attribution_policies_from_json(
                json.dumps({"policies": [], "extra": True})
            )

        environment = {
            "IIP_AI_ATTRIBUTION_ENABLED": "true",
            "IIP_AI_ATTRIBUTION_POLICIES_JSON": json.dumps(wrapper),
            "IIP_AI_ATTRIBUTION_ALLOW_TEST_FIXTURES": "true",
            "IIP_AI_ATTRIBUTION_BATCH_SIZE": "25",
            "IIP_WORKER_TENANTS": "local",
        }
        with patch.dict("os.environ", environment, clear=True):
            policies, allow, batch_size = (
                _ai_attribution_engine_configuration_from_env()
            )
        self.assertEqual(len(policies or ()), 1)
        self.assertTrue(allow)
        self.assertEqual(batch_size, 25)

        runtime = build_local_runtime(
            ai_attribution_policies=(fixture("ai-attribution-policy"),),
            ai_attribution_allow_test_fixtures=True,
        )
        self.assertIsNotNone(runtime.ai_attribution_resolution)
        runtime.close()
        disabled = build_local_runtime()
        self.assertIsNone(disabled.ai_attribution_resolution)
        disabled.close()

    def test_policy_tenants_must_exactly_match_worker_enrollment(self) -> None:
        environment = {
            "IIP_AI_ATTRIBUTION_ENABLED": "true",
            "IIP_AI_ATTRIBUTION_POLICIES_JSON": json.dumps(
                {"policies": [fixture("ai-attribution-policy")]}
            ),
            "IIP_AI_ATTRIBUTION_ALLOW_TEST_FIXTURES": "true",
            "IIP_WORKER_TENANTS": "local,other",
        }
        with patch.dict("os.environ", environment, clear=True):
            with self.assertRaisesRegex(
                AiAttributionConfigurationError,
                "ai.attribution.tenants.invalid",
            ):
                _ai_attribution_engine_configuration_from_env()


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and PostgresResourceStore is not None,
    "set IIP_TEST_DATABASE_URL to run PostgreSQL AI attribution tests",
)
class AiAttributionPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        assert DATABASE_URL is not None
        assert psycopg is not None
        assert PostgresResourceStore is not None
        self.store = PostgresResourceStore(DATABASE_URL)
        self.store.migrate()
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                """
                TRUNCATE iip.ai_usage_attributions,
                         iip.ai_attribution_policies,
                         iip.ai_cost_records, iip.ai_price_catalogs,
                         iip.ai_usage_records, iip.event_outbox, iip.event_log
                RESTART IDENTITY CASCADE
                """
            )

    def test_policy_usage_attribution_and_event_are_durable(self) -> None:
        seed_usage(self.store)
        service = AiAttributionService(
            self.store,
            MutableClock(),
            (fixture("ai-attribution-policy"),),
            allow_test_fixtures=True,
        )
        self.assertEqual(service.run_once("local", "postgres-worker").processed, 1)
        self.assertEqual(service.run_once("local", "postgres-worker").processed, 0)

        assert DATABASE_URL is not None
        assert psycopg is not None
        with psycopg.connect(DATABASE_URL) as connection:
            row = connection.execute(
                """
                SELECT status, application_id, team_id
                FROM iip.ai_usage_attributions
                WHERE tenant_id = 'local'
                """
            ).fetchone()
            events = connection.execute(
                "SELECT count(*) FROM iip.event_log WHERE tenant_id = 'local'"
            ).fetchone()[0]
            outbox = connection.execute(
                "SELECT count(*) FROM iip.event_outbox WHERE tenant_id = 'local'"
            ).fetchone()[0]
        self.assertEqual(tuple(row), ("allocated", "support-experience", "customer-experience"))
        self.assertEqual(events, 2)
        self.assertEqual(outbox, 2)


if __name__ == "__main__":
    unittest.main()
