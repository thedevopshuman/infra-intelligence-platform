from __future__ import annotations

import json
import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from iip.adapters.evidence import SystemClock
from iip.adapters.loki import (
    LokiIntegrationRegistry,
    LokiTelemetryLogsBackend,
    StaticLokiCredentialBroker,
)
from iip.application.ingest_resource import IngestResourceCommand
from iip.application.investigate import RunInvestigationCommand
from iip.application.ports import ActorContext, TelemetryLogsQuery
from iip.bootstrap import build_local_runtime


ENDPOINT = os.environ.get("IIP_TEST_LOKI_ENDPOINT")
ROOT = Path(__file__).resolve().parents[1]


def integration_document() -> dict:
    return {
        "integrations": [
            {
                "tenantId": "local",
                "integrationId": "loki-test",
                "provider": "loki",
                "endpoint": ENDPOINT,
                "credentialRef": None,
                "organizationId": None,
                "enabled": True,
                "requestTimeoutSeconds": 10,
                "maxResponseBytes": 1048576,
                "labels": {
                    "resourceUid": "resource_uid",
                    "service": "service_name",
                    "severity": "level",
                    "traceId": "trace_id",
                    "spanId": "span_id",
                    "attributes": {
                        "deployment.environment": "environment",
                        "k8s.namespace.name": "namespace",
                    },
                },
                "services": {"api": "api"},
                "severities": {"error": "ERROR"},
            }
        ]
    }


def backend() -> LokiTelemetryLogsBackend:
    return LokiTelemetryLogsBackend(
        LokiIntegrationRegistry.from_json(json.dumps(integration_document())),
        StaticLokiCredentialBroker.empty(),
        SystemClock(),
    )


def wait_for_loki() -> None:
    error_type = "none"
    for _ in range(80):
        try:
            with urlopen(f"{ENDPOINT}/ready", timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, URLError) as exc:
            error_type = type(exc).__name__
            close = getattr(exc, "close", None)
            if close is not None:
                close()
        time.sleep(0.25)
    raise AssertionError(f"Loki did not become ready: {error_type}")


def push_logs(resource_uid: str, timestamp_ns: int) -> None:
    payload = {
        "streams": [
            {
                "stream": {
                    "resource_uid": resource_uid,
                    "service_name": "api",
                    "level": "ERROR",
                    "environment": "development",
                    "namespace": "default",
                    "trace_id": "0123456789abcdef0123456789abcdef",
                    "span_id": "0123456789abcdef",
                },
                "values": [
                    [str(timestamp_ns), "rollout unavailable replica observed"],
                    [str(timestamp_ns + 1_000_000), "progress deadline exceeded"],
                ],
            }
        ]
    }
    request = Request(
        f"{ENDPOINT}/loki/api/v1/push",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        if response.status != 204:
            raise AssertionError(f"unexpected Loki push status {response.status}")


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@unittest.skipUnless(ENDPOINT, "IIP_TEST_LOKI_ENDPOINT enables the Loki integration test")
class LokiIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        wait_for_loki()

    def test_real_query_range_normalizes_and_drives_investigation(self) -> None:
        now = datetime.now(timezone.utc)
        timestamp_ns = int((now - timedelta(seconds=2)).timestamp() * 1_000_000_000)
        resource_uid = "res_e0ae9225a316fce4c97df5c23057b97a"
        push_logs(resource_uid, timestamp_ns)
        logs_backend = backend()

        result = logs_backend.query_logs(
            TelemetryLogsQuery(
                tenant_id="local",
                actor_id="integration-test",
                request_id="leq_99999999999999999999999999999999",
                integration_id="loki-test",
                resource_uids=(resource_uid,),
                start=iso(now - timedelta(seconds=30)),
                end=iso(now),
                service_names=("api",),
                severities=("error",),
                filters=(("deployment.environment", "eq", "development"),),
                max_records=10,
                max_bytes=1048576,
                deadline=iso(now + timedelta(seconds=15)),
            )
        )

        self.assertEqual(result.status, "complete")
        self.assertEqual(len(result.records), 2)
        self.assertEqual({record.service_name for record in result.records}, {"api"})
        self.assertEqual({record.severity for record in result.records}, {"error"})
        self.assertTrue(all(record.trace_id for record in result.records))

        runtime = build_local_runtime(telemetry_logs_backend=logs_backend)
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
                "question": "Do bounded logs support the degraded rollout hypothesis?",
                "trigger": {
                    "type": "user",
                    "source": "urn:iip:test:loki",
                    "summary": "Verify Loki-selected evidence.",
                },
                "scope": {
                    "resourceUids": [stored.identity.uid],
                    "timeRange": {
                        "start": iso(now - timedelta(seconds=30)),
                        "end": iso(now),
                    },
                },
                "evidenceTypes": ["kubernetes.resource-status", "telemetry.logs"],
                "allowedTools": ["evidence/fetch", "telemetry/query"],
                "logSelections": [
                    {
                        "id": "lqs_9999999999999999",
                        "integrationId": "loki-test",
                        "rootCauseClasses": [
                            "kubernetes.rollout.unavailable-replicas"
                        ],
                        "query": {
                            "serviceNames": ["api"],
                            "severities": ["error"],
                            "filters": [
                                {
                                    "attribute": "deployment.environment",
                                    "operator": "eq",
                                    "value": "development",
                                }
                            ],
                        },
                        "limits": {"maxRecords": 10, "maxBytes": 1048576},
                        "interpretation": {
                            "minRecords": 2,
                            "whenMatched": "supports",
                            "whenNotMatched": "neutral",
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

        report = runtime.investigations.execute(RunInvestigationCommand(actor, request))

        self.assertEqual(report["spec"]["outcome"], "conclusive")
        self.assertIn("logAssessments", report["spec"], report)
        assessment = report["spec"]["logAssessments"][0]
        self.assertEqual(assessment["observedRecordCount"], 2)
        self.assertEqual(assessment["disposition"], "supporting")
        self.assertIn(
            assessment["evidenceId"],
            report["spec"]["hypotheses"][0]["supportingEvidenceIds"],
        )
        evidence = tuple(runtime.evidence_store.list(actor))
        self.assertEqual(
            {item["spec"]["type"] for item in evidence},
            {"kubernetes.resource-status", "telemetry.logs"},
        )


if __name__ == "__main__":
    unittest.main()
