from __future__ import annotations

import copy
import hashlib
import json
import os
import unittest
from http import HTTPStatus
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator, FormatChecker

try:
    import psycopg

    from iip.adapters.postgres import PostgresResourceStore
except ModuleNotFoundError:
    psycopg = None
    PostgresResourceStore = None

from infra_intelligence_sdk import (
    AiEconomicsInvocationObservation,
    AiEconomicsInvocationObservationRequest,
    Client,
)
from iip.adapters.memory import AllowTenantPolicy, InMemoryResourceStore
from iip.application.attribute_ai_usage import AiAttributionService
from iip.application.calculate_ai_cost import AiCostCalculationService
from iip.application.ports import ActorContext
from iip.application.query_ai_invocation import (
    AiInvocationObservationAuthorizationError,
    AiInvocationObservationConfigurationError,
    AiInvocationObservationQueryError,
    AiInvocationObservationService,
)
from iip.domain.models import PlatformEvent
from iip.surfaces.http import ApiHandler


ROOT = Path(__file__).resolve().parents[1]
DATABASE_URL = os.environ.get("IIP_TEST_DATABASE_URL")
TRACE_ID = "1234567890abcdef1234567890abcdef"
SPAN_ID = "fedcba0987654321"


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


def usage_record(*, tenant_id: str = "local") -> dict:
    document = copy.deepcopy(fixture("ai-usage-record"))
    document["metadata"].update(
        {
            "id": "aiu_00000000000000000000000000000001",
            "tenantId": tenant_id,
            "recordedAt": "2026-09-05T10:59:00Z",
        }
    )
    document["spec"]["invocation"].update(
        {
            "startedAt": "2026-09-05T10:00:00Z",
            "traceId": TRACE_ID,
            "spanId": SPAN_ID,
            "requestIdHash": "sha256:" + "01" * 32,
        }
    )
    document["spec"]["deduplicationKey"] = "sha256:" + "02" * 32
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
            "modelId": invocation["requestModel"],
            "serviceName": attribution["serviceName"],
            "outcome": invocation["outcome"],
        },
    )


def seed(store, document: dict) -> None:
    actor = ActorContext(
        "ai-usage-channel:" + document["spec"]["source"]["channelId"],
        document["metadata"]["tenantId"],
        ("telemetry-ingest",),
    )
    store.commit_usage_batch(actor, (document,), (usage_event(document),))


class AiInvocationObservationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryResourceStore()
        self.policy = fixture("ai-attribution-policy")
        self.catalog = fixture("ai-price-catalog")
        self.actor = ActorContext("operator", "local", ("platform-admin",))

    def service(self, *, decision_policy=None) -> AiInvocationObservationService:
        return AiInvocationObservationService(
            self.store,
            decision_policy or AllowTenantPolicy(),
            FixedClock(),
            (self.policy,),
            (self.catalog,),
            allow_test_fixtures=True,
        )

    @staticmethod
    def request(trace_id: str = TRACE_ID, span_id: str = SPAN_ID) -> dict:
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "AiEconomicsInvocationObservationRequest",
            "spec": {"traceId": trace_id, "spanId": span_id},
        }

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
        self.assertEqual(attribution.run_once("local", "observation-test").processed, 1)
        self.assertEqual(cost.run_once("local", "observation-test").processed, 1)

    def test_exact_invocation_progresses_from_absent_to_complete(self) -> None:
        absent = self.service().observe(self.actor, self.request())
        self.assertEqual(absent["spec"]["status"], "not-observed")
        self.assertEqual(absent["spec"]["usage"], {"status": "not-observed"})

        seed(self.store, usage_record())
        pending = self.service().observe(self.actor, self.request())
        self.assertEqual(pending["spec"]["status"], "processing")
        self.assertEqual(pending["spec"]["usage"]["status"], "recorded")
        self.assertEqual(pending["spec"]["cost"], {"status": "pending"})

        self.run_engines()
        complete = self.service().observe(self.actor, self.request())
        schema = json.loads(
            (
                ROOT
                / "contracts"
                / "schemas"
                / "ai-economics-invocation-observation.schema.json"
            ).read_text(encoding="utf-8")
        )
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(
            complete
        )
        self.assertEqual(complete["spec"]["status"], "complete")
        self.assertEqual(complete["spec"]["attribution"]["status"], "allocated")
        self.assertEqual(
            complete["spec"]["attribution"]["applicationId"],
            "support-experience",
        )
        self.assertEqual(complete["spec"]["cost"]["status"], "priced")
        self.assertEqual(
            complete["spec"]["cost"]["pricedCost"]["totalSubunits"],
            15_735_000,
        )
        self.assertEqual(
            complete["spec"]["sources"]["attribution"]["documentDigest"],
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    self.policy,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("utf-8")
            ).hexdigest(),
        )
        self.assertEqual(
            complete["spec"]["sources"]["pricing"]["documentDigest"],
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    self.catalog,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=True,
                ).encode("utf-8")
            ).hexdigest(),
        )
        serialized = json.dumps(complete)
        self.assertNotIn(TRACE_ID, serialized)
        self.assertNotIn(SPAN_ID, serialized)
        expected = hashlib.sha256(
            json.dumps(
                {"spanId": SPAN_ID, "tenantId": "local", "traceId": TRACE_ID},
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(
            complete["spec"]["correlationDigest"], "sha256:" + expected
        )

    def test_wrong_tenant_cannot_observe_same_trace_and_span(self) -> None:
        seed(self.store, usage_record())
        other_policy = copy.deepcopy(self.policy)
        other_policy["metadata"]["tenantId"] = "other"
        other_policy["metadata"]["id"] = "aap_" + "3" * 32
        other_policy["metadata"]["version"] = "2026-09-05.2"
        other_policy["spec"]["source"]["contentHash"] = "sha256:" + "3" * 64
        other_catalog = copy.deepcopy(self.catalog)
        other_catalog["metadata"]["tenantId"] = "other"
        other_catalog["metadata"]["id"] = "apc_" + "4" * 32
        other_catalog["metadata"]["version"] = "2026-09-05.2"
        other_catalog["spec"]["source"]["contentHash"] = "sha256:" + "4" * 64
        service = AiInvocationObservationService(
            self.store,
            AllowTenantPolicy(),
            FixedClock(),
            (other_policy,),
            (other_catalog,),
            allow_test_fixtures=True,
        )
        result = service.observe(
            ActorContext("other-operator", "other", ("platform-admin",)),
            self.request(),
        )
        self.assertEqual(result["spec"]["status"], "not-observed")

    def test_role_policy_and_closed_request_fail_closed(self) -> None:
        with self.assertRaises(AiInvocationObservationAuthorizationError):
            self.service().observe(
                ActorContext("developer", "local", ("developer",)), self.request()
            )

        class Deny:
            def decide(self, actor, action, resource):
                from iip.application.ports import PolicyDecision

                return PolicyDecision(False, "denied")

        with self.assertRaises(AiInvocationObservationAuthorizationError):
            self.service(decision_policy=Deny()).observe(self.actor, self.request())
        for invalid in (
            self.request("A" * 32),
            self.request(span_id="0" * 15),
            {**self.request(), "tenantId": "local"},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(AiInvocationObservationQueryError):
                    self.service().observe(self.actor, invalid)

    def test_http_surface_returns_observation_and_stable_errors(self) -> None:
        handler = object.__new__(ApiHandler)
        handler.runtime = SimpleNamespace(ai_invocation_observation=self.service())
        responses: list[tuple[HTTPStatus, dict]] = []
        handler._json = lambda status, payload: responses.append((status, payload))
        handler.runtime.ai_invocation_observation.observe = lambda actor, payload: {
            "kind": "AiEconomicsInvocationObservation"
        }
        handler._read_json = lambda: self.request()
        handler._actor = lambda: self.actor
        handler.path = "/v1/operations/ai-economics/invocation-observations"
        handler.do_POST()
        self.assertEqual(responses, [(HTTPStatus.OK, {"kind": "AiEconomicsInvocationObservation"})])

        for error, expected in (
            (
                AiInvocationObservationQueryError("provider detail"),
                (
                    HTTPStatus.BAD_REQUEST,
                    {
                        "error": {
                            "code": "ai.invocation-observation.request.invalid"
                        }
                    },
                ),
            ),
            (
                AiInvocationObservationAuthorizationError("provider detail"),
                (HTTPStatus.FORBIDDEN, {"error": {"code": "policy.denied"}}),
            ),
            (
                AiInvocationObservationConfigurationError("provider detail"),
                (
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {
                        "error": {
                            "code": "ai.invocation-observation.not-configured"
                        }
                    },
                ),
            ),
        ):
            with self.subTest(error=type(error).__name__):
                responses.clear()

                def reject(actor, payload, *, selected=error):
                    raise selected

                handler.runtime.ai_invocation_observation.observe = reject
                handler.do_POST()
                self.assertEqual(responses, [expected])

    def test_python_sdk_uses_post_body_and_parses_observation(self) -> None:
        request = AiEconomicsInvocationObservationRequest.from_dict(self.request())
        response = fixture("ai-economics-invocation-observation")
        calls: list[tuple[str, dict]] = []
        client = Client("https://control.example", "observation-token-0123456789")
        client._post = lambda path, payload, correlation_id=None: (  # type: ignore[method-assign]
            calls.append((path, dict(payload))) or response
        )
        observed = client.observe_ai_economics_invocation(request)
        self.assertIsInstance(observed, AiEconomicsInvocationObservation)
        self.assertEqual(observed.status, "complete")
        self.assertEqual(
            calls,
            [
                (
                    "/v1/operations/ai-economics/invocation-observations",
                    self.request(),
                )
            ],
        )

        invalid = self.request()
        invalid["spec"]["traceId"] = "A" * 32
        with self.assertRaises(ValueError):
            AiEconomicsInvocationObservationRequest.from_dict(invalid)

    def test_openapi_uses_post_body_and_public_schemas(self) -> None:
        document = json.loads(
            (ROOT / "api" / "openapi" / "control-plane.openapi.json").read_text(
                encoding="utf-8"
            )
        )
        operation = document["paths"][
            "/v1/operations/ai-economics/invocation-observations"
        ]["post"]
        self.assertTrue(operation["requestBody"]["required"])
        self.assertEqual(
            operation["requestBody"]["content"]["application/json"]["schema"][
                "$ref"
            ],
            "../../contracts/schemas/ai-economics-invocation-observation-request.schema.json",
        )
        self.assertNotIn("parameters", operation)


@unittest.skipUnless(
    DATABASE_URL and psycopg is not None and PostgresResourceStore is not None,
    "set IIP_TEST_DATABASE_URL to run PostgreSQL AI invocation observation tests",
)
class AiInvocationObservationPostgresTests(AiInvocationObservationTests):
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
        self.policy = fixture("ai-attribution-policy")
        self.catalog = fixture("ai-price-catalog")
        self.actor = ActorContext("operator", "local", ("platform-admin",))


if __name__ == "__main__":
    unittest.main()
