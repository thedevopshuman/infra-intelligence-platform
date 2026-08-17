from __future__ import annotations

import json
import logging
import os
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader, MetricExportResult
from opentelemetry.sdk.resources import Resource


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(
    os.environ.get("IIP_TEST_OTLP_RECEIVER_ENDPOINT"),
    "set IIP_TEST_OTLP_RECEIVER_ENDPOINT to run the Docker receiver gate",
)
class OtlpReceiverDockerIntegrationTests(unittest.TestCase):
    def seed_resource(self, base_url: str, control_token: str) -> None:
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

    def test_official_exporter_reaches_isolated_receiver(self) -> None:
        control_url = os.environ["IIP_TEST_CONTROL_ENDPOINT"].rstrip("/")
        receiver_url = os.environ["IIP_TEST_OTLP_RECEIVER_ENDPOINT"].rstrip("/")
        control_token = os.environ["IIP_TEST_OTLP_CONTROL_TOKEN"]
        channel_token = os.environ["IIP_TEST_OTLP_CHANNEL_TOKEN"]
        self.seed_resource(control_url, control_token)

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
            endpoint=f"{receiver_url}/v1/metrics",
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

    def test_official_log_exporter_reaches_isolated_receiver(self) -> None:
        control_url = os.environ["IIP_TEST_CONTROL_ENDPOINT"].rstrip("/")
        receiver_url = os.environ["IIP_TEST_OTLP_RECEIVER_ENDPOINT"].rstrip("/")
        control_token = os.environ["IIP_TEST_OTLP_CONTROL_TOKEN"]
        channel_token = os.environ["IIP_TEST_OTLP_CHANNEL_TOKEN"]
        self.seed_resource(control_url, control_token)

        exporter = OTLPLogExporter(
            endpoint=f"{receiver_url}/v1/logs",
            headers={"Authorization": f"Bearer {channel_token}"},
            timeout=5,
        )
        provider = LoggerProvider(
            resource=Resource.create(
                {
                    "service.name": "checkout",
                    "deployment.environment.name": "docker-test",
                    "k8s.namespace.name": "default",
                }
            )
        )
        provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
        logger = logging.getLogger("iip.receiver.docker-test")
        logger.setLevel(logging.ERROR)
        handler = LoggingHandler(logger_provider=provider)
        logger.addHandler(handler)
        try:
            logger.error("database request exceeded its deadline")
            self.assertTrue(provider.force_flush(timeout_millis=5000))
        finally:
            logger.removeHandler(handler)
            handler.close()
            provider.shutdown()

    def test_control_and_receiver_routes_are_process_isolated(self) -> None:
        control_url = os.environ["IIP_TEST_CONTROL_ENDPOINT"].rstrip("/")
        receiver_url = os.environ["IIP_TEST_OTLP_RECEIVER_ENDPOINT"].rstrip("/")
        channel_token = os.environ["IIP_TEST_OTLP_CHANNEL_TOKEN"]

        control_request = urllib.request.Request(
            f"{control_url}/v1/metrics",
            data=b"",
            method="POST",
            headers={
                "Authorization": f"Bearer {channel_token}",
                "Content-Type": "application/x-protobuf",
            },
        )
        with self.assertRaises(urllib.error.HTTPError) as control_error:
            urllib.request.urlopen(control_request, timeout=5)
        self.assertEqual(control_error.exception.code, 404)
        control_error.exception.close()

        with self.assertRaises(urllib.error.HTTPError) as receiver_error:
            urllib.request.urlopen(f"{receiver_url}/console", timeout=5)
        self.assertEqual(receiver_error.exception.code, 404)
        receiver_error.exception.close()


if __name__ == "__main__":
    unittest.main()
