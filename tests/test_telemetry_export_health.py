from __future__ import annotations

import json
import unittest
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from infra_intelligence_sdk import Client, TelemetryExportHealthReport
from iip.adapters.memory import AllowTenantPolicy
from iip.adapters.otel import (
    CompositeTelemetryRuntime,
    TelemetryExportHealthState,
    TrackingMetricExporter,
    TrackingSpanExporter,
)
from iip.application.ports import ActorContext, TelemetryExportSignalState
from iip.application.query_telemetry_export_health import (
    GetTelemetryExportHealthCommand,
    TelemetryExportHealthAuthorizationError,
    TelemetryExportHealthService,
    TelemetryExportHealthStateError,
)
from iip.bootstrap import build_local_runtime
from iip.surfaces.http import ApiHandler


ROOT = Path(__file__).resolve().parents[1]
TOKEN = "telemetry-health-token-0123456789abcdef0123456789abcdef"


def example() -> dict:
    return json.loads(
        (
            ROOT
            / "contracts/examples/telemetry-export-health-report.json"
        ).read_text(encoding="utf-8")
    )


class Clock:
    def now(self) -> str:
        return "2026-08-17T12:00:10Z"


class Result:
    def __init__(self, name: str) -> None:
        self.name = name


class Exporter:
    def __init__(self, results: list[object]) -> None:
        self.results = list(results)
        self.flushes: list[float] = []
        self.shutdowns = 0
        self._preferred_temporality = {"counter": "delta"}
        self._preferred_aggregation = {"histogram": "explicit"}

    def export(self, payload: object, **kwargs: object) -> object:
        del payload, kwargs
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        self.flushes.append(timeout_millis)
        return True

    def shutdown(self, **kwargs: object) -> None:
        del kwargs
        self.shutdowns += 1


class TelemetryExportTrackingTests(unittest.TestCase):
    def test_metric_failure_and_recovery_are_bounded_and_provider_neutral(self) -> None:
        health = TelemetryExportHealthState(("metrics",))
        delegate = Exporter(
            [
                Result("FAILURE"),
                RuntimeError("provider secret response"),
                Result("SUCCESS"),
            ]
        )
        exporter = TrackingMetricExporter(delegate, health)

        self.assertEqual(exporter.export(object()).name, "FAILURE")
        with self.assertRaises(RuntimeError):
            exporter.export(object())
        degraded = health.read_export_health()[0]
        self.assertEqual(degraded.status, "degraded")
        self.assertEqual(degraded.attempts, 2)
        self.assertEqual(degraded.consecutive_failures, 2)
        self.assertEqual(degraded.last_failure_code, "telemetry.export.exception")
        self.assertNotIn("secret", repr(degraded))

        self.assertEqual(exporter.export(object()).name, "SUCCESS")
        recovered = health.read_export_health()[0]
        self.assertEqual(recovered.status, "healthy")
        self.assertEqual(recovered.attempts, 3)
        self.assertEqual(recovered.successes, 1)
        self.assertEqual(recovered.failures, 2)
        self.assertEqual(recovered.consecutive_failures, 0)
        self.assertTrue(exporter.force_flush(1234))
        exporter.shutdown(4321)

    def test_trace_outcomes_are_independent_from_metrics(self) -> None:
        health = TelemetryExportHealthState(("traces",))
        exporter = TrackingSpanExporter(
            Exporter([Result("SUCCESS"), Result("FAILURE")]),
            health,
        )

        exporter.export(())
        exporter.export(())

        metrics, traces = health.read_export_health()
        self.assertEqual(metrics.status, "disabled")
        self.assertEqual(traces.status, "degraded")
        self.assertEqual(traces.successes, 1)
        self.assertEqual(traces.failures, 1)

    def test_composite_prefers_each_enabled_signal(self) -> None:
        class Part:
            def __init__(self, health: TelemetryExportHealthState) -> None:
                self.health = health

            def read_export_health(self):
                return self.health.read_export_health()

        metrics = TelemetryExportHealthState(("metrics",))
        traces = TelemetryExportHealthState(("traces",))
        metrics.record_success("metrics")
        traces.record_failure("traces", "telemetry.export.rejected")

        states = CompositeTelemetryRuntime(
            (Part(metrics), Part(traces))
        ).read_export_health()

        self.assertEqual([state.status for state in states], ["healthy", "degraded"])


class TelemetryExportHealthServiceTests(unittest.TestCase):
    def test_report_requires_role_and_policy_and_recovers_to_healthy(self) -> None:
        health = TelemetryExportHealthState(("metrics", "traces"))
        service = TelemetryExportHealthService(health, AllowTenantPolicy(), Clock())
        operator = ActorContext("operator", "local", ("platform-admin",))

        awaiting = service.get(GetTelemetryExportHealthCommand(operator)).to_dict()
        self.assertEqual(awaiting["spec"]["status"], "awaiting-first-attempt")

        health.record_success("metrics")
        health.record_failure("traces", "telemetry.export.rejected")
        degraded = service.get(GetTelemetryExportHealthCommand(operator)).to_dict()
        self.assertEqual(degraded["spec"]["status"], "degraded")

        health.record_success("traces")
        recovered = service.get(GetTelemetryExportHealthCommand(operator)).to_dict()
        self.assertEqual(recovered["spec"]["status"], "healthy")
        self.assertNotIn("tenantId", recovered["metadata"])

        with self.assertRaises(TelemetryExportHealthAuthorizationError):
            service.get(
                GetTelemetryExportHealthCommand(
                    ActorContext("viewer", "local", ("operator",))
                )
            )

    def test_inconsistent_adapter_state_fails_closed(self) -> None:
        class InvalidReader:
            def read_export_health(self):
                return (
                    TelemetryExportSignalState(
                        "metrics", True, "healthy", 0, 0, 0, 0
                    ),
                    TelemetryExportSignalState(
                        "traces", False, "disabled", 0, 0, 0, 0
                    ),
                )

        service = TelemetryExportHealthService(
            InvalidReader(), AllowTenantPolicy(), Clock()
        )
        with self.assertRaises(TelemetryExportHealthStateError):
            service.get(
                GetTelemetryExportHealthCommand(
                    ActorContext("operator", "local", ("platform-admin",))
                )
            )


class TelemetryExportHealthHttpAndSdkTests(unittest.TestCase):
    def _handler(self, roles: tuple[str, ...]) -> ApiHandler:
        health = TelemetryExportHealthState(("metrics",))

        class RuntimeHealth:
            def read_export_health(self):
                return health.read_export_health()

            def force_flush(self, timeout_millis: int) -> bool:
                del timeout_millis
                return True

            def shutdown(self, timeout_millis: int = 30_000) -> None:
                del timeout_millis

        class Authenticator:
            def authenticate_bearer(self, token: str) -> ActorContext:
                if token != TOKEN:
                    raise AssertionError(token)
                return ActorContext("operator", "local", roles)

        handler = object.__new__(ApiHandler)
        handler.runtime = build_local_runtime(
            Authenticator(), telemetry_runtime=RuntimeHealth()
        )
        handler.path = "/v1/operations/telemetry/export-health"
        handler.headers = {"authorization": f"Bearer {TOKEN}"}
        handler.responses = []
        handler._json = lambda status, body: handler.responses.append((status, body))
        return handler

    def test_http_surface_is_authenticated_role_bound_and_query_closed(self) -> None:
        handler = self._handler(("platform-admin",))
        handler.do_GET()

        self.assertEqual(handler.responses[0][0], HTTPStatus.OK)
        self.assertEqual(
            handler.responses[0][1]["spec"]["status"],
            "awaiting-first-attempt",
        )

        denied = self._handler(("operator",))
        denied.do_GET()
        self.assertEqual(
            denied.responses[0],
            (
                HTTPStatus.FORBIDDEN,
                {"error": {"code": "policy.denied"}},
            ),
        )

        handler.responses.clear()
        handler.path += "?endpoint=true"
        handler.do_GET()
        self.assertEqual(handler.responses[0][0], HTTPStatus.BAD_REQUEST)

    def test_python_sdk_calls_operator_route_and_parses_contract(self) -> None:
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
            report = client.get_telemetry_export_health()

        self.assertIsInstance(report, TelemetryExportHealthReport)
        self.assertEqual(report.to_dict(), payload)
        request = send.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://control.example/v1/operations/telemetry/export-health",
        )


if __name__ == "__main__":
    unittest.main()
