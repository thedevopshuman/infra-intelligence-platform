from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timezone
from urllib.parse import parse_qs
from unittest.mock import patch

from iip.adapters.auth import HashedBearerAuthenticator
from iip.adapters.prometheus import (
    NoRedirectHandler,
    PrometheusBackendError,
    PrometheusConfigurationError,
    PrometheusIntegrationRegistry,
    PrometheusTelemetryMetricsBackend,
    StaticBearerCredentialBroker,
    UrllibPrometheusHttpTransport,
)
from iip.application.ports import (
    CredentialLease,
    CredentialLeaseRequest,
    TelemetryMetricsQuery,
)
from iip.bootstrap import build_runtime_from_env


TOKEN = "prometheus-reference-bearer-token-0123456789abcdef"


class FixedClock:
    def __init__(self, value: str = "2026-08-14T10:30:04Z") -> None:
        self.value = value

    def now(self) -> str:
        return self.value


class RecordingTransport:
    def __init__(self, document: object | None = None) -> None:
        self.document = document if document is not None else prometheus_result()
        self.requests: list[dict[str, object]] = []

    def post(
        self,
        url: str,
        body: bytes,
        headers: dict[str, str],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        self.requests.append(
            {
                "url": url,
                "body": body,
                "headers": dict(headers),
                "timeoutSeconds": timeout_seconds,
                "maxResponseBytes": max_response_bytes,
            }
        )
        return json.dumps(self.document).encode()


class RecordingCredentialBroker:
    def __init__(self, lease: CredentialLease | None = None) -> None:
        self.lease = lease or CredentialLease("bearer", TOKEN)
        self.requests: list[CredentialLeaseRequest] = []

    def resolve(self, request: CredentialLeaseRequest) -> CredentialLease:
        self.requests.append(request)
        return self.lease


def integration_document(
    *,
    endpoint: str = "https://prometheus.example/base",
    credential_ref: str | None = "credential://local/prometheus/primary",
) -> dict:
    return {
        "integrations": [
            {
                "tenantId": "local",
                "integrationId": "observability-local",
                "provider": "prometheus",
                "endpoint": endpoint,
                "credentialRef": credential_ref,
                "enabled": True,
                "requestTimeoutSeconds": 10,
                "maxResponseBytes": 1048576,
                "metrics": [
                    {
                        "name": "k8s.pod.container.restarts",
                        "backendMetric": (
                            "kube_pod_container_status_restarts_total"
                        ),
                        "unit": "1",
                        "attributes": {
                            "k8s.namespace.name": "namespace",
                            "k8s.pod.name": "pod",
                        },
                    }
                ],
            }
        ]
    }


def credentials_document() -> dict:
    return {
        "credentials": [
            {
                "tenantId": "local",
                "integrationId": "observability-local",
                "credentialRef": "credential://local/prometheus/primary",
                "bearerToken": TOKEN,
                "expiresAt": "2026-08-14T10:32:00Z",
            }
        ]
    }


def query(**overrides: object) -> TelemetryMetricsQuery:
    values: dict[str, object] = {
        "tenant_id": "local",
        "actor_id": "developer",
        "request_id": "teq_8f5e2a7c1d9046b3a1c7e9f204d6b830",
        "integration_id": "observability-local",
        "resource_uids": ("res_e0ae9225a316fce4c97df5c23057b97a",),
        "start": "2026-08-14T10:25:00Z",
        "end": "2026-08-14T10:30:00Z",
        "metric": "k8s.pod.container.restarts",
        "filters": (("k8s.namespace.name", "eq", "default"),),
        "aggregation": "max",
        "step_seconds": 60,
        "group_by": ("k8s.pod.name",),
        "max_series": 20,
        "max_data_points": 1000,
        "max_bytes": 1048576,
        "deadline": "2026-08-14T10:31:02Z",
    }
    values.update(overrides)
    return TelemetryMetricsQuery(**values)  # type: ignore[arg-type]


def unix(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def prometheus_result() -> dict:
    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": [
                {
                    "metric": {"pod": "api-7d9f5c6b9f-x2k4m"},
                    "values": [
                        [unix("2026-08-14T10:28:00Z"), "0"],
                        [unix("2026-08-14T10:29:00Z"), "1"],
                        [unix("2026-08-14T10:30:00Z"), "2"],
                    ],
                }
            ],
        },
    }


class PrometheusConfigurationTests(unittest.TestCase):
    def test_registry_accepts_only_closed_non_secret_metric_mappings(self) -> None:
        invalid = []
        inline_secret = integration_document()
        inline_secret["integrations"][0]["authorization"] = "Bearer secret"
        invalid.append(inline_secret)
        userinfo = integration_document(endpoint="https://user:secret@example.com")
        invalid.append(userinfo)
        vendor_query = integration_document()
        vendor_query["integrations"][0]["metrics"][0]["backendMetric"] = (
            "up or vector(1)"
        )
        invalid.append(vendor_query)
        duplicate = integration_document()
        duplicate["integrations"].append(duplicate["integrations"][0].copy())
        invalid.append(duplicate)
        duplicate_label = integration_document()
        duplicate_label["integrations"][0]["metrics"][0]["attributes"] = {
            "k8s.namespace.name": "scope",
            "k8s.pod.name": "scope",
        }
        invalid.append(duplicate_label)

        for document in invalid:
            with self.subTest(document=document):
                with self.assertRaisesRegex(
                    PrometheusConfigurationError,
                    "telemetry.backend.configuration.invalid",
                ):
                    PrometheusIntegrationRegistry.from_json(json.dumps(document))

    def test_static_broker_is_exact_scoped_and_never_represents_secret(self) -> None:
        broker = StaticBearerCredentialBroker.from_json(
            json.dumps(credentials_document())
        )
        lease = broker.resolve(
            CredentialLeaseRequest(
                tenant_id="local",
                actor_id="developer",
                integration_id="observability-local",
                credential_ref="credential://local/prometheus/primary",
                provider="prometheus",
                scopes=("metrics:read",),
                deadline="2026-08-14T10:31:02Z",
            )
        )

        self.assertEqual(lease.secret, TOKEN)
        self.assertNotIn(TOKEN, repr(lease))
        self.assertNotIn(TOKEN, repr(broker))
        with self.assertRaisesRegex(
            PrometheusBackendError, "telemetry.credential.unavailable"
        ):
            broker.resolve(
                CredentialLeaseRequest(
                    tenant_id="other",
                    actor_id="developer",
                    integration_id="observability-local",
                    credential_ref="credential://local/prometheus/primary",
                    provider="prometheus",
                    scopes=("metrics:read",),
                    deadline="2026-08-14T10:31:02Z",
                )
            )

    def test_credential_configuration_rejects_short_or_expired_lease(self) -> None:
        with self.assertRaisesRegex(
            PrometheusConfigurationError,
            "telemetry.credential.configuration.invalid",
        ):
            StaticBearerCredentialBroker.from_json("")

        invalid = credentials_document()
        invalid["credentials"][0]["bearerToken"] = "short"
        with self.assertRaisesRegex(
            PrometheusConfigurationError,
            "telemetry.credential.configuration.invalid",
        ):
            StaticBearerCredentialBroker.from_json(json.dumps(invalid))

        expired = credentials_document()
        expired["credentials"][0]["expiresAt"] = "2026-08-14T10:30:00Z"
        broker = StaticBearerCredentialBroker.from_json(json.dumps(expired))
        with self.assertRaisesRegex(
            PrometheusBackendError, "telemetry.credential.unavailable"
        ):
            broker.resolve(
                CredentialLeaseRequest(
                    tenant_id="local",
                    actor_id="developer",
                    integration_id="observability-local",
                    credential_ref="credential://local/prometheus/primary",
                    provider="prometheus",
                    scopes=("metrics:read",),
                    deadline="2026-08-14T10:31:02Z",
                )
            )


class PrometheusBackendTests(unittest.TestCase):
    def backend(
        self,
        *,
        document: object | None = None,
        credential_ref: str | None = "credential://local/prometheus/primary",
    ) -> tuple[
        PrometheusTelemetryMetricsBackend,
        RecordingTransport,
        RecordingCredentialBroker,
    ]:
        transport = RecordingTransport(document)
        broker = RecordingCredentialBroker()
        registry = PrometheusIntegrationRegistry.from_json(
            json.dumps(integration_document(credential_ref=credential_ref))
        )
        return (
            PrometheusTelemetryMetricsBackend(
                registry, broker, FixedClock(), transport
            ),
            transport,
            broker,
        )

    def test_query_translation_uses_catalog_post_bounds_and_scoped_credential(self) -> None:
        backend, transport, broker = self.backend()

        result = backend.query_metrics(query())

        self.assertEqual(result.status, "complete")
        self.assertEqual(result.executed_at, "2026-08-14T10:30:04Z")
        self.assertEqual(result.warnings, ())
        self.assertEqual(result.series[0].metric, "k8s.pod.container.restarts")
        self.assertEqual(result.series[0].unit, "1")
        self.assertEqual(
            result.series[0].attributes,
            (("k8s.pod.name", "api-7d9f5c6b9f-x2k4m"),),
        )
        self.assertEqual([point.value for point in result.series[0].points], [0, 1, 2])

        outgoing = transport.requests[0]
        self.assertEqual(
            outgoing["url"],
            "https://prometheus.example/base/api/v1/query_range",
        )
        parameters = parse_qs(outgoing["body"].decode())
        self.assertEqual(
            parameters["query"],
            [
                "max by (pod) "
                '(kube_pod_container_status_restarts_total{namespace="default"})'
            ],
        )
        self.assertEqual(parameters["step"], ["60s"])
        self.assertEqual(parameters["limit"], ["21"])
        self.assertEqual(outgoing["maxResponseBytes"], 1048576)
        self.assertEqual(outgoing["headers"]["Authorization"], f"Bearer {TOKEN}")

        credential_request = broker.requests[0]
        self.assertEqual(credential_request.tenant_id, "local")
        self.assertEqual(credential_request.actor_id, "developer")
        self.assertEqual(credential_request.scopes, ("metrics:read",))
        self.assertEqual(credential_request.deadline, query().deadline)

    def test_anonymous_integration_never_resolves_a_credential(self) -> None:
        backend, transport, broker = self.backend(credential_ref=None)

        backend.query_metrics(query())

        self.assertEqual(broker.requests, [])
        self.assertNotIn("Authorization", transport.requests[0]["headers"])

    def test_provider_warning_maps_to_stable_partial_without_text(self) -> None:
        document = prometheus_result()
        document["warnings"] = ["backend may contain sensitive provider detail"]
        backend, _, _ = self.backend(document=document)

        result = backend.query_metrics(query())

        self.assertEqual(result.status, "partial")
        self.assertEqual(result.warnings, ("backend-partial",))
        self.assertNotIn("sensitive", repr(result))

    def test_empty_success_is_honest_no_data(self) -> None:
        document = prometheus_result()
        document["data"]["result"] = []
        backend, _, _ = self.backend(document=document)

        result = backend.query_metrics(query())

        self.assertEqual(result.status, "no-data")
        self.assertEqual(result.series, ())

    def test_unsupported_or_unmapped_query_fails_before_network(self) -> None:
        backend, transport, broker = self.backend()
        invalid = (
            query(aggregation="rate"),
            query(metric="unmapped.metric"),
            query(filters=(("unmapped.attribute", "eq", "value"),)),
            query(tenant_id="another"),
        )
        for request in invalid:
            with self.subTest(request=request):
                with self.assertRaises(PrometheusBackendError):
                    backend.query_metrics(request)
        self.assertEqual(transport.requests, [])
        self.assertEqual(broker.requests, [])

    def test_neq_requires_label_presence_before_comparing_value(self) -> None:
        backend, transport, _ = self.backend()

        backend.query_metrics(
            query(filters=(("k8s.namespace.name", "neq", "system"),))
        )

        parameters = parse_qs(transport.requests[0]["body"].decode())
        self.assertEqual(
            parameters["query"],
            [
                "max by (pod) "
                '(kube_pod_container_status_restarts_total{namespace!="",'
                'namespace!="system"})'
            ],
        )

    def test_malformed_nonfinite_or_excess_provider_result_fails_closed(self) -> None:
        malformed = (
            {"status": "error", "error": "token=provider-secret"},
            {
                "status": "success",
                "data": {"resultType": "vector", "result": []},
            },
            {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"pod": "api"},
                            "values": [[unix("2026-08-14T10:30:00Z"), "NaN"]],
                        }
                    ],
                },
            },
        )
        for document in malformed:
            with self.subTest(document=document):
                backend, _, _ = self.backend(document=document)
                with self.assertRaises(PrometheusBackendError) as raised:
                    backend.query_metrics(query())
                self.assertNotIn("provider-secret", str(raised.exception))

        too_many = prometheus_result()
        too_many["data"]["result"] = too_many["data"]["result"] * 2
        backend, _, _ = self.backend(document=too_many)
        with self.assertRaisesRegex(
            PrometheusBackendError, "telemetry.backend.response.limited"
        ):
            backend.query_metrics(query(max_series=1))


class PrometheusRuntimeCompositionTests(unittest.TestCase):
    def test_environment_composes_prometheus_only_when_explicitly_selected(self) -> None:
        identity_token = "runtime-reference-token-0123456789abcdef0123456789"
        identities = json.dumps(
            {
                "identities": [
                    {
                        "tokenSha256": HashedBearerAuthenticator.token_sha256(
                            identity_token
                        ),
                        "actorId": "operator",
                        "tenantId": "local",
                        "roles": ["operator"],
                    }
                ]
            }
        )
        environment = {
            "IIP_AUTH_IDENTITIES_JSON": identities,
            "IIP_TELEMETRY_METRICS_BACKEND": "prometheus",
            "IIP_PROMETHEUS_INTEGRATIONS_JSON": json.dumps(
                integration_document(credential_ref=None)
            ),
            "IIP_PROMETHEUS_CREDENTIALS_JSON": "",
        }
        with patch.dict(os.environ, environment, clear=True):
            runtime = build_runtime_from_env()

        provider = runtime.evidence._providers["telemetry-query"]
        self.assertIsInstance(
            provider._backend,  # type: ignore[attr-defined]
            PrometheusTelemetryMetricsBackend,
        )
        runtime.close()

    def test_unknown_backend_or_missing_registry_fails_closed_at_startup(self) -> None:
        identity_token = "runtime-reference-token-0123456789abcdef0123456789"
        identities = json.dumps(
            {
                "identities": [
                    {
                        "tokenSha256": HashedBearerAuthenticator.token_sha256(
                            identity_token
                        ),
                        "actorId": "operator",
                        "tenantId": "local",
                        "roles": ["operator"],
                    }
                ]
            }
        )
        for environment in (
            {
                "IIP_AUTH_IDENTITIES_JSON": identities,
                "IIP_TELEMETRY_METRICS_BACKEND": "vendor-query",
            },
            {
                "IIP_AUTH_IDENTITIES_JSON": identities,
                "IIP_TELEMETRY_METRICS_BACKEND": "prometheus",
            },
        ):
            with self.subTest(environment=environment), patch.dict(
                os.environ, environment, clear=True
            ):
                with self.assertRaises(PrometheusConfigurationError):
                    build_runtime_from_env()


class PrometheusHttpTransportTests(unittest.TestCase):
    def test_transport_installs_handler_that_refuses_every_redirect(self) -> None:
        handler = NoRedirectHandler()
        self.assertIsNone(
            handler.redirect_request(
                object(), object(), 302, "redirect", {}, "https://other.example"
            )
        )

        transport = UrllibPrometheusHttpTransport()
        self.assertTrue(
            any(
                isinstance(installed, NoRedirectHandler)
                for installed in transport._opener.handlers
            )
        )


if __name__ == "__main__":
    unittest.main()
    NoRedirectHandler,
