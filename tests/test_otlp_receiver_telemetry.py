from __future__ import annotations

import io
import json
import time
import unittest
from http import HTTPStatus
from types import SimpleNamespace
from unittest.mock import Mock, patch

from iip.adapters.otel import OpenTelemetryOtlpReceiverSink
from iip.application.observe_otlp_receiver import (
    OtlpReceiverObjectives,
    OtlpReceiverTelemetryService,
    RecordOtlpReceiverCommand,
)
from iip.application.ports import OtlpReceiverMeasurement
from iip.bootstrap import (
    _otlp_receiver_objectives_from_env,
    build_local_runtime,
    build_otlp_receiver_runtime_from_env,
)
from iip.surfaces.http import ApiHandler

from tests.test_otel_metrics import RecordingMeter
from tests.test_otlp_receiver import receiver_config


class RecordingSink:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.measurements: list[OtlpReceiverMeasurement] = []

    def record_otlp_receiver(self, measurement: OtlpReceiverMeasurement) -> None:
        if self.fail:
            raise RuntimeError("sink failed")
        self.measurements.append(measurement)


class OtlpReceiverTelemetryServiceTests(unittest.TestCase):
    def test_statuses_have_closed_availability_semantics(self) -> None:
        sink = RecordingSink()
        service = OtlpReceiverTelemetryService(sink)
        cases = {
            200: ("success", "available"),
            400: ("invalid", "excluded"),
            401: ("unauthenticated", "excluded"),
            403: ("denied", "excluded"),
            404: ("disabled", "excluded"),
            413: ("invalid", "excluded"),
            415: ("invalid", "excluded"),
            429: ("rate-limited", "unavailable"),
            502: ("unavailable", "unavailable"),
            503: ("unavailable", "unavailable"),
            504: ("unavailable", "unavailable"),
            500: ("internal-error", "unavailable"),
        }
        for status, expected in cases.items():
            with self.subTest(status=status):
                service.record(
                    RecordOtlpReceiverCommand("metrics", status, 0.125)
                )
                observed = sink.measurements[-1]
                self.assertEqual((observed.outcome, observed.availability), expected)

        service.record(
            RecordOtlpReceiverCommand(
                "logs", 200, 0.25, uncaught_failure=True
            )
        )
        self.assertEqual(sink.measurements[-1].outcome, "internal-error")
        self.assertEqual(sink.measurements[-1].availability, "unavailable")

    def test_measurement_is_bounded_private_and_failure_isolated(self) -> None:
        sink = RecordingSink()
        service = OtlpReceiverTelemetryService(
            sink,
            OtlpReceiverObjectives(7200, 9950, 250),
        )
        service.record(RecordOtlpReceiverCommand("logs", 200, 100_000.0))
        measurement = sink.measurements[0]
        self.assertEqual(measurement.duration_seconds, 86_400.0)
        self.assertEqual(measurement.objective_window_seconds, 7200)
        self.assertEqual(
            measurement.objective_minimum_availability_basis_points, 9950
        )
        self.assertEqual(measurement.objective_minimum_eligible_requests, 250)
        serialized = json.dumps(measurement.__dict__)
        for forbidden in (
            "tenant",
            "channel",
            "spiffe",
            "certificate",
            "credential",
            "payload",
            "endpoint",
        ):
            self.assertNotIn(forbidden, serialized)

        failing = OtlpReceiverTelemetryService(RecordingSink(fail=True))
        failing.record(RecordOtlpReceiverCommand("metrics", 200, 0.1))
        failing.record(RecordOtlpReceiverCommand("traces", 200, 0.1))

    def test_objectives_are_closed_and_environment_owned(self) -> None:
        with patch.dict(
            "os.environ",
            {
                "IIP_OTLP_RECEIVER_SLO_WINDOW_SECONDS": "7200",
                "IIP_OTLP_RECEIVER_SLO_MINIMUM_BASIS_POINTS": "9950",
                "IIP_OTLP_RECEIVER_SLO_MINIMUM_ELIGIBLE_REQUESTS": "250",
            },
            clear=True,
        ):
            objectives = _otlp_receiver_objectives_from_env()
        self.assertEqual(objectives, OtlpReceiverObjectives(7200, 9950, 250))

        with patch.dict(
            "os.environ",
            {"IIP_OTLP_RECEIVER_SLO_WINDOW_SECONDS": "forever"},
            clear=True,
        ), self.assertRaisesRegex(
            ValueError, "otlp.receiver.availability.configuration.invalid"
        ):
            _otlp_receiver_objectives_from_env()


class OpenTelemetryOtlpReceiverSinkTests(unittest.TestCase):
    @staticmethod
    def measurement() -> OtlpReceiverMeasurement:
        return OtlpReceiverMeasurement(
            signal="metrics",
            outcome="success",
            availability="available",
            duration_seconds=0.125,
            objective_window_seconds=3600,
            objective_minimum_availability_basis_points=9990,
            objective_minimum_eligible_requests=100,
        )

    def test_sink_emits_only_bounded_counter_and_histogram_attributes(self) -> None:
        meter = RecordingMeter()
        sink = OpenTelemetryOtlpReceiverSink(meter)
        sink.record_otlp_receiver(self.measurement())

        request = meter.instruments["iip.otlp.receiver.requests"].records[0]
        duration = meter.instruments["iip.otlp.receiver.duration"].records[0]
        self.assertEqual(request[0], 1)
        self.assertEqual(duration[0], 0.125)
        self.assertEqual(request[1], duration[1])
        self.assertEqual(request[1]["iip.otlp.receiver.signal"], "metrics")
        self.assertEqual(
            request[1]["iip.otlp.receiver.availability"], "available"
        )
        serialized = json.dumps(request[1])
        for forbidden in ("tenant", "channel", "spiffe", "credential", "path"):
            self.assertNotIn(forbidden, serialized)

    def test_instrument_failure_is_local_and_counted(self) -> None:
        meter = RecordingMeter(failing_name="iip.otlp.receiver.requests")
        sink = OpenTelemetryOtlpReceiverSink(meter)
        sink.record_otlp_receiver(self.measurement())
        self.assertEqual(sink.record_failures, 1)
        self.assertEqual(
            meter.instruments["iip.telemetry.record.failures"].records[0][1],
            {
                "iip.telemetry.signal": "metrics",
                "iip.telemetry.instrument": "otlp-receiver-availability",
            },
        )


class OtlpReceiverHttpTelemetryTests(unittest.TestCase):
    def test_response_write_records_once_after_completion(self) -> None:
        commands = []
        handler = object.__new__(ApiHandler)
        handler.runtime = SimpleNamespace(
            otlp_receiver_telemetry=SimpleNamespace(record=commands.append)
        )
        handler._otlp_receiver_context = ("metrics", time.monotonic() - 0.01)
        handler.wfile = io.BytesIO()
        handler.send_response = lambda status: None
        handler.send_header = lambda name, value: None
        handler.end_headers = lambda: None
        handler._security_headers = lambda: None

        handler._otlp_response(HTTPStatus.OK, b"")
        handler._finish_otlp_receiver_telemetry(HTTPStatus.SERVICE_UNAVAILABLE)

        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0].signal, "metrics")
        self.assertEqual(commands[0].status_code, 200)
        self.assertGreaterEqual(commands[0].duration_seconds, 0.01)

    def test_response_write_failure_is_unavailable_and_not_raised_by_sink(self) -> None:
        commands = []
        handler = object.__new__(ApiHandler)
        handler.runtime = SimpleNamespace(
            otlp_receiver_telemetry=SimpleNamespace(record=commands.append)
        )
        handler._otlp_receiver_context = ("logs", time.monotonic())
        handler.wfile = SimpleNamespace(
            write=lambda body: (_ for _ in ()).throw(OSError("closed"))
        )
        handler.send_response = lambda status: None
        handler.send_header = lambda name, value: None
        handler.end_headers = lambda: None
        handler._security_headers = lambda: None

        with self.assertRaises(OSError):
            handler._otlp_response(HTTPStatus.OK, b"")
        self.assertEqual(len(commands), 1)
        self.assertTrue(commands[0].uncaught_failure)


class OtlpReceiverTelemetryCompositionTests(unittest.TestCase):
    def test_dedicated_receiver_composes_metrics_only_and_health_reporting(self) -> None:
        receiver_sink = object()

        class MetricsRuntime:
            def __init__(self) -> None:
                self.receiver_sink = receiver_sink
                self.shutdowns = 0

            def shutdown(self) -> None:
                self.shutdowns += 1

        metrics_runtime = MetricsRuntime()
        sentinel = object()
        with (
            patch.dict(
                "os.environ",
                {
                    "IIP_DATABASE_URL": "postgresql://receiver.example/iip",
                    "IIP_OTLP_RECEIVER_ENABLED": "true",
                    "IIP_OTLP_RECEIVER_CHANNELS_JSON": receiver_config(),
                    "IIP_OTEL_METRICS_ENABLED": "true",
                    "OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.example",
                },
                clear=True,
            ),
            patch(
                "iip.bootstrap._otel_metrics_runtime_from_env",
                return_value=metrics_runtime,
            ),
            patch("iip.bootstrap.build_postgres_runtime", return_value=sentinel) as build,
        ):
            runtime = build_otlp_receiver_runtime_from_env()

        self.assertIs(runtime, sentinel)
        self.assertIs(build.call_args.kwargs["telemetry_runtime"], metrics_runtime)
        self.assertIs(
            build.call_args.kwargs["otlp_receiver_telemetry_sink"], receiver_sink
        )
        self.assertEqual(
            build.call_args.kwargs["telemetry_health_reporting"].component,
            "otlp-receiver",
        )
        self.assertEqual(metrics_runtime.shutdowns, 0)

    def test_runtime_exposes_failure_isolated_receiver_service(self) -> None:
        sink = RecordingSink()
        runtime = build_local_runtime(otlp_receiver_telemetry_sink=sink)
        self.addCleanup(runtime.close)
        runtime.otlp_receiver_telemetry.record(
            RecordOtlpReceiverCommand("metrics", 429, 0.01)
        )
        self.assertEqual(sink.measurements[0].outcome, "rate-limited")

    def test_receiver_exporter_is_shutdown_when_later_configuration_fails(self) -> None:
        metrics_runtime = SimpleNamespace(
            receiver_sink=object(),
            shutdown=Mock(),
        )
        with (
            patch.dict(
                "os.environ",
                {
                    "IIP_DATABASE_URL": "postgresql://receiver.example/iip",
                    "IIP_OTLP_RECEIVER_ENABLED": "true",
                    "IIP_OTLP_RECEIVER_CHANNELS_JSON": receiver_config(),
                },
                clear=True,
            ),
            patch(
                "iip.bootstrap._otel_metrics_runtime_from_env",
                return_value=metrics_runtime,
            ),
            patch(
                "iip.bootstrap._telemetry_health_reporting_from_env",
                side_effect=ValueError("telemetry.export-health.reporting.configuration.invalid"),
            ),
            self.assertRaises(ValueError),
        ):
            build_otlp_receiver_runtime_from_env()
        metrics_runtime.shutdown.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
