#!/usr/bin/env python3
"""Run and verify bounded sustained load through the local AI FinOps slice."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import psycopg
from jsonschema import Draft202012Validator, FormatChecker
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)
from opentelemetry.proto.trace.v1.trace_pb2 import Span, Status
from google.protobuf.message import DecodeError
from google.rpc.status_pb2 import Status as RpcStatus
from referencing import Registry, Resource

import ai_finops_fixture
from iip.application.attribute_ai_usage import (
    ENGINE_VERSION as ATTRIBUTION_ENGINE_VERSION,
)
from iip.application.calculate_ai_cost import ENGINE_VERSION as COST_ENGINE_VERSION


ROOT = Path(__file__).resolve().parents[1]
PROFILE_SCHEMA = (
    ROOT / "contracts/schemas/ai-finops-sustained-load-profile.schema.json"
)
REPORT_SCHEMA = (
    ROOT
    / "contracts/schemas/ai-finops-sustained-load-qualification-report.schema.json"
)
API_VERSION = "iip.dev/v1alpha1"
PROFILE_KIND = "AiFinopsSustainedLoadProfile"
REPORT_KIND = "AiFinopsSustainedLoadQualificationReport"
QUALIFICATION_LEVEL = "local-ai-finops-sustained-load-v1"
QUALIFICATION_BOUNDARY = "single-host-docker-synthetic-ai-economics-load"
EXPECTED_COLLECTOR_IMAGE_DIGEST = (
    "sha256:c5918f78992ee73b0d6f0e599423ac5ec52dd5d9726733114d6eca53d5a32ed5"
)
ATTRIBUTION_POLICY_ID = "aap_22222222222222222222222222222222"
ATTRIBUTION_POLICY_VERSION = "2026-09-05.1"
PRICE_CATALOG_ID = "apc_11111111111111111111111111111111"
PRICE_CATALOG_VERSION = "2026-09-05.1"
MAX_SPANS = 9_984
PROFILE_ID = re.compile(r"^afslp_[a-f0-9]{32}$")
REPORT_ID = re.compile(r"^afslq_[a-f0-9]{32}$")
REVISION = re.compile(r"^[a-f0-9]{40,64}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
PLATFORM = re.compile(r"^[a-z0-9_.-]+/[a-zA-Z0-9_.-]+$")
TRACE_PREFIX = re.compile(r"^[a-f0-9]{24}$")
SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
MIGRATION = re.compile(r"^[0-9]{4}_[a-z0-9_]+\.sql$")
MAX_PROFILE_BYTES = 128 * 1024
MAX_REPORT_BYTES = 4 * 1024 * 1024
DATABASE_CONNECT_TIMEOUT_SECONDS = 5
DATABASE_STATEMENT_TIMEOUT_MILLISECONDS = 5_000
DATABASE_SNAPSHOT_QUERY_COUNT = 4
DATABASE_SNAPSHOT_BOUND_MILLISECONDS = (
    DATABASE_CONNECT_TIMEOUT_SECONDS * 1000
    + DATABASE_SNAPSHOT_QUERY_COUNT * DATABASE_STATEMENT_TIMEOUT_MILLISECONDS
)
MONITOR_JOIN_TIMEOUT_SECONDS = DATABASE_SNAPSHOT_BOUND_MILLISECONDS / 1000 + 1
OBSERVABILITY_REQUEST_TIMEOUT_SECONDS = 5
CHECK_IDS = (
    "source-binding",
    "profile-binding",
    "compose-configuration",
    "all-components-healthy",
    "bounded-load-volume",
    "fixed-rate-scheduler",
    "collector-acceptance",
    "usage-ledger-persistence",
    "replay-idempotency",
    "attribution-completion",
    "cost-completion",
    "priced-coverage",
    "export-p95-latency",
    "attribution-p95-latency",
    "cost-p95-latency",
    "pipeline-drain",
    "prometheus-convergence",
    "grafana-dashboard",
    "metadata-only-boundaries",
)
LIMITATIONS = (
    "synthetic-provider-spans",
    "test-fixture-pricing",
    "single-tenant-single-host-docker-runtime",
    "customer-collector-pki-and-network-not-qualified",
    "live-provider-private-prices-and-invoice-not-qualified",
    "burst-failure-regional-ha-and-long-window-slo-not-qualified",
    "customer-workload-representativeness-not-approved",
)
PROVIDERS = ("aws.bedrock", "openai")
PROHIBITED_RETAINED_KEYS = frozenset(
    {
        "actorId",
        "authorization",
        "channelId",
        "credential",
        "endpoint",
        "modelId",
        "password",
        "prompt",
        "requestId",
        "response",
        "secret",
        "serviceName",
        "spanId",
        "tenantId",
        "token",
        "traceId",
    }
)
CONTENT_SENTINEL = "must-never-cross-sustained-load-boundary"


class AiFinopsSustainedLoadError(RuntimeError):
    """Stable local sustained-load qualification failure."""


def _fail(code: str) -> None:
    raise AiFinopsSustainedLoadError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _integer(value: object, minimum: int, maximum: int, code: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        _fail(code)
    return value


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        _fail("ai-finops-sustained-load.time.invalid")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: object, code: str) -> datetime:
    if not isinstance(value, str):
        _fail(code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        _fail(code)
    return parsed.astimezone(timezone.utc)


def _schema(path: Path, code: str) -> Mapping[str, Any]:
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")), code)
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail(code)


def _validate_schema(document: object, path: Path, code: str) -> None:
    profile_schema = _schema(
        PROFILE_SCHEMA, "ai-finops-sustained-load.schema.unavailable"
    )
    registry = Registry().with_resource(
        str(profile_schema["$id"]), Resource.from_contents(profile_schema)
    )
    errors = sorted(
        Draft202012Validator(
            _schema(path, "ai-finops-sustained-load.schema.unavailable"),
            format_checker=FormatChecker(),
            registry=registry,
        ).iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        _fail(code)


def _profile_identifier(
    metadata_without_id: Mapping[str, Any], spec: Mapping[str, Any]
) -> str:
    return "afslp_" + hashlib.sha256(
        _canonical({"metadata": metadata_without_id, "spec": spec})
    ).hexdigest()[:32]


def _report_identifier(
    metadata_without_id: Mapping[str, Any], spec: Mapping[str, Any]
) -> str:
    return "afslq_" + hashlib.sha256(
        _canonical({"metadata": metadata_without_id, "spec": spec})
    ).hexdigest()[:32]


def _profile_values(
    profile: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    spec = _mapping(profile.get("spec"), "ai-finops-sustained-load.profile.invalid")
    return (
        _mapping(
            spec.get("workload"), "ai-finops-sustained-load.profile.invalid"
        ),
        _mapping(
            spec.get("objectives"), "ai-finops-sustained-load.profile.invalid"
        ),
    )


def validate_profile(profile: Mapping[str, Any]) -> None:
    code = "ai-finops-sustained-load.profile.invalid"
    _validate_schema(profile, PROFILE_SCHEMA, code)
    metadata = _mapping(profile.get("metadata"), code)
    spec = _mapping(profile.get("spec"), code)
    workload, _objectives = _profile_values(profile)
    without_id = dict(metadata)
    identifier = without_id.pop("id", None)
    duration = int(workload["durationSeconds"])
    rate = int(workload["spansPerSecond"])
    total = duration * rate
    replay = total * int(workload["replayBasisPoints"]) // 10_000
    if (
        profile.get("apiVersion") != API_VERSION
        or profile.get("kind") != PROFILE_KIND
        or spec.get("qualificationLevel") != QUALIFICATION_LEVEL
        or spec.get("providers") != list(PROVIDERS)
        or spec.get("distribution") != "round-robin-equal"
        or not 1 <= total <= MAX_SPANS
        or total % len(PROVIDERS) != 0
        or not 1 <= replay <= total
        or not isinstance(identifier, str)
        or PROFILE_ID.fullmatch(identifier) is None
        or identifier != _profile_identifier(without_id, spec)
    ):
        _fail(code)


def load_profile(path: Path) -> Mapping[str, Any]:
    selected = _read_json(
        path,
        maximum_bytes=MAX_PROFILE_BYTES,
        code="ai-finops-sustained-load.profile.unreadable",
    )
    validate_profile(selected)
    return selected


def _percentile(values: Sequence[int], percentile: int) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * percentile / 100) - 1)]


def _latency(values: Sequence[int]) -> dict[str, int | None]:
    return {
        "p50Milliseconds": _percentile(values, 50),
        "p95Milliseconds": _percentile(values, 95),
        "p99Milliseconds": _percentile(values, 99),
        "maximumMilliseconds": max(values) if values else None,
    }


@dataclass(frozen=True)
class ExportAttempt:
    provider: str
    success: bool
    duration_milliseconds: int
    payload: bytes = b""


@dataclass(frozen=True)
class RawGeneratorResult:
    started_at: datetime
    completed_at: datetime
    actual_duration_milliseconds: int
    target_spans: int
    attempts: tuple[ExportAttempt, ...]
    scheduler_missed_spans: int
    scheduler_lags_milliseconds: tuple[int, ...]


def _run_fixed_rate(
    *,
    duration_seconds: int,
    spans_per_second: int,
    producer_concurrency: int,
    maximum_scheduler_lag_milliseconds: int,
    sender_factory: Callable[[], Callable[[int], ExportAttempt]],
    on_started: Callable[[], None] | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> RawGeneratorResult:
    target = duration_seconds * spans_per_second
    if target < 1:
        _fail("ai-finops-sustained-load.generator.target.invalid")
    barrier = threading.Barrier(producer_concurrency + 1, timeout=30)
    cancelled = threading.Event()
    start: dict[str, float] = {}

    def worker(
        index: int,
    ) -> tuple[list[tuple[int, ExportAttempt]], int, list[tuple[int, int]]]:
        try:
            sender = sender_factory()
            barrier.wait()
        except Exception as error:
            cancelled.set()
            barrier.abort()
            raise AiFinopsSustainedLoadError(
                "ai-finops-sustained-load.generator.start.failed"
            ) from error
        attempts: list[tuple[int, ExportAttempt]] = []
        missed = 0
        lags: list[tuple[int, int]] = []
        for slot in range(index, target, producer_concurrency):
            if cancelled.is_set():
                break
            due = start["monotonic"] + slot / spans_per_second
            current = monotonic()
            if current < due:
                delay = due - current
                if sleeper is time.sleep:
                    if cancelled.wait(delay):
                        break
                else:
                    sleeper(delay)
                    if cancelled.is_set():
                        break
                current = monotonic()
            lag = max(0, math.ceil((current - due) * 1000))
            lags.append((slot, lag))
            if lag > maximum_scheduler_lag_milliseconds:
                missed += 1
                continue
            try:
                attempts.append((slot, sender(slot)))
            except Exception:
                attempts.append(
                    (
                        slot,
                        ExportAttempt(PROVIDERS[slot % len(PROVIDERS)], False, 0),
                    )
                )
        return attempts, missed, lags

    executor = ThreadPoolExecutor(
        max_workers=producer_concurrency,
        thread_name_prefix="iip-ai-finops-sustained-load",
    )
    futures = [executor.submit(worker, index) for index in range(producer_concurrency)]
    try:
        start["monotonic"] = monotonic()
        started_at = clock()
        if on_started is not None:
            on_started()
        barrier.wait()
        results = [future.result() for future in futures]
        completed_mono = monotonic()
        completed_at = clock()
    except BaseException as error:
        cancelled.set()
        barrier.abort()
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        _fail("ai-finops-sustained-load.generator.failed")
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    attempts = tuple(
        item
        for _slot, item in sorted(
            (item for result in results for item in result[0]),
            key=lambda selected: selected[0],
        )
    )
    missed = sum(result[1] for result in results)
    lags = tuple(
        lag
        for _slot, lag in sorted(
            (item for result in results for item in result[2]),
            key=lambda selected: selected[0],
        )
    )
    if len(attempts) + missed != target or len(lags) != target:
        _fail("ai-finops-sustained-load.generator.accounting.invalid")
    return RawGeneratorResult(
        started_at=started_at,
        completed_at=completed_at,
        actual_duration_milliseconds=max(
            1, math.ceil((completed_mono - start["monotonic"]) * 1000)
        ),
        target_spans=target,
        attempts=attempts,
        scheduler_missed_spans=missed,
        scheduler_lags_milliseconds=lags,
    )


@dataclass(frozen=True)
class DatabaseSnapshot:
    usage_records: int
    attribution_records: int
    cost_records: int
    priced_cost_records: int
    unpriced_cost_records: int
    provider_counts: Mapping[str, tuple[int, int, int, int]]
    attribution_latencies_milliseconds: tuple[int, ...]
    cost_latencies_milliseconds: tuple[int, ...]
    content_preserved: bool
    cohort_isolation_preserved: bool

    @property
    def attribution_backlog(self) -> int:
        return max(0, self.usage_records - self.attribution_records)

    @property
    def cost_backlog(self) -> int:
        return max(0, self.usage_records - self.cost_records)


@dataclass(frozen=True)
class QualificationRun:
    generator: RawGeneratorResult
    replay_scheduled: int
    replay_attempts: tuple[ExportAttempt, ...]
    usage_before_replay: int
    usage_after_replay: int
    final_snapshot: DatabaseSnapshot
    peak_attribution_backlog: int
    peak_cost_backlog: int
    backlog_sample_count: int
    drain_milliseconds: int
    prometheus_usage: int
    prometheus_cost: int
    prometheus_converged: bool
    grafana_available: bool
    prohibited_labels_absent: bool
    content_rejected: bool
    completed_at: datetime
    actual_duration_milliseconds: int


class _DenyRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        del req, fp, code, msg, headers, newurl
        return None


def _local_origin(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        _fail("ai-finops-sustained-load.endpoint.invalid")
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        _fail("ai-finops-sustained-load.endpoint.invalid")
    return f"http://{parsed.hostname}:{port}"


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _DenyRedirects(),
        urllib.request.HTTPHandler(),
    )


def _post_protobuf(
    opener: urllib.request.OpenerDirector,
    *,
    url: str,
    payload: bytes,
    timeout_milliseconds: int,
    token: str | None = None,
) -> tuple[bool, int]:
    headers = {
        "Content-Type": "application/x-protobuf",
        "Connection": "close",
    }
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    started = time.monotonic()
    try:
        with opener.open(request, timeout=timeout_milliseconds / 1000) as response:
            body = response.read(1024 * 1024 + 1)
            content_type = response.headers.get_content_type()
            success = False
            if (
                response.status == 200
                and content_type == "application/x-protobuf"
                and len(body) <= 1024 * 1024
            ):
                try:
                    parsed = ExportTraceServiceResponse.FromString(body)
                except DecodeError:
                    pass
                else:
                    success = parsed.partial_success.rejected_spans == 0
    except urllib.error.HTTPError as error:
        error.read(1024 * 1024 + 1)
        error.close()
        success = False
    except (OSError, urllib.error.URLError):
        success = False
    elapsed = max(0, math.ceil((time.monotonic() - started) * 1000))
    return success, elapsed


def _attribute(container: Any, key: str, value: str | int) -> None:
    item = container.add(key=key)
    if isinstance(value, int):
        item.value.int_value = value
    else:
        item.value.string_value = value


def _trace_payload(
    *,
    provider: str,
    identity: bytes,
    timestamp_ns: int,
    content_probe: bool = False,
) -> bytes:
    request = ExportTraceServiceRequest()
    resource_spans = request.resource_spans.add()
    if provider == "aws.bedrock":
        service_name = "support-assistant"
        namespace = "customer-experience"
        region = "us-east-1"
        model = ai_finops_fixture.KNOWN_MODEL
        scope = ai_finops_fixture.INSTRUMENTATION_SCOPE
        provider_key = "gen_ai.system"
    elif provider == "openai":
        service_name = "order-copilot"
        namespace = "commerce"
        region = "global"
        model = ai_finops_fixture.OPENAI_MODEL
        scope = ai_finops_fixture.OPENAI_INSTRUMENTATION_SCOPE
        provider_key = "gen_ai.provider.name"
    else:
        _fail("ai-finops-sustained-load.provider.invalid")
    for key, value in (
        ("service.name", service_name),
        ("service.namespace", namespace),
        ("deployment.environment.name", "ai-finops-demo"),
        ("cloud.region", region),
    ):
        _attribute(resource_spans.resource.attributes, key, value)
    scope_spans = resource_spans.scope_spans.add()
    scope_spans.scope.name = scope
    scope_spans.scope.version = "0.0.0-ai-finops-sustained-load"
    span = scope_spans.spans.add()
    span.trace_id = identity[:16]
    span.span_id = identity[16:24]
    span.name = f"chat {model}"
    span.kind = Span.SPAN_KIND_CLIENT
    span.start_time_unix_nano = timestamp_ns
    span.end_time_unix_nano = timestamp_ns + 1_000_000
    span.status.code = Status.STATUS_CODE_OK
    for key, value in (
        (provider_key, provider),
        ("gen_ai.operation.name", "chat"),
        ("gen_ai.request.model", model),
        ("gen_ai.response.model", model),
        ("gen_ai.usage.input_tokens", 100),
        ("gen_ai.usage.output_tokens", 20),
        ("gen_ai.usage.cache_read.input_tokens", 0),
        ("gen_ai.usage.cache_creation.input_tokens", 0),
        ("gen_ai.usage.reasoning.output_tokens", 0),
    ):
        _attribute(span.attributes, key, value)
    if provider == "aws.bedrock":
        _attribute(span.attributes, "aws.request_id", "iip-load-" + identity.hex())
        _attribute(span.attributes, "aws.retry_count", 0)
    if content_probe:
        _attribute(span.attributes, "gen_ai.prompt", CONTENT_SENTINEL)
    return request.SerializeToString()


def _run_trace_prefix(run_key: str) -> bytes:
    if not isinstance(run_key, str) or not 1 <= len(run_key) <= 256:
        _fail("ai-finops-sustained-load.run-key.invalid")
    return hashlib.sha256(f"iip-ai-finops-load:{run_key}".encode()).digest()[:12]


def _slot_identity(trace_prefix: bytes, slot: int) -> bytes:
    if len(trace_prefix) != 12 or not 0 <= slot < MAX_SPANS:
        _fail("ai-finops-sustained-load.identity.invalid")
    material = trace_prefix + slot.to_bytes(4, "big")
    return material + hashlib.sha256(material).digest()[:8]


class LoadSender:
    """One retry-free direct client for a worker's Collector requests."""

    def __init__(
        self,
        *,
        bedrock_collector: str,
        openai_collector: str,
        run_key: str,
        timeout_milliseconds: int,
    ) -> None:
        self._origins = {
            "aws.bedrock": _local_origin(bedrock_collector),
            "openai": _local_origin(openai_collector),
        }
        self._trace_prefix = _run_trace_prefix(run_key)
        self._timeout = timeout_milliseconds
        self._opener = _opener()

    def __call__(self, slot: int) -> ExportAttempt:
        provider = PROVIDERS[slot % len(PROVIDERS)]
        identity = _slot_identity(self._trace_prefix, slot)
        payload = _trace_payload(
            provider=provider,
            identity=identity,
            timestamp_ns=time.time_ns(),
        )
        success, elapsed = _post_protobuf(
            self._opener,
            url=self._origins[provider] + "/v1/traces",
            payload=payload,
            timeout_milliseconds=self._timeout,
        )
        return ExportAttempt(provider, success, elapsed, payload)


class ReceiverReplaySender:
    """Authenticate exact replays directly to the commit-bound receiver."""

    def __init__(self, *, receiver: str, timeout_milliseconds: int) -> None:
        self._origin = _local_origin(receiver)
        self._timeout = timeout_milliseconds
        self._tokens = {
            "aws.bedrock": ai_finops_fixture.CHANNEL_TOKEN,
            "openai": ai_finops_fixture.OPENAI_CHANNEL_TOKEN,
        }
        self._local = threading.local()

    def replay(self, attempt: ExportAttempt) -> ExportAttempt:
        token = self._tokens.get(attempt.provider)
        if token is None or not attempt.payload:
            return ExportAttempt(
                attempt.provider,
                False,
                0,
                attempt.payload,
            )
        opener = getattr(self._local, "opener", None)
        if opener is None:
            opener = _opener()
            self._local.opener = opener
        success, elapsed = _post_protobuf(
            opener,
            url=self._origin + "/v1/traces",
            payload=attempt.payload,
            timeout_milliseconds=self._timeout,
            token=token,
        )
        return ExportAttempt(attempt.provider, success, elapsed, attempt.payload)


def _database_connect(database_url: str) -> psycopg.Connection[Any]:
    return psycopg.connect(
        database_url,
        connect_timeout=DATABASE_CONNECT_TIMEOUT_SECONDS,
        options=(
            f"-c statement_timeout={DATABASE_STATEMENT_TIMEOUT_MILLISECONDS} "
            f"-c lock_timeout={DATABASE_STATEMENT_TIMEOUT_MILLISECONDS}"
        ),
    )


def _database_marker(database_url: str) -> datetime:
    try:
        with _database_connect(database_url) as connection:
            row = connection.execute("SELECT clock_timestamp()").fetchone()
    except psycopg.Error:
        _fail("ai-finops-sustained-load.database.unavailable")
    if row is None or not isinstance(row[0], datetime):
        _fail("ai-finops-sustained-load.database.unavailable")
    return row[0]


def _database_version(database_url: str) -> str:
    try:
        with _database_connect(database_url) as connection:
            row = connection.execute("SHOW server_version").fetchone()
    except psycopg.Error:
        _fail("ai-finops-sustained-load.database.unavailable")
    if row is None or not isinstance(row[0], str):
        _fail("ai-finops-sustained-load.database.unavailable")
    return row[0]


def _database_snapshot(
    database_url: str, marker: datetime, trace_prefix: str
) -> DatabaseSnapshot:
    if TRACE_PREFIX.fullmatch(trace_prefix) is None:
        _fail("ai-finops-sustained-load.run-identity.invalid")
    try:
        with _database_connect(database_url) as connection:
            totals = connection.execute(
                """
                SELECT count(DISTINCT usage.usage_record_id) AS usage_records,
                       count(DISTINCT attribution.usage_record_id)
                           AS attribution_records,
                       count(DISTINCT cost.usage_record_id) AS cost_records,
                       count(DISTINCT cost.usage_record_id) FILTER (
                           WHERE cost.cost_status = 'priced'
                       ) AS priced_cost_records,
                       count(DISTINCT cost.usage_record_id) FILTER (
                           WHERE cost.cost_status = 'unpriced'
                       ) AS unpriced_cost_records
                FROM iip.ai_usage_records AS usage
                LEFT JOIN iip.ai_usage_attributions AS attribution
                  ON attribution.tenant_id = usage.tenant_id
                 AND attribution.usage_record_id = usage.usage_record_id
                 AND attribution.policy_id = %s
                 AND attribution.policy_version = %s
                 AND attribution.engine_version = %s
                LEFT JOIN iip.ai_cost_records AS cost
                  ON cost.tenant_id = usage.tenant_id
                 AND cost.usage_record_id = usage.usage_record_id
                 AND cost.catalog_id = %s
                 AND cost.catalog_version = %s
                 AND cost.engine_version = %s
                WHERE usage.tenant_id = 'local' AND usage.created_at >= %s
                  AND left(
                      usage.document->'spec'->'invocation'->>'traceId', 24
                  ) = %s
                """,
                (
                    ATTRIBUTION_POLICY_ID,
                    ATTRIBUTION_POLICY_VERSION,
                    ATTRIBUTION_ENGINE_VERSION,
                    PRICE_CATALOG_ID,
                    PRICE_CATALOG_VERSION,
                    COST_ENGINE_VERSION,
                    marker,
                    trace_prefix,
                ),
            ).fetchone()
            provider_rows = connection.execute(
                """
                SELECT usage.provider,
                       count(DISTINCT usage.usage_record_id) AS usage_records,
                       count(DISTINCT attribution.usage_record_id)
                           AS attribution_records,
                       count(DISTINCT cost.usage_record_id) AS cost_records,
                       count(DISTINCT cost.usage_record_id) FILTER (
                           WHERE cost.cost_status = 'priced'
                       ) AS priced_cost_records
                FROM iip.ai_usage_records AS usage
                LEFT JOIN iip.ai_usage_attributions AS attribution
                  ON attribution.tenant_id = usage.tenant_id
                 AND attribution.usage_record_id = usage.usage_record_id
                 AND attribution.policy_id = %s
                 AND attribution.policy_version = %s
                 AND attribution.engine_version = %s
                LEFT JOIN iip.ai_cost_records AS cost
                  ON cost.tenant_id = usage.tenant_id
                 AND cost.usage_record_id = usage.usage_record_id
                 AND cost.catalog_id = %s
                 AND cost.catalog_version = %s
                 AND cost.engine_version = %s
                WHERE usage.tenant_id = 'local' AND usage.created_at >= %s
                  AND left(
                      usage.document->'spec'->'invocation'->>'traceId', 24
                  ) = %s
                GROUP BY usage.provider
                """,
                (
                    ATTRIBUTION_POLICY_ID,
                    ATTRIBUTION_POLICY_VERSION,
                    ATTRIBUTION_ENGINE_VERSION,
                    PRICE_CATALOG_ID,
                    PRICE_CATALOG_VERSION,
                    COST_ENGINE_VERSION,
                    marker,
                    trace_prefix,
                ),
            ).fetchall()
            latency_rows = connection.execute(
                """
                SELECT CASE WHEN attribution.resolved_at IS NULL THEN NULL ELSE
                           greatest(0, ceil(extract(epoch FROM
                               (attribution.resolved_at - usage.recorded_at)) * 1000))::bigint
                       END AS attribution_ms,
                       CASE WHEN cost.calculated_at IS NULL THEN NULL ELSE
                           greatest(0, ceil(extract(epoch FROM
                               (cost.calculated_at - usage.recorded_at)) * 1000))::bigint
                       END AS cost_ms
                FROM iip.ai_usage_records AS usage
                LEFT JOIN iip.ai_usage_attributions AS attribution
                  ON attribution.tenant_id = usage.tenant_id
                 AND attribution.usage_record_id = usage.usage_record_id
                 AND attribution.policy_id = %s
                 AND attribution.policy_version = %s
                 AND attribution.engine_version = %s
                LEFT JOIN iip.ai_cost_records AS cost
                  ON cost.tenant_id = usage.tenant_id
                 AND cost.usage_record_id = usage.usage_record_id
                 AND cost.catalog_id = %s
                 AND cost.catalog_version = %s
                 AND cost.engine_version = %s
                WHERE usage.tenant_id = 'local' AND usage.created_at >= %s
                  AND left(
                      usage.document->'spec'->'invocation'->>'traceId', 24
                  ) = %s
                """,
                (
                    ATTRIBUTION_POLICY_ID,
                    ATTRIBUTION_POLICY_VERSION,
                    ATTRIBUTION_ENGINE_VERSION,
                    PRICE_CATALOG_ID,
                    PRICE_CATALOG_VERSION,
                    COST_ENGINE_VERSION,
                    marker,
                    trace_prefix,
                ),
            ).fetchall()
            integrity = connection.execute(
                """
                SELECT count(*) FILTER (
                           WHERE left(
                               usage.document->'spec'->'invocation'->>'traceId',
                               24
                           ) IS DISTINCT FROM %s
                       ) AS foreign_usage_records,
                       count(*) FILTER (
                           WHERE usage.document::text LIKE %s
                       ) AS content_records
                FROM iip.ai_usage_records AS usage
                WHERE usage.tenant_id = 'local' AND usage.created_at >= %s
                """,
                (trace_prefix, f"%{CONTENT_SENTINEL}%", marker),
            ).fetchone()
    except psycopg.Error:
        _fail("ai-finops-sustained-load.database.unavailable")
    if totals is None or integrity is None:
        _fail("ai-finops-sustained-load.database.unavailable")
    providers = {
        str(row[0]): (int(row[1]), int(row[2]), int(row[3]), int(row[4]))
        for row in provider_rows
    }
    return DatabaseSnapshot(
        usage_records=int(totals[0]),
        attribution_records=int(totals[1]),
        cost_records=int(totals[2]),
        priced_cost_records=int(totals[3]),
        unpriced_cost_records=int(totals[4]),
        provider_counts=providers,
        attribution_latencies_milliseconds=tuple(
            int(row[0]) for row in latency_rows if row[0] is not None
        ),
        cost_latencies_milliseconds=tuple(
            int(row[1]) for row in latency_rows if row[1] is not None
        ),
        content_preserved=int(integrity[1]) > 0,
        cohort_isolation_preserved=int(integrity[0]) == 0,
    )


class SnapshotMonitor:
    def __init__(self, snapshot: Callable[[], DatabaseSnapshot]) -> None:
        self._snapshot = snapshot
        self._stopped = threading.Event()
        self._lock = threading.Lock()
        self._started = False
        self._samples: list[DatabaseSnapshot] = []
        self._thread = threading.Thread(
            target=self._run,
            name="iip-ai-finops-backlog-monitor",
            daemon=True,
        )

    def start(self) -> None:
        if self._started:
            _fail("ai-finops-sustained-load.monitor.start.invalid")
        self._started = True
        self._thread.start()

    def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                sample = self._snapshot()
            except AiFinopsSustainedLoadError:
                pass
            else:
                with self._lock:
                    self._samples.append(sample)
            self._stopped.wait(1)

    def stop(self, final: DatabaseSnapshot) -> tuple[int, int, int]:
        self.cancel(require_stopped=True)
        with self._lock:
            workload_samples = list(self._samples)
        samples = [*workload_samples, final]
        return (
            max((item.attribution_backlog for item in samples), default=0),
            max((item.cost_backlog for item in samples), default=0),
            len(workload_samples),
        )

    def cancel(self, *, require_stopped: bool = False) -> None:
        self._stopped.set()
        if not self._started:
            return
        self._thread.join(timeout=MONITOR_JOIN_TIMEOUT_SECONDS)
        if require_stopped and self._thread.is_alive():
            _fail("ai-finops-sustained-load.monitor.stop.timeout")


def _json_url(url: str) -> Mapping[str, Any]:
    request = urllib.request.Request(url, headers={"Connection": "close"})
    try:
        with _opener().open(request, timeout=5) as response:
            if response.status != 200:
                _fail("ai-finops-sustained-load.observability.unavailable")
            payload = json.loads(response.read(4 * 1024 * 1024 + 1))
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        urllib.error.HTTPError,
        urllib.error.URLError,
    ):
        _fail("ai-finops-sustained-load.observability.unavailable")
    return _mapping(payload, "ai-finops-sustained-load.observability.invalid")


def _prometheus_query(endpoint: str, expression: str) -> list[Mapping[str, Any]]:
    url = _local_origin(endpoint) + "/api/v1/query?" + urllib.parse.urlencode(
        {"query": expression}
    )
    payload = _json_url(url)
    data = payload.get("data")
    result = data.get("result") if isinstance(data, Mapping) else None
    if payload.get("status") != "success" or not isinstance(result, list):
        _fail("ai-finops-sustained-load.prometheus.invalid")
    return [item for item in result if isinstance(item, Mapping)]


def _prometheus_scalar(endpoint: str, expression: str) -> int:
    result = _prometheus_query(endpoint, expression)
    if not result:
        return 0
    value = result[0].get("value")
    try:
        parsed = float(value[1])  # type: ignore[index]
    except (IndexError, TypeError, ValueError):
        _fail("ai-finops-sustained-load.prometheus.invalid")
    if parsed < 0 or not parsed.is_integer():
        _fail("ai-finops-sustained-load.prometheus.invalid")
    return int(parsed)


_PROM_USAGE = (
    'sum(iip_ai_allocation_requests{iip_ai_allocation_dimension="application"})'
)
_PROM_COST = (
    'sum(iip_ai_allocation_cost_requests{iip_ai_allocation_dimension="application"})'
)


def _prometheus_totals(endpoint: str) -> tuple[int, int]:
    return (
        _prometheus_scalar(endpoint, _PROM_USAGE),
        _prometheus_scalar(endpoint, _PROM_COST),
    )


def _prohibited_labels_absent(endpoint: str) -> bool:
    series = _prometheus_query(
        endpoint,
        '{__name__=~"iip_ai_(usage|cost|allocation).*"}',
    )
    prohibited = (
        "trace",
        "span",
        "request",
        "prompt",
        "response",
        "content",
        "credential",
    )
    for item in series:
        metric = item.get("metric")
        if isinstance(metric, Mapping) and any(
            fragment in str(candidate).lower()
            for key, value in metric.items()
            if key != "__name__"
            for candidate in (key, value)
            for fragment in prohibited
        ):
            return False
    return True


def _grafana_available(endpoint: str) -> bool:
    try:
        payload = _json_url(
            _local_origin(endpoint) + "/api/dashboards/uid/iip-ai-finops"
        )
    except AiFinopsSustainedLoadError:
        return False
    dashboard = payload.get("dashboard")
    return (
        isinstance(dashboard, Mapping)
        and dashboard.get("title") == "IIP AI FinOps — Multi-provider V0"
    )


def _content_rejected(receiver: str, timeout_milliseconds: int, run_key: str) -> bool:
    origin = _local_origin(receiver)
    probes = (
        ("aws.bedrock", ai_finops_fixture.CHANNEL_TOKEN),
        ("openai", ai_finops_fixture.OPENAI_CHANNEL_TOKEN),
    )
    for provider, token in probes:
        identity = hashlib.sha256(
            f"{run_key}:content-probe:{provider}".encode()
        ).digest()
        payload = _trace_payload(
            provider=provider,
            identity=identity,
            timestamp_ns=time.time_ns(),
            content_probe=True,
        )
        request = urllib.request.Request(
            origin + "/v1/traces",
            data=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/x-protobuf",
                "Connection": "close",
            },
            method="POST",
        )
        try:
            with _opener().open(
                request, timeout=timeout_milliseconds / 1000
            ) as response:
                response.read(1024 * 1024 + 1)
            return False
        except urllib.error.HTTPError as error:
            body = error.read(1024 * 1024 + 1)
            status = error.code
            headers = error.headers
            error.close()
            content_type = (
                headers.get_content_type() if headers is not None else None
            )
            try:
                rejection = RpcStatus.FromString(body)
            except DecodeError:
                return False
            if (
                status != 400
                or content_type != "application/x-protobuf"
                or len(body) > 1024 * 1024
                or rejection.code != 0
                or rejection.message != "otlp.span.content-prohibited"
                or rejection.details
            ):
                return False
        except (OSError, urllib.error.URLError):
            return False
    return True


def _wait_for_usage(
    snapshot: Callable[[], DatabaseSnapshot],
    expected: int,
    deadline: float,
) -> DatabaseSnapshot:
    latest = snapshot()
    while latest.usage_records < expected and time.monotonic() < deadline:
        time.sleep(0.5)
        latest = snapshot()
    return latest


def _wait_for_pipeline(
    snapshot: Callable[[], DatabaseSnapshot],
    expected: int,
    deadline: float,
) -> DatabaseSnapshot:
    latest = snapshot()
    while (
        latest.usage_records < expected
        or latest.attribution_records < latest.usage_records
        or latest.cost_records < latest.usage_records
    ) and time.monotonic() < deadline:
        time.sleep(0.5)
        latest = snapshot()
    return latest


def _replay(
    attempts: Sequence[ExportAttempt],
    *,
    scheduled: int,
    sender: ReceiverReplaySender,
    concurrency: int,
    spans_per_second: int,
    deadline: float,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[ExportAttempt, ...]:
    if (
        scheduled < 0
        or not 1 <= concurrency <= 32
        or not 1 <= spans_per_second <= 40
        or not math.isfinite(deadline)
    ):
        _fail("ai-finops-sustained-load.replay.configuration.invalid")
    replayable = [item for item in attempts if item.payload]
    selected = replayable[-scheduled:] if scheduled else []
    if not selected:
        return ()
    executor = ThreadPoolExecutor(
        max_workers=min(concurrency, len(selected)),
        thread_name_prefix="iip-ai-finops-replay",
    )
    pending: dict[Future[ExportAttempt], tuple[int, ExportAttempt]] = {}
    completed: dict[int, ExportAttempt] = {}

    def collect(done: set[Future[ExportAttempt]]) -> None:
        for future in done:
            index, original = pending.pop(future)
            try:
                completed[index] = future.result()
            except Exception:
                completed[index] = ExportAttempt(
                    original.provider,
                    False,
                    0,
                    original.payload,
                )

    interval = 1 / spans_per_second
    next_allowed = monotonic()
    try:
        for index, attempt in enumerate(selected):
            while len(pending) >= concurrency:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    break
                done, _not_done = wait(
                    pending,
                    timeout=remaining,
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    break
                collect(done)
            if len(pending) >= concurrency:
                break
            current = monotonic()
            if current < next_allowed:
                sleeper(min(next_allowed - current, max(0.0, deadline - current)))
                current = monotonic()
            if current >= deadline:
                break
            pending[executor.submit(sender.replay, attempt)] = (index, attempt)
            next_allowed = current + interval
        if pending:
            done, not_done = wait(
                pending,
                timeout=max(0.0, deadline - monotonic()),
            )
            collect(done)
            for future in not_done:
                index, original = pending.pop(future)
                completed[index] = ExportAttempt(
                    original.provider,
                    False,
                    0,
                    original.payload,
                )
                future.cancel()
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    return tuple(completed[index] for index in sorted(completed))


def run_qualification(
    *,
    profile: Mapping[str, Any],
    database_url: str,
    bedrock_collector: str,
    openai_collector: str,
    receiver: str,
    prometheus: str,
    grafana: str,
    run_key: str,
    sender_factory: Callable[[], Callable[[int], ExportAttempt]] | None = None,
) -> tuple[QualificationRun, str]:
    validate_profile(profile)
    workload, _objectives = _profile_values(profile)
    marker = _database_marker(database_url)
    database_version = _database_version(database_url)
    trace_prefix = _run_trace_prefix(run_key).hex()
    snapshot = lambda: _database_snapshot(database_url, marker, trace_prefix)
    baseline_prometheus = _prometheus_totals(prometheus)
    timeout = int(workload["requestTimeoutMilliseconds"])
    create_sender = sender_factory or (
        lambda: LoadSender(
            bedrock_collector=bedrock_collector,
            openai_collector=openai_collector,
            run_key=run_key,
            timeout_milliseconds=timeout,
        )
    )
    monitor = SnapshotMonitor(snapshot)
    started_mono = time.monotonic()
    try:
        generator = _run_fixed_rate(
            duration_seconds=int(workload["durationSeconds"]),
            spans_per_second=int(workload["spansPerSecond"]),
            producer_concurrency=int(workload["producerConcurrency"]),
            maximum_scheduler_lag_milliseconds=int(
                workload["maximumSchedulerLagMilliseconds"]
            ),
            sender_factory=create_sender,
            on_started=monitor.start,
        )
        workload_boundary = snapshot()
        peak_attribution, peak_cost, backlog_sample_count = monitor.stop(
            workload_boundary
        )
        drain_started = time.monotonic()
        deadline = (
            drain_started
            + int(workload["maximumPipelineDrainMilliseconds"]) / 1000
        )
        attempted = len(generator.attempts)
        before = _wait_for_usage(snapshot, attempted, deadline)
        replay_scheduled = (
            before.usage_records * int(workload["replayBasisPoints"]) // 10_000
            if before.usage_records == attempted
            else 0
        )
        replay_sender = ReceiverReplaySender(
            receiver=receiver,
            timeout_milliseconds=timeout,
        )
        replay_attempts = (
            _replay(
                generator.attempts,
                scheduled=replay_scheduled,
                sender=replay_sender,
                concurrency=int(workload["producerConcurrency"]),
                spans_per_second=int(workload["spansPerSecond"]),
                deadline=deadline,
            )
            if replay_scheduled > 0
            else ()
        )
        content_rejected = _content_rejected(receiver, timeout, run_key)
        final = _wait_for_pipeline(snapshot, attempted, deadline)
        current_usage, current_cost = _prometheus_totals(prometheus)
        observed_usage = max(0, current_usage - baseline_prometheus[0])
        observed_cost = max(0, current_cost - baseline_prometheus[1])
        while (
            (
                observed_usage < final.usage_records
                or observed_cost < final.cost_records
            )
            and time.monotonic() < deadline
        ):
            time.sleep(1)
            current_usage, current_cost = _prometheus_totals(prometheus)
            observed_usage = max(0, current_usage - baseline_prometheus[0])
            observed_cost = max(0, current_cost - baseline_prometheus[1])
        after = snapshot()
    except BaseException:
        monitor.cancel()
        raise
    drain_milliseconds = max(0, math.ceil((time.monotonic() - drain_started) * 1000))
    grafana_available = _grafana_available(grafana)
    prohibited_labels_absent = _prohibited_labels_absent(prometheus)
    completed_at = datetime.now(timezone.utc)
    actual_duration_milliseconds = max(
        1, math.ceil((time.monotonic() - started_mono) * 1000)
    )
    run = QualificationRun(
        generator=generator,
        replay_scheduled=replay_scheduled,
        replay_attempts=replay_attempts,
        usage_before_replay=before.usage_records,
        usage_after_replay=after.usage_records,
        final_snapshot=after,
        peak_attribution_backlog=peak_attribution,
        peak_cost_backlog=peak_cost,
        backlog_sample_count=backlog_sample_count,
        drain_milliseconds=drain_milliseconds,
        prometheus_usage=observed_usage,
        prometheus_cost=observed_cost,
        prometheus_converged=(
            observed_usage == after.usage_records
            and observed_cost == after.cost_records
        ),
        grafana_available=grafana_available,
        prohibited_labels_absent=prohibited_labels_absent,
        content_rejected=content_rejected,
        completed_at=completed_at,
        actual_duration_milliseconds=actual_duration_milliseconds,
    )
    return run, database_version


def _basis_points(numerator: int, denominator: int) -> int:
    if numerator < 0 or denominator < 0:
        _fail("ai-finops-sustained-load.measurement.invalid")
    if denominator == 0:
        return 0
    return min(10_000, numerator * 10_000 // denominator)


def _measurements(
    profile: Mapping[str, Any], run: QualificationRun
) -> dict[str, Any]:
    workload, _objectives = _profile_values(profile)
    target = int(workload["durationSeconds"]) * int(workload["spansPerSecond"])
    if run.generator.target_spans != target:
        _fail("ai-finops-sustained-load.measurement.target-invalid")
    attempts = len(run.generator.attempts)
    accepted = sum(item.success for item in run.generator.attempts)
    replay_attempted = len(run.replay_attempts)
    replay_acknowledged = sum(item.success for item in run.replay_attempts)
    final = run.final_snapshot
    export_latency = tuple(
        item.duration_milliseconds
        for item in run.generator.attempts
        if item.success
    )
    providers: list[dict[str, Any]] = []
    for provider in PROVIDERS:
        counts = final.provider_counts.get(provider, (0, 0, 0, 0))
        providers.append(
            {
                "provider": provider,
                "scheduledSpans": target // len(PROVIDERS),
                "usageRecords": counts[0],
                "attributionRecords": counts[1],
                "costRecords": counts[2],
                "pricedCostRecords": counts[3],
            }
        )
    return {
        "startedAt": _timestamp(run.generator.started_at),
        "completedAt": _timestamp(run.completed_at),
        "actualDurationMilliseconds": run.actual_duration_milliseconds,
        "generator": {
            "scheduledSpans": target,
            "attemptedSpans": attempts,
            "schedulerMissedSpans": run.generator.scheduler_missed_spans,
            "collectorAcceptedSpans": accepted,
            "collectorRejectedSpans": attempts - accepted,
            "schedulerMissBasisPoints": _basis_points(
                run.generator.scheduler_missed_spans, target
            ),
            "collectorAcceptanceBasisPoints": _basis_points(accepted, target),
            "exportLatency": _latency(export_latency),
        },
        "replay": {
            "scheduledSpans": run.replay_scheduled,
            "attemptedSpans": replay_attempted,
            "receiverAcknowledgedSpans": replay_acknowledged,
            "receiverUnacknowledgedSpans": replay_attempted - replay_acknowledged,
            "usageRecordsBeforeReplay": run.usage_before_replay,
            "usageRecordsAfterReplay": run.usage_after_replay,
        },
        "pipeline": {
            "usageRecords": final.usage_records,
            "usagePersistenceBasisPoints": _basis_points(
                final.usage_records, target
            ),
            "attributionRecords": final.attribution_records,
            "attributionCompletionBasisPoints": _basis_points(
                final.attribution_records, target
            ),
            "costRecords": final.cost_records,
            "costCompletionBasisPoints": _basis_points(final.cost_records, target),
            "pricedCostRecords": final.priced_cost_records,
            "unpricedCostRecords": final.unpriced_cost_records,
            "pricedCoverageBasisPoints": _basis_points(
                final.priced_cost_records, final.cost_records
            ),
            "peakAttributionBacklogRecords": run.peak_attribution_backlog,
            "finalAttributionBacklogRecords": final.attribution_backlog,
            "peakCostBacklogRecords": run.peak_cost_backlog,
            "finalCostBacklogRecords": final.cost_backlog,
            "backlogSampleCount": run.backlog_sample_count,
            "attributionLatency": _latency(
                final.attribution_latencies_milliseconds
            ),
            "costLatency": _latency(final.cost_latencies_milliseconds),
            "drainMilliseconds": run.drain_milliseconds,
        },
        "providers": providers,
        "observability": {
            "prometheusConverged": run.prometheus_converged,
            "expectedUsageRequests": final.usage_records,
            "observedUsageRequests": run.prometheus_usage,
            "expectedCostRequests": final.cost_records,
            "observedCostRequests": run.prometheus_cost,
            "grafanaDashboardAvailable": run.grafana_available,
            "cohortIsolationPreserved": final.cohort_isolation_preserved,
            "metadataOnlyPreserved": (
                run.content_rejected and not final.content_preserved
            ),
            "prohibitedLabelsAbsent": run.prohibited_labels_absent,
        },
    }


def _check(identifier: str, passed: bool, error_code: str) -> dict[str, str]:
    if passed:
        return {"id": identifier, "status": "passed"}
    return {"id": identifier, "status": "failed", "errorCode": error_code}


def _profile_document_from_report(spec: Mapping[str, Any]) -> dict[str, Any]:
    bindings = _mapping(spec.get("bindings"), "ai-finops-sustained-load.report.invalid")
    return {
        "apiVersion": API_VERSION,
        "kind": PROFILE_KIND,
        "metadata": {"id": bindings.get("profileId")},
        "spec": {
            "qualificationLevel": QUALIFICATION_LEVEL,
            "providers": list(PROVIDERS),
            "distribution": "round-robin-equal",
            "workload": spec.get("workload"),
            "objectives": spec.get("objectives"),
        },
    }


def _derived_checks(
    *, metadata: Mapping[str, Any], spec: Mapping[str, Any]
) -> list[dict[str, str]]:
    subject = _mapping(spec.get("subject"), "ai-finops-sustained-load.report.invalid")
    bindings = _mapping(spec.get("bindings"), "ai-finops-sustained-load.report.invalid")
    workload = _mapping(spec.get("workload"), "ai-finops-sustained-load.report.invalid")
    objectives = _mapping(
        spec.get("objectives"), "ai-finops-sustained-load.report.invalid"
    )
    environment = _mapping(
        spec.get("environment"), "ai-finops-sustained-load.report.invalid"
    )
    measurements = _mapping(
        spec.get("measurements"), "ai-finops-sustained-load.report.invalid"
    )
    generator = _mapping(
        measurements.get("generator"), "ai-finops-sustained-load.report.invalid"
    )
    replay = _mapping(
        measurements.get("replay"), "ai-finops-sustained-load.report.invalid"
    )
    pipeline = _mapping(
        measurements.get("pipeline"), "ai-finops-sustained-load.report.invalid"
    )
    observability = _mapping(
        measurements.get("observability"),
        "ai-finops-sustained-load.report.invalid",
    )
    candidate_profile = _profile_document_from_report(spec)
    profile_bound = False
    try:
        validate_profile(candidate_profile)
        profile_bound = bindings.get("profileDigest") == _digest(candidate_profile)
    except AiFinopsSustainedLoadError:
        profile_bound = False
    target = int(workload.get("durationSeconds", 0)) * int(
        workload.get("spansPerSecond", 0)
    )

    def latency_passes(name: str, objective: str) -> bool:
        latency = _mapping(pipeline.get(name), "ai-finops-sustained-load.report.invalid")
        p95 = latency.get("p95Milliseconds")
        return isinstance(p95, int) and p95 <= int(objectives[objective])

    export_latency = _mapping(
        generator.get("exportLatency"), "ai-finops-sustained-load.report.invalid"
    )
    export_p95 = export_latency.get("p95Milliseconds")
    original_durability_exact = replay.get(
        "usageRecordsBeforeReplay"
    ) == generator.get("attemptedSpans")
    replay_expected = (
        int(replay.get("usageRecordsBeforeReplay", 0))
        * int(workload.get("replayBasisPoints", 0))
        // 10_000
        if original_durability_exact
        else 0
    )
    return [
        _check(
            "source-binding",
            metadata.get("sourceDirty") is False
            and metadata.get("sourceRevision") == subject.get("sourceRevision"),
            "ai-finops-sustained-load.source.not-clean-bound",
        ),
        _check(
            "profile-binding",
            profile_bound,
            "ai-finops-sustained-load.profile.binding-mismatch",
        ),
        _check(
            "compose-configuration",
            environment.get("composeConfigurationValid") is True,
            "ai-finops-sustained-load.compose.invalid",
        ),
        _check(
            "all-components-healthy",
            environment.get("allComponentsHealthy") is True,
            "ai-finops-sustained-load.components.unhealthy",
        ),
        _check(
            "bounded-load-volume",
            1 <= target <= MAX_SPANS
            and target % len(PROVIDERS) == 0
            and generator.get("scheduledSpans") == target,
            "ai-finops-sustained-load.volume.invalid",
        ),
        _check(
            "fixed-rate-scheduler",
            generator.get("attemptedSpans", 0)
            + generator.get("schedulerMissedSpans", 0)
            == target
            and generator.get("schedulerMissBasisPoints", 10_001)
            <= objectives["maximumSchedulerMissBasisPoints"],
            "ai-finops-sustained-load.scheduler.objective-missed",
        ),
        _check(
            "collector-acceptance",
            generator.get("collectorAcceptanceBasisPoints", -1)
            >= objectives["minimumCollectorAcceptanceBasisPoints"],
            "ai-finops-sustained-load.collector.objective-missed",
        ),
        _check(
            "usage-ledger-persistence",
            pipeline.get("usagePersistenceBasisPoints", -1)
            >= objectives["minimumUsagePersistenceBasisPoints"],
            "ai-finops-sustained-load.usage.objective-missed",
        ),
        _check(
            "replay-idempotency",
            original_durability_exact
            and replay.get("scheduledSpans") == replay_expected
            and replay.get("attemptedSpans") == replay_expected
            and replay.get("receiverAcknowledgedSpans") == replay_expected
            and replay.get("receiverUnacknowledgedSpans") == 0
            and replay.get("usageRecordsBeforeReplay")
            == replay.get("usageRecordsAfterReplay")
            == pipeline.get("usageRecords"),
            "ai-finops-sustained-load.replay.not-idempotent",
        ),
        _check(
            "attribution-completion",
            pipeline.get("attributionCompletionBasisPoints", -1)
            >= objectives["minimumAttributionCompletionBasisPoints"],
            "ai-finops-sustained-load.attribution.objective-missed",
        ),
        _check(
            "cost-completion",
            pipeline.get("costCompletionBasisPoints", -1)
            >= objectives["minimumCostCompletionBasisPoints"],
            "ai-finops-sustained-load.cost.objective-missed",
        ),
        _check(
            "priced-coverage",
            pipeline.get("pricedCoverageBasisPoints", -1)
            >= objectives["minimumPricedCoverageBasisPoints"],
            "ai-finops-sustained-load.pricing.objective-missed",
        ),
        _check(
            "export-p95-latency",
            isinstance(export_p95, int)
            and export_p95 <= objectives["maximumExportP95Milliseconds"],
            "ai-finops-sustained-load.export.p95-missed",
        ),
        _check(
            "attribution-p95-latency",
            latency_passes(
                "attributionLatency", "maximumAttributionP95Milliseconds"
            ),
            "ai-finops-sustained-load.attribution.p95-missed",
        ),
        _check(
            "cost-p95-latency",
            latency_passes("costLatency", "maximumCostP95Milliseconds"),
            "ai-finops-sustained-load.cost.p95-missed",
        ),
        _check(
            "pipeline-drain",
            pipeline.get("drainMilliseconds", 1_000_001)
            <= int(workload["maximumPipelineDrainMilliseconds"])
            and pipeline.get("finalAttributionBacklogRecords") == 0
            and pipeline.get("finalCostBacklogRecords") == 0
            and pipeline.get("backlogSampleCount", 0) > 0,
            "ai-finops-sustained-load.pipeline.drain-missed",
        ),
        _check(
            "prometheus-convergence",
            observability.get("cohortIsolationPreserved") is True
            and observability.get("prometheusConverged") is True
            and observability.get("expectedUsageRequests")
            == observability.get("observedUsageRequests")
            == pipeline.get("usageRecords")
            and observability.get("expectedCostRequests")
            == observability.get("observedCostRequests")
            == pipeline.get("costRecords"),
            "ai-finops-sustained-load.prometheus.not-converged",
        ),
        _check(
            "grafana-dashboard",
            observability.get("grafanaDashboardAvailable") is True,
            "ai-finops-sustained-load.grafana.unavailable",
        ),
        _check(
            "metadata-only-boundaries",
            observability.get("metadataOnlyPreserved") is True
            and observability.get("prohibitedLabelsAbsent") is True,
            "ai-finops-sustained-load.metadata.boundary-failed",
        ),
    ]


def _latency_valid(value: object, completed: int) -> bool:
    if not isinstance(value, Mapping):
        return False
    keys = (
        "p50Milliseconds",
        "p95Milliseconds",
        "p99Milliseconds",
        "maximumMilliseconds",
    )
    selected = [value.get(key) for key in keys]
    if completed == 0:
        return all(item is None for item in selected)
    return all(isinstance(item, int) and not isinstance(item, bool) for item in selected) and (
        selected[0] <= selected[1] <= selected[2] <= selected[3]  # type: ignore[operator]
    )


def _maximum_actual_duration_milliseconds(workload: Mapping[str, Any]) -> int:
    request_timeout = int(workload["requestTimeoutMilliseconds"])
    bounded_database_overhead = 4 * DATABASE_SNAPSHOT_BOUND_MILLISECONDS
    bounded_monitor_overhead = math.ceil(MONITOR_JOIN_TIMEOUT_SECONDS * 1000)
    bounded_observability_overhead = 6 * OBSERVABILITY_REQUEST_TIMEOUT_SECONDS * 1000
    return (
        int(workload["durationSeconds"]) * 1000
        + int(workload["maximumSchedulerLagMilliseconds"])
        + int(workload["maximumPipelineDrainMilliseconds"])
        + 4 * request_timeout
        + bounded_database_overhead
        + bounded_monitor_overhead
        + bounded_observability_overhead
    )


def _validate_measurements(
    profile: Mapping[str, Any], measurements: Mapping[str, Any]
) -> None:
    code = "ai-finops-sustained-load.report.invalid"
    workload, _objectives = _profile_values(profile)
    generator = _mapping(measurements.get("generator"), code)
    replay = _mapping(measurements.get("replay"), code)
    pipeline = _mapping(measurements.get("pipeline"), code)
    providers = measurements.get("providers")
    observability = _mapping(measurements.get("observability"), code)
    if not isinstance(providers, list) or len(providers) != len(PROVIDERS):
        _fail(code)
    started = _parse_timestamp(measurements.get("startedAt"), code)
    completed = _parse_timestamp(measurements.get("completedAt"), code)
    target = int(workload["durationSeconds"]) * int(workload["spansPerSecond"])
    attempted = int(generator["attemptedSpans"])
    missed = int(generator["schedulerMissedSpans"])
    accepted = int(generator["collectorAcceptedSpans"])
    rejected = int(generator["collectorRejectedSpans"])
    usage = int(pipeline["usageRecords"])
    attribution = int(pipeline["attributionRecords"])
    cost = int(pipeline["costRecords"])
    priced = int(pipeline["pricedCostRecords"])
    unpriced = int(pipeline["unpricedCostRecords"])
    before = int(replay["usageRecordsBeforeReplay"])
    after = int(replay["usageRecordsAfterReplay"])
    replay_scheduled = int(replay["scheduledSpans"])
    replay_attempted = int(replay["attemptedSpans"])
    replay_acknowledged = int(replay["receiverAcknowledgedSpans"])
    replay_unacknowledged = int(replay["receiverUnacknowledgedSpans"])
    selected_providers = [_mapping(item, code) for item in providers]
    minimum_workload_duration = int(workload["durationSeconds"]) * 1000 - math.ceil(
        1000 / int(workload["spansPerSecond"])
    )
    maximum_duration = _maximum_actual_duration_milliseconds(workload)
    drain_milliseconds = int(pipeline["drainMilliseconds"])
    minimum_replay_drain = (
        (replay_attempted - 1) * 1000 // int(workload["spansPerSecond"])
        if replay_attempted > 0
        else 0
    )
    actual_duration = int(measurements["actualDurationMilliseconds"])
    exact_original_durability = before == attempted
    replay_expected = (
        before * int(workload["replayBasisPoints"]) // 10_000
        if exact_original_durability
        else 0
    )
    if (
        completed < started
        or not minimum_workload_duration <= actual_duration <= maximum_duration
        or drain_milliseconds < minimum_replay_drain
        or actual_duration < minimum_workload_duration + drain_milliseconds
        or generator.get("scheduledSpans") != target
        or attempted + missed != target
        or accepted + rejected != attempted
        or generator.get("schedulerMissBasisPoints")
        != _basis_points(missed, target)
        or generator.get("collectorAcceptanceBasisPoints")
        != _basis_points(accepted, target)
        or not _latency_valid(generator.get("exportLatency"), accepted)
        or replay_scheduled != replay_expected
        or replay_attempted > replay_scheduled
        or replay_acknowledged + replay_unacknowledged != replay_attempted
        or not 0 <= before <= after == usage <= attempted <= target
        or not 0 <= priced <= cost <= usage
        or unpriced + priced != cost
        or not 0 <= attribution <= usage
        or pipeline.get("usagePersistenceBasisPoints")
        != _basis_points(usage, target)
        or pipeline.get("attributionCompletionBasisPoints")
        != _basis_points(attribution, target)
        or pipeline.get("costCompletionBasisPoints")
        != _basis_points(cost, target)
        or pipeline.get("pricedCoverageBasisPoints")
        != _basis_points(priced, cost)
        or pipeline.get("finalAttributionBacklogRecords")
        != usage - attribution
        or pipeline.get("finalCostBacklogRecords") != usage - cost
        or int(pipeline["peakAttributionBacklogRecords"])
        < int(pipeline["finalAttributionBacklogRecords"])
        or int(pipeline["peakCostBacklogRecords"])
        < int(pipeline["finalCostBacklogRecords"])
        or not _latency_valid(pipeline.get("attributionLatency"), attribution)
        or not _latency_valid(pipeline.get("costLatency"), cost)
        or [item.get("provider") for item in selected_providers]
        != list(PROVIDERS)
        or any(item.get("scheduledSpans") != target // 2 for item in selected_providers)
        or any(
            not (
                0
                <= int(item["pricedCostRecords"])
                <= int(item["costRecords"])
                <= int(item["usageRecords"])
                <= int(item["scheduledSpans"])
                and 0
                <= int(item["attributionRecords"])
                <= int(item["usageRecords"])
            )
            for item in selected_providers
        )
        or sum(int(item["usageRecords"]) for item in selected_providers) != usage
        or sum(int(item["attributionRecords"]) for item in selected_providers)
        != attribution
        or sum(int(item["costRecords"]) for item in selected_providers) != cost
        or sum(int(item["pricedCostRecords"]) for item in selected_providers)
        != priced
        or observability.get("expectedUsageRequests") != usage
        or observability.get("expectedCostRequests") != cost
    ):
        _fail(code)


def _has_forbidden_key(value: object) -> bool:
    if isinstance(value, Mapping):
        return any(
            key in PROHIBITED_RETAINED_KEYS or _has_forbidden_key(nested)
            for key, nested in value.items()
        )
    if isinstance(value, list):
        return any(_has_forbidden_key(item) for item in value)
    return False


def _repository_identity() -> tuple[str, str]:
    try:
        with (ROOT / "pyproject.toml").open("rb") as handle:
            application_version = tomllib.load(handle)["project"]["version"]
        migrations = sorted(
            (ROOT / "src/iip/adapters/postgres/migrations").glob("*.sql")
        )
    except (OSError, KeyError, TypeError, tomllib.TOMLDecodeError):
        _fail("ai-finops-sustained-load.source.identity-invalid")
    if (
        not isinstance(application_version, str)
        or SEMVER.fullmatch(application_version) is None
        or not migrations
        or MIGRATION.fullmatch(migrations[-1].name) is None
    ):
        _fail("ai-finops-sustained-load.source.identity-invalid")
    return application_version, migrations[-1].name


def build_report(
    *,
    profile: Mapping[str, Any],
    run: QualificationRun,
    source_revision: str,
    source_dirty: bool,
    application_version: str,
    image_digest: str,
    collector_image_digest: str,
    platform_name: str,
    container_runtime_version: str,
    database_version: str,
    migration: str,
    compose_configuration_valid: bool,
    all_components_healthy: bool,
) -> Mapping[str, Any]:
    validate_profile(profile)
    if (
        REVISION.fullmatch(source_revision) is None
        or not isinstance(source_dirty, bool)
        or SEMVER.fullmatch(application_version) is None
        or DIGEST.fullmatch(image_digest) is None
        or collector_image_digest != EXPECTED_COLLECTOR_IMAGE_DIGEST
        or PLATFORM.fullmatch(platform_name) is None
        or not isinstance(container_runtime_version, str)
        or not 1 <= len(container_runtime_version) <= 64
        or not isinstance(database_version, str)
        or not 1 <= len(database_version) <= 64
        or MIGRATION.fullmatch(migration) is None
        or not isinstance(compose_configuration_valid, bool)
        or not isinstance(all_components_healthy, bool)
    ):
        _fail("ai-finops-sustained-load.environment.invalid")
    profile_spec = _mapping(
        profile.get("spec"), "ai-finops-sustained-load.profile.invalid"
    )
    profile_metadata = _mapping(
        profile.get("metadata"), "ai-finops-sustained-load.profile.invalid"
    )
    measurements = _measurements(profile, run)
    metadata: dict[str, Any] = {
        "generatedAt": measurements["completedAt"],
        "sourceRevision": source_revision,
        "sourceDirty": source_dirty,
    }
    spec: dict[str, Any] = {
        "status": "not-qualified",
        "qualificationLevel": QUALIFICATION_LEVEL,
        "qualificationBoundary": QUALIFICATION_BOUNDARY,
        "subject": {
            "applicationVersion": application_version,
            "attributionEngineVersion": ATTRIBUTION_ENGINE_VERSION,
            "costEngineVersion": COST_ENGINE_VERSION,
            "sourceRevision": source_revision,
            "imageDigest": image_digest,
        },
        "bindings": {
            "profileId": profile_metadata["id"],
            "profileDigest": _digest(profile),
            "attributionPolicyId": ATTRIBUTION_POLICY_ID,
            "attributionPolicyVersion": ATTRIBUTION_POLICY_VERSION,
            "priceCatalogId": PRICE_CATALOG_ID,
            "priceCatalogVersion": PRICE_CATALOG_VERSION,
        },
        "workload": dict(_mapping(profile_spec.get("workload"), "ai-finops-sustained-load.profile.invalid")),
        "objectives": dict(_mapping(profile_spec.get("objectives"), "ai-finops-sustained-load.profile.invalid")),
        "environment": {
            "platform": platform_name,
            "pythonVersion": platform.python_version(),
            "containerRuntime": "docker",
            "containerRuntimeVersion": container_runtime_version,
            "collectorImageDigest": collector_image_digest,
            "composeConfigurationValid": compose_configuration_valid,
            "allComponentsHealthy": all_components_healthy,
            "collectionTransport": "otlp-http-protobuf",
            "collectorAcceptanceBoundary": "collector-pipeline-acceptance-only",
            "receiverDeliveryBoundary": "postgresql-commit-before-http-200",
            "database": {
                "engine": "postgresql",
                "version": database_version,
                "migration": migration,
            },
            "telemetryBackend": "prometheus",
            "dashboard": "grafana",
            "pricingSource": "test-fixture",
            "contentPolicy": "metadata-only",
            "scheduler": "bounded-fixed-rate-v1",
        },
        "measurements": measurements,
    }
    checks = _derived_checks(metadata=metadata, spec=spec)
    failed = sum(item["status"] == "failed" for item in checks)
    status = "qualified" if failed == 0 else "not-qualified"
    pipeline = _mapping(
        measurements.get("pipeline"), "ai-finops-sustained-load.report.invalid"
    )
    spec.update(
        {
            "status": status,
            "checks": checks,
            "limitations": list(LIMITATIONS),
            "summary": {
                "totalChecks": len(CHECK_IDS),
                "passedChecks": len(CHECK_IDS) - failed,
                "failedChecks": failed,
                "scheduledSpans": _mapping(
                    measurements.get("generator"),
                    "ai-finops-sustained-load.report.invalid",
                )["scheduledSpans"],
                "persistedUsageRecords": pipeline["usageRecords"],
                "completedAttributions": pipeline["attributionRecords"],
                "completedCosts": pipeline["costRecords"],
                "overallStatus": status,
            },
        }
    )
    if _has_forbidden_key(spec):
        _fail("ai-finops-sustained-load.output.not-minimized")
    metadata["id"] = _report_identifier(metadata, spec)
    report = {
        "apiVersion": API_VERSION,
        "kind": REPORT_KIND,
        "metadata": metadata,
        "spec": spec,
    }
    validate_report_document(report)
    return report


def validate_report_document(report: Mapping[str, Any]) -> None:
    code = "ai-finops-sustained-load.report.invalid"
    _validate_schema(report, REPORT_SCHEMA, code)
    metadata = _mapping(report.get("metadata"), code)
    spec = _mapping(report.get("spec"), code)
    subject = _mapping(spec.get("subject"), code)
    bindings = _mapping(spec.get("bindings"), code)
    measurements = _mapping(spec.get("measurements"), code)
    checks = spec.get("checks")
    summary = _mapping(spec.get("summary"), code)
    if not isinstance(checks, list):
        _fail(code)
    profile = _profile_document_from_report(spec)
    try:
        validate_profile(profile)
    except AiFinopsSustainedLoadError:
        _fail(code)
    _validate_measurements(profile, measurements)
    expected_checks = _derived_checks(metadata=metadata, spec=spec)
    failed = sum(item["status"] == "failed" for item in expected_checks)
    status = "qualified" if failed == 0 else "not-qualified"
    generator = _mapping(measurements.get("generator"), code)
    pipeline = _mapping(measurements.get("pipeline"), code)
    expected_summary = {
        "totalChecks": len(CHECK_IDS),
        "passedChecks": len(CHECK_IDS) - failed,
        "failedChecks": failed,
        "scheduledSpans": generator["scheduledSpans"],
        "persistedUsageRecords": pipeline["usageRecords"],
        "completedAttributions": pipeline["attributionRecords"],
        "completedCosts": pipeline["costRecords"],
        "overallStatus": status,
    }
    without_id = dict(metadata)
    identifier = without_id.pop("id", None)
    generated = _parse_timestamp(metadata.get("generatedAt"), code)
    completed = _parse_timestamp(measurements.get("completedAt"), code)
    if (
        report.get("apiVersion") != API_VERSION
        or report.get("kind") != REPORT_KIND
        or spec.get("qualificationLevel") != QUALIFICATION_LEVEL
        or spec.get("qualificationBoundary") != QUALIFICATION_BOUNDARY
        or metadata.get("sourceRevision") != subject.get("sourceRevision")
        or subject.get("attributionEngineVersion") != ATTRIBUTION_ENGINE_VERSION
        or subject.get("costEngineVersion") != COST_ENGINE_VERSION
        or bindings.get("profileDigest") != _digest(profile)
        or bindings.get("attributionPolicyId") != ATTRIBUTION_POLICY_ID
        or bindings.get("attributionPolicyVersion") != ATTRIBUTION_POLICY_VERSION
        or bindings.get("priceCatalogId") != PRICE_CATALOG_ID
        or bindings.get("priceCatalogVersion") != PRICE_CATALOG_VERSION
        or _mapping(spec.get("environment"), code).get("collectorImageDigest")
        != EXPECTED_COLLECTOR_IMAGE_DIGEST
        or generated != completed
        or list(checks) != expected_checks
        or tuple(item.get("id") for item in checks) != CHECK_IDS
        or spec.get("limitations") != list(LIMITATIONS)
        or spec.get("status") != status
        or summary != expected_summary
        or _has_forbidden_key(report)
        or not isinstance(identifier, str)
        or REPORT_ID.fullmatch(identifier) is None
        or identifier != _report_identifier(without_id, spec)
    ):
        _fail(code)


def _read_json(path: Path, *, maximum_bytes: int, code: str) -> Mapping[str, Any]:
    candidate = path.expanduser()
    descriptor = -1
    try:
        if candidate.is_symlink():
            _fail(code)
        descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode) or not 1 <= details.st_size <= maximum_bytes:
            _fail(code)
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            descriptor = -1
            raw = handle.read(maximum_bytes + 1)
        if len(raw) > maximum_bytes:
            _fail(code)
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _fail(code)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return _mapping(value, code)


def _source_identity() -> tuple[str, bool]:
    try:
        revision = subprocess.run(
            ("git", "rev-parse", "HEAD"),
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ("git", "status", "--porcelain", "--untracked-files=normal"),
                cwd=ROOT,
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        _fail("ai-finops-sustained-load.source.unavailable")
    if REVISION.fullmatch(revision) is None:
        _fail("ai-finops-sustained-load.source.unavailable")
    return revision, dirty


def _local_database_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        _fail("ai-finops-sustained-load.database.invalid")
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or port is None
        or parsed.password is not None
        or parsed.path != "/iip"
        or parsed.query
        or parsed.fragment
    ):
        _fail("ai-finops-sustained-load.database.invalid")
    return value


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    destination = path.expanduser().absolute()
    if destination.is_symlink():
        _fail("ai-finops-sustained-load.output.invalid")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.chmod(temporary, 0o644)
        os.replace(temporary, destination)
    except (OSError, RuntimeError):
        _fail("ai-finops-sustained-load.output.invalid")
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _distinct(paths: Sequence[Path]) -> None:
    try:
        selected = [
            path.expanduser().absolute().resolve(strict=False) for path in paths
        ]
    except OSError:
        _fail("ai-finops-sustained-load.paths.invalid")
    if len(selected) != len(set(selected)):
        _fail("ai-finops-sustained-load.paths.overlap")


def generate_report(
    *,
    allow_traffic: bool,
    profile_path: Path,
    report_path: Path,
    database_url: str,
    bedrock_collector: str,
    openai_collector: str,
    receiver: str,
    prometheus: str,
    grafana: str,
    source_revision: str,
    source_dirty: bool,
    platform_name: str,
    container_runtime_version: str,
    application_version: str,
    image_digest: str,
    collector_image_digest: str,
    compose_configuration_valid: bool,
    all_components_healthy: bool,
    runner: Callable[..., tuple[QualificationRun, str]] = run_qualification,
) -> Mapping[str, Any]:
    if allow_traffic is not True:
        _fail("ai-finops-sustained-load.traffic.enable-required")
    _distinct((profile_path, report_path))
    profile = load_profile(profile_path)
    for endpoint in (
        bedrock_collector,
        openai_collector,
        receiver,
        prometheus,
        grafana,
    ):
        _local_origin(endpoint)
    _local_database_url(database_url)
    current_revision, current_dirty = _source_identity()
    repository_version, migration = _repository_identity()
    if (
        source_revision != current_revision
        or source_dirty is not current_dirty
        or application_version != repository_version
    ):
        _fail("ai-finops-sustained-load.source.binding-mismatch")
    if (
        DIGEST.fullmatch(image_digest) is None
        or collector_image_digest != EXPECTED_COLLECTOR_IMAGE_DIGEST
    ):
        _fail("ai-finops-sustained-load.image.invalid")
    run_key = hashlib.sha256(
        _canonical(
            {
                "profileDigest": _digest(profile),
                "sourceRevision": source_revision,
                "startedAtNanoseconds": time.time_ns(),
            }
        )
    ).hexdigest()
    run, database_version = runner(
        profile=profile,
        database_url=database_url,
        bedrock_collector=bedrock_collector,
        openai_collector=openai_collector,
        receiver=receiver,
        prometheus=prometheus,
        grafana=grafana,
        run_key=run_key,
    )
    ending_revision, ending_dirty = _source_identity()
    if ending_revision != current_revision or ending_dirty is not current_dirty:
        _fail("ai-finops-sustained-load.source.changed-during-run")
    report = build_report(
        profile=profile,
        run=run,
        source_revision=source_revision,
        source_dirty=source_dirty,
        application_version=application_version,
        image_digest=image_digest,
        collector_image_digest=collector_image_digest,
        platform_name=platform_name,
        container_runtime_version=container_runtime_version,
        database_version=database_version,
        migration=migration,
        compose_configuration_valid=compose_configuration_valid,
        all_components_healthy=all_components_healthy,
    )
    _write_report(report_path, report)
    return report


def verify_report(
    *,
    profile_path: Path,
    report_path: Path,
    image_digest: str | None = None,
    require_clean: bool = False,
    require_qualified: bool = False,
) -> Mapping[str, Any]:
    _distinct((profile_path, report_path))
    profile = load_profile(profile_path)
    report = _read_json(
        report_path,
        maximum_bytes=MAX_REPORT_BYTES,
        code="ai-finops-sustained-load.report.unreadable",
    )
    validate_report_document(report)
    revision, dirty = _source_identity()
    application_version, migration = _repository_identity()
    metadata = _mapping(report.get("metadata"), "ai-finops-sustained-load.report.invalid")
    spec = _mapping(report.get("spec"), "ai-finops-sustained-load.report.invalid")
    subject = _mapping(spec.get("subject"), "ai-finops-sustained-load.report.invalid")
    bindings = _mapping(spec.get("bindings"), "ai-finops-sustained-load.report.invalid")
    environment = _mapping(spec.get("environment"), "ai-finops-sustained-load.report.invalid")
    database = _mapping(environment.get("database"), "ai-finops-sustained-load.report.invalid")
    profile_metadata = _mapping(profile.get("metadata"), "ai-finops-sustained-load.profile.invalid")
    profile_spec = _mapping(profile.get("spec"), "ai-finops-sustained-load.profile.invalid")
    if (
        metadata.get("sourceRevision") != revision
        or metadata.get("sourceDirty") is not dirty
        or subject.get("sourceRevision") != revision
        or subject.get("applicationVersion") != application_version
        or subject.get("attributionEngineVersion") != ATTRIBUTION_ENGINE_VERSION
        or subject.get("costEngineVersion") != COST_ENGINE_VERSION
        or database.get("migration") != migration
        or bindings.get("profileId") != profile_metadata.get("id")
        or bindings.get("profileDigest") != _digest(profile)
        or bindings.get("attributionPolicyId") != ATTRIBUTION_POLICY_ID
        or bindings.get("attributionPolicyVersion") != ATTRIBUTION_POLICY_VERSION
        or bindings.get("priceCatalogId") != PRICE_CATALOG_ID
        or bindings.get("priceCatalogVersion") != PRICE_CATALOG_VERSION
        or environment.get("collectorImageDigest")
        != EXPECTED_COLLECTOR_IMAGE_DIGEST
        or spec.get("workload") != profile_spec.get("workload")
        or spec.get("objectives") != profile_spec.get("objectives")
        or (image_digest is not None and subject.get("imageDigest") != image_digest)
    ):
        _fail("ai-finops-sustained-load.report.binding-mismatch")
    if require_clean and (dirty or metadata.get("sourceDirty") is not False):
        _fail("ai-finops-sustained-load.source.dirty")
    if require_qualified and spec.get("status") != "qualified":
        _fail("ai-finops-sustained-load.report.not-qualified")
    return report


def _parse_bool(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    profile_id = commands.add_parser("profile-id")
    profile_id.add_argument("--profile", type=Path, required=True)
    run = commands.add_parser("run")
    run.add_argument("--profile", type=Path, required=True)
    run.add_argument("--database", required=True)
    run.add_argument("--bedrock-collector", required=True)
    run.add_argument("--openai-collector", required=True)
    run.add_argument("--receiver", required=True)
    run.add_argument("--prometheus", required=True)
    run.add_argument("--grafana", required=True)
    run.add_argument("--report", type=Path, required=True)
    run.add_argument("--source-revision", required=True)
    run.add_argument("--source-dirty", type=_parse_bool, required=True)
    run.add_argument("--platform", dest="platform_name", required=True)
    run.add_argument("--container-runtime-version", required=True)
    run.add_argument("--application-version", required=True)
    run.add_argument("--image-digest", required=True)
    run.add_argument("--collector-image-digest", required=True)
    run.add_argument("--compose-configuration-valid", action="store_true")
    run.add_argument("--all-components-healthy", action="store_true")
    run.add_argument("--allow-traffic", action="store_true")
    verify = commands.add_parser("verify")
    verify.add_argument("--profile", type=Path, required=True)
    verify.add_argument("--report", type=Path, required=True)
    verify.add_argument("--image-digest")
    verify.add_argument("--require-clean", action="store_true")
    verify.add_argument("--require-qualified", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "profile-id":
            profile = _read_json(
                arguments.profile,
                maximum_bytes=MAX_PROFILE_BYTES,
                code="ai-finops-sustained-load.profile.unreadable",
            )
            metadata = _mapping(
                profile.get("metadata"),
                "ai-finops-sustained-load.profile.invalid",
            )
            spec = _mapping(
                profile.get("spec"), "ai-finops-sustained-load.profile.invalid"
            )
            without_id = dict(metadata)
            without_id.pop("id", None)
            candidate = dict(profile)
            candidate["metadata"] = {
                **without_id,
                "id": _profile_identifier(without_id, spec),
            }
            validate_profile(candidate)
            print(candidate["metadata"]["id"])
            return 0
        if arguments.command == "run":
            report = generate_report(
                allow_traffic=arguments.allow_traffic,
                profile_path=arguments.profile,
                report_path=arguments.report,
                database_url=arguments.database,
                bedrock_collector=arguments.bedrock_collector,
                openai_collector=arguments.openai_collector,
                receiver=arguments.receiver,
                prometheus=arguments.prometheus,
                grafana=arguments.grafana,
                source_revision=arguments.source_revision,
                source_dirty=arguments.source_dirty,
                platform_name=arguments.platform_name,
                container_runtime_version=arguments.container_runtime_version,
                application_version=arguments.application_version,
                image_digest=arguments.image_digest,
                collector_image_digest=arguments.collector_image_digest,
                compose_configuration_valid=arguments.compose_configuration_valid,
                all_components_healthy=arguments.all_components_healthy,
            )
            print(
                f"AI FinOps sustained load {report['spec']['status']}: "
                f"{arguments.report}"
            )
            return 0 if report["spec"]["status"] == "qualified" else 1
        verify_report(
            profile_path=arguments.profile,
            report_path=arguments.report,
            image_digest=arguments.image_digest,
            require_clean=arguments.require_clean,
            require_qualified=arguments.require_qualified,
        )
        print("AI FinOps sustained-load qualification report verified")
    except AiFinopsSustainedLoadError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
