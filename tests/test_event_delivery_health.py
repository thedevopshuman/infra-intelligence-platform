from __future__ import annotations

import json
import unittest
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from infra_intelligence_sdk import Client, EventDeliveryHealthReport
from iip.adapters.memory import AllowTenantPolicy
from iip.application.deliver_events import EventDeliveryService
from iip.application.ingest_resource import IngestResourceCommand
from iip.application.ports import (
    ActorContext,
    EventDeliveryState,
    EventPublicationError,
    QuarantinedOutboxMessage,
)
from iip.application.query_event_delivery_health import (
    EventDeliveryHealthAuthorizationError,
    EventDeliveryHealthService,
    EventDeliveryHealthStateError,
    GetEventDeliveryHealthCommand,
)
from iip.bootstrap import build_local_runtime
from iip.domain.models import PlatformEvent
from iip.surfaces.http import ApiHandler


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "event-health-token-0123456789abcdef0123456789abcdef"


def document(name: str) -> dict[str, object]:
    return json.loads(
        (ROOT / "contracts" / "examples" / name).read_text(encoding="utf-8")
    )


class Clock:
    def now(self) -> str:
        return "2026-08-17T13:00:00Z"


class FailingPublisher:
    def publish(self, event: PlatformEvent) -> None:
        del event
        raise EventPublicationError("event.publisher.unavailable")


class EventDeliveryHealthTests(unittest.TestCase):
    def test_failed_final_attempt_is_visible_and_not_claimed_again(self) -> None:
        runtime = build_local_runtime()
        actor = ActorContext("operator", "local", ("platform-admin",))
        resource = document("resource.json")
        runtime.ingestion.execute(IngestResourceCommand(actor, resource))
        backlog = runtime.event_delivery_health.get(
            GetEventDeliveryHealthCommand(actor)
        ).to_dict()
        self.assertEqual(backlog["spec"]["status"], "backlogged")
        self.assertGreaterEqual(
            backlog["spec"]["delivery"]["oldestPendingEventAgeSeconds"],
            0,
        )
        delivery = EventDeliveryService(
            runtime.outbox,
            FailingPublisher(),
            worker_id="worker-1",
            max_attempts=1,
        )

        summary = delivery.run_once("local")
        report = runtime.event_delivery_health.get(
            GetEventDeliveryHealthCommand(actor)
        ).to_dict()

        self.assertEqual(summary.quarantined, 1)
        self.assertEqual(report["spec"]["status"], "degraded")
        self.assertEqual(report["spec"]["delivery"]["pendingEvents"], 0)
        self.assertEqual(report["spec"]["delivery"]["quarantinedEvents"], 1)
        item = report["spec"]["quarantine"]["items"][0]
        self.assertEqual(item["attempts"], 1)
        self.assertEqual(item["lastErrorCode"], "event.publisher.unavailable")
        self.assertNotIn("data", item)
        self.assertEqual(delivery.run_once("local").claimed, 0)
        runtime.close()

    def test_report_matches_contract_shape_and_requires_role(self) -> None:
        example = document("event-delivery-health-report.json")
        class Outbox:
            def get_event_delivery_state(self, tenant_id, *, quarantine_limit):
                self.call = (tenant_id, quarantine_limit)
                return EventDeliveryState(
                    tenant_id="tenant-acme",
                    pending_events=3,
                    in_flight_events=1,
                    retrying_events=2,
                    quarantined_events=1,
                    oldest_pending_event_recorded_at="2026-08-17T12:58:25Z",
                    quarantined=(
                        QuarantinedOutboxMessage(
                            message_id=1042,
                            tenant_id="tenant-acme",
                            event_id="evt_3b03ac5bb4d94fd3b574ebee8ed5c14f",
                            event_source="urn:iip:resource-ingestion",
                            event_type="io.iip.resource.observed.v1",
                            subject="res_e0ae9225a316fce4c97df5c23057b97a",
                            attempts=8,
                            quarantined_at="2026-08-17T12:58:45Z",
                            last_error_code="event.publisher.unavailable",
                        ),
                    ),
                )

        outbox = Outbox()
        service = EventDeliveryHealthService(outbox, AllowTenantPolicy(), Clock())
        operator = ActorContext(
            "operator", "tenant-acme", ("platform-admin",)
        )

        report = service.get(GetEventDeliveryHealthCommand(operator)).to_dict()

        self.assertEqual(report, example)
        self.assertEqual(outbox.call, ("tenant-acme", 50))
        with self.assertRaises(EventDeliveryHealthAuthorizationError):
            service.get(
                GetEventDeliveryHealthCommand(
                    ActorContext("viewer", "tenant-acme", ("developer",))
                )
            )

    def test_cross_tenant_or_inconsistent_state_fails_closed(self) -> None:
        class Outbox:
            def get_event_delivery_state(self, tenant_id, *, quarantine_limit):
                del tenant_id, quarantine_limit
                return EventDeliveryState(
                    tenant_id="another-tenant",
                    pending_events=0,
                    in_flight_events=1,
                    retrying_events=0,
                    quarantined_events=0,
                    oldest_pending_event_recorded_at=None,
                    quarantined=(),
                )

        service = EventDeliveryHealthService(Outbox(), AllowTenantPolicy(), Clock())
        with self.assertRaisesRegex(
            EventDeliveryHealthStateError,
            "event.delivery-health.state-invalid",
        ):
            service.get(
                GetEventDeliveryHealthCommand(
                    ActorContext(
                        "operator", "tenant-acme", ("platform-admin",)
                    )
                )
            )

    def test_malformed_adapter_metadata_is_replaced_by_stable_error(self) -> None:
        class Outbox:
            def get_event_delivery_state(self, tenant_id, *, quarantine_limit):
                del quarantine_limit
                return EventDeliveryState(
                    tenant_id=tenant_id,
                    pending_events=0,
                    in_flight_events=0,
                    retrying_events=0,
                    quarantined_events=1,
                    oldest_pending_event_recorded_at=None,
                    quarantined=(
                        QuarantinedOutboxMessage(
                            message_id="provider-value",  # type: ignore[arg-type]
                            tenant_id=tenant_id,
                            event_id="evt_valid_shape",
                            event_source="urn:iip:test",
                            event_type="io.iip.resource.observed.v1",
                            subject="res_valid_shape",
                            attempts=8,
                            quarantined_at="2026-08-17T12:58:45Z",
                            last_error_code="event.publisher.unavailable",
                        ),
                    ),
                )

        service = EventDeliveryHealthService(Outbox(), AllowTenantPolicy(), Clock())
        with self.assertRaisesRegex(
            EventDeliveryHealthStateError,
            "event.delivery-health.state-invalid",
        ):
            service.get(
                GetEventDeliveryHealthCommand(
                    ActorContext("operator", "tenant-acme", ("platform-admin",))
                )
            )


class EventDeliveryHealthHttpAndSdkTests(unittest.TestCase):
    def _handler(self, roles: tuple[str, ...]) -> ApiHandler:
        class Authenticator:
            def authenticate_bearer(self, token: str) -> ActorContext:
                if token != TOKEN:
                    raise AssertionError(token)
                return ActorContext("operator", "local", roles)

        handler = object.__new__(ApiHandler)
        handler.runtime = build_local_runtime(Authenticator())
        handler.path = "/v1/operations/events/delivery-health?limit=25"
        handler.headers = {"authorization": f"Bearer {TOKEN}"}
        handler.responses = []
        handler._json = lambda status, body: handler.responses.append((status, body))
        return handler

    def test_http_is_tenant_scoped_role_bound_and_query_closed(self) -> None:
        handler = self._handler(("platform-admin",))
        handler.do_GET()

        self.assertEqual(handler.responses[0][0], HTTPStatus.OK)
        report = handler.responses[0][1]
        self.assertEqual(report["metadata"]["tenantId"], "local")
        self.assertEqual(report["spec"]["status"], "healthy")
        self.assertEqual(report["spec"]["quarantine"]["limit"], 25)

        denied = self._handler(("developer",))
        denied.do_GET()
        self.assertEqual(
            denied.responses[0],
            (HTTPStatus.FORBIDDEN, {"error": {"code": "policy.denied"}}),
        )

        for suffix in ("?tenantId=another", "?limit=0", "?limit=10&limit=20"):
            with self.subTest(suffix=suffix):
                invalid = self._handler(("platform-admin",))
                invalid.path = "/v1/operations/events/delivery-health" + suffix
                invalid.do_GET()
                self.assertEqual(invalid.responses[0][0], HTTPStatus.BAD_REQUEST)

    def test_python_sdk_calls_operator_route_and_parses_contract(self) -> None:
        payload = document("event-delivery-health-report.json")

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
            report = client.get_event_delivery_health(limit=25)

        self.assertIsInstance(report, EventDeliveryHealthReport)
        self.assertEqual(report.to_dict(), payload)
        request = send.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://control.example/v1/operations/events/delivery-health?limit=25",
        )


if __name__ == "__main__":
    unittest.main()
