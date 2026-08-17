from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from infra_intelligence_sdk import Client, TelemetryExportSloReport
from iip.adapters.memory import AllowTenantPolicy
from iip.adapters.operations import InMemoryOperationalStore
from iip.adapters.otel import TelemetryExportHealthState
from iip.application.ports import ActorContext, TelemetryExportInstanceState
from iip.application.query_telemetry_export_slo import (
    GetTelemetryExportSloCommand,
    TelemetryExportSloAuthorizationError,
    TelemetryExportSloObjectives,
    TelemetryExportSloService,
)
from iip.application.report_telemetry_export_health import (
    TelemetryExportHealthReporter,
    TelemetryExportHealthReportingConfiguration,
)
from iip.bootstrap import build_local_runtime
from iip.surfaces.http import ApiHandler


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "telemetry-slo-token-0123456789abcdef0123456789abcdef"
INSTANCE_ID = "sha256:" + "b" * 64


class Clock:
    def __init__(self, value: str = "2026-08-17T13:00:00Z") -> None:
        self.value = value

    def now(self) -> str:
        return self.value


class TelemetryExportSloTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = InMemoryOperationalStore()
        self.clock = Clock()
        self.health = TelemetryExportHealthState(("metrics",))
        self.reporter = TelemetryExportHealthReporter(
            self.health,
            self.store,
            self.clock,
            TelemetryExportHealthReportingConfiguration(
                instance_id=INSTANCE_ID,
                component="api",
                sample_retention_seconds=604_800,
            ),
        )
        self.service = TelemetryExportSloService(
            self.store,
            AllowTenantPolicy(),
            self.clock,
            TelemetryExportSloObjectives(minimum_eligible_attempts=2),
        )
        self.actor = ActorContext("operator", "local", ("platform-admin",))

    def test_sampled_counter_deltas_measure_meeting_and_breached_windows(self) -> None:
        self.reporter.report_once()
        self.health.record_success("metrics")
        self.clock.value = "2026-08-17T13:00:30Z"
        self.reporter.report_once()
        self.health.record_success("metrics")
        self.clock.value = "2026-08-17T13:01:00Z"
        self.reporter.report_once()

        meeting = self.service.get(
            GetTelemetryExportSloCommand(self.actor)
        ).to_dict()
        self.assertEqual(meeting["spec"]["status"], "meeting")
        metrics = meeting["spec"]["signals"][0]
        self.assertEqual(metrics["eligibleAttempts"], 2)
        self.assertEqual(metrics["attainmentBasisPoints"], 10_000)
        self.assertEqual(meeting["spec"]["signals"][1]["status"], "disabled")
        self.assertNotIn("tenantId", meeting["metadata"])

        self.health.record_failure("metrics", "telemetry.export.failed")
        self.clock.value = "2026-08-17T13:01:30Z"
        self.reporter.report_once()
        breached = self.service.get(
            GetTelemetryExportSloCommand(self.actor)
        ).to_dict()
        self.assertEqual(breached["spec"]["status"], "breached")
        self.assertEqual(
            breached["spec"]["signals"][0]["attainmentBasisPoints"], 6_666
        )

    def test_pre_window_sample_is_a_baseline_and_role_is_required(self) -> None:
        self.clock.value = "2026-08-17T12:59:30Z"
        reporter = TelemetryExportHealthReporter(
            self.health,
            self.store,
            self.clock,
            self.reporter.configuration,
        )
        reporter.report_once()
        self.health.record_success("metrics")
        self.clock.value = "2026-08-17T13:00:30Z"
        reporter.report_once()
        self.clock.value = "2026-08-17T14:00:00Z"
        report = self.service.get(GetTelemetryExportSloCommand(self.actor)).to_dict()
        self.assertEqual(report["spec"]["signals"][0]["eligibleAttempts"], 1)
        self.assertEqual(report["spec"]["status"], "insufficient-data")

        with self.assertRaises(TelemetryExportSloAuthorizationError):
            self.service.get(
                GetTelemetryExportSloCommand(
                    ActorContext("viewer", "local", ("operator",))
                )
            )

    def test_configuration_is_bounded(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "telemetry.export-slo.configuration.invalid"
        ):
            TelemetryExportSloObjectives(window_seconds=299)
        with self.assertRaisesRegex(
            ValueError, "telemetry.export-health.reporting.configuration.invalid"
        ):
            TelemetryExportHealthReportingConfiguration(
                instance_id=INSTANCE_ID,
                component="api",
                sample_retention_seconds=599,
            ).validate()


class TelemetryExportSloHttpAndSdkTests(unittest.TestCase):
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
        handler.runtime.operational_store.record_telemetry_export_health(
            TelemetryExportInstanceState(
                instance_id=INSTANCE_ID,
                component="api",
                started_at="2026-08-17T13:00:00Z",
                last_reported_at="2026-08-17T13:00:30Z",
                signals=health.read_export_health(),
            ),
            expire_before="2026-08-17T12:00:00Z",
            sample_expire_before="2026-08-10T12:00:00Z",
        )
        handler.path = "/v1/operations/telemetry/export-slo"
        handler.headers = {"authorization": f"Bearer {TOKEN}"}
        handler.responses = []
        handler._json = lambda status, body: handler.responses.append((status, body))
        return handler

    def test_http_surface_is_role_bound_and_query_closed(self) -> None:
        handler = self._handler(("platform-admin",))
        handler.do_GET()
        self.assertEqual(handler.responses[0][0], 200)

        denied = self._handler(("operator",))
        denied.do_GET()
        self.assertEqual(denied.responses[0][0], 403)

        handler.responses.clear()
        handler.path += "?window=all"
        handler.do_GET()
        self.assertEqual(handler.responses[0][0], 400)

    def test_python_sdk_calls_export_slo_route_and_parses_contract(self) -> None:
        payload = json.loads(
            (
                ROOT / "contracts/examples/telemetry-export-slo-report.json"
            ).read_text(encoding="utf-8")
        )

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
            report = client.get_telemetry_export_slo()

        self.assertIsInstance(report, TelemetryExportSloReport)
        self.assertEqual(report.to_dict(), payload)
        self.assertEqual(
            send.call_args.args[0].full_url,
            "https://control.example/v1/operations/telemetry/export-slo",
        )


if __name__ == "__main__":
    unittest.main()
