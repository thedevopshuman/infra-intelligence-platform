#!/usr/bin/env python3
"""Qualify one exact official botocore Bedrock instrumentation profile."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import sys
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Mapping, Sequence

import boto3
from botocore.stub import Stubber
from jsonschema import Draft202012Validator, FormatChecker
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.instrumentation.botocore import BotocoreInstrumentor
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
    / "bedrock-instrumentation-compatibility-report.schema.json"
)
TOKEN = "bedrock-compatibility-token-0123456789abcdef0123456789abcdef"
SERVICE_NAME = "iip-bedrock-compatibility"
SCOPE_NAME = "opentelemetry.instrumentation.botocore.bedrock-runtime"
SEMANTIC_CONVENTION_VERSION = "1.37.0-development"
OFFLINE_MODEL = "example.foundation-model-v1:0"
CHECK_IDS = (
    "provider-call-completed",
    "official-instrumentation-span",
    "supported-instrumentation-scope",
    "provider-alias-normalized",
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


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def require(condition: object, code: str) -> None:
    if not condition:
        raise CompatibilityFailure(code)


def offline_response() -> dict[str, object]:
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": "acknowledged"}],
            }
        },
        "stopReason": "end_turn",
        "usage": {"inputTokens": 17, "outputTokens": 5, "totalTokens": 22},
        "metrics": {"latencyMs": 12},
        "ResponseMetadata": {
            "RequestId": "offline-request-id",
            "HTTPStatusCode": 200,
            "HTTPHeaders": {},
            "RetryAttempts": 0,
        },
    }


def request_parameters(model_id: str) -> dict[str, object]:
    return {
        "modelId": model_id,
        "messages": [
            {
                "role": "user",
                "content": [{"text": "Return the single word acknowledged."}],
            }
        ],
        "inferenceConfig": {"maxTokens": 16, "temperature": 0.0},
    }


def provider_client(mode: str, region: str):
    if mode == "offline":
        return boto3.client(
            "bedrock-runtime",
            region_name=region,
            aws_access_key_id="offline-access-key",
            aws_secret_access_key="offline-secret-key",
            aws_session_token="offline-session-token",
        )
    return boto3.client("bedrock-runtime", region_name=region)


def instrumented_call(
    *, mode: str, model_id: str, region: str
) -> tuple[Mapping[str, object], ReadableSpan, int]:
    os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "false"
    capture = InMemorySpanExporter()
    failed_export = _FailingAsyncExporter()
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": SERVICE_NAME,
                "service.namespace": "iip-qualification",
                "deployment.environment.name": "compatibility-test",
                "cloud.region": region,
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
    instrumentor = BotocoreInstrumentor()
    client = provider_client(mode, region)
    parameters = request_parameters(model_id)
    stubber = None
    if mode == "offline":
        stubber = Stubber(client)
        stubber.add_response("converse", offline_response(), parameters)
        stubber.activate()

    instrumentor.instrument(tracer_provider=provider)
    try:
        response = client.converse(**parameters)
        require(isinstance(response, Mapping), "provider-response.invalid")
        usage = response.get("usage")
        require(isinstance(usage, Mapping), "provider-usage.missing")
        require(
            isinstance(usage.get("inputTokens"), int)
            and usage["inputTokens"] > 0
            and isinstance(usage.get("outputTokens"), int)
            and usage["outputTokens"] > 0,
            "provider-usage.invalid",
        )
        provider.force_flush(timeout_millis=5_000)
    finally:
        instrumentor.uninstrument()
        if stubber is not None:
            stubber.deactivate()
        provider.shutdown()

    spans = tuple(
        span
        for span in capture.get_finished_spans()
        if span.instrumentation_scope.name == SCOPE_NAME
    )
    require(len(spans) == 1, "instrumentation-span.count")
    require(failed_export.calls >= 1, "async-export.not-attempted")
    return response, spans[0], failed_export.calls


def channel_document(model_id: str, region: str) -> dict[str, object]:
    return {
        "channels": [
            {
                "channelId": "bedrock-compatibility",
                "tokenSha256": ConfiguredAiUsageReceiver.token_sha256(TOKEN),
                "tenantId": "compatibility",
                "integrationId": "aws-bedrock-compatibility",
                "provider": "aws.bedrock",
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
                "models": [model_id],
                "operations": ["chat"],
                "regions": [region],
                "instrumentationScopes": [SCOPE_NAME],
                "usageAttributes": {
                    "inputTokens": "gen_ai.usage.input_tokens",
                    "outputTokens": "gen_ai.usage.output_tokens",
                    "cacheReadInputTokens": "gen_ai.usage.cache_read.input_tokens",
                    "cacheWriteInputTokens": "gen_ai.usage.cache_write.input_tokens",
                    "reasoningOutputTokens": "gen_ai.usage.reasoning_tokens",
                    "zeroWhenAbsent": [],
                    "reportedBy": "provider",
                },
                "commercial": {
                    "serviceTier": "standard",
                    "routingMode": "in-region",
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
    response: Mapping[str, object],
    model_id: str,
    region: str,
) -> Mapping[str, object]:
    attributes = span.attributes
    require(span.kind == SpanKind.CLIENT, "instrumentation-span.kind")
    require(
        span.instrumentation_scope.name == SCOPE_NAME,
        "instrumentation-scope.unsupported",
    )
    require(
        span.instrumentation_scope.version == "0.65b0",
        "instrumentation-version.unexpected",
    )
    require(attributes.get("gen_ai.system") == "aws.bedrock", "provider-alias.missing")
    require(
        attributes.get("gen_ai.operation.name") == "chat"
        and attributes.get("gen_ai.request.model") == model_id,
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
    usage = response["usage"]
    assert isinstance(usage, Mapping)
    require(
        attributes.get("gen_ai.usage.input_tokens") == usage["inputTokens"]
        and attributes.get("gen_ai.usage.output_tokens") == usage["outputTokens"],
        "provider-token-totals.mismatch",
    )

    receiver = ConfiguredAiUsageReceiver.from_json(
        json.dumps(channel_document(model_id, region), separators=(",", ":"))
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
        and invocation.get("provider") == "aws.bedrock"
        and invocation.get("operationName") == "chat",
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


def compatibility_report(
    *, mode: str, model_id: str, region: str
) -> dict[str, object]:
    response, span, failed_export_calls = instrumented_call(
        mode=mode,
        model_id=model_id,
        region=region,
    )
    normalize_span(span, response=response, model_id=model_id, region=region)
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
            "region": region,
            "versions": {
                "boto3": version("boto3"),
                "botocore": version("botocore"),
                "instrumentation": version("opentelemetry-instrumentation-botocore"),
                "otelSdk": version("opentelemetry-sdk"),
            },
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    checks = [{"id": check_id, "status": "passed"} for check_id in CHECK_IDS]
    return {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "BedrockInstrumentationCompatibilityReport",
        "metadata": {
            "id": "bic_" + hashlib.sha256(identity).hexdigest()[:32],
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
                "boto3Version": version("boto3"),
                "botocoreVersion": version("botocore"),
                "instrumentationVersion": version(
                    "opentelemetry-instrumentation-botocore"
                ),
                "otelSdkVersion": version("opentelemetry-sdk"),
            },
            "profile": {
                "name": "otel-python-botocore-converse-v1",
                "provider": "aws.bedrock",
                "service": "bedrock-runtime",
                "operation": "Converse",
                "modelId": model_id,
                "region": region,
                "invocationTarget": (
                    "botocore-stubber" if mode == "offline" else "aws-bedrock"
                ),
                "instrumentationScope": SCOPE_NAME,
                "providerAttribute": "gen_ai.system",
                "contentCapture": False,
                "requestPath": "direct-to-provider",
                "telemetryPath": "asynchronous-otel",
            },
            "result": {
                "normalizedProvider": "aws.bedrock",
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
        == (profile["invocationTarget"] == "aws-bedrock")
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
                os.environ.get("IIP_BEDROCK_LIVE_TEST_ENABLED") == "true",
                "live-test.not-enabled",
            )
            model_id = os.environ.get("IIP_BEDROCK_MODEL_ID", "")
            region = os.environ.get("AWS_REGION", "")
            require(bool(model_id), "live-test.model-required")
            require(bool(region), "live-test.region-required")
        else:
            model_id = OFFLINE_MODEL
            region = "us-east-1"
        report = compatibility_report(
            mode=arguments.mode,
            model_id=model_id,
            region=region,
        )
        validate_report(report)
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(
            json.dumps(report, indent=2, sort_keys=False) + "\n",
            encoding="utf-8",
        )
    except Exception as error:  # pylint: disable=broad-exception-caught
        print(
            "bedrock.compatibility.failed:" + type(error).__name__,
            file=sys.stderr,
        )
        return 1
    print(
        "Bedrock instrumentation compatibility passed "
        f"({report['spec']['qualificationLevel']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
