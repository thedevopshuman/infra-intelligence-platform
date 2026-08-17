from __future__ import annotations

import os
import unittest

from iip.adapters.otel import (
    OtlpMetricsConfiguration,
    OtlpTracesConfiguration,
    build_otlp_metrics_runtime,
    build_otlp_traces_runtime,
)
from iip.application.ports import QueryAvailabilityMeasurement

from tests.test_otel_metrics import measurement
from tests.test_otel_traces import measurement as investigation_measurement


ENDPOINT = os.environ.get("IIP_TEST_OTEL_ENDPOINT")
TRACES_ENDPOINT = os.environ.get("IIP_TEST_OTEL_TRACES_ENDPOINT")


@unittest.skipUnless(ENDPOINT, "IIP_TEST_OTEL_ENDPOINT enables the OTLP integration test")
class OtlpCollectorIntegrationTests(unittest.TestCase):
    def test_official_sdk_exports_freshness_metrics_to_collector(self) -> None:
        runtime = build_otlp_metrics_runtime(
            OtlpMetricsConfiguration(
                endpoint=ENDPOINT,
                attribute_mode="tenant-source",
                service_name="iip-otel-integration-test",
                export_interval_millis=60_000,
                export_timeout_millis=5_000,
            )
        )
        try:
            runtime.sink.record_ingestion_freshness(measurement())
            self.assertIsNotNone(runtime.query_sink)
            runtime.query_sink.record_query_availability(
                QueryAvailabilityMeasurement(
                    operation="runtime-version",
                    outcome="success",
                    availability="available",
                    duration_seconds=0.025,
                    objective_window_seconds=3600,
                    objective_minimum_availability_basis_points=9990,
                    objective_minimum_eligible_requests=100,
                )
            )
            self.assertTrue(runtime.force_flush(5_000))
            self.assertEqual(runtime.sink.record_failures, 0)
            self.assertEqual(runtime.query_sink.record_failures, 0)
            metrics = runtime.read_export_health()[0]
            self.assertEqual(metrics.status, "healthy")
            self.assertGreaterEqual(metrics.successes, 1)
        finally:
            runtime.shutdown(5_000)

    def test_official_sdk_exports_investigation_trace_to_collector(self) -> None:
        self.assertIsNotNone(TRACES_ENDPOINT)
        runtime = build_otlp_traces_runtime(
            OtlpTracesConfiguration(
                endpoint=str(TRACES_ENDPOINT),
                attribute_mode="none",
                service_name="iip-otel-integration-test",
                schedule_delay_millis=5_000,
                export_timeout_millis=5_000,
                max_queue_size=32,
                max_export_batch_size=8,
            )
        )
        try:
            runtime.sink.record_investigation_execution(
                investigation_measurement()
            )
            self.assertTrue(runtime.force_flush(5_000))
            self.assertEqual(runtime.sink.record_failures, 0)
            traces = runtime.read_export_health()[1]
            self.assertEqual(traces.status, "healthy")
            self.assertGreaterEqual(traces.successes, 1)
        finally:
            runtime.shutdown(5_000)


if __name__ == "__main__":
    unittest.main()
