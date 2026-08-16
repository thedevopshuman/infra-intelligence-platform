from __future__ import annotations

import copy
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from iip.adapters.evidence import SystemClock
from iip.adapters.kubernetes_events import (
    KubernetesApiEventsBackend,
    KubernetesEventsIntegrationRegistry,
    StaticKubernetesBearerCredentialBroker,
)
from iip.application.ingest_resource import IngestResourceCommand
from iip.application.kubernetes_event_evidence import (
    CollectKubernetesEventEvidenceCommand,
)
from iip.application.ports import (
    ActorContext,
    KubernetesEventQuery,
    KubernetesEventResourceRef,
)
from iip.bootstrap import build_local_runtime


ENDPOINT = os.environ.get("IIP_TEST_KUBERNETES_EVENTS_ENDPOINT")
CA_BUNDLE = os.environ.get("IIP_TEST_KUBERNETES_EVENTS_CA_BUNDLE")
TOKEN = os.environ.get("IIP_TEST_KUBERNETES_EVENTS_TOKEN")
CLUSTER_ID = os.environ.get("IIP_TEST_KUBERNETES_EVENTS_CLUSTER_ID")
NAMESPACE = os.environ.get("IIP_TEST_KUBERNETES_EVENTS_NAMESPACE")
ROOT = Path(__file__).resolve().parents[1]
ENABLED = all((ENDPOINT, CA_BUNDLE, TOKEN, CLUSTER_ID, NAMESPACE))


def iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def live_backend() -> KubernetesApiEventsBackend:
    registry = KubernetesEventsIntegrationRegistry.from_json(
        json.dumps(
            {
                "integrations": [
                    {
                        "tenantId": "local",
                        "integrationId": "kubernetes-live",
                        "provider": "kubernetes",
                        "endpoint": ENDPOINT,
                        "credentialRef": "credential://kubernetes/live/events-reader",
                        "caBundlePath": CA_BUNDLE,
                        "clusterExternalId": CLUSTER_ID,
                        "namespaces": [NAMESPACE],
                        "clusterEventNamespace": NAMESPACE,
                        "resourceTypes": ["apps/deployment"],
                        "requestTimeoutSeconds": 10,
                        "maxResponseBytes": 1048576,
                        "pageSize": 100,
                        "maxPages": 5,
                        "conditionMappings": {
                            "ProgressDeadlineExceeded": "workload.progress-deadline-exceeded"
                        },
                        "enabled": True,
                    }
                ]
            }
        )
    )
    credentials = StaticKubernetesBearerCredentialBroker.from_json(
        json.dumps(
            {
                "credentials": [
                    {
                        "tenantId": "local",
                        "integrationId": "kubernetes-live",
                        "credentialRef": "credential://kubernetes/live/events-reader",
                        "bearerToken": TOKEN,
                        "expiresAt": None,
                    }
                ]
            }
        )
    )
    return KubernetesApiEventsBackend(registry, credentials, SystemClock())


@unittest.skipUnless(
    ENABLED,
    "explicit Kubernetes API endpoint, CA, token, cluster, and namespace enable this test",
)
class KubernetesEventsIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.actor = ActorContext("integration-test", "local")
        self.backend = live_backend()
        self.runtime = build_local_runtime(kubernetes_events_backend=self.backend)
        resource = json.loads(
            (ROOT / "contracts/examples/resource.json").read_text(encoding="utf-8")
        )
        now = datetime.now(timezone.utc)
        resource["metadata"]["observedAt"] = iso(now)
        resource["metadata"]["observation"].update(
            {
                "sourceId": "kubernetes-live",
                "sequence": 1,
                "resourceVersion": "integration-test",
                "checkpoint": "kubernetes:integration-test",
            }
        )
        resource["spec"].update(
            {
                "externalId": f"{CLUSTER_ID}/{NAMESPACE}/event-probe",
                "displayName": "event-probe",
                "attributes": {
                    "namespace": NAMESPACE,
                    "replicas": 0,
                    "availableReplicas": 0,
                },
                "relationships": [],
            }
        )
        self.resource = self.runtime.ingestion.execute(
            IngestResourceCommand(self.actor, resource)
        )

    def tearDown(self) -> None:
        self.runtime.close()

    def request(self) -> dict:
        now = datetime.now(timezone.utc)
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "KubernetesEventEvidenceRequest",
            "metadata": {
                "requestId": "keq_99999999999999999999999999999999",
                "tenantId": "local",
                "actorId": "integration-test",
                "requestedAt": iso(now),
            },
            "spec": {
                "integrationId": "kubernetes-live",
                "resourceRefs": [self.resource.identity.uid],
                "timeRange": {
                    "start": iso(now - timedelta(minutes=5)),
                    "end": iso(now),
                },
                "query": {
                    "severities": ["warning"],
                    "reasons": ["ProgressDeadlineExceeded"],
                },
                "limits": {"maxEvents": 10, "maxBytes": 524288},
                "deadline": iso(now + timedelta(seconds=30)),
            },
        }

    def test_real_read_only_api_query_commits_normalized_evidence(self) -> None:
        now = datetime.now(timezone.utc)
        backend_result = self.backend.query_events(
            KubernetesEventQuery(
                tenant_id="local",
                actor_id="integration-test",
                request_id="keq_88888888888888888888888888888888",
                integration_id="kubernetes-live",
                resources=(
                    KubernetesEventResourceRef(
                        platform_uid=self.resource.identity.uid,
                        provider="kubernetes",
                        resource_type="apps/deployment",
                        external_id=f"{CLUSTER_ID}/{NAMESPACE}/event-probe",
                    ),
                ),
                start=iso(now - timedelta(minutes=5)),
                end=iso(now),
                severities=("warning",),
                reasons=("ProgressDeadlineExceeded",),
                max_events=10,
                max_bytes=524288,
                deadline=iso(now + timedelta(seconds=30)),
            )
        )
        self.assertEqual(backend_result.status, "complete")

        evidence = self.runtime.kubernetes_event_evidence.execute(
            CollectKubernetesEventEvidenceCommand(self.actor, self.request())
        )

        artifact = self.runtime.evidence_store.read_artifact(
            self.actor, evidence["metadata"]["id"]
        )
        result = json.loads(artifact)
        self.assertEqual(result["kind"], "KubernetesEventEvidenceResult")
        self.assertEqual(result["spec"]["status"], "complete")
        self.assertEqual(result["spec"]["summary"]["eventCount"], 1)
        event = result["spec"]["events"][0]
        self.assertEqual(event["resourceRef"], self.resource.identity.uid)
        self.assertEqual(event["reason"], "ProgressDeadlineExceeded")
        self.assertEqual(event["condition"], "workload.progress-deadline-exceeded")
        self.assertNotIn(TOKEN, artifact.decode())


if __name__ == "__main__":
    unittest.main()
