"""Real Docker gate: a live OpenTelemetry Collector's own self-metrics,
scraped by a live Prometheus, drive a real CollectorQueueLossReport.

Distinct from tests.test_prometheus_integration, which proves the generic
backend-neutral metric query against a real Prometheus. This proves the
Collector queue/loss binding specifically: that the six closed logical
metric names resolve against the real, version-pinned Collector's actual
self-metric names and labels (ADR 0087), not a fixture double.
"""

from __future__ import annotations

import json
import os
import sys
import time
import unittest
import urllib.request
from pathlib import Path

from iip.adapters.evidence import SystemClock
from iip.adapters.memory import AllowTenantPolicy
from iip.adapters.prometheus import (
    PrometheusIntegrationRegistry,
    PrometheusTelemetryMetricsBackend,
    StaticBearerCredentialBroker,
)
from iip.application.ports import ActorContext
from iip.application.query_collector_queue_loss import (
    CollectorQueueLossBinding,
    CollectorQueueLossObjectives,
    CollectorQueueLossService,
    GetCollectorQueueLossCommand,
)


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


PROMETHEUS_ENDPOINT = os.environ.get("IIP_TEST_PROMETHEUS_ENDPOINT")
COLLECTOR_OTLP_ENDPOINT = os.environ.get("IIP_TEST_COLLECTOR_OTLP_ENDPOINT")
EXPORTER_NAME = "otlphttp/iip"


def _binding_registry() -> PrometheusIntegrationRegistry:
    def metric(name: str, backend_metric: str, *, data_type: bool) -> dict:
        attributes = {"exporterName": "exporter"}
        if data_type:
            attributes["dataType"] = "data_type"
        return {
            "name": name,
            "backendMetric": backend_metric,
            "unit": "1",
            "attributes": attributes,
        }

    return PrometheusIntegrationRegistry.from_json(
        json.dumps(
            {
                "integrations": [
                    {
                        "tenantId": "local",
                        "integrationId": "collector-queue-loss-test",
                        "provider": "prometheus",
                        "endpoint": PROMETHEUS_ENDPOINT,
                        "credentialRef": None,
                        "enabled": True,
                        "requestTimeoutSeconds": 10,
                        "maxResponseBytes": 1048576,
                        "metrics": [
                            metric(
                                "platform.collector.exporter.queue-size",
                                "otelcol_exporter_queue_size",
                                data_type=True,
                            ),
                            metric(
                                "platform.collector.exporter.queue-capacity",
                                "otelcol_exporter_queue_capacity",
                                data_type=True,
                            ),
                            metric(
                                "platform.collector.exporter.sent-metric-points",
                                "otelcol_exporter_sent_metric_points",
                                data_type=False,
                            ),
                            metric(
                                "platform.collector.exporter.send-failed-metric-points",
                                "otelcol_exporter_send_failed_metric_points",
                                data_type=False,
                            ),
                            metric(
                                "platform.collector.exporter.sent-log-records",
                                "otelcol_exporter_sent_log_records",
                                data_type=False,
                            ),
                            metric(
                                "platform.collector.exporter.send-failed-log-records",
                                "otelcol_exporter_send_failed_log_records",
                                data_type=False,
                            ),
                        ],
                    }
                ]
            }
        )
    )


def _post_otlp(path: str, payload: dict) -> None:
    request = urllib.request.Request(
        f"{COLLECTOR_OTLP_ENDPOINT}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.status == 200


def _send_failing_metrics(count: int) -> None:
    for index in range(count):
        _post_otlp(
            "/v1/metrics",
            {
                "resourceMetrics": [
                    {
                        "resource": {
                            "attributes": [
                                {
                                    "key": "service.name",
                                    "value": {"stringValue": "collector-queue-loss-test"},
                                }
                            ]
                        },
                        "scopeMetrics": [
                            {
                                "scope": {"name": "probe"},
                                "metrics": [
                                    {
                                        "name": "probe.counter",
                                        "sum": {
                                            "dataPoints": [
                                                {
                                                    "startTimeUnixNano": "1700000000000000000",
                                                    "timeUnixNano": "1700000001000000000",
                                                    "asInt": str(index),
                                                }
                                            ],
                                            "aggregationTemporality": 2,
                                            "isMonotonic": True,
                                        },
                                    }
                                ],
                            }
                        ],
                    }
                ]
            },
        )


def _send_failing_logs(count: int) -> None:
    for _ in range(count):
        _post_otlp(
            "/v1/logs",
            {
                "resourceLogs": [
                    {
                        "resource": {
                            "attributes": [
                                {
                                    "key": "service.name",
                                    "value": {"stringValue": "collector-queue-loss-test"},
                                }
                            ]
                        },
                        "scopeLogs": [
                            {
                                "scope": {"name": "probe"},
                                "logRecords": [
                                    {
                                        "timeUnixNano": "1700000001000000000",
                                        "body": {"stringValue": "probe log"},
                                    }
                                ],
                            }
                        ],
                    }
                ]
            },
        )


def _wait_for_failed_sends(metrics_backend: PrometheusTelemetryMetricsBackend) -> dict:
    # A cumulative counter that stopped growing shows a zero delta between
    # any two readings taken after it plateaus, however wide the window: the
    # earlier scratch probe confirmed the Collector's own send-failed
    # counters stabilize almost immediately once retries exhaust. Keep
    # sending new failing attempts while polling so the window always
    # spans genuine growth, the same way a live customer pipeline would.
    service = CollectorQueueLossService(
        metrics_backend,
        AllowTenantPolicy(),
        SystemClock(),
        CollectorQueueLossBinding(
            "collector-queue-loss-test", EXPORTER_NAME, ("metrics", "logs")
        ),
        CollectorQueueLossObjectives(
            window_seconds=300,
            max_loss_basis_points=0,
            max_queue_utilization_basis_points=10_000,
            minimum_eligible_attempts=1,
        ),
    )
    actor = ActorContext("integration-test", "local", ("platform-admin",))
    for _ in range(30):
        _send_failing_metrics(1)
        _send_failing_logs(1)
        time.sleep(1.5)
        report = service.get(GetCollectorQueueLossCommand(actor)).to_dict()
        metrics_signal = next(
            item
            for item in report["spec"]["signals"]
            if item["signal"] == "metrics"
        )
        logs_signal = next(
            item for item in report["spec"]["signals"] if item["signal"] == "logs"
        )
        if metrics_signal["status"] == "breached" and logs_signal["status"] == "breached":
            return report
    raise AssertionError("Collector never reported a measurable failed send")


@unittest.skipUnless(
    PROMETHEUS_ENDPOINT and COLLECTOR_OTLP_ENDPOINT,
    "IIP_TEST_PROMETHEUS_ENDPOINT and IIP_TEST_COLLECTOR_OTLP_ENDPOINT enable "
    "the Collector queue/loss integration test",
)
class CollectorQueueLossIntegrationTests(unittest.TestCase):
    def test_real_collector_self_metrics_drive_a_breached_report(self) -> None:
        _send_failing_metrics(3)
        _send_failing_logs(3)

        metrics_backend = PrometheusTelemetryMetricsBackend(
            _binding_registry(),
            StaticBearerCredentialBroker.empty(),
            SystemClock(),
        )
        report = _wait_for_failed_sends(metrics_backend)

        self.assertEqual(report["spec"]["status"], "breached")
        self.assertEqual(report["spec"]["binding"]["exporterName"], EXPORTER_NAME)
        for signal in report["spec"]["signals"]:
            with self.subTest(signal=signal["signal"]):
                self.assertEqual(signal["status"], "breached")
                self.assertEqual(signal["sentDelta"], 0)
                self.assertGreaterEqual(signal["failedDelta"], 1)
                self.assertEqual(signal["lossBasisPoints"], 10_000)
                self.assertIsNotNone(signal["queueSize"])
                self.assertIsNotNone(signal["queueCapacity"])
                self.assertEqual(signal["queueCapacity"], 100)

        schema = json.loads(
            (ROOT / "contracts/schemas/collector-queue-loss-report.schema.json").read_text()
        )
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                schema, report, label="collector queue/loss report"
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
