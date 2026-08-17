from __future__ import annotations

import copy
import gzip
import json
import os
import time
import unittest
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from google.rpc.status_pb2 import Status
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import (
    ExportMetricsServiceRequest,
)
from opentelemetry.proto.metrics.v1 import metrics_pb2

from iip.adapters.auth import HashedBearerAuthenticator
from iip.adapters.otlp_receiver import ConfiguredOtlpMetricsReceiver
from iip.application.ingest_otlp_metrics import (
    InvalidOtlpMetricsRequestError,
    OtlpPayloadTooLargeError,
    OtlpReceiverAuthenticationError,
    OtlpReceiverConfigurationError,
)
from iip.application.ingest_resource import IngestResourceCommand
from iip.application.ports import ActorContext
from iip.bootstrap import build_local_runtime, build_runtime_from_env
from iip.surfaces.http import ApiHandler


ROOT = Path(__file__).resolve().parents[1]
CHANNEL_TOKEN = "otlp-channel-token-0123456789abcdef0123456789abcdef"
CONTROL_TOKEN = "control-plane-token-0123456789abcdef0123456789abcdef"
RESOURCE_UID = "res_e0ae9225a316fce4c97df5c23057b97a"


def receiver_config(
    *,
    token: str = CHANNEL_TOKEN,
    tenant_id: str = "local",
    max_request_bytes: int = 1_048_576,
) -> str:
    return json.dumps(
        {
            "channels": [
                {
                    "channelId": "otlp-local",
                    "tokenSha256": ConfiguredOtlpMetricsReceiver.token_sha256(token),
                    "tenantId": tenant_id,
                    "integrationId": "observability-local",
                    "resourceRefs": [RESOURCE_UID],
                    "metrics": [
                        {
                            "otlpName": "http.server.request.duration",
                            "metric": "service.request.duration",
                            "unit": "s",
                            "attributes": {
                                "service.name": "service.name",
                                "deployment.environment.name": "deployment.environment.name",
                            },
                        },
                        {
                            "otlpName": "http.server.request.count",
                            "metric": "service.request.count",
                            "unit": "{request}",
                            "attributes": {"service.name": "service.name"},
                        },
                    ],
                    "limits": {
                        "maxRequestBytes": max_request_bytes,
                        "maxArtifactBytes": 1_048_576,
                        "maxSeries": 10,
                        "maxDataPoints": 100,
                        "maxAttributesPerPoint": 8,
                        "maxAgeSeconds": 3600,
                        "maxClockSkewSeconds": 30,
                        "maxProcessingSeconds": 10,
                    },
                    "handling": {
                        "sensitivity": "internal",
                        "retentionClass": "ephemeral",
                    },
                }
            ]
        }
    )


def control_identity_config() -> str:
    return json.dumps(
        {
            "identities": [
                {
                    "tokenSha256": HashedBearerAuthenticator.token_sha256(
                        CONTROL_TOKEN
                    ),
                    "actorId": "local-developer",
                    "tenantId": "local",
                    "roles": ["developer"],
                }
            ]
        }
    )


def metric_request(
    *,
    metric_name: str = "http.server.request.duration",
    unit: str = "s",
    value: float = 0.125,
    service_name: str = "checkout",
    timestamp_ns: int | None = None,
    kind: str = "gauge",
) -> bytes:
    request = ExportMetricsServiceRequest()
    resource_metrics = request.resource_metrics.add()
    resource_metrics.resource.attributes.add(
        key="service.name"
    ).value.string_value = service_name
    # Payload identity assertions remain untrusted and deliberately unallowlisted.
    resource_metrics.resource.attributes.add(
        key="iip.tenant.id"
    ).value.string_value = "attacker-selected-tenant"
    scope_metrics = resource_metrics.scope_metrics.add()
    scope_metrics.scope.name = "test-instrumentation"
    metric = scope_metrics.metrics.add(name=metric_name, unit=unit)
    if kind == "gauge":
        point = metric.gauge.data_points.add()
    elif kind == "sum":
        metric.sum.aggregation_temporality = (
            metrics_pb2.AGGREGATION_TEMPORALITY_CUMULATIVE
        )
        metric.sum.is_monotonic = True
        point = metric.sum.data_points.add()
    else:
        point = metric.histogram.data_points.add()
        point.count = 1
    point.time_unix_nano = timestamp_ns or time.time_ns()
    if kind in ("gauge", "sum"):
        point.as_double = value
    return request.SerializeToString()


def conflicting_sum_request() -> bytes:
    request = ExportMetricsServiceRequest()
    resource_metrics = request.resource_metrics.add()
    resource_metrics.resource.attributes.add(
        key="service.name"
    ).value.string_value = "checkout"
    scope_metrics = resource_metrics.scope_metrics.add()
    timestamp_ns = time.time_ns()
    for temporality in (
        metrics_pb2.AGGREGATION_TEMPORALITY_DELTA,
        metrics_pb2.AGGREGATION_TEMPORALITY_CUMULATIVE,
    ):
        metric = scope_metrics.metrics.add(
            name="http.server.request.count",
            unit="{request}",
        )
        metric.sum.aggregation_temporality = temporality
        metric.sum.is_monotonic = True
        point = metric.sum.data_points.add()
        point.time_unix_nano = timestamp_ns
        point.as_int = 1
    return request.SerializeToString()


def ingest_fixture(runtime: object) -> None:
    resource = json.loads(
        (ROOT / "contracts" / "examples" / "resource.json").read_text()
    )
    resource = copy.deepcopy(resource)
    resource["metadata"]["tenantId"] = "local"
    runtime.ingestion.execute(
        IngestResourceCommand(
            actor=ActorContext("fixture-loader", "local"),
            payload=resource,
        )
    )


class OtlpReceiverAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.receiver = ConfiguredOtlpMetricsReceiver.from_json(receiver_config())
        self.channel = self.receiver.authenticate_bearer(CHANNEL_TOKEN)

    def test_channel_authentication_is_hashed_and_scope_is_configured(self) -> None:
        self.assertEqual(self.channel.actor.tenant_id, "local")
        self.assertEqual(self.channel.integration_id, "observability-local")
        self.assertEqual(self.channel.resource_uids, (RESOURCE_UID,))
        self.assertNotIn(CHANNEL_TOKEN, repr(self.receiver.__dict__))

        with self.assertRaisesRegex(
            OtlpReceiverAuthenticationError,
            "otlp.authentication.invalid",
        ):
            self.receiver.authenticate_bearer(CONTROL_TOKEN)

    def test_committed_deployment_example_is_valid_protected_configuration(self) -> None:
        receiver = ConfiguredOtlpMetricsReceiver.from_json(
            (ROOT / "deploy" / "otlp" / "receiver-channels.example.json").read_text()
        )

        self.assertIsInstance(receiver, ConfiguredOtlpMetricsReceiver)

    def test_configuration_is_strict_and_never_accepts_raw_tokens(self) -> None:
        malformed = json.loads(receiver_config())
        entry = malformed["channels"][0]
        entry["rawToken"] = CHANNEL_TOKEN
        del entry["tokenSha256"]

        with self.assertRaisesRegex(
            OtlpReceiverConfigurationError,
            "otlp.configuration.invalid",
        ) as raised:
            ConfiguredOtlpMetricsReceiver.from_json(json.dumps(malformed))
        self.assertNotIn(CHANNEL_TOKEN, str(raised.exception))

        sensitive_mapping = json.loads(receiver_config())
        sensitive_mapping["channels"][0]["metrics"][0]["attributes"] = {
            "authorization": "http.authorization"
        }
        with self.assertRaisesRegex(
            OtlpReceiverConfigurationError,
            "otlp.configuration.invalid",
        ):
            ConfiguredOtlpMetricsReceiver.from_json(
                json.dumps(sensitive_mapping)
            )

    def test_gauge_and_sum_are_normalized_with_only_allowlisted_attributes(self) -> None:
        gauge = self.receiver.decode_metrics(
            self.channel,
            metric_request(),
            content_encoding="identity",
        )
        summed = self.receiver.decode_metrics(
            self.channel,
            metric_request(
                metric_name="http.server.request.count",
                unit="{request}",
                value=3,
                kind="sum",
            ),
            content_encoding="identity",
        )

        self.assertEqual(gauge.series[0].metric, "service.request.duration")
        self.assertEqual(gauge.series[0].attributes, (("service.name", "checkout"),))
        self.assertEqual(summed.series[0].temporality, "cumulative")
        self.assertTrue(summed.series[0].monotonic)

    def test_gzip_is_supported_and_decoded_size_is_bounded(self) -> None:
        encoded = gzip.compress(metric_request())
        result = self.receiver.decode_metrics(
            self.channel,
            encoded,
            content_encoding="gzip",
        )
        self.assertEqual(len(result.series), 1)

        small_receiver = ConfiguredOtlpMetricsReceiver.from_json(
            receiver_config(max_request_bytes=32)
        )
        channel = small_receiver.authenticate_bearer(CHANNEL_TOKEN)
        with self.assertRaisesRegex(OtlpPayloadTooLargeError, "otlp.request.too-large"):
            small_receiver.decode_metrics(
                channel,
                encoded,
                content_encoding="gzip",
            )

    def test_unallowlisted_and_unsupported_metrics_fail_atomically(self) -> None:
        for payload, code in (
            (metric_request(metric_name="process.secret.metric"), "not-allowlisted"),
            (metric_request(kind="histogram"), "kind.unsupported"),
        ):
            with self.subTest(code=code):
                with self.assertRaisesRegex(InvalidOtlpMetricsRequestError, code):
                    self.receiver.decode_metrics(
                        self.channel,
                        payload,
                        content_encoding="identity",
                    )


class OtlpMetricsIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        receiver = ConfiguredOtlpMetricsReceiver.from_json(receiver_config())
        self.runtime = build_local_runtime(otlp_metrics_receiver=receiver)
        ingest_fixture(self.runtime)
        self.service = self.runtime.otlp_metrics_ingestion
        assert self.service is not None
        self.channel = self.service.authenticate_bearer(CHANNEL_TOKEN)

    def test_normalized_batch_is_stored_as_immutable_evidence(self) -> None:
        evidence = self.service.ingest(
            self.channel,
            metric_request(),
        )
        evidence_id = evidence["metadata"]["id"]
        artifact = self.runtime.evidence_store.read_artifact(
            self.channel.actor,
            evidence_id,
        )
        self.assertIsNotNone(artifact)
        document = json.loads(artifact)

        self.assertEqual(document["kind"], "OtlpMetricsEvidence")
        self.assertEqual(document["metadata"]["tenantId"], "local")
        self.assertEqual(document["metadata"]["channelId"], "otlp-local")
        self.assertEqual(
            document["spec"]["series"][0]["attributes"]["service.name"],
            "checkout",
        )
        self.assertNotIn("attacker-selected-tenant", artifact.decode())
        self.assertEqual(evidence["spec"]["type"], "telemetry.metrics.push")
        self.assertEqual(evidence["spec"]["handling"]["retentionClass"], "ephemeral")

    def test_secret_bearing_mapped_attribute_is_rejected_before_persistence(self) -> None:
        sensitive = "Bearer customer-secret-value"

        with self.assertRaisesRegex(
            InvalidOtlpMetricsRequestError,
            "otlp.request.invalid",
        ):
            self.service.ingest(
                self.channel,
                metric_request(service_name=sensitive),
            )

        self.assertEqual(tuple(self.runtime.evidence_store.list(self.channel.actor)), ())

    def test_sample_age_budget_is_enforced_before_persistence(self) -> None:
        two_hours_ago = time.time_ns() - 2 * 60 * 60 * 1_000_000_000

        with self.assertRaisesRegex(
            InvalidOtlpMetricsRequestError,
            "otlp.data-point.time.invalid",
        ):
            self.service.ingest(
                self.channel,
                metric_request(timestamp_ns=two_hours_ago),
            )

        self.assertEqual(tuple(self.runtime.evidence_store.list(self.channel.actor)), ())

    def test_conflicting_sum_semantics_for_one_series_are_rejected_atomically(
        self,
    ) -> None:
        with self.assertRaisesRegex(
            InvalidOtlpMetricsRequestError,
            "otlp.series.duplicate",
        ):
            self.service.ingest(self.channel, conflicting_sum_request())

        self.assertEqual(tuple(self.runtime.evidence_store.list(self.channel.actor)), ())

    def test_empty_request_is_successful_without_creating_evidence(self) -> None:
        result = self.service.ingest(self.channel, b"")

        self.assertEqual(result, {})
        self.assertEqual(tuple(self.runtime.evidence_store.list(self.channel.actor)), ())

    def test_configured_resource_must_exist_in_the_bound_tenant(self) -> None:
        receiver = ConfiguredOtlpMetricsReceiver.from_json(
            receiver_config(tenant_id="other-tenant")
        )
        runtime = build_local_runtime(otlp_metrics_receiver=receiver)
        service = runtime.otlp_metrics_ingestion
        assert service is not None
        channel = service.authenticate_bearer(CHANNEL_TOKEN)

        with self.assertRaisesRegex(ValueError, "evidence.resource.unavailable"):
            service.ingest(channel, metric_request())
        self.assertEqual(tuple(runtime.evidence_store.list(channel.actor)), ())


class OtlpReceiverCompositionAndHttpTests(unittest.TestCase):
    def _runtime(self, *, enabled: bool = True) -> object:
        environment = {"IIP_AUTH_IDENTITIES_JSON": control_identity_config()}
        if enabled:
            environment.update(
                {
                    "IIP_OTLP_RECEIVER_MODE": "shared",
                    "IIP_OTLP_RECEIVER_ENABLED": "true",
                    "IIP_OTLP_RECEIVER_CHANNELS_JSON": receiver_config(),
                }
            )
        with patch.dict(os.environ, environment, clear=True):
            runtime = build_runtime_from_env()
        ingest_fixture(runtime)
        return runtime

    def _handler(self, runtime: object, payload: bytes, token: str) -> tuple[object, list, list, BytesIO]:
        handler = object.__new__(ApiHandler)
        handler.runtime = runtime
        handler.path = "/v1/metrics"
        handler.headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/x-protobuf",
            "content-length": str(len(payload)),
        }
        handler.rfile = BytesIO(payload)
        output = BytesIO()
        handler.wfile = output
        statuses: list[int] = []
        headers: list[tuple[str, str]] = []
        handler.send_response = lambda status: statuses.append(status)
        handler.send_header = lambda name, value: headers.append((name, value))
        handler.end_headers = lambda: None
        return handler, statuses, headers, output

    def test_http_endpoint_uses_channel_auth_and_returns_otlp_protobuf(self) -> None:
        runtime = self._runtime()
        handler, statuses, headers, output = self._handler(
            runtime,
            metric_request(),
            CHANNEL_TOKEN,
        )

        handler.do_POST()

        self.assertEqual(statuses, [HTTPStatus.OK.value])
        self.assertIn(("content-type", "application/x-protobuf"), headers)
        self.assertEqual(output.getvalue(), b"")
        channel_actor = runtime.otlp_metrics_ingestion.authenticate_bearer(CHANNEL_TOKEN).actor
        self.assertEqual(len(tuple(runtime.evidence_store.list(channel_actor))), 1)

    def test_control_plane_token_cannot_authenticate_receiver_channel(self) -> None:
        runtime = self._runtime()
        handler, statuses, headers, output = self._handler(
            runtime,
            metric_request(),
            CONTROL_TOKEN,
        )

        handler.do_POST()

        self.assertEqual(statuses, [HTTPStatus.UNAUTHORIZED.value])
        self.assertIn(("WWW-Authenticate", "Bearer"), headers)
        status = Status()
        status.ParseFromString(output.getvalue())
        self.assertEqual(status.message, "otlp.authentication.invalid")

        handler, statuses, _, output = self._handler(runtime, b"", CHANNEL_TOKEN)
        handler.path = "/v1/resources"
        handler.do_GET()
        self.assertEqual(statuses, [HTTPStatus.UNAUTHORIZED.value])
        self.assertEqual(
            json.loads(output.getvalue())["error"]["code"],
            "authentication.invalid",
        )

    def test_disabled_receiver_is_not_exposed(self) -> None:
        runtime = self._runtime(enabled=False)
        handler, statuses, _, output = self._handler(runtime, b"", CHANNEL_TOKEN)

        handler.do_POST()

        self.assertEqual(statuses, [HTTPStatus.NOT_FOUND.value])
        status = Status()
        status.ParseFromString(output.getvalue())
        self.assertEqual(status.message, "otlp.receiver.disabled")

    def test_http_media_and_encoded_body_limits_fail_with_otlp_status(self) -> None:
        runtime = self._runtime()
        handler, statuses, _, output = self._handler(
            runtime,
            metric_request(),
            CHANNEL_TOKEN,
        )
        handler.headers["content-type"] = "application/json"

        handler.do_POST()

        self.assertEqual(statuses, [HTTPStatus.UNSUPPORTED_MEDIA_TYPE.value])
        status = Status()
        status.ParseFromString(output.getvalue())
        self.assertEqual(status.message, "otlp.content-type.unsupported")

        handler, statuses, _, output = self._handler(runtime, b"", CHANNEL_TOKEN)
        handler.headers["content-length"] = str(1_048_577)
        handler.do_POST()
        self.assertEqual(statuses, [HTTPStatus.REQUEST_ENTITY_TOO_LARGE.value])
        status = Status()
        status.ParseFromString(output.getvalue())
        self.assertEqual(status.message, "otlp.request.too-large")

    def test_http_requires_one_content_length_and_rejects_transfer_encoding(self) -> None:
        runtime = self._runtime()
        for header_change in ("missing", "chunked"):
            with self.subTest(header_change=header_change):
                handler, statuses, _, output = self._handler(
                    runtime,
                    metric_request(),
                    CHANNEL_TOKEN,
                )
                if header_change == "missing":
                    del handler.headers["content-length"]
                else:
                    handler.headers["transfer-encoding"] = "chunked"

                handler.do_POST()

                self.assertEqual(statuses, [HTTPStatus.BAD_REQUEST.value])
                status = Status()
                status.ParseFromString(output.getvalue())
                self.assertEqual(status.message, "otlp.content-length.invalid")

    def test_enabling_receiver_requires_channel_configuration(self) -> None:
        with patch.dict(
            os.environ,
            {
                "IIP_AUTH_IDENTITIES_JSON": control_identity_config(),
                "IIP_OTLP_RECEIVER_MODE": "shared",
                "IIP_OTLP_RECEIVER_ENABLED": "true",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(
                OtlpReceiverConfigurationError,
                "otlp.configuration.required",
            ):
                build_runtime_from_env()


if __name__ == "__main__":
    unittest.main()
