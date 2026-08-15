from __future__ import annotations

import os
import unittest

from iip.adapters.otel import OtlpMetricsConfiguration, build_otlp_metrics_runtime

from tests.test_otel_metrics import measurement


ENDPOINT = os.environ.get("IIP_TEST_OTEL_ENDPOINT")


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
            self.assertTrue(runtime.force_flush(5_000))
            self.assertEqual(runtime.sink.record_failures, 0)
        finally:
            runtime.shutdown(5_000)


if __name__ == "__main__":
    unittest.main()
