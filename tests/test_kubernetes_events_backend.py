from __future__ import annotations

import copy
import json
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from iip.adapters.auth import HashedBearerAuthenticator
from iip.adapters.kubernetes_events import (
    KubernetesApiEventsBackend,
    KubernetesEventsBackendError,
    KubernetesEventsConfigurationError,
    KubernetesEventsIntegrationRegistry,
    StaticKubernetesBearerCredentialBroker,
    _NoRedirectHandler,
    _RESOURCE_BINDINGS,
    build_kubernetes_events_backend_from_environment,
)
from iip.application.ports import (
    CredentialLease,
    CredentialLeaseRequest,
    KubernetesEventQuery,
    KubernetesEventResourceRef,
)
from iip.bootstrap import build_runtime_from_env


TOKEN = "kubernetes-api-test-token-0123456789abcdef"


class FixedClock:
    def __init__(self, value: str = "2026-08-16T10:30:05Z") -> None:
        self.value = value

    def now(self) -> str:
        return self.value


def integration_document(**changes: object) -> dict:
    integration = {
        "tenantId": "local",
        "integrationId": "kubernetes-local",
        "provider": "kubernetes",
        "endpoint": "https://127.0.0.1:6443",
        "credentialRef": "credential://kubernetes/local/events-reader",
        "caBundlePath": "/var/run/iip-kubernetes-events/ca.crt",
        "clusterExternalId": "cluster-local",
        "namespaces": ["default", "iip-demo"],
        "clusterEventNamespace": "default",
        "resourceTypes": ["apps/deployment", "core/node"],
        "requestTimeoutSeconds": 10,
        "maxResponseBytes": 1048576,
        "pageSize": 100,
        "maxPages": 5,
        "conditionMappings": {
            "ProgressDeadlineExceeded": "workload.progress-deadline-exceeded"
        },
        "enabled": True,
    }
    integration.update(changes)
    return {"integrations": [integration]}


def credentials_document(**changes: object) -> dict:
    credential = {
        "tenantId": "local",
        "integrationId": "kubernetes-local",
        "credentialRef": "credential://kubernetes/local/events-reader",
        "bearerToken": TOKEN,
        "expiresAt": "2026-08-16T10:35:00Z",
    }
    credential.update(changes)
    return {"credentials": [credential]}


def query(**changes: object) -> KubernetesEventQuery:
    value = KubernetesEventQuery(
        tenant_id="local",
        actor_id="developer",
        request_id="keq_28f7d4ea459b4ce9835fbe8142ccdb11",
        integration_id="kubernetes-local",
        resources=(
            KubernetesEventResourceRef(
                platform_uid="res_e0ae9225a316fce4c97df5c23057b97a",
                provider="kubernetes",
                resource_type="apps/deployment",
                external_id="cluster-local/iip-demo/api",
            ),
        ),
        start="2026-08-16T10:15:00Z",
        end="2026-08-16T10:29:59Z",
        severities=("warning",),
        reasons=("ProgressDeadlineExceeded",),
        max_events=100,
        max_bytes=524288,
        deadline="2026-08-16T10:31:00Z",
    )
    return replace(value, **changes)


def deployment_document() -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "api",
            "namespace": "iip-demo",
            "uid": "9991ed4d-e928-4035-bcbf-67141ce92e7a",
        },
    }


def event_document(
    *,
    uid: str = "36dc54ab-ae8b-43b0-a97a-042995f0b75d",
    reason: str = "ProgressDeadlineExceeded",
    event_type: str = "Warning",
    first: str = "2026-08-16T10:26:52Z",
    last: str = "2026-08-16T10:29:54Z",
    count: int = 4,
) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Event",
        "metadata": {
            "name": "api.progress-deadline",
            "namespace": "iip-demo",
            "uid": uid,
            "creationTimestamp": first,
        },
        "involvedObject": {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "namespace": "iip-demo",
            "name": "api",
            "uid": "9991ed4d-e928-4035-bcbf-67141ce92e7a",
        },
        "reason": reason,
        "type": event_type,
        "firstTimestamp": first,
        "lastTimestamp": last,
        "count": count,
        "source": {"component": "deployment-controller"},
        "message": "Deployment exceeded its configured progress deadline.",
    }


def event_list(items: list[dict], continuation: str = "") -> dict:
    normalized_items = []
    for item in items:
        normalized = copy.deepcopy(item)
        normalized.pop("apiVersion", None)
        normalized.pop("kind", None)
        normalized_items.append(normalized)
    return {
        "apiVersion": "v1",
        "kind": "EventList",
        "metadata": {"continue": continuation, "resourceVersion": "123"},
        "items": normalized_items,
    }


class RecordingTransport:
    def __init__(self, responses: list[dict]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, str], dict[str, object]]] = []

    def get(
        self,
        url: str,
        headers: object,
        **bounds: object,
    ) -> bytes:
        self.calls.append((url, dict(headers), bounds))  # type: ignore[arg-type]
        if not self.responses:
            raise AssertionError("unexpected Kubernetes API request")
        return json.dumps(self.responses.pop(0)).encode()


class RecordingCredentials:
    def __init__(self, lease: CredentialLease | None = None) -> None:
        self.lease = lease or CredentialLease("bearer", TOKEN, None)
        self.requests: list[CredentialLeaseRequest] = []

    def resolve(self, request: CredentialLeaseRequest) -> CredentialLease:
        self.requests.append(request)
        return self.lease


def backend(
    responses: list[dict],
    *,
    integration: dict | None = None,
    clock: FixedClock | None = None,
) -> tuple[KubernetesApiEventsBackend, RecordingTransport, RecordingCredentials]:
    transport = RecordingTransport(responses)
    credentials = RecordingCredentials()
    registry = KubernetesEventsIntegrationRegistry.from_json(
        json.dumps(integration or integration_document())
    )
    return (
        KubernetesApiEventsBackend(
            registry,
            credentials,
            clock or FixedClock(),
            transport,
        ),
        transport,
        credentials,
    )


class KubernetesEventsConfigurationTests(unittest.TestCase):
    def test_resource_catalog_matches_the_observer_public_types(self) -> None:
        self.assertEqual(
            set(_RESOURCE_BINDINGS),
            {
                "core/namespace",
                "core/node",
                "core/pod",
                "core/service",
                "core/configmap",
                "apps/deployment",
                "apps/replicaset",
                "apps/statefulset",
                "apps/daemonset",
                "networking.k8s.io/ingress",
            },
        )

    def test_registry_is_exact_tenant_bound_and_rejects_unsafe_configuration(self) -> None:
        registry = KubernetesEventsIntegrationRegistry.from_json(
            json.dumps(integration_document())
        )
        self.assertEqual(
            registry.resolve("local", "kubernetes-local").cluster_external_id,
            "cluster-local",
        )
        with self.assertRaisesRegex(
            KubernetesEventsBackendError,
            "kubernetes.events.integration.unavailable",
        ):
            registry.resolve("other", "kubernetes-local")

        invalid_documents = []
        for changes in (
            {"endpoint": "http://127.0.0.1:8080"},
            {"caBundlePath": "relative/ca.crt"},
            {"namespaces": ["iip-demo", "iip-demo"]},
            {"clusterEventNamespace": "outside"},
            {"resourceTypes": ["vendor/arbitrary"]},
            {"conditionMappings": {"bad reason": "INVALID"}},
        ):
            invalid_documents.append(integration_document(**changes))
        invalid_documents.append(
            {
                "integrations": integration_document()["integrations"] * 2,
            }
        )
        for document in invalid_documents:
            with self.subTest(document=document), self.assertRaises(
                KubernetesEventsConfigurationError
            ):
                KubernetesEventsIntegrationRegistry.from_json(json.dumps(document))

    def test_static_broker_enforces_provider_scope_and_lease_lifetime(self) -> None:
        broker = StaticKubernetesBearerCredentialBroker.from_json(
            json.dumps(credentials_document())
        )
        request = CredentialLeaseRequest(
            tenant_id="local",
            actor_id="developer",
            integration_id="kubernetes-local",
            credential_ref="credential://kubernetes/local/events-reader",
            provider="kubernetes",
            scopes=("events:read", "resources:read"),
            deadline="2026-08-16T10:31:00Z",
        )
        self.assertEqual(broker.resolve(request).secret, TOKEN)
        self.assertNotIn(TOKEN, repr(broker))
        for invalid in (
            replace(request, provider="prometheus"),
            replace(request, tenant_id="other"),
            replace(request, deadline="2026-08-16T10:36:00Z"),
        ):
            with self.subTest(request=invalid), self.assertRaisesRegex(
                KubernetesEventsBackendError,
                "kubernetes.events.credential.unavailable",
            ):
                broker.resolve(invalid)

    def test_environment_builder_requires_both_protected_documents(self) -> None:
        for environment in ({}, {"IIP_KUBERNETES_EVENTS_INTEGRATIONS_JSON": "{}"}):
            with self.subTest(environment=environment), self.assertRaisesRegex(
                KubernetesEventsConfigurationError,
                "kubernetes.events.configuration.required",
            ):
                build_kubernetes_events_backend_from_environment(
                    environment, FixedClock()
                )


class KubernetesApiEventsBackendTests(unittest.TestCase):
    def test_credential_bearing_redirects_are_disabled(self) -> None:
        handler = _NoRedirectHandler()
        self.assertIsNone(
            handler.redirect_request(
                object(), object(), 302, "redirect", {}, "https://other.example"
            )
        )

    def test_exact_resource_and_event_reads_normalize_without_leaking_credentials(self) -> None:
        adapter, transport, credentials = backend(
            [deployment_document(), event_list([event_document()])]
        )

        result = adapter.query_events(query())

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.warnings, ())
        self.assertEqual(len(result.events), 1)
        event = result.events[0]
        self.assertRegex(event.event_id, r"kve_[a-f0-9]{32}")
        self.assertEqual(event.resource_uid, query().resources[0].platform_uid)
        self.assertEqual(event.severity, "warning")
        self.assertEqual(event.condition, "workload.progress-deadline-exceeded")
        self.assertEqual(event.occurrence_count, 4)
        self.assertEqual(event.reporting_controller, "deployment-controller")
        self.assertEqual(
            transport.calls[0][0],
            "https://127.0.0.1:6443/apis/apps/v1/namespaces/iip-demo/deployments/api",
        )
        self.assertIn("fieldSelector=involvedObject.uid%3D", transport.calls[1][0])
        self.assertEqual(
            credentials.requests[0].scopes,
            ("events:read", "resources:read"),
        )
        self.assertNotIn(TOKEN, transport.calls[0][0])
        self.assertEqual(transport.calls[0][1]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(
            transport.calls[0][2]["ca_bundle_path"],
            "/var/run/iip-kubernetes-events/ca.crt",
        )

    def test_filters_and_time_range_return_honest_no_data(self) -> None:
        events = [
            event_document(reason="FailedScheduling"),
            event_document(last="2026-08-16T10:14:59Z"),
            event_document(event_type="Normal"),
        ]
        adapter, _, _ = backend([deployment_document(), event_list(events)])

        result = adapter.query_events(query())

        self.assertEqual(result.status, "no-data")
        self.assertEqual(result.events, ())
        self.assertEqual(result.warnings, ())

    def test_series_started_before_range_is_represented_as_one_bounded_occurrence(self) -> None:
        series_event = event_document(count=4)
        series_event.pop("lastTimestamp")
        series_event.pop("count")
        series_event["series"] = {
            "lastObservedTime": "2026-08-16T10:29:54Z",
            "count": 7,
        }
        adapter, _, _ = backend(
            [deployment_document(), event_list([series_event])]
        )
        self.assertEqual(adapter.query_events(query()).events[0].occurrence_count, 7)

        adapter, _, _ = backend(
            [
                deployment_document(),
                event_list(
                    [event_document(first="2026-08-15T10:00:00Z", count=900)]
                ),
            ]
        )

        event = adapter.query_events(query()).events[0]

        self.assertEqual(event.first_observed_at, event.last_observed_at)
        self.assertEqual(event.occurrence_count, 1)

    def test_pagination_is_bounded_and_partial_results_are_explicit(self) -> None:
        configured = integration_document(pageSize=1, maxPages=1)
        adapter, transport, _ = backend(
            [deployment_document(), event_list([event_document()], "next-token")],
            integration=configured,
        )

        result = adapter.query_events(query())

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.warnings, ("backend-partial",))
        self.assertEqual(len(transport.calls), 2)

        adapter, _, _ = backend(
            [deployment_document(), event_list([], "next-token")],
            integration=configured,
        )
        with self.assertRaisesRegex(
            KubernetesEventsBackendError,
            "kubernetes.events.backend.response.limited",
        ):
            adapter.query_events(query())

    def test_event_count_limit_is_explicit_and_deterministic(self) -> None:
        first = event_document(uid="11111111-1111-4111-8111-111111111111")
        second = event_document(
            uid="22222222-2222-4222-8222-222222222222",
            last="2026-08-16T10:29:55Z",
        )
        adapter, _, _ = backend(
            [deployment_document(), event_list([second, first])]
        )

        result = adapter.query_events(query(max_events=1))

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.warnings, ("event-limit",))
        self.assertEqual(len(result.events), 1)

    def test_external_identity_namespace_and_api_identity_fail_closed(self) -> None:
        out_of_scope = replace(
            query().resources[0], external_id="cluster-local/kube-system/api"
        )
        adapter, transport, _ = backend([])
        with self.assertRaisesRegex(
            KubernetesEventsBackendError,
            "kubernetes.events.resource.out-of-scope",
        ):
            adapter.query_events(query(resources=(out_of_scope,)))
        self.assertEqual(transport.calls, [])

        invalid = deployment_document()
        invalid["metadata"]["uid"] = ""
        adapter, _, _ = backend([invalid])
        with self.assertRaisesRegex(
            KubernetesEventsBackendError,
            "kubernetes.events.backend.response.invalid",
        ):
            adapter.query_events(query())

    def test_deadline_and_credential_failures_use_stable_codes(self) -> None:
        adapter, transport, _ = backend([], clock=FixedClock("2026-08-16T10:31:00Z"))
        with self.assertRaisesRegex(
            KubernetesEventsBackendError,
            "kubernetes.events.backend.deadline.exceeded",
        ):
            adapter.query_events(query())
        self.assertEqual(transport.calls, [])

        adapter, _, _ = backend([deployment_document()])
        adapter._credentials = RecordingCredentials(CredentialLease("basic", TOKEN))
        with self.assertRaisesRegex(
            KubernetesEventsBackendError,
            "kubernetes.events.credential.unavailable",
        ):
            adapter.query_events(query())

        class FailingCredentials:
            def resolve(self, request: CredentialLeaseRequest) -> CredentialLease:
                del request
                raise RuntimeError("upstream token=provider-secret")

        adapter, transport, _ = backend([])
        adapter._credentials = FailingCredentials()
        with self.assertRaisesRegex(
            KubernetesEventsBackendError,
            "kubernetes.events.credential.unavailable",
        ) as raised:
            adapter.query_events(query())
        self.assertNotIn("provider-secret", str(raised.exception))
        self.assertEqual(transport.calls, [])


class KubernetesEventsRuntimeCompositionTests(unittest.TestCase):
    def identities(self) -> str:
        token = "runtime-reference-token-0123456789abcdef0123456789"
        return json.dumps(
            {
                "identities": [
                    {
                        "tokenSha256": HashedBearerAuthenticator.token_sha256(token),
                        "actorId": "operator",
                        "tenantId": "local",
                        "roles": ["operator"],
                    }
                ]
            }
        )

    def test_runtime_composes_live_backend_only_when_explicitly_selected(self) -> None:
        environment = {
            "IIP_AUTH_IDENTITIES_JSON": self.identities(),
            "IIP_KUBERNETES_EVENTS_BACKEND": "kubernetes-api",
            "IIP_KUBERNETES_EVENTS_INTEGRATIONS_JSON": json.dumps(
                integration_document()
            ),
            "IIP_KUBERNETES_EVENTS_CREDENTIALS_JSON": json.dumps(
                credentials_document(expiresAt=None)
            ),
        }
        with patch.dict(os.environ, environment, clear=True):
            runtime = build_runtime_from_env()

        provider = runtime.evidence._providers["kubernetes-events"]
        self.assertIsInstance(provider._backend, KubernetesApiEventsBackend)
        runtime.close()

    def test_unknown_backend_or_missing_configuration_fails_at_startup(self) -> None:
        for environment in (
            {
                "IIP_AUTH_IDENTITIES_JSON": self.identities(),
                "IIP_KUBERNETES_EVENTS_BACKEND": "kubectl",
            },
            {
                "IIP_AUTH_IDENTITIES_JSON": self.identities(),
                "IIP_KUBERNETES_EVENTS_BACKEND": "kubernetes-api",
            },
        ):
            with self.subTest(environment=environment), patch.dict(
                os.environ, environment, clear=True
            ), self.assertRaises(KubernetesEventsConfigurationError):
                build_runtime_from_env()


if __name__ == "__main__":
    unittest.main()
