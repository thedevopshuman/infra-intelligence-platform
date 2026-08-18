from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from iip.adapters.loki import LokiConfigurationError
from iip.adapters.opensearch import (
    OpenSearchBackendError,
    OpenSearchConfigurationError,
    OpenSearchIntegrationRegistry,
    OpenSearchTelemetryLogsBackend,
    NoRedirectHandler,
    StaticOpenSearchCredentialBroker,
    UrllibOpenSearchHttpTransport,
    build_opensearch_backend_from_environment,
)
from iip.adapters.auth import HashedBearerAuthenticator
from iip.application.ports import CredentialLease, CredentialLeaseRequest, TelemetryLogsQuery
from iip.bootstrap import build_runtime_from_env


TOKEN = "opensearch-reference-bearer-token-0123456789ab"


class FixedClock:
    def __init__(self, value: str = "2026-08-17T00:31:00Z") -> None:
        self.value = value

    def now(self) -> str:
        return self.value


class RecordingTransport:
    def __init__(self, document: object | None = None) -> None:
        self.document = document if document is not None else opensearch_result()
        self.requests: list[dict[str, object]] = []

    def post(
        self,
        url: str,
        headers: dict[str, str],
        body: bytes,
        *,
        timeout_seconds: float,
        max_response_bytes: int,
    ) -> bytes:
        self.requests.append(
            {
                "url": url,
                "headers": dict(headers),
                "body": json.loads(body.decode("utf-8")),
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
    endpoint: str = "https://opensearch.example/base",
    credential_ref: str | None = "credential://local/opensearch/primary",
) -> dict:
    return {
        "integrations": [
            {
                "tenantId": "local",
                "integrationId": "logs-local",
                "provider": "opensearch",
                "endpoint": endpoint,
                "credentialRef": credential_ref,
                "index": "logs-checkout",
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
                "credentialRef": "credential://local/opensearch/primary",
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


def _hit(hit_id: str, timestamp: str, message: str) -> dict:
    return {
        "_index": "logs-checkout",
        "_id": hit_id,
        "_source": {
            "resource_uid": "res_e0ae9225a316fce4c97df5c23057b97a",
            "service_name": "svc-checkout",
            "level": "ERROR",
            "@timestamp": timestamp,
            "message": message,
            "environment": "production",
            "namespace": "shop",
            "trace_id": "0123456789abcdef0123456789abcdef",
            "span_id": "0123456789abcdef",
        },
        "sort": [timestamp, hit_id],
    }


def opensearch_result() -> dict:
    return {
        "took": 5,
        "timed_out": False,
        "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0},
        "hits": {
            "total": {"value": 2, "relation": "eq"},
            "hits": [
                _hit("doc-1", "2026-08-17T00:28:00.000000Z", "upstream request failed"),
                _hit("doc-2", "2026-08-17T00:29:00.000000Z", "retry budget exhausted"),
            ],
        },
    }


class OpenSearchConfigurationTests(unittest.TestCase):
    def test_registry_is_closed_tenant_bound_and_rejects_secret_or_query_fields(self) -> None:
        invalid = []
        inline_secret = integration_document()
        inline_secret["integrations"][0]["authorization"] = "Bearer secret"
        invalid.append(inline_secret)
        userinfo = integration_document(endpoint="https://user:secret@opensearch.example")
        invalid.append(userinfo)
        duplicate_field = integration_document()
        duplicate_field["integrations"][0]["fields"]["attributes"]["k8s.namespace.name"] = "environment"
        invalid.append(duplicate_field)
        partial_trace = integration_document()
        partial_trace["integrations"][0]["fields"]["spanId"] = None
        invalid.append(partial_trace)
        duplicate_backend_service = integration_document()
        duplicate_backend_service["integrations"][0]["services"]["billing-api"] = "svc-checkout"
        invalid.append(duplicate_backend_service)
        bad_index = integration_document()
        bad_index["integrations"][0]["index"] = "Logs-Checkout"
        invalid.append(bad_index)

        for document in invalid:
            with self.subTest(document=document):
                with self.assertRaisesRegex(
                    OpenSearchConfigurationError,
                    "logs.backend.configuration.invalid",
                ):
                    OpenSearchIntegrationRegistry.from_json(json.dumps(document))

        registry = OpenSearchIntegrationRegistry.from_json(json.dumps(integration_document()))
        with self.assertRaisesRegex(OpenSearchBackendError, "integration.unavailable"):
            registry.resolve("another-tenant", "logs-local")

    def test_static_broker_enforces_exact_scope_and_hides_secret(self) -> None:
        broker = StaticOpenSearchCredentialBroker.from_json(json.dumps(credentials_document()))
        request = CredentialLeaseRequest(
            tenant_id="local",
            actor_id="local-operator",
            integration_id="logs-local",
            credential_ref="credential://local/opensearch/primary",
            provider="opensearch",
            scopes=("logs:read",),
            deadline="2026-08-17T00:32:00Z",
        )

        lease = broker.resolve(request)

        self.assertEqual(lease.secret, TOKEN)
        self.assertNotIn(TOKEN, repr(lease))
        self.assertNotIn(TOKEN, repr(broker))
        with self.assertRaisesRegex(OpenSearchBackendError, "credential.unavailable"):
            broker.resolve(
                CredentialLeaseRequest(
                    **{**request.__dict__, "provider": "loki"}
                )
            )

    def test_environment_builder_requires_integration_configuration(self) -> None:
        with self.assertRaisesRegex(OpenSearchConfigurationError, "configuration.required"):
            build_opensearch_backend_from_environment({}, FixedClock())
        with self.assertRaisesRegex(OpenSearchConfigurationError, "credential.configuration.invalid"):
            build_opensearch_backend_from_environment(
                {
                    "IIP_OPENSEARCH_INTEGRATIONS_JSON": json.dumps(integration_document()),
                    "IIP_OPENSEARCH_CREDENTIALS_JSON": "not-json",
                },
                FixedClock(),
            )


class OpenSearchBackendTests(unittest.TestCase):
    def backend(
        self,
        *,
        document: object | None = None,
        credential_ref: str | None = "credential://local/opensearch/primary",
    ) -> tuple[OpenSearchTelemetryLogsBackend, RecordingTransport, RecordingCredentialBroker]:
        transport = RecordingTransport(document)
        broker = RecordingCredentialBroker()
        backend = OpenSearchTelemetryLogsBackend(
            OpenSearchIntegrationRegistry.from_json(
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
        self.assertEqual(sent["url"], "https://opensearch.example/base/logs-checkout/_search")
        body = sent["body"]
        self.assertEqual(body["size"], 101)
        self.assertEqual(
            body["query"]["bool"]["filter"][1],
            {"terms": {"resource_uid.keyword": ["res_e0ae9225a316fce4c97df5c23057b97a"]}},
        )
        self.assertEqual(
            body["query"]["bool"]["filter"][2],
            {"terms": {"service_name.keyword": ["svc-checkout"]}},
        )
        self.assertEqual(
            body["query"]["bool"]["filter"][3],
            {"terms": {"level.keyword": ["ERROR"]}},
        )
        self.assertIn(
            {"term": {"environment.keyword": "production"}}, body["query"]["bool"]["filter"]
        )
        self.assertEqual(sent["headers"]["Authorization"], f"Bearer {TOKEN}")
        self.assertNotIn("X-Scope-OrgID", sent["headers"])
        self.assertEqual(
            broker.requests,
            [
                CredentialLeaseRequest(
                    tenant_id="local",
                    actor_id="local-operator",
                    integration_id="logs-local",
                    credential_ref="credential://local/opensearch/primary",
                    provider="opensearch",
                    scopes=("logs:read",),
                    deadline="2026-08-17T00:32:00Z",
                )
            ],
        )

    def test_neq_filter_requires_field_existence_and_inequality(self) -> None:
        backend, transport, _ = self.backend()

        backend.query_logs(
            log_query(filters=(("deployment.environment", "neq", "staging"),))
        )

        filters = transport.requests[0]["body"]["query"]["bool"]["filter"]
        must_not = transport.requests[0]["body"]["query"]["bool"]["must_not"]
        self.assertIn({"exists": {"field": "environment"}}, filters)
        self.assertIn({"term": {"environment.keyword": "staging"}}, must_not)

    def test_anonymous_integration_never_resolves_a_credential(self) -> None:
        backend, transport, broker = self.backend(credential_ref=None)

        backend.query_logs(log_query())

        self.assertEqual(broker.requests, [])
        self.assertNotIn("Authorization", transport.requests[0]["headers"])

    def test_record_limit_and_shard_failure_are_stable_partial_states(self) -> None:
        limited_backend, _, _ = self.backend()
        limited = limited_backend.query_logs(log_query(max_records=1))
        self.assertEqual(limited.status, "partial")
        self.assertEqual(limited.warnings, ("record-limit",))
        self.assertEqual(len(limited.records), 1)

        excessive_document = opensearch_result()
        excessive_document["hits"]["hits"].append(
            _hit("doc-3", "2026-08-17T00:29:30.000000Z", "provider ignored record limit")
        )
        excessive_backend, _, _ = self.backend(document=excessive_document)
        with self.assertRaisesRegex(OpenSearchBackendError, "response.limited"):
            excessive_backend.query_logs(log_query(max_records=1))

        failed_shard_document = opensearch_result()
        failed_shard_document["_shards"]["failed"] = 1
        failed_shard_backend, _, _ = self.backend(document=failed_shard_document)
        partial = failed_shard_backend.query_logs(log_query())
        self.assertEqual(partial.status, "partial")
        self.assertEqual(partial.warnings, ("backend-partial",))

    def test_no_data_is_honest_and_malformed_or_cross_scope_output_fails_closed(self) -> None:
        empty = opensearch_result()
        empty["hits"]["hits"] = []
        backend, _, _ = self.backend(document=empty)
        self.assertEqual(backend.query_logs(log_query()).status, "no-data")

        invalid_documents = []
        timed_out = opensearch_result()
        timed_out["timed_out"] = True
        invalid_documents.append(timed_out)
        cross_scope = opensearch_result()
        cross_scope["hits"]["hits"][0]["_source"]["resource_uid"] = (
            "res_ffffffffffffffffffffffffffffffff"
        )
        invalid_documents.append(cross_scope)
        secret_field = opensearch_result()
        secret_field["hits"]["hits"][0]["_source"]["environment"] = (
            "authorization=Bearer-value"
        )
        invalid_documents.append(secret_field)
        partial_trace = opensearch_result()
        del partial_trace["hits"]["hits"][0]["_source"]["span_id"]
        invalid_documents.append(partial_trace)
        cross_service = opensearch_result()
        cross_service["hits"]["hits"][0]["_source"]["service_name"] = "other"
        invalid_documents.append(cross_service)
        filter_mismatch = opensearch_result()
        filter_mismatch["hits"]["hits"][0]["_source"]["environment"] = "staging"
        invalid_documents.append(filter_mismatch)
        out_of_range = opensearch_result()
        out_of_range["hits"]["hits"][0]["_source"]["@timestamp"] = "2026-08-17T00:00:00.000000Z"
        invalid_documents.append(out_of_range)

        for document in invalid_documents:
            with self.subTest(document=document):
                failing, _, _ = self.backend(document=document)
                with self.assertRaisesRegex(OpenSearchBackendError, "response.invalid"):
                    failing.query_logs(log_query())

    def test_unsupported_catalog_fields_and_elapsed_deadline_never_reach_transport(self) -> None:
        backend, transport, _ = self.backend()
        with self.assertRaisesRegex(OpenSearchBackendError, "query.unsupported"):
            backend.query_logs(
                log_query(filters=(("unconfigured.attribute", "eq", "value"),))
            )
        with self.assertRaisesRegex(OpenSearchBackendError, "deadline.exceeded"):
            backend.query_logs(log_query(deadline="2026-08-17T00:30:59Z"))
        self.assertEqual(transport.requests, [])


class OpenSearchHttpTransportTests(unittest.TestCase):
    def test_credential_bearing_redirects_are_disabled(self) -> None:
        transport = UrllibOpenSearchHttpTransport()
        handlers = transport._opener.handlers
        redirect = [handler for handler in handlers if isinstance(handler, NoRedirectHandler)]
        self.assertEqual(len(redirect), 1)
        self.assertIsNone(redirect[0].redirect_request())


class OpenSearchRuntimeCompositionTests(unittest.TestCase):
    @staticmethod
    def identities() -> str:
        token = "runtime-opensearch-token-0123456789abcdef0123"
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

    def test_environment_composes_opensearch_only_when_explicitly_selected(self) -> None:
        environment = {
            "IIP_AUTH_IDENTITIES_JSON": self.identities(),
            "IIP_TELEMETRY_LOGS_BACKEND": "opensearch",
            "IIP_OPENSEARCH_INTEGRATIONS_JSON": json.dumps(
                integration_document(credential_ref=None)
            ),
            "IIP_OPENSEARCH_CREDENTIALS_JSON": "",
        }
        with patch.dict(os.environ, environment, clear=True):
            runtime = build_runtime_from_env()

        provider = runtime.evidence._providers["log-query"]
        self.assertIsInstance(
            provider._backend,  # type: ignore[attr-defined]
            OpenSearchTelemetryLogsBackend,
        )
        runtime.close()

    def test_unknown_backend_or_missing_registry_fails_closed_at_startup(self) -> None:
        # An entirely unrecognized backend name falls through the shared
        # log-backend selector, which still raises the historical
        # LokiConfigurationError for that generic case; a backend explicitly
        # selected as "opensearch" without its own configuration raises the
        # OpenSearch-specific error instead.
        cases = (
            (
                {
                    "IIP_AUTH_IDENTITIES_JSON": self.identities(),
                    "IIP_TELEMETRY_LOGS_BACKEND": "vendor-query",
                },
                LokiConfigurationError,
            ),
            (
                {
                    "IIP_AUTH_IDENTITIES_JSON": self.identities(),
                    "IIP_TELEMETRY_LOGS_BACKEND": "opensearch",
                },
                OpenSearchConfigurationError,
            ),
        )
        for environment, expected_error in cases:
            with self.subTest(environment=environment), patch.dict(
                os.environ, environment, clear=True
            ):
                with self.assertRaises(expected_error):
                    build_runtime_from_env()


if __name__ == "__main__":
    unittest.main()
