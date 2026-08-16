from __future__ import annotations

import json
import os
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from iip.adapters.loki import (
    LokiBackendError,
    LokiConfigurationError,
    LokiIntegrationRegistry,
    LokiTelemetryLogsBackend,
    NoRedirectHandler,
    StaticLokiCredentialBroker,
    UrllibLokiHttpTransport,
    build_loki_backend_from_environment,
)
from iip.adapters.auth import HashedBearerAuthenticator
from iip.application.ports import CredentialLease, CredentialLeaseRequest, TelemetryLogsQuery
from iip.bootstrap import build_runtime_from_env


TOKEN = "loki-reference-bearer-token-0123456789abcdef"


class FixedClock:
    def __init__(self, value: str = "2026-08-17T00:31:00Z") -> None:
        self.value = value

    def now(self) -> str:
        return self.value


class RecordingTransport:
    def __init__(self, document: object | None = None) -> None:
        self.document = document if document is not None else loki_result()
        self.requests: list[dict[str, object]] = []

    def get(
        self,
        url: str,
        headers: dict[str, str],
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        self.requests.append(
            {
                "url": url,
                "headers": dict(headers),
                "timeoutSeconds": timeout_seconds,
                "maxResponseBytes": max_response_bytes,
            }
        )
        return json.dumps(self.document).encode("utf-8")


class RecordingCredentialBroker:
    def __init__(self, lease: CredentialLease | None = None) -> None:
        self.lease = lease or CredentialLease("bearer", TOKEN)
        self.requests: list[CredentialLeaseRequest] = []

    def resolve(self, request: CredentialLeaseRequest) -> CredentialLease:
        self.requests.append(request)
        return self.lease


def integration_document(
    *,
    endpoint: str = "https://loki.example/base",
    credential_ref: str | None = "credential://local/loki/primary",
) -> dict:
    return {
        "integrations": [
            {
                "tenantId": "local",
                "integrationId": "logs-local",
                "provider": "loki",
                "endpoint": endpoint,
                "credentialRef": credential_ref,
                "organizationId": "tenant-a",
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
                "services": {"checkout-api": "svc-checkout"},
                "severities": {
                    "debug": "DEBUG",
                    "info": "INFO",
                    "warn": "WARN",
                    "error": "ERROR",
                    "fatal": "FATAL",
                },
            }
        ]
    }


def credentials_document() -> dict:
    return {
        "credentials": [
            {
                "tenantId": "local",
                "integrationId": "logs-local",
                "credentialRef": "credential://local/loki/primary",
                "bearerToken": TOKEN,
                "expiresAt": "2026-08-17T00:33:00Z",
            }
        ]
    }


def log_query(**overrides: object) -> TelemetryLogsQuery:
    values: dict[str, object] = {
        "tenant_id": "local",
        "actor_id": "local-operator",
        "request_id": "leq_8f5e2a7c1d9046b3a1c7e9f204d6b830",
        "integration_id": "logs-local",
        "resource_uids": ("res_e0ae9225a316fce4c97df5c23057b97a",),
        "start": "2026-08-17T00:25:00Z",
        "end": "2026-08-17T00:30:00Z",
        "service_names": ("checkout-api",),
        "severities": ("error",),
        "filters": (("deployment.environment", "eq", "production"),),
        "max_records": 100,
        "max_bytes": 1048576,
        "deadline": "2026-08-17T00:32:00Z",
    }
    values.update(overrides)
    return TelemetryLogsQuery(**values)  # type: ignore[arg-type]


def loki_result() -> dict:
    return {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {
                    "stream": {
                        "resource_uid": "res_e0ae9225a316fce4c97df5c23057b97a",
                        "service_name": "svc-checkout",
                        "level": "ERROR",
                        "environment": "production",
                        "namespace": "shop",
                        "trace_id": "0123456789abcdef0123456789abcdef",
                        "span_id": "0123456789abcdef",
                    },
                    "values": [
                        ["1786926480000000000", "upstream request failed"],
                        ["1786926540000000000", "retry budget exhausted"],
                    ],
                }
            ],
        },
    }


class LokiConfigurationTests(unittest.TestCase):
    def test_registry_is_closed_tenant_bound_and_rejects_secret_or_query_fields(self) -> None:
        invalid = []
        inline_secret = integration_document()
        inline_secret["integrations"][0]["authorization"] = "Bearer secret"
        invalid.append(inline_secret)
        userinfo = integration_document(endpoint="https://user:secret@loki.example")
        invalid.append(userinfo)
        duplicate_label = integration_document()
        duplicate_label["integrations"][0]["labels"]["attributes"]["k8s.namespace.name"] = "environment"
        invalid.append(duplicate_label)
        partial_trace = integration_document()
        partial_trace["integrations"][0]["labels"]["spanId"] = None
        invalid.append(partial_trace)
        duplicate_backend_service = integration_document()
        duplicate_backend_service["integrations"][0]["services"]["billing-api"] = "svc-checkout"
        invalid.append(duplicate_backend_service)

        for document in invalid:
            with self.subTest(document=document):
                with self.assertRaisesRegex(
                    LokiConfigurationError,
                    "logs.backend.configuration.invalid",
                ):
                    LokiIntegrationRegistry.from_json(json.dumps(document))

        registry = LokiIntegrationRegistry.from_json(json.dumps(integration_document()))
        with self.assertRaisesRegex(LokiBackendError, "integration.unavailable"):
            registry.resolve("another-tenant", "logs-local")

    def test_static_broker_enforces_exact_loki_scope_and_hides_secret(self) -> None:
        broker = StaticLokiCredentialBroker.from_json(json.dumps(credentials_document()))
        request = CredentialLeaseRequest(
            tenant_id="local",
            actor_id="local-operator",
            integration_id="logs-local",
            credential_ref="credential://local/loki/primary",
            provider="loki",
            scopes=("logs:read",),
            deadline="2026-08-17T00:32:00Z",
        )

        lease = broker.resolve(request)

        self.assertEqual(lease.secret, TOKEN)
        self.assertNotIn(TOKEN, repr(lease))
        self.assertNotIn(TOKEN, repr(broker))
        with self.assertRaisesRegex(LokiBackendError, "credential.unavailable"):
            broker.resolve(
                CredentialLeaseRequest(
                    **{**request.__dict__, "provider": "prometheus"}
                )
            )

    def test_environment_builder_requires_integration_configuration(self) -> None:
        with self.assertRaisesRegex(LokiConfigurationError, "configuration.required"):
            build_loki_backend_from_environment({}, FixedClock())
        with self.assertRaisesRegex(LokiConfigurationError, "credential.configuration.invalid"):
            build_loki_backend_from_environment(
                {
                    "IIP_LOKI_INTEGRATIONS_JSON": json.dumps(integration_document()),
                    "IIP_LOKI_CREDENTIALS_JSON": "not-json",
                },
                FixedClock(),
            )


class LokiBackendTests(unittest.TestCase):
    def backend(
        self,
        *,
        document: object | None = None,
        credential_ref: str | None = "credential://local/loki/primary",
    ) -> tuple[LokiTelemetryLogsBackend, RecordingTransport, RecordingCredentialBroker]:
        transport = RecordingTransport(document)
        broker = RecordingCredentialBroker()
        backend = LokiTelemetryLogsBackend(
            LokiIntegrationRegistry.from_json(
                json.dumps(integration_document(credential_ref=credential_ref))
            ),
            broker,
            FixedClock(),
            transport,
        )
        return backend, transport, broker

    def test_query_translation_uses_allowlists_bounds_and_scoped_credential(self) -> None:
        backend, transport, broker = self.backend()

        result = backend.query_logs(log_query())

        self.assertEqual(result.status, "complete")
        self.assertEqual(len(result.records), 2)
        record = result.records[0]
        self.assertEqual(record.resource_uid, "res_e0ae9225a316fce4c97df5c23057b97a")
        self.assertEqual(record.service_name, "checkout-api")
        self.assertEqual(record.severity, "error")
        self.assertEqual(
            record.attributes,
            (("deployment.environment", "production"), ("k8s.namespace.name", "shop")),
        )
        self.assertEqual(record.trace_id, "0123456789abcdef0123456789abcdef")
        self.assertRegex(record.record_id, r"^log_[a-f0-9]{32}$")

        sent = transport.requests[0]
        parsed = urlsplit(sent["url"])
        parameters = parse_qs(parsed.query)
        self.assertEqual(parsed.path, "/base/loki/api/v1/query_range")
        self.assertEqual(parameters["direction"], ["forward"])
        self.assertEqual(parameters["limit"], ["101"])
        self.assertEqual(
            parameters["query"],
            ['{resource_uid=~"res_e0ae9225a316fce4c97df5c23057b97a",service_name=~"svc-checkout",level=~"ERROR",environment="production"}'],
        )
        self.assertEqual(sent["headers"]["X-Scope-OrgID"], "tenant-a")
        self.assertEqual(sent["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertEqual(
            broker.requests,
            [
                CredentialLeaseRequest(
                    tenant_id="local",
                    actor_id="local-operator",
                    integration_id="logs-local",
                    credential_ref="credential://local/loki/primary",
                    provider="loki",
                    scopes=("logs:read",),
                    deadline="2026-08-17T00:32:00Z",
                )
            ],
        )

    def test_anonymous_integration_never_resolves_a_credential(self) -> None:
        backend, transport, broker = self.backend(credential_ref=None)

        backend.query_logs(log_query())

        self.assertEqual(broker.requests, [])
        self.assertNotIn("Authorization", transport.requests[0]["headers"])

    def test_record_limit_and_provider_warning_are_stable_partial_states(self) -> None:
        limited_backend, _, _ = self.backend()
        limited = limited_backend.query_logs(log_query(max_records=1))
        self.assertEqual(limited.status, "partial")
        self.assertEqual(limited.warnings, ("record-limit",))
        self.assertEqual(len(limited.records), 1)

        excessive_document = loki_result()
        excessive_document["data"]["result"][0]["values"].append(
            ["1786926570000000000", "provider ignored record limit"]
        )
        excessive_backend, _, _ = self.backend(document=excessive_document)
        with self.assertRaisesRegex(LokiBackendError, "response.limited"):
            excessive_backend.query_logs(log_query(max_records=1))

        warning_document = loki_result()
        warning_document["warnings"] = ["provider implementation detail"]
        warning_backend, _, _ = self.backend(document=warning_document)
        warning = warning_backend.query_logs(log_query())
        self.assertEqual(warning.status, "partial")
        self.assertEqual(warning.warnings, ("backend-partial",))
        self.assertNotIn("implementation detail", repr(warning))

    def test_no_data_is_honest_and_malformed_or_cross_scope_output_fails_closed(self) -> None:
        empty = loki_result()
        empty["data"]["result"] = []
        backend, _, _ = self.backend(document=empty)
        self.assertEqual(backend.query_logs(log_query()).status, "no-data")

        invalid_documents = []
        wrong_type = loki_result()
        wrong_type["data"]["resultType"] = "matrix"
        invalid_documents.append(wrong_type)
        cross_scope = loki_result()
        cross_scope["data"]["result"][0]["stream"]["resource_uid"] = (
            "res_ffffffffffffffffffffffffffffffff"
        )
        invalid_documents.append(cross_scope)
        secret_label = loki_result()
        secret_label["data"]["result"][0]["stream"]["environment"] = (
            "authorization=Bearer-value"
        )
        invalid_documents.append(secret_label)
        partial_trace = loki_result()
        del partial_trace["data"]["result"][0]["stream"]["span_id"]
        invalid_documents.append(partial_trace)
        cross_service = loki_result()
        cross_service["data"]["result"][0]["stream"]["service_name"] = "other"
        invalid_documents.append(cross_service)
        filter_mismatch = loki_result()
        filter_mismatch["data"]["result"][0]["stream"]["environment"] = "staging"
        invalid_documents.append(filter_mismatch)
        out_of_range = loki_result()
        out_of_range["data"]["result"][0]["values"][0][0] = "1786926000000000000"
        invalid_documents.append(out_of_range)

        for document in invalid_documents:
            with self.subTest(document=document):
                failing, _, _ = self.backend(document=document)
                with self.assertRaisesRegex(LokiBackendError, "response.invalid"):
                    failing.query_logs(log_query())

    def test_unsupported_catalog_fields_and_elapsed_deadline_never_reach_transport(self) -> None:
        backend, transport, _ = self.backend()
        with self.assertRaisesRegex(LokiBackendError, "query.unsupported"):
            backend.query_logs(
                log_query(filters=(("unconfigured.attribute", "eq", "value"),))
            )
        with self.assertRaisesRegex(LokiBackendError, "deadline.exceeded"):
            backend.query_logs(log_query(deadline="2026-08-17T00:30:59Z"))
        self.assertEqual(transport.requests, [])


class LokiHttpTransportTests(unittest.TestCase):
    def test_credential_bearing_redirects_are_disabled(self) -> None:
        transport = UrllibLokiHttpTransport()
        handlers = transport._opener.handlers
        redirect = [handler for handler in handlers if isinstance(handler, NoRedirectHandler)]
        self.assertEqual(len(redirect), 1)
        self.assertIsNone(redirect[0].redirect_request())


class LokiRuntimeCompositionTests(unittest.TestCase):
    @staticmethod
    def identities() -> str:
        token = "runtime-loki-token-0123456789abcdef0123456789"
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

    def test_environment_composes_loki_only_when_explicitly_selected(self) -> None:
        environment = {
            "IIP_AUTH_IDENTITIES_JSON": self.identities(),
            "IIP_TELEMETRY_LOGS_BACKEND": "loki",
            "IIP_LOKI_INTEGRATIONS_JSON": json.dumps(
                integration_document(credential_ref=None)
            ),
            "IIP_LOKI_CREDENTIALS_JSON": "",
        }
        with patch.dict(os.environ, environment, clear=True):
            runtime = build_runtime_from_env()

        provider = runtime.evidence._providers["log-query"]
        self.assertIsInstance(
            provider._backend,  # type: ignore[attr-defined]
            LokiTelemetryLogsBackend,
        )
        runtime.close()

    def test_unknown_backend_or_missing_registry_fails_closed_at_startup(self) -> None:
        for environment in (
            {
                "IIP_AUTH_IDENTITIES_JSON": self.identities(),
                "IIP_TELEMETRY_LOGS_BACKEND": "vendor-query",
            },
            {
                "IIP_AUTH_IDENTITIES_JSON": self.identities(),
                "IIP_TELEMETRY_LOGS_BACKEND": "loki",
            },
        ):
            with self.subTest(environment=environment), patch.dict(
                os.environ, environment, clear=True
            ):
                with self.assertRaises(LokiConfigurationError):
                    build_runtime_from_env()


if __name__ == "__main__":
    unittest.main()
