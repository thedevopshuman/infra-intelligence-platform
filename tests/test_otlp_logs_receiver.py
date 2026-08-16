from __future__ import annotations

import copy
import gzip
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch

from google.rpc.status_pb2 import Status
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
)

from iip.adapters.auth import HashedBearerAuthenticator
from iip.adapters.otlp_logs_receiver import ConfiguredOtlpLogsReceiver
from iip.application.ingest_otlp_logs import (
    InvalidOtlpLogsRequestError,
    OtlpLogsPayloadTooLargeError,
)
from iip.application.ingest_otlp_metrics import (
    OtlpReceiverAuthenticationError,
    OtlpReceiverConfigurationError,
)
from iip.application.ingest_resource import IngestResourceCommand
from iip.application.ports import ActorContext
from iip.bootstrap import _otlp_logs_receiver_from_env, build_local_runtime
from iip.surfaces.http import ApiHandler


ROOT = Path(__file__).resolve().parents[1]
CHANNEL_TOKEN = "otlp-logs-channel-token-0123456789abcdef0123456789abcdef"
CONTROL_TOKEN = "otlp-logs-control-token-0123456789abcdef0123456789abcdef"
RESOURCE_UID = "res_e0ae9225a316fce4c97df5c23057b97a"
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


def example(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text())


def channel_document() -> dict:
    document = json.loads(
        (ROOT / "deploy" / "otlp" / "log-receiver-channels.example.json").read_text()
    )
    document["channels"][0]["tokenSha256"] = (
        ConfiguredOtlpLogsReceiver.token_sha256(CHANNEL_TOKEN)
    )
    return document


def payload(
    *,
    service_name: str = "checkout",
    body: str = "database request exceeded its deadline",
    severity_number: int = 17,
    timestamp: datetime | None = None,
) -> bytes:
    instant = timestamp or datetime(2026, 8, 14, 10, 29, 48, tzinfo=timezone.utc)
    request = ExportLogsServiceRequest()
    resource_logs = request.resource_logs.add()
    resource_logs.resource.attributes.add(
        key="service.name"
    ).value.string_value = service_name
    resource_logs.resource.attributes.add(
        key="deployment.environment.name"
    ).value.string_value = "production"
    scope_logs = resource_logs.scope_logs.add()
    scope_logs.scope.name = "checkout.logger"
    record = scope_logs.log_records.add()
    record.time_unix_nano = int(instant.timestamp() * 1_000_000_000)
    record.observed_time_unix_nano = record.time_unix_nano + 1_000_000_000
    record.severity_number = severity_number
    record.severity_text = "ERROR"
    record.body.string_value = body
    record.attributes.add(
        key="k8s.namespace.name"
    ).value.string_value = "default"
    record.trace_id = bytes.fromhex("4bf92f3577b34da6a3ce929d0e0e4736")
    record.span_id = bytes.fromhex("00f067aa0ba902b7")
    return request.SerializeToString()


class OtlpLogsAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.receiver = ConfiguredOtlpLogsReceiver.from_json(
            json.dumps(channel_document())
        )
        self.channel = self.receiver.authenticate_bearer(CHANNEL_TOKEN)

    def test_configuration_authentication_and_redacted_representation(self) -> None:
        self.assertEqual(self.channel.actor.tenant_id, "local")
        self.assertEqual(self.channel.resource_uid, RESOURCE_UID)
        self.assertEqual(self.channel.service_names, ("checkout",))
        with self.assertRaisesRegex(
            OtlpReceiverAuthenticationError, "otlp.authentication.invalid"
        ):
            self.receiver.authenticate_bearer(CONTROL_TOKEN)
        self.assertNotIn(CHANNEL_TOKEN, repr(self.receiver))

        raw_token = channel_document()
        raw_token["channels"][0]["token"] = CHANNEL_TOKEN
        with self.assertRaisesRegex(
            OtlpReceiverConfigurationError, "otlp.configuration.invalid"
        ):
            ConfiguredOtlpLogsReceiver.from_json(json.dumps(raw_token))

    def test_official_protobuf_normalizes_allowlisted_log_record(self) -> None:
        batch = self.receiver.decode_logs(
            self.channel, payload(), content_encoding="identity"
        )
        self.assertEqual(len(batch.records), 1)
        record = batch.records[0]
        self.assertRegex(record.record_id, r"^log_[a-f0-9]{32}$")
        self.assertEqual(record.resource_uid, RESOURCE_UID)
        self.assertEqual(record.severity, "error")
        self.assertEqual(record.service_name, "checkout")
        self.assertEqual(
            record.attributes,
            (
                ("deployment.environment.name", "production"),
                ("k8s.namespace.name", "default"),
            ),
        )
        self.assertEqual(record.trace_id, "4bf92f3577b34da6a3ce929d0e0e4736")
        self.assertEqual(record.span_id, "00f067aa0ba902b7")

    def test_service_body_type_compression_and_decoded_size_fail_closed(self) -> None:
        with self.assertRaisesRegex(
            InvalidOtlpLogsRequestError, "otlp.service.not-allowlisted"
        ):
            self.receiver.decode_logs(
                self.channel,
                payload(service_name="unapproved"),
                content_encoding="identity",
            )

        request = ExportLogsServiceRequest()
        resource_logs = request.resource_logs.add()
        resource_logs.resource.attributes.add(
            key="service.name"
        ).value.string_value = "checkout"
        record = resource_logs.scope_logs.add().log_records.add()
        record.time_unix_nano = 1_786_704_588_000_000_000
        record.body.int_value = 42
        with self.assertRaisesRegex(
            InvalidOtlpLogsRequestError, "otlp.log-body.type.unsupported"
        ):
            self.receiver.decode_logs(
                self.channel, request.SerializeToString(), content_encoding="identity"
            )

        compressed = gzip.compress(payload())
        batch = self.receiver.decode_logs(
            self.channel, compressed, content_encoding="gzip"
        )
        self.assertEqual(len(batch.records), 1)
        small = copy.deepcopy(channel_document())
        small["channels"][0]["limits"]["maxRequestBytes"] = 100
        limited = ConfiguredOtlpLogsReceiver.from_json(json.dumps(small))
        with self.assertRaisesRegex(
            OtlpLogsPayloadTooLargeError, "otlp.request.too-large"
        ):
            limited.decode_logs(
                limited.authenticate_bearer(CHANNEL_TOKEN),
                compressed,
                content_encoding="gzip",
            )

    def test_sensitive_attribute_mapping_is_rejected(self) -> None:
        document = channel_document()
        document["channels"][0]["services"][0]["attributes"] = {
            "authorization": "authorization"
        }
        with self.assertRaisesRegex(
            OtlpReceiverConfigurationError, "otlp.configuration.invalid"
        ):
            ConfiguredOtlpLogsReceiver.from_json(json.dumps(document))


class OtlpLogsIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        authenticator = HashedBearerAuthenticator.from_json(
            json.dumps(
                {
                    "identities": [
                        {
                            "tokenSha256": HashedBearerAuthenticator.token_sha256(
                                CONTROL_TOKEN
                            ),
                            "actorId": "developer",
                            "tenantId": "local",
                            "roles": ["developer"],
                        }
                    ]
                }
            )
        )
        self.receiver = ConfiguredOtlpLogsReceiver.from_json(
            json.dumps(channel_document())
        )
        self.runtime = build_local_runtime(
            authenticator,
            otlp_logs_receiver=self.receiver,
        )
        self.actor = ActorContext("developer", "local")
        self.runtime.ingestion.execute(
            IngestResourceCommand(self.actor, example("resource.json"))
        )
        self.service = self.runtime.otlp_logs_ingestion
        self.assertIsNotNone(self.service)

    def test_tenant_bound_batch_is_redacted_and_persisted(self) -> None:
        channel = self.service.authenticate_bearer(CHANNEL_TOKEN)
        evidence = self.service.ingest(
            channel,
            payload(
                body="authorization=customer-secret",
                timestamp=datetime.now(timezone.utc),
            ),
        )
        artifact = self.runtime.evidence_store.read_artifact(
            channel.actor, evidence["metadata"]["id"]
        )
        self.assertNotIn(b"customer-secret", artifact)
        document = json.loads(artifact)
        self.assertEqual(document["kind"], "OtlpLogsEvidence")
        self.assertEqual(document["metadata"]["tenantId"], "local")
        self.assertEqual(document["spec"]["summary"]["recordCount"], 1)
        self.assertEqual(evidence["spec"]["type"], "telemetry.logs.push")
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                json.loads(
                    (
                        ROOT
                        / "contracts"
                        / "schemas"
                        / "otlp-logs-evidence.schema.json"
                    ).read_text()
                ),
                document,
                label="OTLP logs artifact",
            ),
            [],
        )

    def test_http_endpoint_uses_logs_channel_auth_and_otlp_response(self) -> None:
        handler = object.__new__(ApiHandler)
        handler.runtime = self.runtime
        handler.path = "/v1/logs"
        handler.headers = {
            "authorization": f"Bearer {CHANNEL_TOKEN}",
            "content-type": "application/x-protobuf",
        }
        handler._read_binary = lambda maximum: payload(
            timestamp=datetime.now(timezone.utc)
        )
        responses: list[tuple[HTTPStatus, bytes]] = []
        handler._otlp_response = lambda status, body: responses.append((status, body))
        handler.do_POST()
        self.assertEqual(responses, [(HTTPStatus.OK, b"")])

        handler.headers["authorization"] = f"Bearer {CONTROL_TOKEN}"
        responses.clear()
        handler.do_POST()
        status = Status()
        status.ParseFromString(responses[0][1])
        self.assertEqual(responses[0][0], HTTPStatus.UNAUTHORIZED)
        self.assertEqual(status.message, "otlp.authentication.invalid")

    def test_disabled_or_missing_configuration_fails_closed(self) -> None:
        disabled = build_local_runtime()
        handler = object.__new__(ApiHandler)
        handler.runtime = disabled
        handler.path = "/v1/logs"
        responses: list[tuple[HTTPStatus, bytes]] = []
        handler._otlp_response = lambda status, body: responses.append((status, body))
        handler.do_POST()
        self.assertEqual(responses[0][0], HTTPStatus.NOT_FOUND)

        with patch.dict(
            os.environ,
            {"IIP_OTLP_LOGS_RECEIVER_ENABLED": "true"},
            clear=True,
        ):
            with self.assertRaisesRegex(
                OtlpReceiverConfigurationError, "otlp.configuration.required"
            ):
                _otlp_logs_receiver_from_env()


if __name__ == "__main__":
    unittest.main()
