from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta, timezone

from iip.adapters.evidence import SystemClock
from iip.adapters.kubernetes_actions import (
    KubernetesActionIntegrationRegistry,
    KubernetesRestartExecutor,
    StaticKubernetesActionCredentialBroker,
)
from iip.application.ports import ActorContext


ENDPOINT = os.environ.get("IIP_TEST_KUBERNETES_ACTIONS_ENDPOINT")
CA_BUNDLE = os.environ.get("IIP_TEST_KUBERNETES_ACTIONS_CA_BUNDLE")
TOKEN = os.environ.get("IIP_TEST_KUBERNETES_ACTIONS_TOKEN")
CLUSTER_ID = os.environ.get("IIP_TEST_KUBERNETES_ACTIONS_CLUSTER_ID")
NAMESPACE = os.environ.get("IIP_TEST_KUBERNETES_ACTIONS_NAMESPACE")
PROVIDER_UID = os.environ.get("IIP_TEST_KUBERNETES_ACTIONS_PROVIDER_UID")
ENABLED = all((ENDPOINT, CA_BUNDLE, TOKEN, CLUSTER_ID, NAMESPACE, PROVIDER_UID))


def iso(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def executor() -> KubernetesRestartExecutor:
    registry = KubernetesActionIntegrationRegistry.from_json(
        json.dumps(
            {
                "integrations": [
                    {
                        "tenantId": "local",
                        "integrationId": "kubernetes-action-live",
                        "provider": "kubernetes",
                        "endpoint": ENDPOINT,
                        "credentialRef": "credential://kubernetes/live/action-executor",
                        "caBundlePath": CA_BUNDLE,
                        "clusterExternalId": CLUSTER_ID,
                        "namespaces": [NAMESPACE],
                        "workloadKinds": ["deployment"],
                        "requestTimeoutSeconds": 10,
                        "maxResponseBytes": 1048576,
                        "verificationTimeoutSeconds": 60,
                        "pollIntervalMilliseconds": 500,
                        "liveExecutionEnabled": True,
                        "enabled": True,
                    }
                ]
            }
        )
    )
    credentials = StaticKubernetesActionCredentialBroker.from_json(
        json.dumps(
            {
                "credentials": [
                    {
                        "tenantId": "local",
                        "integrationId": "kubernetes-action-live",
                        "credentialRef": "credential://kubernetes/live/action-executor",
                        "bearerToken": TOKEN,
                        "expiresAt": None,
                    }
                ]
            }
        )
    )
    return KubernetesRestartExecutor(registry, credentials, SystemClock())


def proposal(*, dry_run: bool) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "ActionProposal",
        "metadata": {
            "id": "act_99999999999999999999999999999999",
            "tenantId": "local",
            "actorId": "integration-proposer",
            "createdAt": iso(now),
        },
        "spec": {
            "actionType": "kubernetes.restart-workload",
            "integrationId": "kubernetes-action-live",
            "providerObjectUid": PROVIDER_UID,
            "parameters": {
                "namespace": NAMESPACE,
                "workloadKind": "deployment",
                "workloadName": "restart-probe",
            },
            "dryRun": dry_run,
            "expiresAt": iso(now + timedelta(minutes=3)),
        },
    }


@unittest.skipUnless(
    ENABLED,
    "explicit Kubernetes action endpoint, CA, token, cluster, namespace, and UID enable this test",
)
class KubernetesActionIntegrationTests(unittest.TestCase):
    def test_real_server_dry_run_and_verified_live_restart(self) -> None:
        action_executor = executor()
        actor = ActorContext("integration-executor", "local", ("executor",))

        dry_run = action_executor.execute(
            actor,
            proposal(dry_run=True),
            approval_id="apr_99999999999999999999999999999999",
        )
        live = action_executor.execute(
            actor,
            proposal(dry_run=False),
            approval_id="apr_99999999999999999999999999999999",
        )

        self.assertEqual(dry_run.outcome, "dry-run")
        self.assertEqual(live.outcome, "succeeded")
        self.assertEqual(live.verification_status, "passed")
        encoded = json.dumps({"dryRun": dry_run.__dict__, "live": live.__dict__})
        self.assertNotIn(TOKEN, encoded)
        self.assertNotIn(ENDPOINT, encoded)


if __name__ == "__main__":
    unittest.main()
