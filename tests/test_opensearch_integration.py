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
from iip.adapters.opensearch import (
    OpenSearchIntegrationRegistry,
    OpenSearchTelemetryLogsBackend,
    StaticOpenSearchCredentialBroker,
)
from iip.application.ingest_resource import IngestResourceCommand
from iip.application.investigate import RunInvestigationCommand
from iip.application.ports import ActorContext, TelemetryLogsQuery
from iip.bootstrap import build_local_runtime


ENDPOINT = os.environ.get("IIP_TEST_OPENSEARCH_ENDPOINT")
INDEX = "logs-test"
ROOT = Path(__file__).resolve().parents[1]


def integration_document() -> dict:
    return {
        "integrations": [
            {
                "tenantId": "local",
                "integrationId": "opensearch-test",
                "provider": "opensearch",
                "endpoint": ENDPOINT,
                "credentialRef": None,
                "index": INDEX,
                "enabled": True,
                "requestTimeoutSeconds": 10,
                "maxResponseBytes": 1048576,
                "fields": {
                    "resourceUid": "resource_uid",
                    "service": "service_name",
                    "severity": "level",
                    "timestamp": "@timestamp",
                    "body": "message",
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


def backend() -> OpenSearchTelemetryLogsBackend:
    return OpenSearchTelemetryLogsBackend(
        OpenSearchIntegrationRegistry.from_json(json.dumps(integration_document())),
        StaticOpenSearchCredentialBroker.empty(),
        SystemClock(),
    )


def wait_for_opensearch() -> None:
    error_type = "none"
    for _ in range(120):
        try:
            with urlopen(f"{ENDPOINT}/_cluster/health", timeout=1) as response:
                if response.status == 200:
                    document = json.loads(response.read().decode("utf-8"))
                    if document.get("status") in ("yellow", "green"):
                        return
        except (OSError, URLError) as exc:
            error_type = type(exc).__name__
            close = getattr(exc, "close", None)
            if close is not None:
                close()
        time.sleep(0.5)
    raise AssertionError(f"OpenSearch did not become ready: {error_type}")


def push_logs(resource_uid: str, timestamp: datetime) -> None:
    documents = [
        {
            "resource_uid": resource_uid,
            "service_name": "api",
            "level": "ERROR",
            "@timestamp": timestamp.isoformat().replace("+00:00", "Z"),
            "message": "rollout unavailable replica observed",
            "environment": "development",
            "namespace": "default",
            "trace_id": "0123456789abcdef0123456789abcdef",
            "span_id": "0123456789abcdef",
        },
        {
            "resource_uid": resource_uid,
            "service_name": "api",
            "level": "ERROR",
            "@timestamp": (timestamp + timedelta(milliseconds=1)).isoformat().replace(
                "+00:00", "Z"
            ),
            "message": "progress deadline exceeded",
            "environment": "development",
            "namespace": "default",
            "trace_id": "0123456789abcdef0123456789abcdef",
            "span_id": "0123456789abcdef",
        },
    ]
    for document in documents:
        request = Request(
            f"{ENDPOINT}/{INDEX}/_doc?refresh=true",
            data=json.dumps(document).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=5) as response:
            if response.status not in (200, 201):
                raise AssertionError(f"unexpected OpenSearch index status {response.status}")


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


@unittest.skipUnless(
    ENDPOINT, "IIP_TEST_OPENSEARCH_ENDPOINT enables the OpenSearch integration test"
)
class OpenSearchIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        wait_for_opensearch()

    def test_real_search_normalizes_and_drives_investigation(self) -> None:
        now = datetime.now(timezone.utc)
        timestamp = now - timedelta(seconds=2)
        resource_uid = "res_e0ae9225a316fce4c97df5c23057b97a"
        push_logs(resource_uid, timestamp)
        logs_backend = backend()

        result = logs_backend.query_logs(
            TelemetryLogsQuery(
                tenant_id="local",
                actor_id="integration-test",
                request_id="leq_99999999999999999999999999999999",
                integration_id="opensearch-test",
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
                "id": "inv_99999999999999999999999999999998",
                "tenantId": actor.tenant_id,
                "actorId": actor.actor_id,
                "requestedAt": iso(now),
            },
            "spec": {
                "question": "Do bounded logs support the degraded rollout hypothesis?",
                "trigger": {
                    "type": "user",
                    "source": "urn:iip:test:opensearch",
                    "summary": "Verify OpenSearch-selected evidence.",
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
                        "id": "lqs_9999999999999998",
                        "integrationId": "opensearch-test",
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
