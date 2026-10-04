from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from urllib.parse import urlencode

from jsonschema import Draft202012Validator, FormatChecker

from iip.application.ports import (
    ActorContext, AiHistoryAvailabilityQuery, AiHistoryAvailabilityState,
    AiHistoryRetiredError, AuthenticationError, PersistenceError, PolicyDecision,
)
from iip.application.query_ai_allocations import AiAllocationProjectionService
from iip.application.query_ai_history import (
    AiHistoryAuthorizationError, AiHistoryAvailabilityService, AiHistoryQueryError,
)
from iip.bootstrap import build_local_runtime
from iip.surfaces.http import ApiHandler


ROOT = Path(__file__).resolve().parents[1]
START, END = "2026-09-05T09:00:00Z", "2026-09-05T11:00:00Z"
ACTOR = ActorContext("operator", "local", ("platform-admin",))


def fixture(name):
    return json.loads((ROOT / "contracts/examples" / f"{name}.json").read_text())


class AiHistoryAvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.store = Mock()
        self.store.get_ai_history_availability.return_value = AiHistoryAvailabilityState(3, 0)
        self.clock = SimpleNamespace(now=lambda: END)
        self.policy = Mock()
        self.policy.decide.return_value = PolicyDecision(True, "allowed")
        self.service = AiHistoryAvailabilityService(self.store, self.policy, self.clock)

    def report(self, actor=ACTOR, **changes):
        return self.service.get(actor, **{"start": START, "end": END, **changes})

    def test_retained_retired_and_empty_are_explicit_and_minimized(self):
        schema = json.loads((ROOT / "contracts/schemas/ai-history-availability-report.schema.json").read_text())
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        for retained, retired in ((3, 0), (2, 1), (0, 0), (0, 3)):
            with self.subTest(retained=retained, retired=retired):
                self.store.get_ai_history_availability.return_value = AiHistoryAvailabilityState(retained, retired)
                report = self.report()
                validator.validate(report)
                self.assertEqual(report["spec"]["status"], "history-retired" if retired else "available")
                self.assertEqual(report["spec"]["coverage"], {
                    "retainedUsageRecords": retained, "retiredUsageRecords": retired,
                })
                self.assertEqual(report["spec"]["scope"], {"start": START, "end": END})
                self.assertEqual(set(report["spec"]), {"scope", "status", "coverage"})
        self.store.get_ai_history_availability.assert_called_with(ACTOR, AiHistoryAvailabilityQuery(START, END))
        self.policy.decide.assert_called_with(ACTOR, "ai-economics:read", {
            "tenantId": "local", "start": START, "end": END,
        })

    def test_closed_actor_and_interval_validation_precedes_policy_and_storage(self):
        for actor, changes in (
            (ActorContext("anonymous", "local", ()), {}),
            (ActorContext("operator", "local:bad", ()), {}),
            (ActorContext("operator", "local", (["invalid"],)), {}),
            (ActorContext("operator", "local", ("admin", "admin")), {}),
            (ACTOR, {"start": START.replace("Z", "+00:00")}),
            (ACTOR, {"start": START.replace("09:", "9:")}),
            (ACTOR, {"start": START.replace("Z", ".0Z")}),
            (ACTOR, {"end": START}),
            (ACTOR, {"start": END, "end": START}),
            (ACTOR, {"start": "2026-08-01T00:00:00Z"}),
            (ACTOR, {"end": None}),
        ):
            with self.subTest(actor=actor, changes=changes), self.assertRaisesRegex(AiHistoryQueryError, "request.invalid"):
                self.report(actor, **changes)
        self.store.get_ai_history_availability.assert_not_called()
        self.policy.decide.assert_not_called()

    def test_exact_31_day_bound_is_allowed(self):
        report = self.report(start="2026-08-05T11:00:00Z")
        self.assertEqual(report["spec"]["status"], "available")

    def test_canonical_microseconds_are_preserved(self):
        report = self.report(start="2026-09-05T09:00:00.000001Z")
        self.assertEqual(report["spec"]["scope"]["start"], "2026-09-05T09:00:00.000001Z")

    def test_policy_requires_literal_allow_before_reading(self):
        for allowed in (False, None, 1, "true"):
            self.policy.decide.return_value = PolicyDecision(allowed, "sensitive provider denial")
            with self.subTest(allowed=allowed), self.assertRaisesRegex(AiHistoryAuthorizationError, "^policy.denied$"):
                self.report()
        self.store.get_ai_history_availability.assert_not_called()

    def test_untrusted_store_counts_fail_closed(self):
        for value in (None, {"retained_usage_records": 0}, AiHistoryAvailabilityState(True, 0),
                      AiHistoryAvailabilityState(-1, 0), AiHistoryAvailabilityState(0, 2**53),
                      AiHistoryAvailabilityState(1, 0.5)):
            self.store.get_ai_history_availability.return_value = value
            with self.subTest(value=value), self.assertRaisesRegex(PersistenceError, "storage.state.invalid"):
                self.report()

    def test_http_route_and_errors_are_closed(self):
        handler = object.__new__(ApiHandler)
        handler.runtime = SimpleNamespace(ai_history_availability=self.service)
        handler._actor = lambda: ACTOR
        results = []
        handler._json = lambda status, body: results.append((int(status), body))
        query = urlencode({"start": START, "end": END})
        handler.path = "/v1/ai/economics/history-availability?" + query
        handler.do_GET()
        self.assertEqual(results[-1][0], 200)
        for invalid in (query + "&tenantId=another", query + "&start=" + START, "", "start=&end=" + END):
            handler._query_ai_history_availability(ACTOR, invalid)
            self.assertEqual(results[-1], (400, {"error": {"code": "request.invalid"}}))
        self.policy.decide.return_value = PolicyDecision(False, "sensitive")
        handler._query_ai_history_availability(ACTOR, query)
        self.assertEqual(results[-1], (403, {"error": {"code": "policy.denied"}}))
        self.policy.decide.return_value = PolicyDecision(True, "allowed")
        self.store.get_ai_history_availability.side_effect = PersistenceError("private SQL")
        handler._query_ai_history_availability(ACTOR, query)
        self.assertEqual(results[-1], (503, {"error": {"code": "storage.unavailable"}}))

    def test_retired_allocation_and_exact_invocation_return_gone(self):
        handler = object.__new__(ApiHandler)
        retired = Mock(side_effect=AiHistoryRetiredError())
        handler.runtime = SimpleNamespace(
            ai_allocation_reports=SimpleNamespace(get=retired),
            ai_invocation_observation=SimpleNamespace(observe=retired),
        )
        results = []
        handler._json = lambda status, body: results.append((int(status), body))
        handler._query_ai_allocation(ACTOR, urlencode({"start": START, "end": END, "groupBy": "team"}))
        handler._actor = lambda: ACTOR
        handler._read_json = lambda: fixture("ai-economics-invocation-observation-request")
        handler.path = "/v1/operations/ai-economics/invocation-observations"
        handler.do_POST()
        self.assertEqual(results, [(410, {"error": {"code": "ai.history.retired"}})] * 2)

    def test_unauthenticated_history_never_reaches_the_service(self):
        handler = object.__new__(ApiHandler)
        handler.path = "/v1/ai/economics/history-availability?" + urlencode({"start": START, "end": END})
        handler.runtime = SimpleNamespace(ai_history_availability=Mock())
        handler._actor = Mock(side_effect=AuthenticationError("auth.required"))
        handler._authentication_failed = Mock()
        handler.do_GET()
        handler._authentication_failed.assert_called_once()
        handler.runtime.ai_history_availability.get.assert_not_called()

    def test_retired_projection_never_replaces_telemetry_with_partial_totals(self):
        store = Mock()
        store.list_ai_allocation_rows.side_effect = AiHistoryRetiredError()
        sink = Mock()
        projection = AiAllocationProjectionService(
            store, self.clock, (fixture("ai-attribution-policy"),),
            (fixture("ai-price-catalog"),), sink, allow_test_fixtures=True,
        )
        with self.assertRaises(AiHistoryRetiredError):
            projection.run_once("local", "history-test")
        sink.record_ai_allocation_snapshot.assert_not_called()

    def test_composition_exposes_read_service_without_pricing_configuration(self):
        runtime = build_local_runtime()
        report = runtime.ai_history_availability.get(ACTOR, start=START, end=END)
        self.assertEqual(report["spec"]["coverage"], {"retainedUsageRecords": 0, "retiredUsageRecords": 0})

    def test_openapi_and_schema_preserve_success_contracts(self):
        api = json.loads((ROOT / "api/openapi/control-plane.openapi.json").read_text())
        for path, method in (("/v1/ai/economics/allocation", "get"), ("/v1/operations/ai-economics/invocation-observations", "post")):
            self.assertEqual(api["paths"][path][method]["responses"]["410"], {"$ref": "#/components/responses/AiHistoryRetired"})
        operation = api["paths"]["/v1/ai/economics/history-availability"]["get"]
        self.assertEqual([p["name"] for p in operation["parameters"]], ["start", "end"])
        example = fixture("ai-history-availability-report")
        schema = json.loads((ROOT / "contracts/schemas/ai-history-availability-report.schema.json").read_text())
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        validator.validate(example)
        for changes in (
            {"unknown": "must reject"},
            {"status": "available", "coverage": {"retainedUsageRecords": 1, "retiredUsageRecords": 1}},
            {"status": "history-retired", "coverage": {"retainedUsageRecords": 0, "retiredUsageRecords": 0}},
            {"coverage": {"retainedUsageRecords": True, "retiredUsageRecords": 0}},
            {"coverage": {"retainedUsageRecords": 0, "retiredUsageRecords": -1}},
        ):
            document = copy.deepcopy(example)
            document["spec"].update(changes)
            self.assertFalse(validator.is_valid(document), changes)


if __name__ == "__main__":
    unittest.main()
