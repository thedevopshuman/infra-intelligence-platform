from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlsplit

from iip.adapters.kubernetes_actions import (
    KubernetesActionIntegrationRegistry,
    KubernetesActionsConfigurationError,
    KubernetesRestartExecutor,
    StaticKubernetesActionCredentialBroker,
    build_kubernetes_action_executor_from_environment,
)
from iip.application.ports import ActorContext, CredentialLease
from iip.bootstrap import _kubernetes_action_executor_from_env, build_runtime_from_env


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)

    def now(self) -> str:
        return iso(self.value)

    def sleep(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class RecordingBroker:
    def __init__(self) -> None:
        self.requests = []

    def resolve(self, request):
        self.requests.append(request)
        return CredentialLease("bearer", "kubernetes-action-token-0123456789", None)


class FakeTransport:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.requests = []

    def request(
        self,
        method,
        url,
        body,
        headers,
        *,
        ca_bundle_path,
        timeout_seconds,
        max_response_bytes,
    ) -> bytes:
        self.requests.append(
            {
                "method": method,
                "url": url,
                "body": json.loads(body) if body is not None else None,
                "headers": dict(headers),
                "ca": ca_bundle_path,
                "timeout": timeout_seconds,
                "limit": max_response_bytes,
            }
        )
        if not self.responses:
            raise AssertionError("unexpected Kubernetes request")
        return json.dumps(self.responses.pop(0)).encode()


def integration(*, live: bool = True) -> dict:
    return {
        "tenantId": "local",
        "integrationId": "kubernetes-local",
        "provider": "kubernetes",
        "endpoint": "https://127.0.0.1:6443",
        "credentialRef": "credential://kubernetes/local/action-executor",
        "caBundlePath": "/var/run/iip-kubernetes-actions/ca.crt",
        "clusterExternalId": "cluster-local",
        "namespaces": ["default"],
        "workloadKinds": ["deployment", "statefulset", "daemonset"],
        "requestTimeoutSeconds": 1,
        "maxResponseBytes": 1048576,
        "verificationTimeoutSeconds": 5,
        "pollIntervalMilliseconds": 5000,
        "liveExecutionEnabled": live,
        "enabled": True,
    }


def workload(
    *,
    resource_version: str,
    generation: int,
    annotation: str | None = None,
    ready: bool = False,
    provider_uid: str = "provider-deployment-uid",
) -> dict:
    annotations = {} if annotation is None else {"iip.platform/restarted-at": annotation}
    count = 1 if ready else 0
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "api",
            "namespace": "default",
            "uid": provider_uid,
            "resourceVersion": resource_version,
            "generation": generation,
        },
        "spec": {
            "replicas": 1,
            "template": {"metadata": {"annotations": annotations}},
        },
        "status": {
            "observedGeneration": generation,
            "updatedReplicas": count,
            "readyReplicas": count,
            "availableReplicas": count,
            "unavailableReplicas": 0 if ready else 1,
        },
    }


def proposal(clock: MutableClock, *, dry_run: bool) -> dict:
    return {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "ActionProposal",
        "metadata": {
            "id": "act_55555555555555555555555555555555",
            "tenantId": "local",
            "actorId": "incident-investigator",
            "createdAt": clock.now(),
        },
        "spec": {
            "actionType": "kubernetes.restart-workload",
            "integrationId": "kubernetes-local",
            "providerObjectUid": "provider-deployment-uid",
            "parameters": {
                "namespace": "default",
                "workloadKind": "deployment",
                "workloadName": "api",
            },
            "dryRun": dry_run,
            "expiresAt": iso(clock.value + timedelta(minutes=5)),
        },
    }


class KubernetesActionExecutorTests(unittest.TestCase):
    def executor(
        self, responses: list[dict], *, live: bool = True
    ) -> tuple[KubernetesRestartExecutor, MutableClock, RecordingBroker, FakeTransport]:
        clock = MutableClock()
        broker = RecordingBroker()
        transport = FakeTransport(responses)
        registry = KubernetesActionIntegrationRegistry.from_json(
            json.dumps({"integrations": [integration(live=live)]})
        )
        return (
            KubernetesRestartExecutor(
                registry,
                broker,
                clock,
                transport=transport,
                sleeper=clock.sleep,
            ),
            clock,
            broker,
            transport,
        )

    def test_server_side_dry_run_uses_exact_scope_without_mutation(self) -> None:
        restart_at = "2026-08-17T10:00:00Z"
        executor, clock, broker, transport = self.executor(
            [
                workload(resource_version="10", generation=1),
                workload(
                    resource_version="10",
                    generation=2,
                    annotation=restart_at,
                ),
            ]
        )

        outcome = executor.execute(
            ActorContext("workflow-executor", "local", ("executor",)),
            proposal(clock, dry_run=True),
            approval_id="apr_66666666666666666666666666666666",
        )

        self.assertEqual(outcome.outcome, "dry-run")
        self.assertEqual(outcome.verification_status, "not-run")
        self.assertEqual(
            broker.requests[0].scopes,
            ("resources:read", "workloads:patch"),
        )
        self.assertEqual([item["method"] for item in transport.requests], ["GET", "PATCH"])
        self.assertEqual(
            urlsplit(transport.requests[1]["url"]).query,
            "fieldManager=iip-action-executor&dryRun=All",
        )
        self.assertEqual(
            transport.requests[1]["body"]["metadata"]["resourceVersion"],
            "10",
        )
        self.assertNotIn(
            broker.requests[0].credential_ref,
            json.dumps(outcome.__dict__),
        )

    def test_live_restart_verifies_controller_readiness(self) -> None:
        restart_at = "2026-08-17T10:00:00Z"
        executor, clock, _, transport = self.executor(
            [
                workload(resource_version="10", generation=1),
                workload(resource_version="10", generation=2, annotation=restart_at),
                workload(
                    resource_version="11",
                    generation=2,
                    annotation=restart_at,
                    ready=True,
                ),
            ]
        )

        outcome = executor.execute(
            ActorContext("workflow-executor", "local", ("executor",)),
            proposal(clock, dry_run=False),
            approval_id="apr_66666666666666666666666666666666",
        )

        self.assertEqual(outcome.outcome, "succeeded")
        self.assertEqual(outcome.verification_status, "passed")
        self.assertEqual([item["method"] for item in transport.requests], ["GET", "PATCH", "PATCH"])
        self.assertNotIn("dryRun", urlsplit(transport.requests[2]["url"]).query)

    def test_failed_verification_restores_prior_annotation(self) -> None:
        restart_at = "2026-08-17T10:00:00Z"
        executor, clock, _, transport = self.executor(
            [
                workload(resource_version="10", generation=1, annotation="prior"),
                workload(resource_version="10", generation=2, annotation=restart_at),
                workload(resource_version="11", generation=2, annotation=restart_at),
                workload(resource_version="11", generation=3, annotation="prior"),
                workload(resource_version="12", generation=3, annotation="prior"),
                workload(resource_version="12", generation=3, annotation="prior"),
            ]
        )

        outcome = executor.execute(
            ActorContext("workflow-executor", "local", ("executor",)),
            proposal(clock, dry_run=False),
            approval_id="apr_66666666666666666666666666666666",
        )

        self.assertEqual(outcome.outcome, "rolled-back")
        self.assertEqual(outcome.rollback_status, "succeeded")
        rollback_bodies = [
            item["body"] for item in transport.requests[-3:-1]
        ]
        self.assertTrue(
            all(
                body["spec"]["template"]["metadata"]["annotations"]
                ["iip.platform/restarted-at"]
                == "prior"
                for body in rollback_bodies
            )
        )

    def test_provider_uid_replacement_fails_before_patch(self) -> None:
        executor, clock, _, transport = self.executor(
            [
                workload(
                    resource_version="10",
                    generation=1,
                    provider_uid="replacement-uid",
                )
            ]
        )

        outcome = executor.execute(
            ActorContext("workflow-executor", "local", ("executor",)),
            proposal(clock, dry_run=False),
            approval_id="apr_66666666666666666666666666666666",
        )

        self.assertEqual(outcome.outcome, "failed")
        self.assertEqual(outcome.error_code, "action.executor.target-replaced")
        self.assertEqual([item["method"] for item in transport.requests], ["GET"])

    def test_live_execution_requires_explicit_integration_flag(self) -> None:
        executor, clock, _, transport = self.executor([], live=False)

        outcome = executor.execute(
            ActorContext("workflow-executor", "local", ("executor",)),
            proposal(clock, dry_run=False),
            approval_id="apr_66666666666666666666666666666666",
        )

        self.assertEqual(
            outcome.error_code, "action.executor.configuration-unavailable"
        )
        self.assertEqual(transport.requests, [])


class KubernetesActionConfigurationTests(unittest.TestCase):
    def test_environment_builder_requires_closed_protected_documents(self) -> None:
        with self.assertRaisesRegex(
            KubernetesActionsConfigurationError, "configuration.required"
        ):
            build_kubernetes_action_executor_from_environment({}, MutableClock())
        invalid = integration()
        invalid["ambientToken"] = "must-not-be-accepted"
        with self.assertRaisesRegex(
            KubernetesActionsConfigurationError, "configuration.invalid"
        ):
            KubernetesActionIntegrationRegistry.from_json(
                json.dumps({"integrations": [invalid]})
            )

    def test_static_broker_is_exact_scope_and_hides_secret(self) -> None:
        secret = "kubernetes-action-token-0123456789"
        broker = StaticKubernetesActionCredentialBroker.from_json(
            json.dumps(
                {
                    "credentials": [
                        {
                            "tenantId": "local",
                            "integrationId": "kubernetes-local",
                            "credentialRef": "credential://kubernetes/local/action-executor",
                            "bearerToken": secret,
                            "expiresAt": None,
                        }
                    ]
                }
            )
        )

        self.assertNotIn(secret, repr(broker))

    def test_runtime_selection_is_explicit_and_defaults_to_safe_executor(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(_kubernetes_action_executor_from_env())

        with patch.dict(
            os.environ,
            {"IIP_KUBERNETES_ACTION_EXECUTOR": "ambient-client"},
            clear=True,
        ):
            with self.assertRaisesRegex(
                KubernetesActionsConfigurationError, "configuration.invalid"
            ):
                _kubernetes_action_executor_from_env()

        broker = RecordingBroker()
        with patch.dict(
            os.environ,
            {
                "IIP_KUBERNETES_ACTION_EXECUTOR": "kubernetes-api",
                "IIP_KUBERNETES_ACTIONS_INTEGRATIONS_JSON": json.dumps(
                    {"integrations": [integration()]}
                ),
            },
            clear=True,
        ):
            selected = _kubernetes_action_executor_from_env(broker)

        self.assertIsInstance(selected, KubernetesRestartExecutor)

    def test_workflow_runtime_does_not_compose_live_action_authority(self) -> None:
        environment = {
            "IIP_AUTH_IDENTITIES_JSON": json.dumps(
                {
                    "identities": [
                        {
                            "tokenSha256": "sha256:" + "0" * 64,
                            "actorId": "worker-bootstrap-test",
                            "tenantId": "local",
                            "roles": ["developer"],
                        }
                    ]
                }
            ),
            "IIP_KUBERNETES_ACTION_EXECUTOR": "ambient-client",
        }
        with patch.dict(os.environ, environment, clear=True):
            runtime = build_runtime_from_env(include_action_executor=False)
            runtime.close()
            with self.assertRaisesRegex(
                KubernetesActionsConfigurationError, "configuration.invalid"
            ):
                build_runtime_from_env()


if __name__ == "__main__":
    unittest.main()
