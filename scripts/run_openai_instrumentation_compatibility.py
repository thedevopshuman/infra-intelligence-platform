#!/usr/bin/env python3
"""Qualify one exact official OpenTelemetry Python OpenAI profile."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import platform
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from threading import Thread
from typing import Iterator, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker
from openai import OpenAI
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.instrumentation.genai.openai import OpenAIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind

from iip import __version__ as APPLICATION_VERSION
from iip.adapters.memory import InMemoryResourceStore
from iip.adapters.otlp_ai_usage_receiver import ConfiguredAiUsageReceiver
from iip.application.ingest_ai_usage import AiUsageIngestionService


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = (
    ROOT
    / "contracts"
    / "schemas"
    / "openai-instrumentation-compatibility-report.schema.json"
)
TOKEN = "openai-compatibility-token-0123456789abcdef0123456789abcdef"
SERVICE_NAME = "iip-openai-compatibility"
SCOPE_NAME = "opentelemetry.util.genai.handler"
SEMANTIC_CONVENTION_VERSION = "1.37.0-development"
OFFLINE_MODEL = "example-openai-model"
CHECK_IDS = (
    "provider-call-completed",
    "official-instrumentation-span",
    "supported-instrumentation-scope",
    "provider-identity-exact",
    "metadata-only-span",
    "provider-token-totals",
    "receiver-normalization",
    "async-export-failure-isolated",
)
MISSING_USAGE_FIELDS = (
    "cacheReadInputTokens",
    "cacheWriteInputTokens",
    "reasoningOutputTokens",
)


class CompatibilityFailure(RuntimeError):
    """A stable compatibility check failed without exposing provider data."""


class _Clock:
    @staticmethod
    def now() -> str:
        return timestamp()


class _FailingAsyncExporter(SpanExporter):
    """Deterministic stopped-sink model used only behind a batch processor."""

    def __init__(self) -> None:
        self.calls = 0

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        self.calls += 1
        return SpanExportResult.FAILURE

    def shutdown(self) -> None:
        return None


class _OfflineOpenAIHandler(BaseHTTPRequestHandler):
    model_id = OFFLINE_MODEL

    def do_POST(self) -> None:  # noqa: N802
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if self.path != "/v1/chat/completions" or not 1 <= length <= 65_536:
                raise ValueError
            document = json.loads(self.rfile.read(length))
            if (
                not isinstance(document, dict)
                or document.get("model") != self.model_id
                or not isinstance(document.get("messages"), list)
            ):
                raise ValueError
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)
            return
        response = {
            "id": "chatcmpl-offline-compatibility",
            "object": "chat.completion",
            "created": 1_788_580_800,
            "model": self.model_id,
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "acknowledged",
                        "refusal": None,
                    },
                    "logprobs": None,
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 17,
                "completion_tokens": 5,
                "total_tokens": 22,
                "prompt_tokens_details": {
                    "audio_tokens": 0,
                    "cached_tokens": 0,
                },
                "completion_tokens_details": {
                    "accepted_prediction_tokens": 0,
                    "audio_tokens": 0,
                    "reasoning_tokens": 0,
                    "rejected_prediction_tokens": 0,
                },
            },
        }
        payload = json.dumps(response, separators=(",", ":")).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return None


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def require(condition: object, code: str) -> None:
    if not condition:
        raise CompatibilityFailure(code)


@contextmanager
def offline_endpoint(model_id: str) -> Iterator[str]:
    handler = type(
        "BoundOfflineOpenAIHandler",
        (_OfflineOpenAIHandler,),
        {"model_id": model_id},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def provider_client(mode: str, model_id: str) -> Iterator[OpenAI]:
    if mode == "offline":
        with offline_endpoint(model_id) as endpoint:
            yield OpenAI(
                api_key="offline-key-not-a-secret",
                base_url=endpoint,
                max_retries=0,
                timeout=5,
            )
        return
    yield OpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        max_retries=0,
        timeout=30,
    )


def instrumented_call(
    *, mode: str, model_id: str
) -> tuple[Mapping[str, object], ReadableSpan, int]:
    os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "NO_CONTENT"
    os.environ["OTEL_INSTRUMENTATION_GENAI_EMIT_EVENT"] = "false"
    os.environ.pop("OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK", None)
    os.environ.pop("OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH", None)
    capture = InMemorySpanExporter()
    failed_export = _FailingAsyncExporter()
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": SERVICE_NAME,
                "service.namespace": "iip-qualification",
                "deployment.environment.name": "compatibility-test",
                "cloud.region": "global",
            }
        )
    )
    provider.add_span_processor(SimpleSpanProcessor(capture))
    provider.add_span_processor(
        BatchSpanProcessor(
            failed_export,
            schedule_delay_millis=60_000,
            export_timeout_millis=1_000,
        )
    )
    instrumentor = OpenAIInstrumentor()
    instrumentor.instrument(tracer_provider=provider)
    try:
        with provider_client(mode, model_id) as client:
            response = client.chat.completions.create(
                model=model_id,
                messages=[
                    {
                        "role": "user",
                        "content": "Return the single word acknowledged.",
                    }
                ],
            )
        usage = response.usage
        require(
            usage is not None
            and isinstance(usage.prompt_tokens, int)
            and usage.prompt_tokens > 0
            and isinstance(usage.completion_tokens, int)
            and usage.completion_tokens > 0,
            "provider-usage.invalid",
        )
        result = {
            "inputTokens": usage.prompt_tokens,
            "outputTokens": usage.completion_tokens,
            "responseModel": response.model,
        }
        provider.force_flush(timeout_millis=5_000)
    finally:
        instrumentor.uninstrument()
        provider.shutdown()

    spans = tuple(
        span
        for span in capture.get_finished_spans()
        if span.instrumentation_scope.name == SCOPE_NAME
    )
    require(len(spans) == 1, "instrumentation-span.count")
    require(failed_export.calls >= 1, "async-export.not-attempted")
    return result, spans[0], failed_export.calls


def channel_document(
    request_model: str,
    response_model: str,
) -> dict[str, object]:
    return {
        "channels": [
            {
                "channelId": "openai-compatibility",
                "tokenSha256": ConfiguredAiUsageReceiver.token_sha256(TOKEN),
                "tenantId": "compatibility",
                "integrationId": "openai-compatibility",
                "provider": "openai",
                "semanticConventionVersion": SEMANTIC_CONVENTION_VERSION,
                "services": [
                    {
                        "otlpName": SERVICE_NAME,
                        "serviceName": SERVICE_NAME,
                        "serviceNamespace": "iip-qualification",
                        "deploymentEnvironment": "compatibility-test",
                        "resourceRefs": [],
                    }
                ],
                "models": sorted({request_model, response_model}),
                "operations": ["chat"],
                "regions": ["global"],
                "instrumentationScopes": [SCOPE_NAME],
                "usageAttributes": {
                    "inputTokens": "gen_ai.usage.input_tokens",
                    "outputTokens": "gen_ai.usage.output_tokens",
                    "cacheReadInputTokens": "gen_ai.usage.cache_read.input_tokens",
                    "cacheWriteInputTokens": "gen_ai.usage.cache_creation.input_tokens",
                    "reasoningOutputTokens": "gen_ai.usage.reasoning_tokens",
                    "zeroWhenAbsent": [],
                    "reportedBy": "provider",
                },
                "commercial": {
                    "serviceTier": "default",
                    "routingMode": "global",
                    "purchaseMode": "on-demand",
                },
                "limits": {
                    "maxRequestBytes": 1_048_576,
                    "maxSpans": 4,
                    "maxAttributesPerSpan": 64,
                    "maxAgeSeconds": 300,
                    "maxClockSkewSeconds": 30,
                    "maxProcessingSeconds": 10,
                },
            }
        ]
    }


def normalize_span(
    span: ReadableSpan,
    *,
    provider_result: Mapping[str, object],
    request_model: str,
) -> Mapping[str, object]:
    attributes = span.attributes
    response_model = provider_result["responseModel"]
    require(isinstance(response_model, str), "provider-model.invalid")
    require(span.kind == SpanKind.CLIENT, "instrumentation-span.kind")
    require(
        span.instrumentation_scope.name == SCOPE_NAME,
        "instrumentation-scope.unsupported",
    )
    require(
        span.instrumentation_scope.version == "1.1b0",
        "instrumentation-version.unexpected",
    )
    require(
        attributes.get("gen_ai.provider.name") == "openai",
        "provider-identity.missing",
    )
    require(
        attributes.get("gen_ai.operation.name") == "chat"
        and attributes.get("gen_ai.request.model") == request_model,
        "genai-identity.invalid",
    )
    require(not span.events and not span.links, "span-content.present")
    prohibited = (
        "prompt",
        "message",
        "content",
        "body",
        "tool.call.arguments",
        "embedding",
        "document",
    )
    require(
        not any(
            fragment in key.lower()
            for key in attributes
            for fragment in prohibited
        ),
        "span-content.present",
    )
    require(
        attributes.get("gen_ai.usage.input_tokens")
        == provider_result["inputTokens"]
        and attributes.get("gen_ai.usage.output_tokens")
        == provider_result["outputTokens"],
        "provider-token-totals.mismatch",
    )

    receiver = ConfiguredAiUsageReceiver.from_json(
        json.dumps(
            channel_document(request_model, response_model),
            separators=(",", ":"),
        )
    )
    service = AiUsageIngestionService(receiver, InMemoryResourceStore(), _Clock())
    channel = service.authenticate_bearer(TOKEN)
    payload = encode_spans((span,)).SerializeToString()
    records = service.ingest(channel, payload)
    require(len(records) == 1, "receiver-normalization.count")
    record = records[0]
    spec = record.get("spec")
    require(isinstance(spec, Mapping), "receiver-record.invalid")
    invocation = spec.get("invocation")
    normalized_usage = spec.get("usage")
    privacy = spec.get("privacy")
    source = spec.get("source")
    require(
        isinstance(invocation, Mapping)
        and invocation.get("provider") == "openai"
        and invocation.get("operationName") == "chat"
        and invocation.get("region") == "global",
        "receiver-identity.invalid",
    )
    require(
        isinstance(normalized_usage, Mapping)
        and normalized_usage.get("completeness") == "partial"
        and normalized_usage.get("missingFields") == list(MISSING_USAGE_FIELDS),
        "receiver-usage-completeness.invalid",
    )
    require(
        isinstance(privacy, Mapping)
        and privacy.get("contentCaptured") is False
        and privacy.get("rawPayloadPersisted") is False,
        "receiver-privacy.invalid",
    )
    require(
        isinstance(source, Mapping)
        and isinstance(source.get("instrumentation"), Mapping)
        and source["instrumentation"].get("scopeName") == SCOPE_NAME,
        "receiver-instrumentation.invalid",
    )
    return record


def compatibility_report(*, mode: str, model_id: str) -> dict[str, object]:
    provider_result, span, failed_export_calls = instrumented_call(
        mode=mode,
        model_id=model_id,
    )
    normalize_span(
        span,
        provider_result=provider_result,
        request_model=model_id,
    )
    require(failed_export_calls >= 1, "async-export.not-attempted")

    source_revision = os.environ.get("IIP_SOURCE_REVISION", "")
    source_dirty_value = os.environ.get("IIP_SOURCE_DIRTY", "")
    require(
        len(source_revision) in range(40, 65)
        and all(character in "0123456789abcdef" for character in source_revision),
        "source-revision.invalid",
    )
    require(source_dirty_value in ("true", "false"), "source-dirty.invalid")
    qualification = (
        "offline-sdk-interoperability"
        if mode == "offline"
        else "live-provider-interoperability"
    )
    identity = json.dumps(
        {
            "sourceRevision": source_revision,
            "qualificationLevel": qualification,
            "modelId": model_id,
            "region": "global",
            "versions": {
                "openai": version("openai"),
                "instrumentation": version(
                    "opentelemetry-instrumentation-genai-openai"
                ),
                "otelUtil": version("opentelemetry-util-genai"),
                "otelSdk": version("opentelemetry-sdk"),
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    checks = [{"id": check_id, "status": "passed"} for check_id in CHECK_IDS]
    return {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "OpenAIInstrumentationCompatibilityReport",
        "metadata": {
            "id": "oic_" + hashlib.sha256(identity).hexdigest()[:32],
            "generatedAt": timestamp(),
            "sourceRevision": source_revision,
            "sourceDirty": source_dirty_value == "true",
        },
        "spec": {
            "status": "compatible",
            "qualificationLevel": qualification,
            "environment": {
                "platform": f"{platform.system().lower()}/{platform.machine()}",
                "containerRuntime": "docker",
                "containerRuntimeVersion": os.environ.get(
                    "IIP_CONTAINER_RUNTIME_VERSION", "unknown"
                ),
                "pythonVersion": platform.python_version(),
                "applicationVersion": APPLICATION_VERSION,
                "openaiVersion": version("openai"),
                "instrumentationVersion": version(
                    "opentelemetry-instrumentation-genai-openai"
                ),
                "otelUtilVersion": version("opentelemetry-util-genai"),
                "otelSdkVersion": version("opentelemetry-sdk"),
            },
            "profile": {
                "name": "otel-python-openai-chat-completions-v1",
                "provider": "openai",
                "api": "chat.completions.create",
                "operation": "chat",
                "modelId": model_id,
                "region": "global",
                "regionSource": "otel-resource.cloud.region",
                "invocationTarget": (
                    "local-http-fixture" if mode == "offline" else "openai-api"
                ),
                "instrumentationScope": SCOPE_NAME,
                "providerAttribute": "gen_ai.provider.name",
                "contentCapture": False,
                "requestPath": "direct-to-provider",
                "telemetryPath": "asynchronous-otel",
            },
            "result": {
                "normalizedProvider": "openai",
                "operationName": "chat",
                "usageCompleteness": "partial",
                "missingUsageFields": list(MISSING_USAGE_FIELDS),
                "contentCaptured": False,
                "rawPayloadPersisted": False,
                "exactCostEligible": False,
                "liveProviderVerified": mode == "live",
                "streamingVerified": False,
            },
            "checks": checks,
            "summary": {
                "totalChecks": len(checks),
                "passedChecks": len(checks),
                "failedChecks": 0,
                "overallStatus": "compatible",
            },
        },
    }


def validate_report(report: Mapping[str, object]) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        ).iter_errors(report),
        key=lambda error: tuple(str(item) for item in error.absolute_path),
    )
    require(not errors, "report.schema-invalid")
    spec = report["spec"]
    assert isinstance(spec, Mapping)
    qualification = spec["qualificationLevel"]
    profile = spec["profile"]
    result = spec["result"]
    assert isinstance(profile, Mapping) and isinstance(result, Mapping)
    require(
        (qualification == "live-provider-interoperability")
        == (profile["invocationTarget"] == "openai-api")
        == (result["liveProviderVerified"] is True),
        "report.qualification-inconsistent",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("offline", "live"), default="offline")
    parser.add_argument("--report", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    arguments = parse_args()
    try:
        if arguments.mode == "live":
            require(
                os.environ.get("IIP_OPENAI_LIVE_TEST_ENABLED") == "true",
                "live-test.not-enabled",
            )
            model_id = os.environ.get("IIP_OPENAI_MODEL", "")
            require(bool(model_id), "live-test.model-required")
            require(bool(os.environ.get("OPENAI_API_KEY")), "live-test.key-required")
        else:
            model_id = OFFLINE_MODEL
        report = compatibility_report(mode=arguments.mode, model_id=model_id)
        validate_report(report)
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(
            json.dumps(report, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        print(
            "openai.compatibility.failed:" + type(error).__name__,
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
