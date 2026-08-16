from __future__ import annotations

import json
import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from iip.adapters.evidence import SystemClock
from iip.adapters.prometheus import (
    PrometheusIntegrationRegistry,
    PrometheusTelemetryMetricsBackend,
    StaticBearerCredentialBroker,
)
from iip.application.ingest_resource import IngestResourceCommand
from iip.application.investigate import RunInvestigationCommand
from iip.application.ports import (
    ActorContext,
    TelemetryMetricsQuery,
    TelemetryMetricsResult,
)
from iip.bootstrap import build_local_runtime


ENDPOINT = os.environ.get("IIP_TEST_PROMETHEUS_ENDPOINT")
ROOT = Path(__file__).resolve().parents[1]


def backend() -> PrometheusTelemetryMetricsBackend:
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
    return PrometheusTelemetryMetricsBackend(
        registry,
        StaticBearerCredentialBroker.empty(),
        SystemClock(),
    )


def wait_for_up(
    metrics_backend: PrometheusTelemetryMetricsBackend,
) -> TelemetryMetricsResult:
    result = None
    for _ in range(40):
        now = datetime.now(timezone.utc)
        result = metrics_backend.query_metrics(
            TelemetryMetricsQuery(
                tenant_id="local",
                actor_id="integration-test",
                request_id="teq_00000000000000000000000000000000",
                integration_id="prometheus-test",
                resource_uids=("res_00000000000000000000000000000000",),
                start=iso(now - timedelta(seconds=30)),
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
        if result.status == "complete":
            prometheus = next(
                (
                    series
                    for series in result.series
                    if ("service.name", "prometheus") in series.attributes
                ),
                None,
            )
            if prometheus is not None and len(prometheus.points) >= 6:
                return result
        time.sleep(0.25)
    assert result is not None
    return result


@unittest.skipUnless(
    ENDPOINT,
    "IIP_TEST_PROMETHEUS_ENDPOINT enables the Prometheus integration test",
)
class PrometheusIntegrationTests(unittest.TestCase):
    def test_real_query_range_response_normalizes_to_backend_contract(self) -> None:
        result = wait_for_up(backend())

        self.assertIsNotNone(result)
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

    def test_investigation_selects_real_prometheus_evidence(self) -> None:
        metrics_backend = backend()
        self.assertEqual(wait_for_up(metrics_backend).status, "complete")
        now = datetime.now(timezone.utc)
        runtime = build_local_runtime(telemetry_metrics_backend=metrics_backend)
        actor = ActorContext("integration-test", "local")
        resource = json.loads(
            (ROOT / "contracts" / "examples" / "resource.json").read_text()
        )
        stored = runtime.ingestion.execute(IngestResourceCommand(actor, resource))
        request = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "InvestigationRequest",
            "metadata": {
                "id": "inv_99999999999999999999999999999999",
                "tenantId": actor.tenant_id,
                "actorId": actor.actor_id,
                "requestedAt": iso(now),
            },
            "spec": {
                "question": "Is Prometheus reachable while the workload is degraded?",
                "trigger": {
                    "type": "user",
                    "source": "urn:iip:test:prometheus",
                    "summary": "Verify investigation-selected metric evidence.",
                },
                "scope": {
                    "resourceUids": [stored.identity.uid],
                    "timeRange": {
                        "start": iso(now - timedelta(seconds=10)),
                        "end": iso(now),
                    },
                },
                "evidenceTypes": [
                    "kubernetes.resource-status",
                    "telemetry.metrics",
                ],
                "allowedTools": ["evidence/fetch", "telemetry/query"],
                "telemetrySelections": [
                    {
                        "id": "tqs_9999999999999999",
                        "integrationId": "prometheus-test",
                        "rootCauseClasses": [
                            "kubernetes.rollout.unavailable-replicas"
                        ],
                        "query": {
                            "metric": "platform.prometheus.up",
                            "filters": [],
                            "aggregation": {
                                "function": "max",
                                "stepSeconds": 1,
                            },
                            "groupBy": ["service.name"],
                        },
                        "limits": {
                            "maxSeries": 5,
                            "maxDataPoints": 100,
                            "maxBytes": 1048576,
                        },
                        "baselineComparison": {
                            "statistic": "mean",
                            "unit": "1",
                            "baselineTimeRange": {
                                "start": iso(now - timedelta(seconds=5)),
                                "end": iso(now - timedelta(seconds=3)),
                            },
                            "evaluationTimeRange": {
                                "start": iso(now - timedelta(seconds=2)),
                                "end": iso(now),
                            },
                            "calculation": "ratio",
                            "operator": "gte",
                            "threshold": 1,
                            "whenMatched": "supports",
                            "whenNotMatched": "contradicts",
                        },
                    }
                ],
                "budgets": {
                    "maxToolCalls": 4,
                    "maxWallTimeSeconds": 30,
                    "maxModelTokens": 0,
                    "maxCostUsd": 0,
                    "maxEvidenceItems": 4,
                    "maxIterations": 1,
                },
                "maxAuthority": "read",
            },
        }

        report = runtime.investigations.execute(
            RunInvestigationCommand(actor, request)
        )

        self.assertEqual(report["spec"]["outcome"], "conclusive")
        self.assertEqual(report["spec"]["usage"]["toolCalls"], 2)
        self.assertEqual(report["spec"]["usage"]["evidenceItems"], 2)
        assessment = report["spec"]["telemetryAssessments"][0]
        self.assertEqual(assessment["assessmentType"], "baseline-comparison")
        self.assertEqual(assessment["baselineValue"], 1)
        self.assertEqual(assessment["evaluationValue"], 1)
        self.assertEqual(assessment["comparisonValue"], 1)
        self.assertEqual(assessment["comparisonUnit"], "1")
        self.assertEqual(assessment["disposition"], "supporting")
        self.assertIn(
            assessment["evidenceId"],
            report["spec"]["hypotheses"][0]["supportingEvidenceIds"],
        )
        evidence = tuple(runtime.evidence_store.list(actor))
        self.assertEqual(
            {item["spec"]["type"] for item in evidence},
            {"kubernetes.resource-status", "telemetry.metrics"},
        )


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


if __name__ == "__main__":
    unittest.main()
