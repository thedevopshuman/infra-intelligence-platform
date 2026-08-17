from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from infra_intelligence_sdk import Client, EventDeliverySloReport
from iip.adapters.memory import AllowTenantPolicy
from iip.application.ingest_resource import IngestResourceCommand
from iip.application.ports import ActorContext, EventDeliverySloState
from iip.application.query_event_delivery_slo import (
    EventDeliverySloAuthorizationError,
    EventDeliverySloObjectives,
    EventDeliverySloService,
    EventDeliverySloStateError,
    GetEventDeliverySloCommand,
)
from iip.bootstrap import build_local_runtime
from iip.surfaces.http import ApiHandler


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "event-slo-token-0123456789abcdef0123456789abcdef"


def document(name: str) -> dict[str, object]:
    return json.loads(
        (ROOT / "contracts" / "examples" / name).read_text(encoding="utf-8")
    )


class FixedClock:
    def __init__(self, value: str = "2026-08-17T13:00:00Z") -> None:
        self.value = value

    def now(self) -> str:
        return self.value


class EventDeliverySloTests(unittest.TestCase):
    def test_report_matches_contract_and_uses_exact_tenant_window(self) -> None:
        expected = document("event-delivery-slo-report.json")

        class Outbox:
            def get_event_delivery_slo_state(self, tenant_id, **parameters):
                self.call = (tenant_id, parameters)
                return EventDeliverySloState(
                    tenant_id="tenant-acme",
                    window_start="2026-08-17T12:00:00Z",
                    window_end="2026-08-17T13:00:00Z",
                    maturity_cutoff="2026-08-17T12:59:00Z",
                    created_events=102,
                    immature_events=2,
                    eligible_events=100,
                    within_objective_events=99,
                    late_delivered_events=0,
                    undelivered_events=1,
                    quarantined_events=1,
                )

        outbox = Outbox()
        service = EventDeliverySloService(
            outbox,
            AllowTenantPolicy(),
            FixedClock(),
        )
        actor = ActorContext("operator", "tenant-acme", ("platform-admin",))

        report = service.get(GetEventDeliverySloCommand(actor)).to_dict()

        self.assertEqual(report, expected)
        self.assertEqual(
            outbox.call,
            (
                "tenant-acme",
                {
                    "window_start": "2026-08-17T12:00:00Z",
                    "window_end": "2026-08-17T13:00:00Z",
                    "maturity_cutoff": "2026-08-17T12:59:00Z",
                    "latency_objective_seconds": 60,
                },
            ),
        )
        with self.assertRaises(EventDeliverySloAuthorizationError):
            service.get(
                GetEventDeliverySloCommand(
                    ActorContext("viewer", "tenant-acme", ("developer",))
                )
            )

    def test_empty_low_sample_and_breach_statuses_are_explicit(self) -> None:
        class Outbox:
            state = EventDeliverySloState(
                tenant_id="tenant-acme",
                window_start="2026-08-17T12:00:00Z",
                window_end="2026-08-17T13:00:00Z",
                maturity_cutoff="2026-08-17T12:59:00Z",
                created_events=0,
                immature_events=0,
                eligible_events=0,
                within_objective_events=0,
                late_delivered_events=0,
                undelivered_events=0,
                quarantined_events=0,
            )

            def get_event_delivery_slo_state(self, tenant_id, **parameters):
                del tenant_id, parameters
                return self.state

        outbox = Outbox()
        service = EventDeliverySloService(
            outbox,
            AllowTenantPolicy(),
            FixedClock(),
        )
        actor = ActorContext("operator", "tenant-acme", ("platform-admin",))

        self.assertEqual(
            service.get(GetEventDeliverySloCommand(actor)).to_dict()["spec"]["status"],
            "no-data",
        )
        outbox.state = EventDeliverySloState(
            **{
                **outbox.state.__dict__,
                "created_events": 2,
                "eligible_events": 2,
                "within_objective_events": 1,
                "undelivered_events": 1,
            }
        )
        self.assertEqual(
            service.get(GetEventDeliverySloCommand(actor)).to_dict()["spec"]["status"],
            "insufficient-data",
        )
        outbox.state = EventDeliverySloState(
            **{
                **outbox.state.__dict__,
                "created_events": 20,
                "eligible_events": 20,
                "within_objective_events": 19,
                "undelivered_events": 1,
            }
        )
        report = service.get(GetEventDeliverySloCommand(actor)).to_dict()
        self.assertEqual(report["spec"]["status"], "breached")
        self.assertEqual(
            report["spec"]["measurement"]["attainmentBasisPoints"],
            9500,
        )

    def test_inconsistent_or_cross_tenant_aggregate_fails_closed(self) -> None:
        class Outbox:
            def get_event_delivery_slo_state(self, tenant_id, **parameters):
                del tenant_id
                return EventDeliverySloState(
                    tenant_id="another-tenant",
                    window_start=parameters["window_start"],
                    window_end=parameters["window_end"],
                    maturity_cutoff=parameters["maturity_cutoff"],
                    created_events=1,
                    immature_events=0,
                    eligible_events=1,
                    within_objective_events=1,
                    late_delivered_events=1,
                    undelivered_events=0,
                    quarantined_events=0,
                )

        service = EventDeliverySloService(
            Outbox(), AllowTenantPolicy(), FixedClock()
        )
        with self.assertRaisesRegex(
            EventDeliverySloStateError,
            "event.delivery-slo.state-invalid",
        ):
            service.get(
                GetEventDeliverySloCommand(
                    ActorContext("operator", "tenant-acme", ("platform-admin",))
                )
            )

    def test_in_memory_outcomes_count_each_event_once(self) -> None:
        objectives = EventDeliverySloObjectives(
            window_seconds=300,
            maximum_delivery_latency_seconds=1,
            minimum_attainment_basis_points=6000,
            minimum_eligible_events=2,
        )
        runtime = build_local_runtime(event_delivery_slo_objectives=objectives)
        actor = ActorContext("operator", "local", ("platform-admin",))
        first = document("resource.json")
        second = copy.deepcopy(first)
        second["metadata"]["observation"]["sequence"] = 43
        second["spec"]["externalId"] = "cluster-local/default/worker"
        second["spec"]["displayName"] = "worker"
        runtime.ingestion.execute(IngestResourceCommand(actor, first))
        runtime.ingestion.execute(IngestResourceCommand(actor, second))
        messages = tuple(runtime.outbox.claim_outbox("local", "publisher"))
        self.assertEqual(len(messages), 2)
        self.assertTrue(
            runtime.outbox.acknowledge_outbox(
                "local", "publisher", messages[0].message_id
            )
        )
        self.assertTrue(
            runtime.outbox.quarantine_outbox(
                "local",
                "publisher",
                messages[1].message_id,
                "event.publisher.unavailable",
            )
        )
        future = datetime.now(timezone.utc) + timedelta(seconds=3)
        service = EventDeliverySloService(
            runtime.outbox,
            AllowTenantPolicy(),
            FixedClock(future.isoformat().replace("+00:00", "Z")),
            objectives,
        )

        report = service.get(GetEventDeliverySloCommand(actor)).to_dict()
        measurement = report["spec"]["measurement"]

        self.assertEqual(report["spec"]["status"], "breached")
        self.assertEqual(measurement["eligibleEvents"], 2)
        self.assertEqual(measurement["withinObjectiveEvents"], 1)
        self.assertEqual(measurement["undeliveredEvents"], 1)
        self.assertEqual(measurement["quarantinedEvents"], 1)
        self.assertEqual(measurement["attainmentBasisPoints"], 5000)
        runtime.close()

    def test_configuration_is_bounded(self) -> None:
        for changes in (
            {"window_seconds": 299},
            {"maximum_delivery_latency_seconds": 3600},
            {"minimum_attainment_basis_points": 0},
            {"minimum_eligible_events": 0},
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(
                ValueError, "event.delivery-slo.configuration.invalid"
            ):
                EventDeliverySloObjectives(**changes)


class EventDeliverySloHttpAndSdkTests(unittest.TestCase):
    def _handler(self, roles: tuple[str, ...]) -> ApiHandler:
        class Authenticator:
            def authenticate_bearer(self, token: str) -> ActorContext:
                if token != TOKEN:
                    raise AssertionError(token)
                return ActorContext("operator", "local", roles)

        handler = object.__new__(ApiHandler)
        handler.runtime = build_local_runtime(Authenticator())
        handler.path = "/v1/operations/events/delivery-slo"
        handler.headers = {"authorization": f"Bearer {TOKEN}"}
        handler.responses = []
        handler._json = lambda status, body: handler.responses.append((status, body))
        return handler

    def test_http_is_tenant_scoped_role_bound_and_query_closed(self) -> None:
        handler = self._handler(("platform-admin",))
        handler.do_GET()
        self.assertEqual(handler.responses[0][0], HTTPStatus.OK)
        self.assertEqual(handler.responses[0][1]["metadata"]["tenantId"], "local")
        self.assertEqual(handler.responses[0][1]["spec"]["status"], "no-data")

        denied = self._handler(("developer",))
        denied.do_GET()
        self.assertEqual(
            denied.responses[0],
            (HTTPStatus.FORBIDDEN, {"error": {"code": "policy.denied"}}),
        )
        invalid = self._handler(("platform-admin",))
        invalid.path += "?tenantId=another"
        invalid.do_GET()
        self.assertEqual(invalid.responses[0][0], HTTPStatus.BAD_REQUEST)

    def test_python_sdk_calls_slo_route_and_parses_contract(self) -> None:
        payload = document("event-delivery-slo-report.json")

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(payload).encode("utf-8")

        client = Client("https://control.example", TOKEN)
        with patch(
            "infra_intelligence_sdk.client.urlopen", return_value=Response()
        ) as send:
            report = client.get_event_delivery_slo()

        self.assertIsInstance(report, EventDeliverySloReport)
        self.assertEqual(report.to_dict(), payload)
        request = send.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://control.example/v1/operations/events/delivery-slo",
        )


if __name__ == "__main__":
    unittest.main()
