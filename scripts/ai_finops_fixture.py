#!/usr/bin/env python3
"""Generate and exercise the deterministic Bedrock-shaped AI FinOps slice."""

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
KNOWN_MODEL = "example.foundation-model-v1:0"
UNKNOWN_MODEL = "unpriced.foundation-model-v1:0"
CATALOG_ID = "apc_11111111111111111111111111111111"
INSTRUMENTATION_SCOPE = (
    "opentelemetry.instrumentation.botocore.bedrock-runtime"
)


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
            }
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
    catalog["spec"]["entries"][0]["effectiveFrom"] = format_timestamp(
        anchor - timedelta(days=1)
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
    policy["spec"]["rules"][0]["match"][
        "deploymentEnvironment"
    ] = "ai-finops-demo"
    policy["spec"]["rules"][0]["effectiveFrom"] = format_timestamp(
        anchor - timedelta(days=1)
    )
    return {"policies": [policy]}


def savings_profile_configuration(anchor: datetime) -> Mapping[str, object]:
    baseline_start, current_start, current_end = _windows(anchor)

    def profile(
        profile_id: str,
        model_id: str,
        service_name: str,
    ) -> Mapping[str, object]:
        return {
            "profileId": profile_id,
            "tenantId": "local",
            "catalogId": CATALOG_ID,
            "costEngineVersion": "0.1.0",
            "scope": {
                "provider": "aws.bedrock",
                "modelId": model_id,
                "region": "us-east-1",
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
                KNOWN_MODEL,
                "support-assistant",
            ),
            profile(
                "research-assistant-coverage",
                UNKNOWN_MODEL,
                "research-assistant",
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
                    "customer-experience"
                    if service_name == "support-assistant"
                    else "research"
                ),
                "deployment.environment.name": "ai-finops-demo",
                "cloud.region": "us-east-1",
            }
        )
    )
    provider.add_span_processor(SimpleSpanProcessor(memory))
    tracer = provider.get_tracer(
        INSTRUMENTATION_SCOPE,
        "0.0.0-ai-finops-fixture",
    )
    for index, (started_at, tokens) in enumerate(
        zip(_span_times(anchor), input_tokens)
    ):
        attributes: dict[str, object] = {
            "gen_ai.system": "aws.bedrock",
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": model_id,
            "gen_ai.response.model": model_id,
            "gen_ai.usage.input_tokens": tokens,
            "gen_ai.usage.output_tokens": 100,
            "aws.request_id": f"ai-finops-provider-{service_name}-{index}",
            "aws.retry_count": 0,
        }
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
    try:
        for batch in (known, unknown):
            if exporter.export(batch) is not SpanExportResult.SUCCESS:
                raise RuntimeError("ai-finops.fixture.export.failed")
        # Let the Collector close the original batch before replaying the exact
        # same spans. Duplicate records inside one OTLP request are malformed;
        # redelivery of a previously committed request is idempotent.
        time.sleep(2)
        if exporter.export(known) is not SpanExportResult.SUCCESS:
            raise RuntimeError("ai-finops.fixture.replay.failed")
        time.sleep(2)
    finally:
        exporter.shutdown()

    content = _finished_spans(
        anchor,
        model_id=KNOWN_MODEL,
        service_name="support-assistant",
        input_tokens=(400, 400, 400, 400),
        content_attribute=True,
    )[:1]
    privacy_probe = OTLPSpanExporter(
        endpoint=receiver_endpoint.rstrip("/") + "/v1/traces",
        headers={"Authorization": f"Bearer {CHANNEL_TOKEN}"},
        timeout=5,
    )
    try:
        if privacy_probe.export(content) is not SpanExportResult.FAILURE:
            raise RuntimeError("ai-finops.fixture.content.accepted")
    finally:
        privacy_probe.shutdown()


def _json_url(url: str) -> Mapping[str, object]:
    with urllib.request.urlopen(url, timeout=5) as response:
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
    database_url: str,
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
            _assert_equal(len(snapshot["usage"]), 8, "usage ledger count")
            _assert_equal(
                len(snapshot["attributions"]),
                8,
                "attribution ledger count",
            )
            _assert_equal(len(snapshot["costs"]), 8, "cost ledger count")
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
                4,
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
            _assert_equal(statuses.count("priced"), 4, "priced records")
            _assert_equal(statuses.count("unpriced"), 4, "unpriced records")
            finding = snapshot["findings"][0]
            _assert_equal(
                finding["spec"]["potentialSavings"]["amountSubunits"],
                7_200_000,
                "evidence-backed saving",
            )

            _assert_equal(
                _scalar(prometheus_endpoint, "sum(iip_ai_usage_requests)"),
                4.0,
                "current-window usage",
            )
            _assert_equal(
                _scalar(prometheus_endpoint, "sum(iip_ai_cost_amount)"),
                17_400_000.0,
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
            series = _prometheus_query(
                prometheus_endpoint,
                "iip_ai_usage_requests",
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

            dashboard = _json_url(
                grafana_endpoint.rstrip("/")
                + "/api/dashboards/uid/iip-ai-finops"
            )
            document = dashboard.get("dashboard")
            if not isinstance(document, dict):
                raise AssertionError("Grafana dashboard is not provisioned")
            _assert_equal(
                document.get("title"),
                "IIP AI FinOps — Bedrock V0",
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
        choices=("channel", "attribution", "catalog", "profiles", "token"),
    )
    configuration.add_argument("--anchor", required=True)

    send = subparsers.add_parser("send")
    send.add_argument("--anchor", required=True)
    send.add_argument("--collector", required=True)
    send.add_argument("--receiver", required=True)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--database", required=True)
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
        print(
            CHANNEL_TOKEN
            if arguments.name == "token"
            else _compact(documents[arguments.name])
        )
        return
    if arguments.command == "send":
        send_fixture(
            _parse_anchor(arguments.anchor),
            arguments.collector,
            arguments.receiver,
        )
        return
    verify_fixture(
        database_url=arguments.database,
        prometheus_endpoint=arguments.prometheus,
        grafana_endpoint=arguments.grafana,
        loki_endpoint=arguments.loki,
        timeout_seconds=arguments.timeout_seconds,
    )


if __name__ == "__main__":
    main()
