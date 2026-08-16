from __future__ import annotations

import json
import os
import unittest
import urllib.request
from pathlib import Path

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, MetricExportResult
from opentelemetry.sdk.resources import Resource


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.environ.get("IIP_TEST_OTLP_RECEIVER_ENDPOINT"),
    "set IIP_TEST_OTLP_RECEIVER_ENDPOINT to run the Docker receiver gate",
)
class OtlpReceiverDockerIntegrationTests(unittest.TestCase):
    def test_official_exporter_reaches_built_api_image(self) -> None:
        base_url = os.environ["IIP_TEST_OTLP_RECEIVER_ENDPOINT"].rstrip("/")
        control_token = os.environ["IIP_TEST_OTLP_CONTROL_TOKEN"]
        channel_token = os.environ["IIP_TEST_OTLP_CHANNEL_TOKEN"]
        resource = json.loads(
            (ROOT / "contracts" / "examples" / "resource.json").read_text()
        )
        request = urllib.request.Request(
            f"{base_url}/v1/resources",
            data=json.dumps(resource).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {control_token}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            self.assertEqual(response.status, 202)

        reader = InMemoryMetricReader()
        provider = MeterProvider(
            metric_readers=[reader],
            resource=Resource.create(
                {
                    "service.name": "checkout",
                    "deployment.environment.name": "docker-test",
                }
            ),
        )
        meter = provider.get_meter("iip.receiver.docker-test")
        counter = meter.create_counter(
            "http.server.request.count",
            unit="{request}",
        )
        counter.add(1)
        metrics_data = reader.get_metrics_data()
        self.assertIsNotNone(metrics_data)

        exporter = OTLPMetricExporter(
            endpoint=f"{base_url}/v1/metrics",
            headers={"Authorization": f"Bearer {channel_token}"},
            timeout=5,
        )
        try:
            self.assertEqual(
                exporter.export(metrics_data),
                MetricExportResult.SUCCESS,
            )
        finally:
            exporter.shutdown()
            provider.shutdown()


if __name__ == "__main__":
    unittest.main()
