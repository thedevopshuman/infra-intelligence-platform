from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from iip.adapters.auth import HashedBearerAuthenticator
from iip.adapters.credential_broker import (
    CredentialBrokerConfigurationError,
    CredentialBrokerUnavailableError,
    ExternalCredentialBrokerConfiguration,
    ExternalHttpCredentialBroker,
    NoCredentialBrokerRedirectHandler,
    WorkloadIdentityTokenSource,
    build_external_credential_broker_from_environment,
)
from iip.adapters.kubernetes_events import KubernetesApiEventsBackend
from iip.adapters.loki import LokiTelemetryLogsBackend
from iip.adapters.prometheus import PrometheusTelemetryMetricsBackend
from iip.application.ports import CredentialLeaseRequest
from iip.bootstrap import build_runtime_from_env


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


WORKLOAD_TOKEN = "workload-identity-token-0123456789abcdef"
LEASE_SECRET = "short-lived-provider-token-0123456789abcdef"
REQUEST_ID = "crq_33333333333333333333333333333333"


class FixedClock:
    def __init__(self, value: str = "2026-08-16T10:30:00Z") -> None:
        self.value = value

    def now(self) -> str:
        return self.value


def configuration_document(token_path: str, **changes: object) -> dict:
    value = {
        "endpoint": "https://credential-broker.example",
        "caBundlePath": "/var/run/iip-credential-broker-ca/ca.crt",
        "workloadIdentityTokenPath": token_path,
        "requestTimeoutSeconds": 5,
        "maxResponseBytes": 65536,
        "maxLeaseSeconds": 900,
        "maxClockSkewSeconds": 30,
    }
    value.update(changes)
    return value


def lease_request(**changes: object) -> CredentialLeaseRequest:
    value = CredentialLeaseRequest(
        tenant_id="local",
        actor_id="investigation-runtime",
        integration_id="kubernetes-local",
        credential_ref="credential://kubernetes/local/events-reader",
        provider="kubernetes",
        scopes=("events:read", "resources:read"),
        deadline="2026-08-16T10:31:00Z",
    )
    values = value.__dict__ | changes
    return CredentialLeaseRequest(**values)


def lease_document(request_id: str = REQUEST_ID, **changes: object) -> dict:
    spec = {
        "scheme": "bearer",
        "secret": LEASE_SECRET,
        "expiresAt": "2026-08-16T10:35:00Z",
    }
    spec.update(changes)
    return {
        "apiVersion": "iip.broker/v1alpha1",
        "kind": "CredentialLease",
        "metadata": {
            "requestId": request_id,
            "issuedAt": "2026-08-16T10:30:00Z",
        },
        "spec": spec,
    }


class RecordingTransport:
    def __init__(self, response: object | None = None) -> None:
        self.response = response if response is not None else lease_document()
        self.requests: list[dict[str, object]] = []

    def post(
        self,
        url: str,
        body: bytes,
        headers: object,
        **bounds: object,
    ) -> bytes:
        request = json.loads(body)
        self.requests.append(
            {
                "url": url,
                "document": request,
                "headers": dict(headers),  # type: ignore[arg-type]
                "bounds": bounds,
            }
        )
        response = copy.deepcopy(self.response)
        if isinstance(response, dict) and response.get("metadata", {}).get(
            "requestId"
        ) == "ECHO_REQUEST_ID":
            response["metadata"]["requestId"] = request["metadata"]["requestId"]
        if isinstance(response, bytes):
            return response
        return json.dumps(response).encode()


def broker(
    token_path: str,
    *,
    response: object | None = None,
    clock: FixedClock | None = None,
    **configuration_changes: object,
) -> tuple[ExternalHttpCredentialBroker, RecordingTransport]:
    configuration = ExternalCredentialBrokerConfiguration.from_json(
        json.dumps(configuration_document(token_path, **configuration_changes))
    )
    transport = RecordingTransport(response)
    return (
        ExternalHttpCredentialBroker(
            configuration,
            clock or FixedClock(),
            transport=transport,
            request_id=lambda: REQUEST_ID,
        ),
        transport,
    )


class CredentialBrokerConfigurationTests(unittest.TestCase):
    def test_configuration_is_closed_tls_only_and_contains_no_identity_value(self) -> None:
        document = configuration_document("/var/run/identity/token")
        configuration = ExternalCredentialBrokerConfiguration.from_json(
            json.dumps(document)
        )
        self.assertEqual(
            configuration.endpoint, "https://credential-broker.example"
        )
        self.assertNotIn(WORKLOAD_TOKEN, json.dumps(document))
        self.assertNotIn(LEASE_SECRET, json.dumps(document))

        invalid_documents = []
        for changes in (
            {"endpoint": "http://credential-broker.example"},
            {"endpoint": "https://user:password@credential-broker.example"},
            {"endpoint": "https://credential-broker.example/path"},
            {"caBundlePath": "relative/ca.crt"},
            {"workloadIdentityTokenPath": "relative/token"},
            {"requestTimeoutSeconds": 0},
            {"maxResponseBytes": 100},
            {"maxLeaseSeconds": 7200},
            {"maxClockSkewSeconds": 301},
        ):
            invalid_documents.append(
                configuration_document("/var/run/identity/token", **changes)
            )
        extra = configuration_document("/var/run/identity/token")
        extra["bearerToken"] = WORKLOAD_TOKEN
        invalid_documents.append(extra)
        for document in invalid_documents:
            with self.subTest(document=document), self.assertRaisesRegex(
                CredentialBrokerConfigurationError,
                "credential.broker.configuration.invalid",
            ):
                ExternalCredentialBrokerConfiguration.from_json(json.dumps(document))

    def test_environment_builder_requires_configuration(self) -> None:
        with self.assertRaisesRegex(
            CredentialBrokerConfigurationError,
            "credential.broker.configuration.required",
        ):
            build_external_credential_broker_from_environment({}, FixedClock())


class WorkloadIdentityTokenSourceTests(unittest.TestCase):
    def test_source_reads_each_time_for_rotation_and_hides_its_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "identity-token"
            path.write_text(WORKLOAD_TOKEN + "\n", encoding="ascii")
            source = WorkloadIdentityTokenSource(str(path))
            self.assertEqual(source.read(), WORKLOAD_TOKEN)

            rotated = "rotated-workload-token-0123456789abcdef"
            path.write_text(rotated, encoding="ascii")
            self.assertEqual(source.read(), rotated)
            self.assertNotIn(str(path), repr(source))
            self.assertNotIn(rotated, repr(source))

    def test_missing_malformed_or_oversized_identity_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "identity-token"
            source = WorkloadIdentityTokenSource(str(path))
            for content in (None, "short", "token with whitespace", "x" * 16385):
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(content, encoding="ascii")
                with self.subTest(content=content is None), self.assertRaisesRegex(
                    CredentialBrokerUnavailableError,
                    "credential.broker.identity.unavailable",
                ):
                    source.read()


class ExternalHttpCredentialBrokerTests(unittest.TestCase):
    def test_exact_scope_is_exchanged_for_a_short_redacted_lease(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "token"
            token_path.write_text(WORKLOAD_TOKEN, encoding="ascii")
            client, transport = broker(str(token_path))

            lease = client.resolve(lease_request())

        self.assertEqual(lease.scheme, "bearer")
        self.assertEqual(lease.secret, LEASE_SECRET)
        self.assertEqual(lease.expires_at, "2026-08-16T10:35:00Z")
        self.assertNotIn(LEASE_SECRET, repr(lease))
        self.assertNotIn(WORKLOAD_TOKEN, repr(client))
        recorded = transport.requests[0]
        document = recorded["document"]
        schema = json.loads(
            (ROOT / "contracts/schemas/credential-lease-request.schema.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                schema, document, label="generated credential lease request"
            ),
            [],
        )
        self.assertEqual(
            recorded["url"],
            "https://credential-broker.example/v1/credential-leases",
        )
        self.assertEqual(
            document["spec"],  # type: ignore[index]
            {
                "integrationId": "kubernetes-local",
                "credentialRef": "credential://kubernetes/local/events-reader",
                "provider": "kubernetes",
                "scopes": ["events:read", "resources:read"],
                "deadline": "2026-08-16T10:31:00Z",
            },
        )
        self.assertEqual(
            recorded["headers"]["Authorization"],  # type: ignore[index]
            f"Bearer {WORKLOAD_TOKEN}",
        )
        encoded_request = json.dumps(document)
        self.assertNotIn(WORKLOAD_TOKEN, encoded_request)
        self.assertNotIn(LEASE_SECRET, encoded_request)
        self.assertEqual(recorded["bounds"]["timeout_seconds"], 5.0)  # type: ignore[index]

    def test_response_identity_structure_and_lease_bounds_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "token"
            token_path.write_text(WORKLOAD_TOKEN, encoding="ascii")
            cases = (
                lease_document("crq_44444444444444444444444444444444"),
                lease_document(secret="short"),
                lease_document(expiresAt="2026-08-16T10:30:30Z"),
                lease_document(expiresAt="2026-08-16T11:00:00Z"),
                {"error": {"message": "provider secret details"}},
                b"not-json",
            )
            for response in cases:
                client, _ = broker(str(token_path), response=response)
                with self.subTest(response_type=type(response)), self.assertRaises(
                    CredentialBrokerUnavailableError
                ) as raised:
                    client.resolve(lease_request())
                self.assertNotIn("provider secret details", str(raised.exception))

    def test_invalid_scope_or_elapsed_deadline_never_reaches_transport(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            token_path = Path(directory) / "token"
            token_path.write_text(WORKLOAD_TOKEN, encoding="ascii")
            for request in (
                lease_request(tenant_id=""),
                lease_request(scopes=("events:*",)),
                lease_request(scopes=("events:read", "events:read")),
                lease_request(deadline="2026-08-16T10:30:00Z"),
            ):
                client, transport = broker(str(token_path))
                with self.subTest(request=request), self.assertRaises(
                    CredentialBrokerUnavailableError
                ):
                    client.resolve(request)
                self.assertEqual(transport.requests, [])

    def test_transport_redirect_handler_refuses_every_redirect(self) -> None:
        handler = NoCredentialBrokerRedirectHandler()
        self.assertIsNone(
            handler.redirect_request(
                object(), object(), 302, "redirect", {}, "https://other.example"
            )
        )


class CredentialBrokerCompositionTests(unittest.TestCase):
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

    def test_external_mode_is_shared_by_selected_provider_adapters(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            token_path = str(Path(directory) / "token")
            environment = {
                "IIP_AUTH_IDENTITIES_JSON": self.identities(),
                "IIP_CREDENTIAL_BROKER_MODE": "external-http",
                "IIP_CREDENTIAL_BROKER_CONFIG_JSON": json.dumps(
                    configuration_document(token_path, caBundlePath=None)
                ),
                "IIP_TELEMETRY_METRICS_BACKEND": "prometheus",
                "IIP_PROMETHEUS_INTEGRATIONS_JSON": json.dumps(
                    prometheus_integrations()
                ),
                "IIP_KUBERNETES_EVENTS_BACKEND": "kubernetes-api",
                "IIP_KUBERNETES_EVENTS_INTEGRATIONS_JSON": json.dumps(
                    kubernetes_integrations()
                ),
                "IIP_TELEMETRY_LOGS_BACKEND": "loki",
                "IIP_LOKI_INTEGRATIONS_JSON": json.dumps(loki_integrations()),
            }
            with patch.dict(os.environ, environment, clear=True):
                runtime = build_runtime_from_env()

        telemetry = runtime.evidence._providers["telemetry-query"]._backend
        events = runtime.evidence._providers["kubernetes-events"]._backend
        logs = runtime.evidence._providers["log-query"]._backend
        self.assertIsInstance(telemetry, PrometheusTelemetryMetricsBackend)
        self.assertIsInstance(events, KubernetesApiEventsBackend)
        self.assertIsInstance(logs, LokiTelemetryLogsBackend)
        self.assertIs(telemetry._credentials, events._credentials)
        self.assertIs(telemetry._credentials, logs._credentials)
        self.assertIsInstance(telemetry._credentials, ExternalHttpCredentialBroker)
        runtime.close()

    def test_unknown_mode_or_missing_external_config_fails_at_startup(self) -> None:
        for environment in (
            {
                "IIP_AUTH_IDENTITIES_JSON": self.identities(),
                "IIP_CREDENTIAL_BROKER_MODE": "ambient",
            },
            {
                "IIP_AUTH_IDENTITIES_JSON": self.identities(),
                "IIP_CREDENTIAL_BROKER_MODE": "external-http",
            },
        ):
            with self.subTest(environment=environment), patch.dict(
                os.environ, environment, clear=True
            ), self.assertRaises(CredentialBrokerConfigurationError):
                build_runtime_from_env()


def prometheus_integrations() -> dict:
    return {
        "integrations": [
            {
                "tenantId": "local",
                "integrationId": "prometheus-local",
                "provider": "prometheus",
                "endpoint": "https://prometheus.example",
                "credentialRef": "credential://prometheus/local/metrics-reader",
                "enabled": True,
                "requestTimeoutSeconds": 10,
                "maxResponseBytes": 1048576,
                "metrics": [
                    {
                        "name": "platform.up",
                        "backendMetric": "up",
                        "unit": "1",
                        "attributes": {},
                    }
                ],
            }
        ]
    }


def kubernetes_integrations() -> dict:
    return {
        "integrations": [
            {
                "tenantId": "local",
                "integrationId": "kubernetes-local",
                "provider": "kubernetes",
                "endpoint": "https://kubernetes.example",
                "credentialRef": "credential://kubernetes/local/events-reader",
                "caBundlePath": None,
                "clusterExternalId": "cluster-local",
                "namespaces": ["default"],
                "clusterEventNamespace": "default",
                "resourceTypes": ["apps/deployment"],
                "requestTimeoutSeconds": 10,
                "maxResponseBytes": 1048576,
                "pageSize": 100,
                "maxPages": 5,
                "conditionMappings": {},
                "enabled": True,
            }
        ]
    }


def loki_integrations() -> dict:
    return {
        "integrations": [
            {
                "tenantId": "local",
                "integrationId": "loki-local",
                "provider": "loki",
                "endpoint": "https://loki.example",
                "credentialRef": "credential://loki/local/log-reader",
                "organizationId": "local",
                "enabled": True,
                "requestTimeoutSeconds": 10,
                "maxResponseBytes": 1048576,
                "labels": {
                    "resourceUid": "resource_uid",
                    "service": "service_name",
                    "severity": "level",
                    "traceId": None,
                    "spanId": None,
                    "attributes": {},
                },
                "services": {"api": "api"},
                "severities": {"error": "ERROR"},
            }
        ]
    }


if __name__ == "__main__":
    unittest.main()
