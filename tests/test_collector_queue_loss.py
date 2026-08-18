from __future__ import annotations

import unittest

from iip.adapters.memory import AllowTenantPolicy
from iip.application.ports import (
    ActorContext,
    TelemetryMetricPoint,
    TelemetryMetricSeries,
    TelemetryMetricsResult,
)
from iip.application.query_collector_queue_loss import (
    CollectorQueueLossAuthorizationError,
    CollectorQueueLossBinding,
    CollectorQueueLossObjectives,
    CollectorQueueLossService,
    CollectorQueueLossStateError,
    GetCollectorQueueLossCommand,
)


class Clock:
    def __init__(self, value: str = "2026-08-18T12:00:00Z") -> None:
        self.value = value

    def now(self) -> str:
        return self.value


class FakeMetricsBackend:
    def __init__(self) -> None:
        self.series: dict[str, tuple[float, ...]] = {}
        self.status: dict[str, str] = {}
        self.requests: list = []

    def query_metrics(self, request):
        self.requests.append(request)
        values = self.series.get(request.metric)
        status = self.status.get(request.metric, "complete" if values else "no-data")
        if values is None:
            return TelemetryMetricsResult(request.end, "no-data", ())
        series = (
            TelemetryMetricSeries(
                request.metric,
                "1",
                request.filters,
                tuple(
                    TelemetryMetricPoint(request.start, value) for value in values
                ),
            ),
        )
        return TelemetryMetricsResult(request.end, status, series)


class UnavailableMetricsBackend:
    def query_metrics(self, request):
        raise RuntimeError("untrusted provider detail")


def _binding() -> CollectorQueueLossBinding:
    return CollectorQueueLossBinding("collector-local", "otlphttp/iip")


class CollectorQueueLossTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = Clock()
        self.actor = ActorContext("operator", "local", ("platform-admin",))

    def _service(
        self,
        backend=None,
        binding=None,
        objectives=None,
    ) -> CollectorQueueLossService:
        return CollectorQueueLossService(
            backend,
            AllowTenantPolicy(),
            self.clock,
            binding,
            objectives
            or CollectorQueueLossObjectives(
                window_seconds=300,
                max_loss_basis_points=100,
                max_queue_utilization_basis_points=8_000,
                minimum_eligible_attempts=5,
            ),
        )

    def _report(self, service: CollectorQueueLossService) -> dict:
        return service.get(GetCollectorQueueLossCommand(self.actor)).to_dict()

    def test_role_is_required(self) -> None:
        service = self._service()
        actor = ActorContext("operator", "local", ("developer",))
        with self.assertRaises(CollectorQueueLossAuthorizationError):
            service.get(GetCollectorQueueLossCommand(actor))

    def test_unconfigured_binding_is_disabled(self) -> None:
        report = self._report(self._service())
        self.assertEqual(report["spec"]["status"], "disabled")
        self.assertIsNone(report["spec"]["binding"])
        for signal in report["spec"]["signals"]:
            self.assertEqual(signal["status"], "disabled")
            self.assertIsNone(signal["sentDelta"])

    def test_no_data_when_backend_has_no_series(self) -> None:
        backend = FakeMetricsBackend()
        report = self._report(self._service(backend, _binding()))
        self.assertEqual(report["spec"]["status"], "no-data")
        self.assertEqual(report["spec"]["binding"]["integrationId"], "collector-local")
        for signal in report["spec"]["signals"]:
            self.assertEqual(signal["status"], "no-data")

    def test_insufficient_data_below_minimum_eligible_attempts(self) -> None:
        backend = FakeMetricsBackend()
        backend.series["platform.collector.exporter.sent-metric-points"] = (0.0, 2.0)
        backend.series["platform.collector.exporter.send-failed-metric-points"] = (
            0.0,
            0.0,
        )
        report = self._report(self._service(backend, _binding()))
        metrics_signal = next(
            item for item in report["spec"]["signals"] if item["signal"] == "metrics"
        )
        self.assertEqual(metrics_signal["status"], "insufficient-data")
        self.assertEqual(metrics_signal["sentDelta"], 2)
        self.assertEqual(metrics_signal["failedDelta"], 0)

    def test_meeting_within_loss_and_queue_objective(self) -> None:
        backend = FakeMetricsBackend()
        backend.series["platform.collector.exporter.sent-metric-points"] = (
            0.0,
            1000.0,
        )
        backend.series["platform.collector.exporter.send-failed-metric-points"] = (
            0.0,
            2.0,
        )
        backend.series["platform.collector.exporter.queue-size"] = (0.0, 10.0)
        backend.series["platform.collector.exporter.queue-capacity"] = (100.0, 100.0)
        binding = CollectorQueueLossBinding(
            "collector-local", "otlphttp/iip", ("metrics",)
        )
        report = self._report(self._service(backend, binding))
        metrics_signal = next(
            item for item in report["spec"]["signals"] if item["signal"] == "metrics"
        )
        self.assertEqual(metrics_signal["status"], "meeting")
        self.assertEqual(metrics_signal["lossBasisPoints"], 19)
        self.assertEqual(metrics_signal["queueUtilizationBasisPoints"], 1_000)
        self.assertEqual(report["spec"]["status"], "meeting")

    def test_breached_on_loss_ratio(self) -> None:
        backend = FakeMetricsBackend()
        backend.series["platform.collector.exporter.sent-metric-points"] = (
            0.0,
            900.0,
        )
        backend.series["platform.collector.exporter.send-failed-metric-points"] = (
            0.0,
            100.0,
        )
        report = self._report(self._service(backend, _binding()))
        metrics_signal = next(
            item for item in report["spec"]["signals"] if item["signal"] == "metrics"
        )
        self.assertEqual(metrics_signal["status"], "breached")
        self.assertEqual(metrics_signal["lossBasisPoints"], 1_000)
        self.assertEqual(report["spec"]["status"], "breached")

    def test_breached_on_queue_utilization_even_with_no_loss(self) -> None:
        backend = FakeMetricsBackend()
        backend.series["platform.collector.exporter.sent-metric-points"] = (
            0.0,
            1000.0,
        )
        backend.series["platform.collector.exporter.send-failed-metric-points"] = (
            0.0,
            0.0,
        )
        backend.series["platform.collector.exporter.queue-size"] = (0.0, 95.0)
        backend.series["platform.collector.exporter.queue-capacity"] = (100.0, 100.0)
        report = self._report(self._service(backend, _binding()))
        metrics_signal = next(
            item for item in report["spec"]["signals"] if item["signal"] == "metrics"
        )
        self.assertEqual(metrics_signal["status"], "breached")
        self.assertEqual(metrics_signal["queueUtilizationBasisPoints"], 9_500)

    def test_counter_reset_is_conservatively_no_data(self) -> None:
        backend = FakeMetricsBackend()
        backend.series["platform.collector.exporter.sent-metric-points"] = (
            500.0,
            10.0,
        )
        backend.series["platform.collector.exporter.send-failed-metric-points"] = (
            0.0,
            0.0,
        )
        report = self._report(self._service(backend, _binding()))
        metrics_signal = next(
            item for item in report["spec"]["signals"] if item["signal"] == "metrics"
        )
        self.assertEqual(metrics_signal["status"], "no-data")
        self.assertIsNone(metrics_signal["sentDelta"])

    def test_disabled_signal_within_a_configured_binding(self) -> None:
        backend = FakeMetricsBackend()
        backend.series["platform.collector.exporter.sent-metric-points"] = (
            0.0,
            1000.0,
        )
        backend.series["platform.collector.exporter.send-failed-metric-points"] = (
            0.0,
            0.0,
        )
        binding = CollectorQueueLossBinding(
            "collector-local", "otlphttp/iip", ("metrics",)
        )
        report = self._report(self._service(backend, binding))
        logs_signal = next(
            item for item in report["spec"]["signals"] if item["signal"] == "logs"
        )
        self.assertEqual(logs_signal["status"], "disabled")
        self.assertEqual(len(backend.requests), 4)

    def test_backend_failure_raises_state_error(self) -> None:
        service = self._service(UnavailableMetricsBackend(), _binding())
        with self.assertRaises(CollectorQueueLossStateError):
            service.get(GetCollectorQueueLossCommand(self.actor))

    def test_ambiguous_multi_series_response_raises_state_error(self) -> None:
        backend = FakeMetricsBackend()
        backend.series["platform.collector.exporter.sent-metric-points"] = (
            0.0,
            1.0,
        )

        def query_metrics(request):
            if request.metric == "platform.collector.exporter.sent-metric-points":
                series = (
                    TelemetryMetricSeries(
                        request.metric, "1", (), (TelemetryMetricPoint(request.end, 1.0),)
                    ),
                    TelemetryMetricSeries(
                        request.metric, "1", (), (TelemetryMetricPoint(request.end, 2.0),)
                    ),
                )
                return TelemetryMetricsResult(request.end, "complete", series)
            return TelemetryMetricsResult(request.end, "no-data", ())

        backend.query_metrics = query_metrics
        with self.assertRaises(CollectorQueueLossStateError):
            self._report(self._service(backend, _binding()))

    def test_invalid_binding_and_objectives_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            CollectorQueueLossBinding("", "otlphttp/iip")
        with self.assertRaises(ValueError):
            CollectorQueueLossBinding("collector-local", "otlphttp/iip", ())
        with self.assertRaises(ValueError):
            CollectorQueueLossBinding(
                "collector-local", "otlphttp/iip", ("metrics", "traces")
            )
        with self.assertRaises(ValueError):
            CollectorQueueLossObjectives(window_seconds=1)
        with self.assertRaises(ValueError):
            CollectorQueueLossObjectives(minimum_eligible_attempts=0)


if __name__ == "__main__":
    unittest.main()
