from __future__ import annotations

import copy
import gzip
import json
import sys
import unittest
from datetime import datetime, timezone
from http import HTTPStatus
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.proto.trace.v1.trace_pb2 import Span, Status
from google.rpc.status_pb2 import Status as RpcStatus

from iip.adapters.memory import InMemoryResourceStore
from iip.adapters.otlp_ai_usage_receiver import ConfiguredAiUsageReceiver
from iip.application.ingest_ai_usage import (
    AiUsageIngestionService,
    AiUsagePayloadTooLargeError,
    InvalidAiUsageRequestError,
)
from iip.application.ingest_otlp_metrics import (
    OtlpReceiverAuthenticationError,
    OtlpReceiverConfigurationError,
)
from iip.application.ports import ActorContext, PersistenceError
from iip.bootstrap import _ai_usage_receiver_from_env, build_local_runtime
from iip.surfaces.http import ApiHandler


ROOT = Path(__file__).resolve().parents[1]
CHANNEL_TOKEN = "bedrock-channel-token-0123456789abcdef0123456789abcdef"
OTHER_TOKEN = "other-channel-token-0123456789abcdef0123456789abcdef"
TRACE_ID = "0123456789abcdef0123456789abcdef"
SPAN_ID = "0123456789abcdef"
sys.path.insert(0, str(ROOT / "scripts"))

import validate_schemas  # noqa: E402


class MutableClock:
    def __init__(self, value: str = "2026-09-05T10:00:02Z") -> None:
        self.value = value

    def now(self) -> str:
        return self.value


def channel_document() -> dict:
    document = json.loads(
        (
            ROOT
            / "deploy"
            / "otlp"
            / "ai-usage-receiver-channels.example.json"
        ).read_text()
    )
    document["channels"][0]["tokenSha256"] = (
        ConfiguredAiUsageReceiver.token_sha256(CHANNEL_TOKEN)
    )
    return document


def trace_payload(
    *,
    input_tokens: int | None = 2400,
    output_tokens: int | None = 600,
    content_attribute: str | None = None,
    service_name: str = "support-assistant",
    scope_name: str = "opentelemetry.instrumentation.botocore",
    model: str = "example.foundation-model-v1:0",
    started_at: datetime | None = None,
) -> bytes:
    request = ExportTraceServiceRequest()
    resource_spans = request.resource_spans.add()
    for key, value in (
        ("service.name", service_name),
        ("service.namespace", "customer-experience"),
        ("deployment.environment.name", "production"),
        ("cloud.region", "us-east-1"),
        ("host.name", "ignored-host"),
    ):
        resource_spans.resource.attributes.add(key=key).value.string_value = value
    scope_spans = resource_spans.scope_spans.add()
    scope_spans.scope.name = scope_name
    scope_spans.scope.version = "0.0.0-example"
    span = scope_spans.spans.add()
    span.trace_id = bytes.fromhex(TRACE_ID)
    span.span_id = bytes.fromhex(SPAN_ID)
    span.name = "chat example.foundation-model-v1:0"
    span.kind = Span.SPAN_KIND_CLIENT
    started = started_at or datetime(2026, 9, 5, 10, 0, 0, tzinfo=timezone.utc)
    span.start_time_unix_nano = int(started.timestamp() * 1_000_000_000)
    span.end_time_unix_nano = span.start_time_unix_nano + 1_250_000_000
    span.status.code = Status.STATUS_CODE_OK
    string_attributes = (
        ("gen_ai.provider.name", "aws.bedrock"),
        ("gen_ai.operation.name", "chat"),
        ("gen_ai.request.model", model),
        ("gen_ai.response.model", model),
        ("aws.request_id", "provider-request-123"),
    )
    for key, value in string_attributes:
        span.attributes.add(key=key).value.string_value = value
    if input_tokens is not None:
        span.attributes.add(
            key="gen_ai.usage.input_tokens"
        ).value.int_value = input_tokens
    if output_tokens is not None:
        span.attributes.add(
            key="gen_ai.usage.output_tokens"
        ).value.int_value = output_tokens
    span.attributes.add(key="aws.retry_count").value.int_value = 0
    if content_attribute is not None:
        span.attributes.add(key=content_attribute).value.string_value = "must-not-cross"
    return request.SerializeToString()


class AiUsageReceiverAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.receiver = ConfiguredAiUsageReceiver.from_json(
            json.dumps(channel_document())
        )
        self.channel = self.receiver.authenticate_bearer(CHANNEL_TOKEN)

    def test_configuration_authentication_and_metadata_normalization(self) -> None:
        self.assertEqual(self.channel.actor.tenant_id, "local")
        self.assertEqual(self.channel.provider, "aws.bedrock")
        self.assertNotIn(CHANNEL_TOKEN, repr(self.receiver.__dict__))
        with self.assertRaisesRegex(
            OtlpReceiverAuthenticationError, "otlp.authentication.invalid"
        ):
            self.receiver.authenticate_bearer(OTHER_TOKEN)

        batch = self.receiver.decode_traces(
            self.channel,
            trace_payload(),
            content_encoding="identity",
        )
        self.assertEqual(len(batch.spans), 1)
        observed = batch.spans[0]
        self.assertEqual(observed.provider, "aws.bedrock")
        self.assertEqual(observed.input_tokens, 2400)
        self.assertEqual(observed.cache_read_input_tokens, 0)
        self.assertEqual(observed.reasoning_output_tokens, 0)
        self.assertEqual(observed.completeness, "complete")
        self.assertEqual(observed.dropped_attribute_count, 1)
        self.assertRegex(observed.request_id_hash or "", r"^sha256:[a-f0-9]{64}$")
        self.assertNotIn("provider-request-123", repr(observed))

    def test_content_events_and_unapproved_identity_fail_closed(self) -> None:
        for content_name in (
            "gen_ai.prompt",
            "gen_ai.input.messages",
            "gen_ai.tool.call.arguments",
            "http.request.body",
            "http.request.header.authorization.sha256",
        ):
            with self.subTest(content_name=content_name):
                with self.assertRaisesRegex(
                    InvalidAiUsageRequestError, "otlp.span.content-prohibited"
                ):
                    self.receiver.decode_traces(
                        self.channel,
                        trace_payload(content_attribute=content_name),
                        content_encoding="identity",
                    )

        with self.assertRaisesRegex(
            InvalidAiUsageRequestError, "otlp.attribute.invalid"
        ):
            self.receiver.decode_traces(
                self.channel,
                trace_payload(content_attribute="invalid attribute name"),
                content_encoding="identity",
            )

        with self.assertRaisesRegex(
            InvalidAiUsageRequestError, "otlp.service.not-allowlisted"
        ):
            self.receiver.decode_traces(
                self.channel,
                trace_payload(service_name="attacker-selected"),
                content_encoding="identity",
            )
        with self.assertRaisesRegex(
            InvalidAiUsageRequestError, "otlp.span.not-allowlisted"
        ):
            self.receiver.decode_traces(
                self.channel,
                trace_payload(model="unapproved-model"),
                content_encoding="identity",
            )

        request = ExportTraceServiceRequest()
        request.ParseFromString(trace_payload())
        request.resource_spans[0].scope_spans[0].spans[0].events.add(
            name="gen_ai.content"
        )
        with self.assertRaisesRegex(
            InvalidAiUsageRequestError, "otlp.span.content-prohibited"
        ):
            self.receiver.decode_traces(
                self.channel,
                request.SerializeToString(),
                content_encoding="identity",
            )

    def test_gzip_and_decoded_size_are_bounded(self) -> None:
        encoded = gzip.compress(trace_payload())
        self.assertEqual(
            len(
                self.receiver.decode_traces(
                    self.channel, encoded, content_encoding="gzip"
                ).spans
            ),
            1,
        )
        limited_document = channel_document()
        limited_document["channels"][0]["limits"]["maxRequestBytes"] = 100
        limited = ConfiguredAiUsageReceiver.from_json(json.dumps(limited_document))
        with self.assertRaisesRegex(
            AiUsagePayloadTooLargeError, "otlp.request.too-large"
        ):
            limited.decode_traces(
                limited.authenticate_bearer(CHANNEL_TOKEN),
                encoded,
                content_encoding="gzip",
            )

    def test_configuration_is_closed_and_contains_no_raw_token(self) -> None:
        malformed = channel_document()
        malformed["channels"][0]["token"] = CHANNEL_TOKEN
        del malformed["channels"][0]["tokenSha256"]
        with self.assertRaisesRegex(
            OtlpReceiverConfigurationError, "otlp.configuration.invalid"
        ) as raised:
            ConfiguredAiUsageReceiver.from_json(json.dumps(malformed))
        self.assertNotIn(CHANNEL_TOKEN, str(raised.exception))


class AiUsageIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = MutableClock()
        self.store = InMemoryResourceStore()
        self.receiver = ConfiguredAiUsageReceiver.from_json(
            json.dumps(channel_document())
        )
        self.service = AiUsageIngestionService(
            self.receiver,
            self.store,
            self.clock,
        )
        self.channel = self.service.authenticate_bearer(CHANNEL_TOKEN)

    def test_record_is_contract_valid_metadata_only_and_event_backed(self) -> None:
        record = self.service.ingest(self.channel, trace_payload())[0]
        schema = json.loads(
            (ROOT / "contracts" / "schemas" / "ai-usage-record.schema.json").read_text()
        )
        self.assertEqual(
            validate_schemas.instance_validation_errors(
                schema,
                record,
                label="accepted AI usage",
            ),
            [],
        )
        serialized = json.dumps(record)
        self.assertNotIn("provider-request-123", serialized)
        self.assertNotIn("must-not-cross", serialized)
        self.assertEqual(record["metadata"]["tenantId"], "local")
        self.assertFalse(record["spec"]["privacy"]["contentCaptured"])
        events = self.store.list_events("local")
        self.assertEqual(len(tuple(events)), 1)
        self.assertEqual(events[0].event.subject, record["metadata"]["id"])
        self.assertEqual(events[0].event.correlation_id, TRACE_ID)

    def test_retry_is_idempotent_but_changed_usage_conflicts_atomically(self) -> None:
        first = self.service.ingest(self.channel, trace_payload())[0]
        self.clock.value = "2026-09-05T10:00:03Z"
        retried = self.service.ingest(self.channel, trace_payload())[0]
        self.assertEqual(retried, first)
        self.assertEqual(len(tuple(self.store.list_events("local"))), 1)

        with self.assertRaisesRegex(PersistenceError, "storage.conflict"):
            self.service.ingest(
                self.channel,
                trace_payload(input_tokens=2401),
            )
        self.assertEqual(len(tuple(self.store.list_events("local"))), 1)

    def test_partial_usage_and_direct_cross_tenant_storage_are_explicit(self) -> None:
        record = self.service.ingest(
            self.channel,
            trace_payload(input_tokens=None),
        )[0]
        self.assertEqual(record["spec"]["usage"]["completeness"], "partial")
        self.assertEqual(record["spec"]["usage"]["missingFields"], ["inputTokens"])

        wrong_actor = ActorContext(
            "ai-usage-channel:bedrock-traces-local",
            "another-tenant",
            ("telemetry-ingest",),
        )
        with self.assertRaisesRegex(PersistenceError, "storage.request.invalid"):
            self.store.commit_usage_batch(
                wrong_actor,
                (copy.deepcopy(record),),
                (self.store.events[-1],),
            )


class AiUsageHttpAndCompositionTests(unittest.TestCase):
    def _handler(
        self,
        runtime: object,
        body: bytes,
        token: str = CHANNEL_TOKEN,
    ) -> tuple[ApiHandler, list[int], list[tuple[str, str]], BytesIO]:
        handler = object.__new__(ApiHandler)
        handler.runtime = runtime
        handler.path = "/v1/traces"
        handler.headers = {
            "authorization": f"Bearer {token}",
            "content-type": "application/x-protobuf",
            "content-length": str(len(body)),
        }
        handler.rfile = BytesIO(body)
        output = BytesIO()
        handler.wfile = output
        statuses: list[int] = []
        headers: list[tuple[str, str]] = []
        handler.send_response = lambda status: statuses.append(status)
        handler.send_header = lambda name, value: headers.append((name, value))
        handler.end_headers = lambda: None
        return handler, statuses, headers, output

    def test_trace_route_commits_before_success_and_uses_channel_identity(self) -> None:
        receiver = ConfiguredAiUsageReceiver.from_json(json.dumps(channel_document()))
        runtime = build_local_runtime(ai_usage_receiver=receiver)
        self.addCleanup(runtime.close)
        body = trace_payload(started_at=datetime.now(timezone.utc))
        handler, statuses, headers, output = self._handler(runtime, body)

        handler.do_POST()

        self.assertEqual(statuses, [HTTPStatus.OK.value])
        self.assertIn(("content-type", "application/x-protobuf"), headers)
        self.assertEqual(output.getvalue(), b"")
        self.assertEqual(len(tuple(runtime.event_log.list_events("local"))), 1)

        handler, statuses, headers, output = self._handler(
            runtime,
            body,
            OTHER_TOKEN,
        )
        handler.do_POST()
        status = RpcStatus()
        status.ParseFromString(output.getvalue())
        self.assertEqual(statuses, [HTTPStatus.UNAUTHORIZED.value])
        self.assertIn(("WWW-Authenticate", "Bearer"), headers)
        self.assertEqual(status.message, "otlp.authentication.invalid")

    def test_disabled_and_missing_environment_configuration_fail_closed(self) -> None:
        runtime = build_local_runtime()
        self.addCleanup(runtime.close)
        handler, statuses, _, output = self._handler(runtime, b"")
        handler.do_POST()
        status = RpcStatus()
        status.ParseFromString(output.getvalue())
        self.assertEqual(statuses, [HTTPStatus.NOT_FOUND.value])
        self.assertEqual(status.message, "otlp.receiver.disabled")

        with patch.dict(
            "os.environ",
            {"IIP_AI_USAGE_RECEIVER_ENABLED": "true"},
            clear=True,
        ):
            with self.assertRaisesRegex(
                OtlpReceiverConfigurationError, "otlp.configuration.required"
            ):
                _ai_usage_receiver_from_env()


if __name__ == "__main__":
    unittest.main()
