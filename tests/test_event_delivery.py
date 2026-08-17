from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iip.adapters.event_publisher import (
    HttpsCloudEventsPublisher,
    HttpsEventPublisherConfiguration,
    StructuredLogEventPublisher,
)
from iip.application.deliver_events import EventDeliveryService
from iip.application.ports import (
    EventPublicationError,
    EventPublisherConfigurationError,
    OutboxMessage,
    PersistenceError,
)
from iip.bootstrap import build_event_delivery_from_env, build_local_runtime
from iip.domain.models import PlatformEvent


ROOT = Path(__file__).resolve().parents[1]


def event(event_id: str, tenant_id: str = "tenant-a") -> PlatformEvent:
    return PlatformEvent(
        event_id=event_id,
        event_type="io.iip.resource.observed.v1",
        source="urn:iip:test",
        time="2026-08-17T10:00:00Z",
        subject="res_" + "1" * 32,
        tenant_id=tenant_id,
        data={"resourceUid": "res_" + "1" * 32},
    )


class _Outbox:
    def __init__(self, messages, *, acknowledge=True, release=True) -> None:
        self.messages = tuple(messages)
        self.acknowledge = acknowledge
        self.release = release
        self.claims = []
        self.acknowledgements = []
        self.releases = []
        self.quarantines = []

    def claim_outbox(self, tenant_id, worker_id, *, limit, lease_seconds):
        self.claims.append((tenant_id, worker_id, limit, lease_seconds))
        return self.messages

    def acknowledge_outbox(self, tenant_id, worker_id, message_id):
        self.acknowledgements.append((tenant_id, worker_id, message_id))
        return self.acknowledge

    def release_outbox(
        self,
        tenant_id,
        worker_id,
        message_id,
        error_code,
        *,
        retry_after_seconds,
    ):
        self.releases.append(
            (
                tenant_id,
                worker_id,
                message_id,
                error_code,
                retry_after_seconds,
            )
        )
        return self.release

    def quarantine_outbox(
        self, tenant_id, worker_id, message_id, error_code
    ):
        self.quarantines.append(
            (tenant_id, worker_id, message_id, error_code)
        )
        return self.release


class _Publisher:
    def __init__(self, failures=()) -> None:
        self.failures = frozenset(failures)
        self.events = []

    def publish(self, item) -> None:
        self.events.append(item)
        if item.event_id in self.failures:
            raise EventPublicationError("event.publisher.unavailable")


class EventDeliveryServiceTests(unittest.TestCase):
    def test_success_is_acknowledged_and_failure_is_released_with_backoff(self) -> None:
        outbox = _Outbox(
            (
                OutboxMessage(1, event("event-failed"), 3),
                OutboxMessage(2, event("event-delivered"), 1),
            )
        )
        publisher = _Publisher(("event-failed",))
        service = EventDeliveryService(
            outbox,
            publisher,
            worker_id="worker-1",
            retry_base_seconds=5,
            retry_max_seconds=300,
        )

        summary = service.run_once("tenant-a")

        self.assertEqual(
            (summary.claimed, summary.delivered, summary.released, summary.ambiguous),
            (2, 1, 1, 0),
        )
        self.assertEqual(outbox.acknowledgements, [("tenant-a", "worker-1", 2)])
        self.assertEqual(
            outbox.releases,
            [
                (
                    "tenant-a",
                    "worker-1",
                    1,
                    "event.publisher.unavailable",
                    20,
                )
            ],
        )

    def test_final_failure_is_quarantined_and_never_released(self) -> None:
        outbox = _Outbox((OutboxMessage(7, event("event-terminal"), 8),))
        service = EventDeliveryService(
            outbox,
            _Publisher(("event-terminal",)),
            worker_id="worker-1",
            max_attempts=8,
        )

        summary = service.run_once("tenant-a")

        self.assertEqual(
            (summary.claimed, summary.released, summary.quarantined, summary.ambiguous),
            (1, 0, 1, 0),
        )
        self.assertEqual(outbox.releases, [])
        self.assertEqual(
            outbox.quarantines,
            [
                (
                    "tenant-a",
                    "worker-1",
                    7,
                    "event.publisher.unavailable",
                )
            ],
        )

    def test_lost_lease_is_ambiguous_and_cross_tenant_claim_is_corrupt(self) -> None:
        outbox = _Outbox(
            (OutboxMessage(1, event("event-a"), 1),), acknowledge=False
        )
        summary = EventDeliveryService(
            outbox, _Publisher(), worker_id="worker-1"
        ).run_once("tenant-a")
        self.assertEqual((summary.delivered, summary.ambiguous), (0, 1))

        corrupt = _Outbox((OutboxMessage(2, event("event-b", "tenant-b"), 1),))
        with self.assertRaisesRegex(PersistenceError, "storage.corrupt"):
            EventDeliveryService(
                corrupt, _Publisher(), worker_id="worker-1"
            ).run_once("tenant-a")

    def test_configuration_and_tenant_scope_are_bounded(self) -> None:
        for worker_id, batch_size, lease, retry_base, retry_max in (
            ("bad worker", 100, 30, 5, 300),
            ("worker-1", 0, 30, 5, 300),
            ("worker-1", 100, 4, 5, 300),
            ("worker-1", 100, 30, 0, 300),
            ("worker-1", 100, 30, 10, 5),
        ):
            with self.subTest(worker_id=worker_id, batch_size=batch_size):
                with self.assertRaisesRegex(
                    ValueError, "event.delivery.configuration.invalid"
                ):
                    EventDeliveryService(
                        _Outbox(()),
                        _Publisher(),
                        worker_id=worker_id,
                        batch_size=batch_size,
                        lease_seconds=lease,
                        retry_base_seconds=retry_base,
                        retry_max_seconds=retry_max,
                    )
        with self.assertRaisesRegex(
            ValueError, "event.delivery.configuration.invalid"
        ):
            EventDeliveryService(
                _Outbox(()),
                _Publisher(),
                worker_id="worker-1",
                max_attempts=0,
            )
        service = EventDeliveryService(
            _Outbox(()), _Publisher(), worker_id="worker-1"
        )
        for tenant_id in ("", "*", "bad tenant"):
            with self.subTest(tenant_id=tenant_id):
                with self.assertRaisesRegex(
                    ValueError, "event.delivery.tenant.invalid"
                ):
                    service.run_once(tenant_id)


class _Transport:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = []

    def post(self, endpoint, body, headers, context, timeout_seconds, max_response_bytes):
        del context
        self.calls.append(
            (endpoint, body, dict(headers), timeout_seconds, max_response_bytes)
        )
        if self.fail:
            raise OSError("private provider detail")


class EventPublisherAdapterTests(unittest.TestCase):
    def test_structured_log_sink_is_exact_tenant_and_cloud_event_shaped(self) -> None:
        output = io.StringIO()
        publisher = StructuredLogEventPublisher(("tenant-a",), output)
        publisher.publish(event("event-a"))

        document = json.loads(output.getvalue())
        self.assertEqual(document["event"], "outbox.event.published")
        self.assertEqual(document["cloudEvent"]["id"], "event-a")
        with self.assertRaisesRegex(
            EventPublicationError, "event.publisher.tenant-denied"
        ):
            publisher.publish(event("event-b", "tenant-b"))

    def test_https_publisher_reads_rotating_token_and_sends_idempotent_event(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "token"
            token_path.write_text("a" * 32, encoding="utf-8")
            configuration = HttpsEventPublisherConfiguration(
                endpoint="https://events.example.test/v1/cloudevents",
                tenant_ids=("tenant-a",),
                bearer_token_path=str(token_path),
            )
            transport = _Transport()
            publisher = HttpsCloudEventsPublisher(configuration, transport)

            publisher.publish(event("event-a"))
            token_path.write_text("b" * 32, encoding="utf-8")
            publisher.publish(event("event-b"))

        self.assertEqual(len(transport.calls), 2)
        first = transport.calls[0]
        second = transport.calls[1]
        self.assertEqual(first[2]["Authorization"], "Bearer " + "a" * 32)
        self.assertEqual(second[2]["Authorization"], "Bearer " + "b" * 32)
        self.assertEqual(first[2]["Idempotency-Key"], "event-a")
        self.assertEqual(first[2]["Content-Type"], "application/cloudevents+json")
        self.assertEqual(json.loads(first[1])["tenantid"], "tenant-a")

    def test_https_configuration_and_transport_fail_closed(self) -> None:
        invalid = (
            "not-json",
            '{"endpoint":"http://events.test","tenantIds":["tenant-a"],"bearerTokenPath":"/token"}',
            '{"endpoint":"https://events.test/path?secret=x","tenantIds":["tenant-a"],"bearerTokenPath":"/token"}',
            '{"endpoint":"https://events.test","tenantIds":["*"],"bearerTokenPath":"/token"}',
            '{"endpoint":"https://events.test","tenantIds":["tenant-a"],"bearerTokenPath":"relative"}',
            '{"endpoint":"https://events.test","tenantIds":["tenant-a"],"bearerTokenPath":"/token","extra":true}',
        )
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(
                    EventPublisherConfigurationError,
                    "event.publisher.configuration.invalid",
                ):
                    HttpsEventPublisherConfiguration.from_json(raw)

        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "token"
            token_path.write_text("a" * 32, encoding="utf-8")
            publisher = HttpsCloudEventsPublisher(
                HttpsEventPublisherConfiguration(
                    endpoint="https://events.example.test/v1/cloudevents",
                    tenant_ids=("tenant-a",),
                    bearer_token_path=str(token_path),
                ),
                _Transport(fail=True),
            )
            with self.assertRaisesRegex(
                EventPublicationError, "event.publisher.unavailable"
            ):
                publisher.publish(event("event-a"))


class EventPublisherCompositionTests(unittest.TestCase):
    def test_disabled_mode_does_not_parse_delivery_configuration(self) -> None:
        runtime = build_local_runtime()
        self.addCleanup(runtime.close)
        with patch.dict(
            os.environ,
            {
                "IIP_EVENT_PUBLISHER_MODE": "disabled",
                "IIP_OUTBOX_BATCH_SIZE": "invalid",
            },
            clear=True,
        ):
            self.assertIsNone(build_event_delivery_from_env(runtime, ("tenant-a",)))

    def test_stdout_mode_uses_exact_worker_tenants_and_bounded_settings(self) -> None:
        runtime = build_local_runtime()
        self.addCleanup(runtime.close)
        with patch.dict(
            os.environ,
            {
                "IIP_EVENT_PUBLISHER_MODE": "stdout-json",
                "IIP_WORKER_ID": "worker-1",
                "IIP_OUTBOX_BATCH_SIZE": "25",
            },
            clear=True,
        ):
            self.assertIsNotNone(
                build_event_delivery_from_env(runtime, ("tenant-a", "tenant-b"))
            )

    def test_https_mode_requires_exact_worker_tenant_set(self) -> None:
        runtime = build_local_runtime()
        self.addCleanup(runtime.close)
        configuration = {
            "endpoint": "https://events.example.test/v1/cloudevents",
            "tenantIds": ["tenant-a"],
            "bearerTokenPath": "/var/run/secrets/iip-event-publisher/token",
        }
        with patch.dict(
            os.environ,
            {
                "IIP_EVENT_PUBLISHER_MODE": "https-webhook",
                "IIP_EVENT_PUBLISHER_CONFIG_JSON": json.dumps(configuration),
                "IIP_WORKER_ID": "worker-1",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(
                ValueError, "event.publisher.configuration.invalid"
            ):
                build_event_delivery_from_env(runtime, ("tenant-b",))

    def test_publisher_secret_is_worker_only_and_worker_policy_has_no_ingress(self) -> None:
        api_template = (
            ROOT
            / "deploy"
            / "helm"
            / "infra-intelligence"
            / "templates"
            / "deployment.yaml"
        ).read_text(encoding="utf-8")
        worker_template = (
            ROOT
            / "deploy"
            / "helm"
            / "infra-intelligence"
            / "templates"
            / "worker-deployment.yaml"
        ).read_text(encoding="utf-8")
        network_policy = (
            ROOT
            / "deploy"
            / "helm"
            / "infra-intelligence"
            / "templates"
            / "networkpolicy.yaml"
        ).read_text(encoding="utf-8")

        self.assertNotIn("event-publisher-token", api_template)
        self.assertIn("event-publisher-token", worker_template)
        self.assertIn('app.kubernetes.io/component: workflow-worker', network_policy)
        self.assertIn("ingress: []", network_policy)


if __name__ == "__main__":
    unittest.main()
