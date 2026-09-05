#!/usr/bin/env python3
"""Generate and exercise the deterministic multi-provider AI FinOps slice."""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping


ROOT = Path(__file__).resolve().parents[1]
CHANNEL_TOKEN = "ai-finops-channel-token-0123456789abcdef"
CHANNEL_TOKEN_SHA256 = (
    "sha256:dcc4a1c70125fb81b9ba226ac4b3bb9d709b3c84c7d60eeffd87d8688d5f806a"
)
OPENAI_CHANNEL_TOKEN = "openai-ai-finops-channel-token-0123456789abcdef"
OPENAI_CHANNEL_TOKEN_SHA256 = (
    "sha256:b329187f7d36b57b5da0efe454a1229980d2718431070dde94050d4429af454a"
)
CONTROL_TOKEN = "ai-finops-local-operator-token-0123456789abcdef"
CONTROL_TOKEN_SHA256 = (
    "sha256:d52e86d5eeda090d11d9c961632390e6da1356df217d662abde728312befb25d"
)
KNOWN_MODEL = "example.foundation-model-v1:0"
UNKNOWN_MODEL = "unpriced.foundation-model-v1:0"
OPENAI_MODEL = "example-openai-model"
CATALOG_ID = "apc_11111111111111111111111111111111"
INSTRUMENTATION_SCOPE = (
    "opentelemetry.instrumentation.botocore.bedrock-runtime"
)
OPENAI_INSTRUMENTATION_SCOPE = "opentelemetry.util.genai.handler"


def _parse_anchor(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ValueError("ai-finops.fixture.anchor.invalid") from None
    if parsed.tzinfo is None:
        raise ValueError("ai-finops.fixture.anchor.invalid")
    normalized = parsed.astimezone(timezone.utc).replace(microsecond=0)
    if normalized.second != 0:
        raise ValueError("ai-finops.fixture.anchor.invalid")
    return normalized


def format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _windows(anchor: datetime) -> tuple[datetime, datetime, datetime]:
    current_end = anchor - timedelta(minutes=1)
    current_start = current_end - timedelta(minutes=2)
    baseline_start = current_start - timedelta(minutes=2)
    return baseline_start, current_start, current_end


def channel_configuration() -> Mapping[str, object]:
    return {
        "channels": [
            {
                "channelId": "bedrock-ai-finops-local",
                "tokenSha256": CHANNEL_TOKEN_SHA256,
                "tenantId": "local",
                "integrationId": "aws-bedrock-ai-finops-local",
                "provider": "aws.bedrock",
                "semanticConventionVersion": "1.37.0-development",
                "services": [
                    {
                        "otlpName": "support-assistant",
                        "serviceName": "support-assistant",
                        "serviceNamespace": "customer-experience",
                        "deploymentEnvironment": "ai-finops-demo",
                        "resourceRefs": [],
                    },
                    {
                        "otlpName": "research-assistant",
                        "serviceName": "research-assistant",
                        "serviceNamespace": "research",
                        "deploymentEnvironment": "ai-finops-demo",
                        "resourceRefs": [],
                    },
                ],
                "models": [KNOWN_MODEL, UNKNOWN_MODEL],
                "operations": ["chat"],
                "regions": ["us-east-1"],
                "instrumentationScopes": [INSTRUMENTATION_SCOPE],
                "usageAttributes": {
                    "inputTokens": "gen_ai.usage.input_tokens",
                    "outputTokens": "gen_ai.usage.output_tokens",
                    "cacheReadInputTokens": (
                        "gen_ai.usage.cache_read.input_tokens"
                    ),
                    "cacheWriteInputTokens": (
                        "gen_ai.usage.cache_write.input_tokens"
                    ),
                    "reasoningOutputTokens": "gen_ai.usage.reasoning_tokens",
                    "zeroWhenAbsent": [
                        "cacheReadInputTokens",
                        "cacheWriteInputTokens",
                        "reasoningOutputTokens",
                    ],
                    "reportedBy": "provider",
                },
                "commercial": {
                    "serviceTier": "standard",
                    "routingMode": "in-region",
                    "purchaseMode": "on-demand",
                },
                "limits": {
                    "maxRequestBytes": 1_048_576,
                    "maxSpans": 100,
                    "maxAttributesPerSpan": 64,
                    "maxAgeSeconds": 900,
                    "maxClockSkewSeconds": 30,
                    "maxProcessingSeconds": 10,
                },
            },
            {
                "channelId": "openai-ai-finops-local",
                "tokenSha256": OPENAI_CHANNEL_TOKEN_SHA256,
                "tenantId": "local",
                "integrationId": "openai-ai-finops-local",
                "provider": "openai",
                "semanticConventionVersion": "1.37.0-development",
                "services": [
                    {
                        "otlpName": "order-copilot",
                        "serviceName": "order-copilot",
                        "serviceNamespace": "commerce",
                        "deploymentEnvironment": "ai-finops-demo",
                        "resourceRefs": [],
                    }
                ],
                "models": [OPENAI_MODEL],
                "operations": ["chat"],
                "regions": ["global"],
                "instrumentationScopes": [OPENAI_INSTRUMENTATION_SCOPE],
                "usageAttributes": {
                    "inputTokens": "gen_ai.usage.input_tokens",
                    "outputTokens": "gen_ai.usage.output_tokens",
                    "cacheReadInputTokens": (
                        "gen_ai.usage.cache_read.input_tokens"
                    ),
                    "cacheWriteInputTokens": (
                        "gen_ai.usage.cache_creation.input_tokens"
                    ),
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
                    "maxSpans": 100,
                    "maxAttributesPerSpan": 64,
                    "maxAgeSeconds": 900,
                    "maxClockSkewSeconds": 30,
                    "maxProcessingSeconds": 10,
                },
            },
        ]
    }


def price_catalog_configuration(anchor: datetime) -> Mapping[str, object]:
    catalog = json.loads(
        (ROOT / "contracts" / "examples" / "ai-price-catalog.json").read_text(
            encoding="utf-8"
        )
    )
    catalog["metadata"]["publishedAt"] = format_timestamp(
        anchor - timedelta(minutes=10)
    )
    catalog["spec"]["source"]["retrievedAt"] = format_timestamp(
        anchor - timedelta(minutes=11)
    )
    catalog["spec"]["source"][
        "locator"
    ] = "urn:iip:pricing-fixture:multi-provider:v1"
    catalog["spec"]["source"]["contentHash"] = "sha256:" + ("3" * 64)
    catalog["spec"]["entries"][0]["effectiveFrom"] = format_timestamp(
        anchor - timedelta(days=1)
    )
    catalog["spec"]["entries"].append(
        {
            "id": "openai.example-openai-model.global.on-demand",
            "provider": "openai",
            "modelId": OPENAI_MODEL,
            "regions": ["global"],
            "serviceTiers": ["default"],
            "routingModes": ["global"],
            "purchaseModes": ["on-demand"],
            "effectiveFrom": format_timestamp(anchor - timedelta(days=1)),
            "rates": {
                "uncachedInputTokens": {
                    "priceSubunitsPerMillionTokens": 1_000_000_000
                },
                "cacheReadInputTokens": {
                    "priceSubunitsPerMillionTokens": 100_000_000
                },
                "cacheWriteInputTokens": {
                    "priceSubunitsPerMillionTokens": 1_000_000_000
                },
                "nonReasoningOutputTokens": {
                    "priceSubunitsPerMillionTokens": 4_000_000_000
                },
                "reasoningOutputTokens": {
                    "priceSubunitsPerMillionTokens": 4_000_000_000
                },
            },
        }
    )
    return {"catalogs": [catalog]}


def attribution_policy_configuration(anchor: datetime) -> Mapping[str, object]:
    policy = json.loads(
        (ROOT / "contracts" / "examples" / "ai-attribution-policy.json").read_text(
            encoding="utf-8"
        )
    )
    policy["metadata"]["publishedAt"] = format_timestamp(
        anchor - timedelta(minutes=10)
    )
    policy["spec"]["source"]["retrievedAt"] = format_timestamp(
        anchor - timedelta(minutes=11)
    )
    policy["spec"]["source"][
        "locator"
    ] = "urn:iip:test-fixture:ai-attribution-policy:multi-provider"
    policy["spec"]["source"]["contentHash"] = "sha256:" + ("4" * 64)
    policy["spec"]["rules"][0]["match"][
        "deploymentEnvironment"
    ] = "ai-finops-demo"
    policy["spec"]["rules"][0]["effectiveFrom"] = format_timestamp(
        anchor - timedelta(days=1)
    )
    policy["spec"]["rules"].append(
        {
            "id": "order-copilot-demo",
            "priority": 90,
            "match": {
                "serviceName": "order-copilot",
                "serviceNamespace": "commerce",
                "deploymentEnvironment": "ai-finops-demo",
            },
            "allocation": {
                "application": {
                    "id": "commerce-ai",
                    "name": "Commerce AI",
                },
                "team": {
                    "id": "commerce-platform",
                    "name": "Commerce Platform",
                },
            },
            "effectiveFrom": format_timestamp(anchor - timedelta(days=1)),
        }
    )
    return {"policies": [policy]}


def savings_profile_configuration(anchor: datetime) -> Mapping[str, object]:
    baseline_start, current_start, current_end = _windows(anchor)

    def profile(
        profile_id: str,
        provider: str,
        model_id: str,
        region: str,
        service_name: str,
    ) -> Mapping[str, object]:
        return {
            "profileId": profile_id,
            "tenantId": "local",
            "catalogId": CATALOG_ID,
            "costEngineVersion": "0.1.0",
            "scope": {
                "provider": provider,
                "modelId": model_id,
                "region": region,
                "serviceName": service_name,
                "deploymentEnvironment": "ai-finops-demo",
            },
            "baselineWindow": {
                "start": format_timestamp(baseline_start),
                "end": format_timestamp(current_start),
            },
            "currentWindow": {
                "start": format_timestamp(current_start),
                "end": format_timestamp(current_end),
            },
            "minimumRequestsPerWindow": 2,
            "growthThresholdBasisPoints": 2500,
            "maxRecordsPerWindow": 100,
            "evaluationGraceSeconds": 0,
        }

    return {
        "profiles": [
            profile(
                "support-assistant-context",
                "aws.bedrock",
                KNOWN_MODEL,
                "us-east-1",
                "support-assistant",
            ),
            profile(
                "research-assistant-coverage",
                "aws.bedrock",
                UNKNOWN_MODEL,
                "us-east-1",
                "research-assistant",
            ),
            profile(
                "order-copilot-coverage",
                "openai",
                OPENAI_MODEL,
                "global",
                "order-copilot",
            ),
        ]
    }


def _span_times(anchor: datetime) -> tuple[datetime, ...]:
    baseline_start, current_start, _current_end = _windows(anchor)
    return (
        baseline_start + timedelta(seconds=30),
        baseline_start + timedelta(seconds=90),
        current_start + timedelta(seconds=30),
        current_start + timedelta(seconds=90),
    )


def _finished_spans(
    anchor: datetime,
    *,
    model_id: str,
    service_name: str,
    input_tokens: tuple[int, int, int, int],
    provider_name: str = "aws.bedrock",
    service_namespace: str | None = None,
    region: str = "us-east-1",
    instrumentation_scope: str = INSTRUMENTATION_SCOPE,
    explicit_usage_breakdowns: bool = False,
    content_attribute: bool = False,
) -> tuple[object, ...]:
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )
    from opentelemetry.trace import SpanKind

    memory = InMemorySpanExporter()
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": service_name,
                "service.namespace": (
                    service_namespace
                    or (
                        "customer-experience"
                        if service_name == "support-assistant"
                        else "research"
                    )
                ),
                "deployment.environment.name": "ai-finops-demo",
                "cloud.region": region,
            }
        )
    )
    provider.add_span_processor(SimpleSpanProcessor(memory))
    tracer = provider.get_tracer(
        instrumentation_scope,
        "0.0.0-ai-finops-fixture",
    )
    for index, (started_at, tokens) in enumerate(
        zip(_span_times(anchor), input_tokens)
    ):
        attributes: dict[str, object] = {
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": model_id,
            "gen_ai.response.model": model_id,
            "gen_ai.usage.input_tokens": tokens,
            "gen_ai.usage.output_tokens": 100,
        }
        if provider_name == "aws.bedrock":
            # The Bedrock qualification profile intentionally exercises the
            # exact legacy provider alias emitted by the pinned instrumentor.
            attributes.update(
                {
                    "gen_ai.system": provider_name,
                    "aws.request_id": (
                        f"ai-finops-provider-{service_name}-{index}"
                    ),
                    "aws.retry_count": 0,
                }
            )
        else:
            attributes["gen_ai.provider.name"] = provider_name
        if explicit_usage_breakdowns:
            attributes.update(
                {
                    "gen_ai.usage.cache_read.input_tokens": 0,
                    "gen_ai.usage.cache_creation.input_tokens": 0,
                    "gen_ai.usage.reasoning_tokens": 0,
                }
            )
        if content_attribute:
            attributes["gen_ai.prompt"] = "must-never-cross-iip-boundary"
        started_ns = int(started_at.timestamp() * 1_000_000_000)
        span = tracer.start_span(
            f"chat {model_id}",
            kind=SpanKind.CLIENT,
            attributes=attributes,
            start_time=started_ns,
        )
        span.end(end_time=started_ns + 100_000_000)
    provider.force_flush(timeout_millis=5000)
    spans = tuple(memory.get_finished_spans())
    provider.shutdown()
    return spans


def send_fixture(
    anchor: datetime,
    collector_endpoint: str,
    openai_collector_endpoint: str,
    receiver_endpoint: str,
) -> None:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.trace.export import SpanExportResult

    exporter = OTLPSpanExporter(
        endpoint=collector_endpoint.rstrip("/") + "/v1/traces",
        timeout=5,
    )
    openai_exporter = OTLPSpanExporter(
        endpoint=openai_collector_endpoint.rstrip("/") + "/v1/traces",
        timeout=5,
    )
    known = _finished_spans(
        anchor,
        model_id=KNOWN_MODEL,
        service_name="support-assistant",
        input_tokens=(1200, 1200, 2400, 2400),
    )
    unknown = _finished_spans(
        anchor,
        model_id=UNKNOWN_MODEL,
        service_name="research-assistant",
        input_tokens=(800, 800, 800, 800),
    )
    openai = _finished_spans(
        anchor,
        model_id=OPENAI_MODEL,
        service_name="order-copilot",
        input_tokens=(600, 600, 600, 600),
        provider_name="openai",
        service_namespace="commerce",
        region="global",
        instrumentation_scope=OPENAI_INSTRUMENTATION_SCOPE,
        explicit_usage_breakdowns=True,
    )
    try:
        for batch in (known, unknown):
            if exporter.export(batch) is not SpanExportResult.SUCCESS:
                raise RuntimeError("ai-finops.fixture.export.failed")
        if openai_exporter.export(openai) is not SpanExportResult.SUCCESS:
            raise RuntimeError("ai-finops.fixture.openai-export.failed")
        # Let the Collector close the original batch before replaying the exact
        # same spans. Duplicate records inside one OTLP request are malformed;
        # redelivery of a previously committed request is idempotent.
        time.sleep(2)
        if exporter.export(known) is not SpanExportResult.SUCCESS:
            raise RuntimeError("ai-finops.fixture.replay.failed")
        if openai_exporter.export(openai) is not SpanExportResult.SUCCESS:
            raise RuntimeError("ai-finops.fixture.openai-replay.failed")
        time.sleep(2)
    finally:
        exporter.shutdown()
        openai_exporter.shutdown()

    content = _finished_spans(
        anchor,
        model_id=KNOWN_MODEL,
        service_name="support-assistant",
        input_tokens=(400, 400, 400, 400),
        content_attribute=True,
    )[:1]
    openai_content = _finished_spans(
        anchor,
        model_id=OPENAI_MODEL,
        service_name="order-copilot",
        input_tokens=(400, 400, 400, 400),
        provider_name="openai",
        service_namespace="commerce",
        region="global",
        instrumentation_scope=OPENAI_INSTRUMENTATION_SCOPE,
        explicit_usage_breakdowns=True,
        content_attribute=True,
    )[:1]
    for token, batch in (
        (CHANNEL_TOKEN, content),
        (OPENAI_CHANNEL_TOKEN, openai_content),
    ):
        privacy_probe = OTLPSpanExporter(
            endpoint=receiver_endpoint.rstrip("/") + "/v1/traces",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        try:
            if privacy_probe.export(batch) is not SpanExportResult.FAILURE:
                raise RuntimeError("ai-finops.fixture.content.accepted")
        finally:
            privacy_probe.shutdown()


def _json_url(
    url: str,
    *,
    headers: Mapping[str, str] | None = None,
) -> Mapping[str, object]:
    request = urllib.request.Request(url, headers=dict(headers or {}))
    with urllib.request.urlopen(request, timeout=5) as response:
        payload = json.loads(response.read())
    if not isinstance(payload, dict):
        raise RuntimeError("ai-finops.fixture.response.invalid")
    return payload


def _prometheus_query(endpoint: str, expression: str) -> list[Mapping[str, object]]:
    query = urllib.parse.urlencode({"query": expression})
    payload = _json_url(endpoint.rstrip("/") + "/api/v1/query?" + query)
    data = payload.get("data")
    result = data.get("result") if isinstance(data, dict) else None
    if payload.get("status") != "success" or not isinstance(result, list):
        raise RuntimeError("ai-finops.fixture.prometheus.invalid")
    return [item for item in result if isinstance(item, dict)]


def _scalar(endpoint: str, expression: str) -> float | None:
    result = _prometheus_query(endpoint, expression)
    if not result:
        return None
    value = result[0].get("value")
    if not isinstance(value, list) or len(value) != 2:
        raise RuntimeError("ai-finops.fixture.prometheus.invalid")
    return float(value[1])


def _database_snapshot(database_url: str) -> Mapping[str, object]:
    import psycopg

    with psycopg.connect(database_url) as connection:
        usage = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT document
                FROM iip.ai_usage_records
                WHERE tenant_id = 'local'
                ORDER BY usage_record_id
                """
            ).fetchall()
        )
        costs = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT document
                FROM iip.ai_cost_records
                WHERE tenant_id = 'local'
                ORDER BY cost_record_id
                """
            ).fetchall()
        )
        attributions = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT document
                FROM iip.ai_usage_attributions
                WHERE tenant_id = 'local'
                ORDER BY attribution_record_id
                """
            ).fetchall()
        )
        findings = tuple(
            row[0]
            for row in connection.execute(
                """
                SELECT document
                FROM iip.ai_savings_findings
                WHERE tenant_id = 'local'
                ORDER BY finding_id
                """
            ).fetchall()
        )
    return {
        "usage": usage,
        "attributions": attributions,
        "costs": costs,
        "findings": findings,
    }


def _assert_equal(actual: object, expected: object, name: str) -> None:
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected!r}, received {actual!r}")


def verify_fixture(
    *,
    anchor: datetime,
    database_url: str,
    api_endpoint: str,
    prometheus_endpoint: str,
    grafana_endpoint: str,
    loki_endpoint: str,
    timeout_seconds: int,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            snapshot = _database_snapshot(database_url)
            _assert_equal(len(snapshot["usage"]), 12, "usage ledger count")
            _assert_equal(
                len(snapshot["attributions"]),
                12,
                "attribution ledger count",
            )
            _assert_equal(len(snapshot["costs"]), 12, "cost ledger count")
            _assert_equal(len(snapshot["findings"]), 1, "finding ledger count")

            serialized = json.dumps(snapshot, sort_keys=True)
            if "must-never-cross-iip-boundary" in serialized:
                raise AssertionError("content crossed the metadata-only boundary")

            attributions = snapshot["attributions"]
            assert isinstance(attributions, tuple)
            allocation_statuses = [
                item["spec"]["resolution"]["status"]
                for item in attributions
            ]
            _assert_equal(
                allocation_statuses.count("allocated"),
                8,
                "allocated usage records",
            )
            _assert_equal(
                allocation_statuses.count("unallocated"),
                4,
                "visible unallocated usage records",
            )

            costs = snapshot["costs"]
            assert isinstance(costs, tuple)
            statuses = [item["spec"]["result"]["costStatus"] for item in costs]
            _assert_equal(statuses.count("priced"), 8, "priced records")
            _assert_equal(statuses.count("unpriced"), 4, "unpriced records")
            finding = snapshot["findings"][0]
            _assert_equal(
                finding["spec"]["potentialSavings"]["amountSubunits"],
                7_200_000,
                "evidence-backed saving",
            )
            priced_total = sum(
                item["spec"]["result"].get("totalSubunits", 0)
                for item in costs
            )

            _assert_equal(
                _scalar(prometheus_endpoint, "sum(iip_ai_usage_requests)"),
                6.0,
                "current-window usage",
            )
            _assert_equal(
                _scalar(prometheus_endpoint, "sum(iip_ai_cost_amount)"),
                19_400_000.0,
                "current-window calculated cost",
            )
            _assert_equal(
                _scalar(
                    prometheus_endpoint,
                    'sum(iip_ai_cost_requests{iip_ai_cost_status="unpriced"})',
                ),
                2.0,
                "visible unpriced coverage",
            )
            _assert_equal(
                _scalar(
                    prometheus_endpoint,
                    'max(iip_ai_context_growth_change{service_name="support-assistant"})',
                ),
                10_000.0,
                "context growth",
            )
            _assert_equal(
                _scalar(prometheus_endpoint, "sum(iip_ai_savings_potential_amount)"),
                7_200_000.0,
                "potential saving metric",
            )
            provider_series = _prometheus_query(
                prometheus_endpoint,
                "sum by (gen_ai_provider_name) (iip_ai_usage_requests)",
            )
            provider_usage = {
                item.get("metric", {}).get("gen_ai_provider_name"): float(
                    item.get("value", [0, "0"])[1]
                )
                for item in provider_series
                if isinstance(item.get("metric"), dict)
                and isinstance(item.get("value"), list)
            }
            _assert_equal(
                provider_usage,
                {"aws.bedrock": 4.0, "openai": 2.0},
                "provider-neutral usage coverage",
            )
            for dimension, protected_ids in (
                ("application", ("support-experience", "commerce-ai")),
                ("team", ("customer-experience", "commerce-platform")),
            ):
                _assert_equal(
                    _scalar(
                        prometheus_endpoint,
                        "sum(iip_ai_allocation_requests"
                        f'{{iip_ai_allocation_dimension="{dimension}"}})',
                    ),
                    12.0,
                    f"{dimension} allocation coverage",
                )
                for protected_id in protected_ids:
                    _assert_equal(
                        _scalar(
                            prometheus_endpoint,
                            "sum(iip_ai_allocation_requests"
                            f'{{iip_ai_{dimension}_id="{protected_id}"}})',
                        ),
                        4.0,
                        f"protected {dimension} allocation",
                    )
                _assert_equal(
                    _scalar(
                        prometheus_endpoint,
                        "sum(iip_ai_allocation_cost_amount"
                        f'{{iip_ai_allocation_dimension="{dimension}"}})',
                    ),
                    float(priced_total),
                    f"{dimension} allocated cost",
                )
            series = _prometheus_query(
                prometheus_endpoint,
                "{__name__=~\"iip_ai_(usage_requests|allocation_requests)\"}",
            )
            labels = tuple(
                key
                for item in series
                for key in (
                    item.get("metric", {}).keys()
                    if isinstance(item.get("metric"), dict)
                    else ()
                )
            )
            if any(
                fragment in label
                for label in labels
                for fragment in ("trace", "span", "request_id", "prompt")
            ):
                raise AssertionError("high-cardinality or content label exported")

            report_start = format_timestamp(anchor - timedelta(minutes=10))
            report_end = format_timestamp(anchor)
            for group_by, protected_ids in (
                ("application", {"support-experience", "commerce-ai"}),
                ("team", {"customer-experience", "commerce-platform"}),
            ):
                parameters = urllib.parse.urlencode(
                    {
                        "start": report_start,
                        "end": report_end,
                        "groupBy": group_by,
                    }
                )
                report = _json_url(
                    api_endpoint.rstrip("/")
                    + "/v1/ai/economics/allocation?"
                    + parameters,
                    headers={"Authorization": f"Bearer {CONTROL_TOKEN}"},
                )
                coverage = report.get("spec", {}).get("coverage", {})
                _assert_equal(coverage.get("usageRecords"), 12, "API usage coverage")
                _assert_equal(coverage.get("allocatedRecords"), 8, "API allocated coverage")
                _assert_equal(coverage.get("unallocatedRecords"), 4, "API unallocated coverage")
                groups = report.get("spec", {}).get("groups", [])
                allocated_ids = {
                    item.get("dimension", {}).get("id")
                    for item in groups
                    if item.get("allocationStatus") == "allocated"
                }
                _assert_equal(
                    allocated_ids,
                    protected_ids,
                    f"API protected {group_by} group",
                )

            dashboard = _json_url(
                grafana_endpoint.rstrip("/")
                + "/api/dashboards/uid/iip-ai-finops"
            )
            document = dashboard.get("dashboard")
            if not isinstance(document, dict):
                raise AssertionError("Grafana dashboard is not provisioned")
            _assert_equal(
                document.get("title"),
                "IIP AI FinOps — Multi-provider V0",
                "Grafana dashboard title",
            )
            with urllib.request.urlopen(
                loki_endpoint.rstrip("/") + "/ready",
                timeout=5,
            ) as response:
                if response.status != 200:
                    raise AssertionError("Loki is not ready")
            return
        except Exception as error:
            last_error = error
            time.sleep(1)
    raise RuntimeError("ai-finops.fixture.verification.timeout") from last_error


def _compact(document: Mapping[str, object]) -> str:
    return json.dumps(document, separators=(",", ":"), sort_keys=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    configuration = subparsers.add_parser("configuration")
    configuration.add_argument(
        "name",
        choices=(
            "channel",
            "attribution",
            "catalog",
            "profiles",
            "token",
            "openai-token",
        ),
    )
    configuration.add_argument("--anchor", required=True)

    send = subparsers.add_parser("send")
    send.add_argument("--anchor", required=True)
    send.add_argument("--collector", required=True)
    send.add_argument("--openai-collector", required=True)
    send.add_argument("--receiver", required=True)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--anchor", required=True)
    verify.add_argument("--database", required=True)
    verify.add_argument("--api", required=True)
    verify.add_argument("--prometheus", required=True)
    verify.add_argument("--grafana", required=True)
    verify.add_argument("--loki", required=True)
    verify.add_argument("--timeout-seconds", type=int, default=60)

    arguments = parser.parse_args()
    if arguments.command == "configuration":
        anchor = _parse_anchor(arguments.anchor)
        documents = {
            "channel": channel_configuration(),
            "attribution": attribution_policy_configuration(anchor),
            "catalog": price_catalog_configuration(anchor),
            "profiles": savings_profile_configuration(anchor),
        }
        if arguments.name == "token":
            print(CHANNEL_TOKEN)
        elif arguments.name == "openai-token":
            print(OPENAI_CHANNEL_TOKEN)
        else:
            print(_compact(documents[arguments.name]))
        return
    if arguments.command == "send":
        send_fixture(
            _parse_anchor(arguments.anchor),
            arguments.collector,
            arguments.openai_collector,
            arguments.receiver,
        )
        return
    verify_fixture(
        anchor=_parse_anchor(arguments.anchor),
        database_url=arguments.database,
        api_endpoint=arguments.api,
        prometheus_endpoint=arguments.prometheus,
        grafana_endpoint=arguments.grafana,
        loki_endpoint=arguments.loki,
        timeout_seconds=arguments.timeout_seconds,
    )


if __name__ == "__main__":
    main()
