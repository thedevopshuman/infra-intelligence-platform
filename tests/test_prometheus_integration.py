from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta, timezone

from iip.adapters.evidence import SystemClock
from iip.adapters.prometheus import (
    PrometheusIntegrationRegistry,
    PrometheusTelemetryMetricsBackend,
    StaticBearerCredentialBroker,
)
from iip.application.ports import TelemetryMetricsQuery


ENDPOINT = os.environ.get("IIP_TEST_PROMETHEUS_ENDPOINT")


@unittest.skipUnless(
    ENDPOINT,
    "IIP_TEST_PROMETHEUS_ENDPOINT enables the Prometheus integration test",
)
class PrometheusIntegrationTests(unittest.TestCase):
    def test_real_query_range_response_normalizes_to_backend_contract(self) -> None:
        registry = PrometheusIntegrationRegistry.from_json(
            json.dumps(
                {
                    "integrations": [
                        {
                            "tenantId": "local",
                            "integrationId": "prometheus-test",
                            "provider": "prometheus",
                            "endpoint": ENDPOINT,
                            "credentialRef": None,
                            "enabled": True,
                            "requestTimeoutSeconds": 10,
                            "maxResponseBytes": 1048576,
                            "metrics": [
                                {
                                    "name": "platform.prometheus.up",
                                    "backendMetric": "up",
                                    "unit": "1",
                                    "attributes": {"service.name": "job"},
                                }
                            ],
                        }
                    ]
                }
            )
        )
        now = datetime.now(timezone.utc)
        backend = PrometheusTelemetryMetricsBackend(
            registry,
            StaticBearerCredentialBroker.empty(),
            SystemClock(),
        )

        result = backend.query_metrics(
            TelemetryMetricsQuery(
                tenant_id="local",
                actor_id="integration-test",
                request_id="teq_00000000000000000000000000000000",
                integration_id="prometheus-test",
                resource_uids=("res_00000000000000000000000000000000",),
                start=iso(now - timedelta(seconds=15)),
                end=iso(now),
                metric="platform.prometheus.up",
                filters=(),
                aggregation="max",
                step_seconds=1,
                group_by=("service.name",),
                max_series=5,
                max_data_points=100,
                max_bytes=1048576,
                deadline=iso(now + timedelta(seconds=10)),
            )
        )

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.warnings, ())
        self.assertGreaterEqual(len(result.series), 1)
        prometheus = next(
            series
            for series in result.series
            if ("service.name", "prometheus") in series.attributes
        )
        self.assertTrue(prometheus.points)
        self.assertTrue(all(point.value == 1 for point in prometheus.points))


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    unittest.main()
