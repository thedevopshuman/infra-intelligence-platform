from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from infra_intelligence_sdk import Client, TelemetryExportBurnRateReport
from iip.adapters.memory import AllowTenantPolicy
from iip.adapters.operations import InMemoryOperationalStore
from iip.adapters.otel import TelemetryExportHealthState
from iip.application.ports import ActorContext, TelemetryExportInstanceState
from iip.application.query_telemetry_export_burn_rate import (
    GetTelemetryExportBurnRateCommand,
    TelemetryExportBurnRateAuthorizationError,
    TelemetryExportBurnRateObjectives,
    TelemetryExportBurnRateService,
)
from iip.application.report_telemetry_export_health import (
    TelemetryExportHealthReporter,
    TelemetryExportHealthReportingConfiguration,
)
from iip.bootstrap import build_local_runtime
from iip.surfaces.http import ApiHandler


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "telemetry-burn-rate-token-0123456789abcdef0123456789abcdef"
INSTANCE_ID = "sha256:" + "c" * 64


class Clock:
    def __init__(self, value: str = "2026-08-17T13:00:00Z") -> None:
        self.value = value

    def now(self) -> str:
        return self.value


class TelemetryExportBurnRateTests(unittest.TestCase):
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
        self.service = TelemetryExportBurnRateService(
            self.store,
            AllowTenantPolicy(),
            self.clock,
            TelemetryExportBurnRateObjectives(
                short_window_seconds=300,
                long_window_seconds=1_200,
                minimum_eligible_attempts=2,
                critical_burn_rate_hundredths=200,
            ),
        )
        self.actor = ActorContext("operator", "local", ("platform-admin",))

    def _report(self) -> dict:
        return self.service.get(
            GetTelemetryExportBurnRateCommand(self.actor)
        ).to_dict()

    def test_sustainable_windows_agree_and_role_is_required(self) -> None:
        self.reporter.report_once()
        self.health.record_success("metrics")
        self.clock.value = "2026-08-17T13:02:00Z"
        self.reporter.report_once()
        self.health.record_success("metrics")
        self.clock.value = "2026-08-17T13:05:00Z"
        self.reporter.report_once()

        report = self._report()
        self.assertEqual(report["spec"]["status"], "sustainable")
        metrics = report["spec"]["signals"][0]
        self.assertEqual(metrics["signal"], "metrics")
        self.assertEqual(metrics["status"], "sustainable")
        self.assertEqual(metrics["short"]["eligibleAttempts"], 2)
        self.assertEqual(metrics["short"]["burnRateHundredths"], 0)
        self.assertEqual(metrics["long"]["burnRateHundredths"], 0)
        self.assertEqual(report["spec"]["signals"][1]["status"], "disabled")
        self.assertNotIn("tenantId", report["metadata"])

        with self.assertRaises(TelemetryExportBurnRateAuthorizationError):
            self.service.get(
                GetTelemetryExportBurnRateCommand(
                    ActorContext("viewer", "local", ("operator",))
                )
            )

    def test_confirmed_two_window_failure_reaches_critical(self) -> None:
        # error budget = 100 basis points (10000 - 9900); one failure out of
        # two attempts is a 5000 basis-point failure rate => burn rate 5000x100//100 = 5000
        self.reporter.report_once()
        self.health.record_failure("metrics", "telemetry.export.failed")
        self.clock.value = "2026-08-17T13:00:30Z"
        self.reporter.report_once()
        self.health.record_success("metrics")
        self.clock.value = "2026-08-17T13:01:00Z"
        self.reporter.report_once()

        report = self._report()
        metrics = report["spec"]["signals"][0]
        self.assertEqual(metrics["short"]["status"], "critical")
        self.assertEqual(metrics["long"]["status"], "critical")
        self.assertEqual(metrics["status"], "critical")
        self.assertEqual(report["spec"]["status"], "critical")

    def test_lone_critical_window_downgrades_to_elevated(self) -> None:
        # The long window (0-1200s ago) sees the failure, but the short
        # window (0-300s ago) only spans the later, all-success samples.
        self.reporter.report_once()
        self.health.record_failure("metrics", "telemetry.export.failed")
        self.clock.value = "2026-08-17T13:00:30Z"
        self.reporter.report_once()
        self.health.record_success("metrics")
        self.clock.value = "2026-08-17T13:10:00Z"
        self.reporter.report_once()
        self.health.record_success("metrics")
        self.clock.value = "2026-08-17T13:14:00Z"
        self.reporter.report_once()

        report = self._report()
        metrics = report["spec"]["signals"][0]
        self.assertEqual(metrics["long"]["status"], "critical")
        self.assertIn(metrics["short"]["status"], ("sustainable", "insufficient-data"))
        self.assertEqual(metrics["status"], "elevated")
        self.assertEqual(report["spec"]["status"], "elevated")

    def test_configuration_is_bounded(self) -> None:
        with self.assertRaisesRegex(
            ValueError, "telemetry.export-burn-rate.configuration.invalid"
        ):
            TelemetryExportBurnRateObjectives(short_window_seconds=299)
        with self.assertRaisesRegex(
            ValueError, "telemetry.export-burn-rate.configuration.invalid"
        ):
            TelemetryExportBurnRateObjectives(
                short_window_seconds=3600, long_window_seconds=3600
            )
        with self.assertRaisesRegex(
            ValueError, "telemetry.export-burn-rate.configuration.invalid"
        ):
            TelemetryExportBurnRateObjectives(minimum_attainment_basis_points=10_000)
        with self.assertRaisesRegex(
            ValueError, "telemetry.export-burn-rate.configuration.invalid"
        ):
            TelemetryExportBurnRateObjectives(critical_burn_rate_hundredths=100)


class TelemetryExportBurnRateHttpAndSdkTests(unittest.TestCase):
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
        handler.path = "/v1/operations/telemetry/export-burn-rate"
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

    def test_python_sdk_calls_export_burn_rate_route_and_parses_contract(
        self,
    ) -> None:
        payload = json.loads(
            (
                ROOT / "contracts/examples/telemetry-export-burn-rate-report.json"
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
            report = client.get_telemetry_export_burn_rate()

        self.assertIsInstance(report, TelemetryExportBurnRateReport)
        self.assertEqual(report.to_dict(), payload)
        self.assertEqual(
            send.call_args.args[0].full_url,
            "https://control.example/v1/operations/telemetry/export-burn-rate",
        )


if __name__ == "__main__":
    unittest.main()
