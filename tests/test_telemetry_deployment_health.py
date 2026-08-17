from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from infra_intelligence_sdk import Client, TelemetryDeploymentExportHealthReport
from iip.adapters.memory import AllowTenantPolicy
from iip.adapters.operations import InMemoryOperationalStore
from iip.adapters.otel import TelemetryExportHealthState
from iip.application.ports import ActorContext, TelemetryExportInstanceState
from iip.application.query_telemetry_deployment_health import (
    GetTelemetryDeploymentHealthCommand,
    TelemetryDeploymentHealthService,
)
from iip.application.query_telemetry_export_health import (
    TelemetryExportHealthAuthorizationError,
)
from iip.application.report_telemetry_export_health import (
    TelemetryExportHealthReporter,
    TelemetryExportHealthReportingConfiguration,
)
from iip.bootstrap import build_local_runtime
from iip.surfaces.http import ApiHandler


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "deployment-health-token-0123456789abcdef0123456789abcdef"
INSTANCE_ID = "sha256:" + "a" * 64


def example() -> dict:
    return json.loads(
        (
            ROOT
            / "contracts/examples/telemetry-deployment-export-health-report.json"
        ).read_text(encoding="utf-8")
    )


class Clock:
    def __init__(self, value: str = "2026-08-17T12:00:00Z") -> None:
        self.value = value

    def now(self) -> str:
        return self.value


class TelemetryDeploymentHealthServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryOperationalStore()
        self.clock = Clock()
        self.health = TelemetryExportHealthState(("metrics",))
        self.configuration = TelemetryExportHealthReportingConfiguration(
            instance_id=INSTANCE_ID,
            component="api",
            interval_seconds=30,
            stale_after_seconds=120,
            retention_seconds=600,
        )
        self.reporter = TelemetryExportHealthReporter(
            self.health,
            self.store,
            self.clock,
            self.configuration,
        )
        self.service = TelemetryDeploymentHealthService(
            self.store,
            AllowTenantPolicy(),
            self.clock,
            stale_after_seconds=120,
            retention_seconds=600,
        )
        self.operator = ActorContext(
            "operator", "local", ("platform-admin",)
        )

    def test_heartbeat_marks_staleness_and_graceful_retirement(self) -> None:
        self.reporter.report_once()
        awaiting = self.service.get(
            GetTelemetryDeploymentHealthCommand(self.operator)
        ).to_dict()
        self.assertEqual(awaiting["spec"]["status"], "awaiting-first-attempt")
        self.assertEqual(awaiting["spec"]["summary"]["currentInstances"], 1)
        self.assertEqual(
            awaiting["spec"]["instances"][0]["instanceId"], INSTANCE_ID
        )
        self.assertNotIn("tenantId", awaiting["metadata"])

        self.health.record_success("metrics")
        self.clock.value = "2026-08-17T12:00:30Z"
        self.reporter.report_once()
        healthy = self.service.get(
            GetTelemetryDeploymentHealthCommand(self.operator)
        ).to_dict()
        self.assertEqual(healthy["spec"]["status"], "healthy")

        self.clock.value = "2026-08-17T12:02:31Z"
        stale = self.service.get(
            GetTelemetryDeploymentHealthCommand(self.operator)
        ).to_dict()
        self.assertEqual(stale["spec"]["status"], "degraded")
        self.assertEqual(stale["spec"]["summary"]["staleInstances"], 1)
        self.assertEqual(stale["spec"]["instances"][0]["freshness"], "stale")

        self.reporter.retire()
        retired = self.service.get(
            GetTelemetryDeploymentHealthCommand(self.operator)
        ).to_dict()
        self.assertEqual(retired["spec"]["status"], "disabled")
        self.assertEqual(retired["spec"]["instances"], [])

    def test_report_is_role_bound_and_configuration_is_bounded(self) -> None:
        with self.assertRaises(TelemetryExportHealthAuthorizationError):
            self.service.get(
                GetTelemetryDeploymentHealthCommand(
                    ActorContext("viewer", "local", ("operator",))
                )
            )
        with self.assertRaises(ValueError):
            TelemetryExportHealthReportingConfiguration(
                instance_id=INSTANCE_ID,
                component="api",
                interval_seconds=30,
                stale_after_seconds=40,
                retention_seconds=600,
            ).validate()

        TelemetryExportHealthReportingConfiguration(
            instance_id=INSTANCE_ID,
            component="otlp-receiver",
        ).validate()

    def test_repository_filters_expired_instances_and_bounds_reads(self) -> None:
        state = TelemetryExportInstanceState(
            instance_id=INSTANCE_ID,
            component="workflow-worker",
            started_at="2026-08-17T11:00:00Z",
            last_reported_at="2026-08-17T11:01:00Z",
            signals=self.health.read_export_health(),
        )
        self.store.record_telemetry_export_health(
            state,
            expire_before="2026-08-17T10:00:00Z",
            sample_expire_before="2026-08-10T10:00:00Z",
        )
        self.assertEqual(
            self.store.list_telemetry_export_health(
                reported_since="2026-08-17T11:02:00Z", limit=1001
            ),
            (),
        )
        with self.assertRaises(Exception):
            self.store.list_telemetry_export_health(
                reported_since="2026-08-17T11:02:00Z", limit=1002
            )

    def test_receiver_component_round_trips_through_deployment_report(self) -> None:
        configuration = TelemetryExportHealthReportingConfiguration(
            instance_id=INSTANCE_ID,
            component="otlp-receiver",
        )
        TelemetryExportHealthReporter(
            self.health,
            self.store,
            self.clock,
            configuration,
        ).report_once()

        report = self.service.get(
            GetTelemetryDeploymentHealthCommand(self.operator)
        ).to_dict()
        self.assertEqual(report["spec"]["instances"][0]["component"], "otlp-receiver")


class TelemetryDeploymentHealthHttpAndSdkTests(unittest.TestCase):
    def _handler(self, roles: tuple[str, ...]) -> ApiHandler:
        class Authenticator:
            def authenticate_bearer(self, token: str) -> ActorContext:
                if token != TOKEN:
                    raise AssertionError(token)
                return ActorContext("operator", "local", roles)

        handler = object.__new__(ApiHandler)
        handler.runtime = build_local_runtime(Authenticator())
        health = TelemetryExportHealthState(("metrics",))
        health.record_success("metrics")
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        handler.runtime.operational_store.record_telemetry_export_health(
            TelemetryExportInstanceState(
                instance_id=INSTANCE_ID,
                component="api",
                started_at=now,
                last_reported_at=now,
                signals=health.read_export_health(),
            ),
            expire_before="2026-08-17T11:00:00Z",
            sample_expire_before="2026-08-10T11:00:00Z",
        )
        handler.path = "/v1/operations/telemetry/deployment-export-health"
        handler.headers = {"authorization": f"Bearer {TOKEN}"}
        handler.responses = []
        handler._json = lambda status, body: handler.responses.append((status, body))
        return handler

    def test_http_surface_is_authenticated_role_bound_and_query_closed(self) -> None:
        handler = self._handler(("platform-admin",))
        handler.do_GET()
        self.assertEqual(handler.responses[0][0], HTTPStatus.OK)

        denied = self._handler(("operator",))
        denied.do_GET()
        self.assertEqual(denied.responses[0][0], HTTPStatus.FORBIDDEN)

        handler.responses.clear()
        handler.path += "?endpoint=true"
        handler.do_GET()
        self.assertEqual(handler.responses[0][0], HTTPStatus.BAD_REQUEST)

    def test_python_sdk_calls_deployment_route_and_parses_contract(self) -> None:
        payload = example()

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
            report = client.get_telemetry_deployment_export_health()

        self.assertIsInstance(report, TelemetryDeploymentExportHealthReport)
        self.assertEqual(report.to_dict(), payload)
        self.assertEqual(
            send.call_args.args[0].full_url,
            "https://control.example/v1/operations/telemetry/deployment-export-health",
        )


if __name__ == "__main__":
    unittest.main()
