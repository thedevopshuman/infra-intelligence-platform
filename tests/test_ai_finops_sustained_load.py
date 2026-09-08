from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import threading
import time
import urllib.error
import unittest
from datetime import datetime, timedelta, timezone
from email.message import Message
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import qualify_ai_finops_sustained_load as qualification  # noqa: E402
from infra_intelligence_sdk import (  # noqa: E402
    AiFinopsSustainedLoadProfile,
    AiFinopsSustainedLoadQualificationReport,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (  # noqa: E402
    ExportTraceServiceRequest,
)
from google.rpc.status_pb2 import Status as RpcStatus  # noqa: E402


REVISION = "0123456789abcdef0123456789abcdef01234567"
IMAGE_DIGEST = "sha256:" + "a" * 64
STARTED = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
COMPLETED = STARTED + timedelta(seconds=330)
MIGRATION = "0024_ai_invocation_correlation.sql"


def example(name: str) -> dict[str, object]:
    return json.loads(
        (ROOT / "contracts" / "examples" / name).read_text(encoding="utf-8")
    )


def raw_result(
    *,
    accepted: int = 3000,
    usage: int | None = None,
    attribution: int | None = None,
    cost: int | None = None,
    priced: int | None = None,
    source_duration_milliseconds: int = 330_000,
    backlog_sample_count: int = 300,
    cohort_isolation_preserved: bool = True,
) -> qualification.QualificationRun:
    usage = accepted if usage is None else usage
    attribution = usage if attribution is None else attribution
    cost = usage if cost is None else cost
    priced = cost if priced is None else priced
    attempts = tuple(
        qualification.ExportAttempt(
            qualification.PROVIDERS[index % 2],
            index < accepted,
            10,
            f"payload-{index}".encode(),
        )
        for index in range(3000)
    )
    provider_usage = usage // 2
    provider_attribution = attribution // 2
    provider_cost = cost // 2
    provider_priced = priced // 2
    snapshot = qualification.DatabaseSnapshot(
        usage_records=usage,
        attribution_records=attribution,
        cost_records=cost,
        priced_cost_records=priced,
        unpriced_cost_records=cost - priced,
        provider_counts={
            "aws.bedrock": (
                provider_usage,
                provider_attribution,
                provider_cost,
                provider_priced,
            ),
            "openai": (
                usage - provider_usage,
                attribution - provider_attribution,
                cost - provider_cost,
                priced - provider_priced,
            ),
        },
        attribution_latencies_milliseconds=tuple(1000 for _ in range(attribution)),
        cost_latencies_milliseconds=tuple(1200 for _ in range(cost)),
        content_preserved=False,
        cohort_isolation_preserved=cohort_isolation_preserved,
    )
    replay_scheduled = usage * 1000 // 10_000 if usage == len(attempts) else 0
    replay_attempts = tuple(
        qualification.ExportAttempt(
            qualification.PROVIDERS[index % 2], True, 9, b"replay"
        )
        for index in range(replay_scheduled)
    )
    return qualification.QualificationRun(
        generator=qualification.RawGeneratorResult(
            started_at=STARTED,
            completed_at=STARTED + timedelta(seconds=300),
            actual_duration_milliseconds=300_000,
            target_spans=3000,
            attempts=attempts,
            scheduler_missed_spans=0,
            scheduler_lags_milliseconds=tuple(1 for _ in range(3000)),
        ),
        replay_scheduled=replay_scheduled,
        replay_attempts=replay_attempts,
        usage_before_replay=usage,
        usage_after_replay=usage,
        final_snapshot=snapshot,
        peak_attribution_backlog=max(47, usage - attribution),
        peak_cost_backlog=max(52, usage - cost),
        backlog_sample_count=backlog_sample_count,
        drain_milliseconds=30_000,
        prometheus_usage=usage,
        prometheus_cost=cost,
        prometheus_converged=True,
        grafana_available=True,
        prohibited_labels_absent=True,
        content_rejected=True,
        completed_at=COMPLETED,
        actual_duration_milliseconds=source_duration_milliseconds,
    )


def build(
    *,
    raw: qualification.QualificationRun | None = None,
    source_dirty: bool = False,
) -> dict[str, object]:
    return dict(
        qualification.build_report(
            profile=example("ai-finops-sustained-load-profile.json"),
            run=raw or raw_result(),
            source_revision=REVISION,
            source_dirty=source_dirty,
            application_version="0.84.0",
            image_digest=IMAGE_DIGEST,
            collector_image_digest=qualification.EXPECTED_COLLECTOR_IMAGE_DIGEST,
            platform_name="linux/arm64",
            container_runtime_version="29.7.2",
            database_version="18.4",
            migration=MIGRATION,
            compose_configuration_valid=True,
            all_components_healthy=True,
        )
    )


def resign(report: dict[str, object]) -> None:
    metadata = report["metadata"]
    spec = report["spec"]
    without_id = dict(metadata)
    without_id.pop("id", None)
    metadata["id"] = qualification._report_identifier(without_id, spec)


class _FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.lock = threading.Lock()

    def monotonic(self) -> float:
        with self.lock:
            return self.value

    def sleep(self, seconds: float) -> None:
        with self.lock:
            self.value += seconds

    def advance(self, seconds: float) -> None:
        with self.lock:
            self.value += seconds


class AiFinopsSustainedLoadTests(unittest.TestCase):
    def test_semantic_volume_reserves_seeded_allocation_capacity(self) -> None:
        maximum = example("ai-finops-sustained-load-profile.json")
        maximum["spec"]["workload"]["durationSeconds"] = 312
        maximum["spec"]["workload"]["spansPerSecond"] = 32
        metadata = maximum["metadata"]
        without_id = dict(metadata)
        without_id.pop("id", None)
        metadata["id"] = qualification._profile_identifier(
            without_id, maximum["spec"]
        )
        qualification.validate_profile(maximum)
        self.assertEqual(312 * 32, qualification.MAX_SPANS)

        too_large = example("ai-finops-sustained-load-profile.json")
        too_large["spec"]["workload"]["durationSeconds"] = 400
        too_large["spec"]["workload"]["spansPerSecond"] = 25
        metadata = too_large["metadata"]
        without_id = dict(metadata)
        without_id.pop("id", None)
        metadata["id"] = qualification._profile_identifier(
            without_id, too_large["spec"]
        )
        with self.assertRaisesRegex(
            qualification.AiFinopsSustainedLoadError,
            "ai-finops-sustained-load.profile.invalid",
        ):
            qualification.validate_profile(too_large)

    def test_examples_validate_and_sdk_envelopes_round_trip(self) -> None:
        profile = example("ai-finops-sustained-load-profile.json")
        report = example("ai-finops-sustained-load-qualification-report.json")
        qualification.validate_profile(profile)
        qualification.validate_report_document(report)
        self.assertEqual(
            qualification._digest(profile),
            "sha256:cbdab6511fde230d52824d38a91b9b5b9af19e03c8c561d3968a16513675bc23",
        )
        self.assertEqual(
            AiFinopsSustainedLoadProfile.from_dict(profile).to_dict(), profile
        )
        self.assertEqual(
            AiFinopsSustainedLoadQualificationReport.from_dict(report).to_dict(),
            report,
        )

    def test_builder_produces_qualified_minimized_evidence(self) -> None:
        report = build()
        qualification.validate_report_document(report)
        self.assertEqual(report["spec"]["status"], "qualified")
        self.assertEqual(len(report["spec"]["checks"]), 19)
        self.assertEqual(report["spec"]["summary"]["failedChecks"], 0)
        self.assertEqual(
            report["spec"]["subject"]["attributionEngineVersion"],
            qualification.ATTRIBUTION_ENGINE_VERSION,
        )
        self.assertEqual(
            report["spec"]["bindings"],
            {
                "profileId": report["spec"]["bindings"]["profileId"],
                "profileDigest": report["spec"]["bindings"]["profileDigest"],
                "attributionPolicyId": qualification.ATTRIBUTION_POLICY_ID,
                "attributionPolicyVersion": qualification.ATTRIBUTION_POLICY_VERSION,
                "priceCatalogId": qualification.PRICE_CATALOG_ID,
                "priceCatalogVersion": qualification.PRICE_CATALOG_VERSION,
            },
        )
        self.assertFalse(qualification._has_forbidden_key(report))
        self.assertNotIn(
            qualification.CONTENT_SENTINEL,
            json.dumps(report, sort_keys=True),
        )

    def test_objective_miss_is_valid_not_qualified_evidence(self) -> None:
        report = build(raw=raw_result(accepted=2900))
        qualification.validate_report_document(report)
        generator = report["spec"]["measurements"]["generator"]
        self.assertEqual(generator["collectorAcceptanceBasisPoints"], 9666)
        self.assertEqual(report["spec"]["status"], "not-qualified")
        failed = {
            item["id"]
            for item in report["spec"]["checks"]
            if item["status"] == "failed"
        }
        self.assertIn("collector-acceptance", failed)
        self.assertIn("usage-ledger-persistence", failed)

        diagnostic = qualification._failure_diagnostic(report)
        self.assertEqual(diagnostic["summary"], report["spec"]["summary"])
        self.assertEqual(
            set(diagnostic["failedChecks"]),
            failed,
        )
        self.assertFalse(qualification._has_forbidden_key(diagnostic))

    def test_absent_workload_backlog_sampling_is_valid_not_qualified_evidence(self) -> None:
        report = build(raw=raw_result(backlog_sample_count=0))
        qualification.validate_report_document(report)
        self.assertEqual(report["spec"]["status"], "not-qualified")
        pipeline = report["spec"]["measurements"]["pipeline"]
        self.assertEqual(pipeline["backlogSampleCount"], 0)
        failed = {
            item["id"]
            for item in report["spec"]["checks"]
            if item["status"] == "failed"
        }
        self.assertIn("pipeline-drain", failed)

    def test_foreign_post_marker_usage_fails_prometheus_convergence(self) -> None:
        report = build(
            raw=raw_result(cohort_isolation_preserved=False),
        )
        qualification.validate_report_document(report)
        observability = report["spec"]["measurements"]["observability"]
        self.assertFalse(observability["cohortIsolationPreserved"])
        self.assertEqual(report["spec"]["status"], "not-qualified")
        failed = {
            item["id"]
            for item in report["spec"]["checks"]
            if item["status"] == "failed"
        }
        self.assertIn("prometheus-convergence", failed)

    def test_durable_usage_can_exceed_observed_collector_success(self) -> None:
        report = build(raw=raw_result(accepted=2900, usage=3000))
        qualification.validate_report_document(report)
        self.assertEqual(report["spec"]["status"], "not-qualified")
        generator = report["spec"]["measurements"]["generator"]
        pipeline = report["spec"]["measurements"]["pipeline"]
        self.assertEqual(generator["collectorAcceptedSpans"], 2900)
        self.assertEqual(pipeline["usageRecords"], 3000)
        failed = {
            item["id"]
            for item in report["spec"]["checks"]
            if item["status"] == "failed"
        }
        self.assertIn("collector-acceptance", failed)
        self.assertNotIn("usage-ledger-persistence", failed)
        self.assertNotIn("replay-idempotency", failed)

    def test_semantic_validation_rejects_shortened_window_after_resigning(self) -> None:
        report = build()
        report["spec"]["measurements"]["actualDurationMilliseconds"] = 1
        resign(report)
        with self.assertRaisesRegex(
            qualification.AiFinopsSustainedLoadError,
            "ai-finops-sustained-load.report.invalid",
        ):
            qualification.validate_report_document(report)

    def test_semantic_validation_binds_replay_pacing_to_reported_time(self) -> None:
        report = build()
        measurements = report["spec"]["measurements"]
        measurements["pipeline"]["drainMilliseconds"] = 29_899
        resign(report)
        with self.assertRaisesRegex(
            qualification.AiFinopsSustainedLoadError,
            "ai-finops-sustained-load.report.invalid",
        ):
            qualification.validate_report_document(report)

        report = build()
        measurements = report["spec"]["measurements"]
        measurements["actualDurationMilliseconds"] = 329_899
        resign(report)
        with self.assertRaisesRegex(
            qualification.AiFinopsSustainedLoadError,
            "ai-finops-sustained-load.report.invalid",
        ):
            qualification.validate_report_document(report)

    def test_actual_duration_bound_covers_legal_worst_case_overhead(self) -> None:
        workload = dict(
            example("ai-finops-sustained-load-profile.json")["spec"]["workload"]
        )
        workload.update(
            {
                "durationSeconds": 1800,
                "requestTimeoutMilliseconds": 30000,
                "maximumSchedulerLagMilliseconds": 5000,
                "maximumPipelineDrainMilliseconds": 300000,
            }
        )
        self.assertEqual(
            qualification._maximum_actual_duration_milliseconds(workload),
            2_381_000,
        )

    def test_provider_compensation_cannot_hide_an_invalid_provider_chain(self) -> None:
        report = build()
        providers = report["spec"]["measurements"]["providers"]
        providers[0]["usageRecords"] = 1499
        providers[1]["usageRecords"] = 1501
        resign(report)
        with self.assertRaisesRegex(
            qualification.AiFinopsSustainedLoadError,
            "ai-finops-sustained-load.report.invalid",
        ):
            qualification.validate_report_document(report)

    def test_tampered_identity_and_derived_check_are_rejected(self) -> None:
        report = build()
        crossed = copy.deepcopy(report)
        crossed["metadata"]["id"] = "afslq_" + "f" * 32
        with self.assertRaises(qualification.AiFinopsSustainedLoadError):
            qualification.validate_report_document(crossed)
        crossed = copy.deepcopy(report)
        crossed["spec"]["checks"][0] = {
            "id": "source-binding",
            "status": "failed",
            "errorCode": "ai-finops-sustained-load.source.not-clean-bound",
        }
        resign(crossed)
        with self.assertRaises(qualification.AiFinopsSustainedLoadError):
            qualification.validate_report_document(crossed)
        crossed = copy.deepcopy(report)
        crossed["spec"]["environment"]["collectorImageDigest"] = (
            "sha256:" + "f" * 64
        )
        resign(crossed)
        with self.assertRaises(qualification.AiFinopsSustainedLoadError):
            qualification.validate_report_document(crossed)

    def test_fixed_rate_scheduler_skips_late_slots_without_catch_up(self) -> None:
        fake = _FakeClock()
        calls: list[int] = []
        scheduler_started = threading.Event()

        def factory():
            def send(slot: int) -> qualification.ExportAttempt:
                self.assertTrue(scheduler_started.is_set())
                calls.append(slot)
                if slot == 0:
                    fake.advance(5)
                return qualification.ExportAttempt(
                    qualification.PROVIDERS[slot % 2], True, 1
                )

            return send

        result = qualification._run_fixed_rate(
            duration_seconds=1,
            spans_per_second=4,
            producer_concurrency=1,
            maximum_scheduler_lag_milliseconds=100,
            sender_factory=factory,
            on_started=scheduler_started.set,
            clock=lambda: STARTED,
            monotonic=fake.monotonic,
            sleeper=fake.sleep,
        )
        self.assertEqual(result.target_spans, 4)
        self.assertEqual(calls, [0])
        self.assertEqual(result.scheduler_missed_spans, 3)
        self.assertEqual(len(result.attempts) + result.scheduler_missed_spans, 4)
        self.assertEqual(len(result.scheduler_lags_milliseconds), 4)

    def test_otlp_response_semantics_reject_partial_and_malformed_success(self) -> None:
        class Headers:
            def get_content_type(self) -> str:
                return "application/x-protobuf"

        class Response:
            status = 200
            headers = Headers()

            def __init__(self, body: bytes) -> None:
                self.body = body

            def __enter__(self):
                return self

            def __exit__(self, *args):  # noqa: ANN002
                return False

            def read(self, maximum: int) -> bytes:
                self.assert_bound = maximum
                return self.body

        class Opener:
            def __init__(self, body: bytes) -> None:
                self.body = body

            def open(self, request, timeout):  # noqa: ANN001
                del request, timeout
                return Response(self.body)

        self.assertTrue(
            qualification._post_protobuf(
                Opener(b""),
                url="http://127.0.0.1:4318/v1/traces",
                payload=b"request",
                timeout_milliseconds=1000,
            )[0]
        )
        rejected = qualification.ExportTraceServiceResponse()
        rejected.partial_success.rejected_spans = 1
        self.assertFalse(
            qualification._post_protobuf(
                Opener(rejected.SerializeToString()),
                url="http://127.0.0.1:4318/v1/traces",
                payload=b"request",
                timeout_milliseconds=1000,
            )[0]
        )
        self.assertFalse(
            qualification._post_protobuf(
                Opener(b"\xff"),
                url="http://127.0.0.1:4318/v1/traces",
                payload=b"request",
                timeout_milliseconds=1000,
            )[0]
        )

    def test_replay_selects_deterministic_recent_durable_tail(self) -> None:
        fake = _FakeClock()
        attempts = tuple(
            qualification.ExportAttempt(
                qualification.PROVIDERS[index % 2], index % 3 != 0, 1, str(index).encode()
            )
            for index in range(10)
        )

        class Sender:
            def replay(self, attempt):  # noqa: ANN001
                return attempt

        selected = qualification._replay(
            attempts,
            scheduled=3,
            sender=Sender(),
            concurrency=2,
            spans_per_second=2,
            deadline=10,
            monotonic=fake.monotonic,
            sleeper=fake.sleep,
        )
        self.assertEqual([item.payload for item in selected], [b"7", b"8", b"9"])
        self.assertEqual(fake.value, 1.0)

    def test_replay_is_rate_deadline_and_concurrency_bounded(self) -> None:
        fake = _FakeClock()
        attempts = tuple(
            qualification.ExportAttempt("aws.bedrock", True, 1, bytes([index]))
            for index in range(5)
        )

        class ImmediateSender:
            def replay(self, attempt):  # noqa: ANN001
                return attempt

        selected = qualification._replay(
            attempts,
            scheduled=5,
            sender=ImmediateSender(),
            concurrency=2,
            spans_per_second=2,
            deadline=0.75,
            monotonic=fake.monotonic,
            sleeper=fake.sleep,
        )
        self.assertEqual(len(selected), 2)

        class LateSender:
            def replay(self, attempt):  # noqa: ANN001
                time.sleep(0.03)
                return attempt

        late = qualification._replay(
            attempts[:1],
            scheduled=1,
            sender=LateSender(),
            concurrency=1,
            spans_per_second=40,
            deadline=time.monotonic() + 0.005,
        )
        self.assertEqual(len(late), 1)
        self.assertFalse(late[0].success)

        active = 0
        maximum_active = 0
        lock = threading.Lock()

        class BlockingSender:
            def replay(self, attempt):  # noqa: ANN001
                nonlocal active, maximum_active
                with lock:
                    active += 1
                    maximum_active = max(maximum_active, active)
                time.sleep(0.01)
                with lock:
                    active -= 1
                return attempt

        qualification._replay(
            attempts,
            scheduled=5,
            sender=BlockingSender(),
            concurrency=2,
            spans_per_second=40,
            deadline=time.monotonic() + 2,
        )
        self.assertLessEqual(maximum_active, 2)

    def test_receiver_replay_uses_provider_token_and_commit_bound_endpoint(self) -> None:
        sender = qualification.ReceiverReplaySender(
            receiver="http://127.0.0.1:4318",
            timeout_milliseconds=1000,
        )
        attempts = (
            qualification.ExportAttempt("aws.bedrock", False, 1, b"bedrock"),
            qualification.ExportAttempt("openai", False, 1, b"openai"),
        )
        with patch.object(
            qualification,
            "_post_protobuf",
            return_value=(True, 3),
        ) as post:
            results = tuple(sender.replay(item) for item in attempts)
        self.assertTrue(all(item.success for item in results))
        self.assertEqual(
            [call.kwargs["url"] for call in post.call_args_list],
            ["http://127.0.0.1:4318/v1/traces"] * 2,
        )
        self.assertEqual(
            [call.kwargs["token"] for call in post.call_args_list],
            [
                qualification.ai_finops_fixture.CHANNEL_TOKEN,
                qualification.ai_finops_fixture.OPENAI_CHANNEL_TOKEN,
            ],
        )

    def test_raw_payloads_have_unique_identity_and_alternating_provider_shape(self) -> None:
        first = qualification._trace_payload(
            provider="aws.bedrock",
            identity=b"a" * 32,
            timestamp_ns=1_000_000,
        )
        next_bedrock = qualification._trace_payload(
            provider="aws.bedrock",
            identity=b"c" * 32,
            timestamp_ns=3_000_000,
        )
        second = qualification._trace_payload(
            provider="openai",
            identity=b"b" * 32,
            timestamp_ns=2_000_000,
        )
        bedrock = ExportTraceServiceRequest.FromString(first)
        other_bedrock = ExportTraceServiceRequest.FromString(next_bedrock)
        openai = ExportTraceServiceRequest.FromString(second)
        bedrock_span = bedrock.resource_spans[0].scope_spans[0].spans[0]
        openai_span = openai.resource_spans[0].scope_spans[0].spans[0]
        self.assertNotEqual(bedrock_span.trace_id, openai_span.trace_id)
        self.assertNotEqual(bedrock_span.start_time_unix_nano, openai_span.start_time_unix_nano)
        bedrock_keys = {item.key for item in bedrock_span.attributes}
        openai_keys = {item.key for item in openai_span.attributes}
        self.assertIn("gen_ai.system", bedrock_keys)
        self.assertIn("aws.request_id", bedrock_keys)
        self.assertIn("gen_ai.provider.name", openai_keys)
        self.assertNotIn("aws.request_id", openai_keys)
        self.assertNotIn("gen_ai.prompt", bedrock_keys | openai_keys)
        request_id = next(
            item.value.string_value
            for item in bedrock_span.attributes
            if item.key == "aws.request_id"
        )
        other_request_id = next(
            item.value.string_value
            for item in other_bedrock.resource_spans[0].scope_spans[0].spans[0].attributes
            if item.key == "aws.request_id"
        )
        self.assertNotEqual(request_id, other_request_id)

    def test_run_trace_prefix_is_unique_and_database_queries_filter_the_cohort(self) -> None:
        prefix = qualification._run_trace_prefix("run-one")
        other = qualification._run_trace_prefix("run-two")
        self.assertEqual(len(prefix), 12)
        self.assertNotEqual(prefix, other)
        first = qualification._slot_identity(prefix, 0)
        second = qualification._slot_identity(prefix, 1)
        self.assertTrue(first[:16].startswith(prefix))
        self.assertTrue(second[:16].startswith(prefix))
        self.assertNotEqual(first[:16], second[:16])

        class Cursor:
            def __init__(self, one=None, many=None):
                self.one = one
                self.many = [] if many is None else many

            def fetchone(self):
                return self.one

            def fetchall(self):
                return self.many

        class Connection:
            def __init__(self):
                self.calls = []

            def __enter__(self):
                return self

            def __exit__(self, *args):  # noqa: ANN002
                return False

            def execute(self, statement, parameters):  # noqa: ANN001
                self.calls.append((statement, parameters))
                index = len(self.calls)
                if index == 1:
                    return Cursor((0, 0, 0, 0, 0))
                if index == 2:
                    return Cursor(many=[])
                if index == 3:
                    return Cursor(many=[])
                return Cursor((0, 0))

        connection = Connection()
        with patch.object(
            qualification.psycopg, "connect", return_value=connection
        ) as connect:
            snapshot = qualification._database_snapshot(
                "postgresql://iip@127.0.0.1:5432/iip",
                STARTED,
                prefix.hex(),
            )
        self.assertTrue(snapshot.cohort_isolation_preserved)
        self.assertEqual(connect.call_args.kwargs["connect_timeout"], 5)
        self.assertIn("statement_timeout=5000", connect.call_args.kwargs["options"])
        self.assertEqual(len(connection.calls), 4)
        generation = (
            qualification.ATTRIBUTION_POLICY_ID,
            qualification.ATTRIBUTION_POLICY_VERSION,
            qualification.ATTRIBUTION_ENGINE_VERSION,
            qualification.PRICE_CATALOG_ID,
            qualification.PRICE_CATALOG_VERSION,
            qualification.COST_ENGINE_VERSION,
            STARTED,
            prefix.hex(),
        )
        for statement, parameters in connection.calls[:2]:
            self.assertIn("traceId", statement)
            self.assertIn("engine_version = %s", statement)
            self.assertIn("DISTINCT", statement)
            self.assertEqual(parameters, generation)
        latency_statement, latency_parameters = connection.calls[2]
        self.assertIn("traceId", latency_statement)
        self.assertIn("engine_version = %s", latency_statement)
        self.assertNotIn("SELECT DISTINCT", latency_statement)
        self.assertEqual(latency_parameters, generation)
        content_statement, content_parameters = connection.calls[3]
        self.assertIn("IS DISTINCT FROM %s", content_statement)
        self.assertEqual(
            content_parameters,
            (
                prefix.hex(),
                f"%{qualification.CONTENT_SENTINEL}%",
                STARTED,
            ),
        )

    def test_privacy_label_scan_ignores_metric_name_but_scans_keys_and_values(self) -> None:
        safe = [{"metric": {"__name__": "iip_ai_allocation_requests", "team": "ops"}}]
        with patch.object(qualification, "_prometheus_query", return_value=safe):
            self.assertTrue(qualification._prohibited_labels_absent("http://127.0.0.1:1"))
        prohibited_key = [{"metric": {"__name__": "iip_ai_cost_requests", "trace_id": "x"}}]
        with patch.object(
            qualification, "_prometheus_query", return_value=prohibited_key
        ):
            self.assertFalse(qualification._prohibited_labels_absent("http://127.0.0.1:1"))
        prohibited_value = [{"metric": {"__name__": "iip_ai_cost_requests", "team": "prompt-lab"}}]
        with patch.object(
            qualification, "_prometheus_query", return_value=prohibited_value
        ):
            self.assertFalse(qualification._prohibited_labels_absent("http://127.0.0.1:1"))

    def test_both_provider_content_probes_must_be_rejected(self) -> None:
        requests = []
        protobuf_headers = Message()
        protobuf_headers["content-type"] = "application/x-protobuf"
        content_rejection = RpcStatus(
            message="otlp.span.content-prohibited"
        ).SerializeToString()

        class RejectingOpener:
            def open(self, request, timeout):  # noqa: ANN001
                del timeout
                requests.append(request)
                raise urllib.error.HTTPError(
                    request.full_url,
                    400,
                    "rejected",
                    hdrs=protobuf_headers,
                    fp=io.BytesIO(content_rejection),
                )

        with patch.object(qualification, "_opener", return_value=RejectingOpener()):
            self.assertTrue(
                qualification._content_rejected("http://127.0.0.1:4318", 1000, "run")
            )
        self.assertEqual(len(requests), 2)
        self.assertNotEqual(
            requests[0].headers["Authorization"], requests[1].headers["Authorization"]
        )
        for request in requests:
            parsed = ExportTraceServiceRequest.FromString(request.data)
            keys = {
                item.key
                for item in parsed.resource_spans[0].scope_spans[0].spans[0].attributes
            }
            self.assertIn("gen_ai.prompt", keys)

        class MixedOpener(RejectingOpener):
            def open(self, request, timeout):  # noqa: ANN001
                if "openai" in request.headers.get("Authorization", ""):
                    return Mock(
                        __enter__=lambda self: self,
                        __exit__=lambda *args: False,
                        read=lambda limit: b"",
                    )
                return super().open(request, timeout)

        with patch.object(qualification, "_opener", return_value=MixedOpener()):
            self.assertFalse(
                qualification._content_rejected("http://127.0.0.1:4318", 1000, "run")
            )

        wrong_rejection = RpcStatus(message="otlp.attribute.required").SerializeToString()

        class WrongReasonOpener(RejectingOpener):
            def open(self, request, timeout):  # noqa: ANN001
                del timeout
                raise urllib.error.HTTPError(
                    request.full_url,
                    400,
                    "rejected",
                    hdrs=protobuf_headers,
                    fp=io.BytesIO(wrong_rejection),
                )

        with patch.object(qualification, "_opener", return_value=WrongReasonOpener()):
            self.assertFalse(
                qualification._content_rejected("http://127.0.0.1:4318", 1000, "run")
            )

    def test_offline_verify_rebinds_profile_source_application_and_image(self) -> None:
        report = build()
        with tempfile.TemporaryDirectory() as temporary:
            report_path = Path(temporary) / "report.json"
            report_path.write_text(json.dumps(report), encoding="utf-8")
            with (
                patch.object(qualification, "_source_identity", return_value=(REVISION, False)),
                patch.object(
                    qualification,
                    "_repository_identity",
                    return_value=("0.84.0", MIGRATION),
                ),
                patch.object(
                    qualification,
                    "_opener",
                    side_effect=AssertionError("verification generated traffic"),
                ),
            ):
                selected = qualification.verify_report(
                    profile_path=ROOT
                    / "contracts/examples/ai-finops-sustained-load-profile.json",
                    report_path=report_path,
                    image_digest=IMAGE_DIGEST,
                    require_clean=True,
                    require_qualified=True,
                )
                self.assertEqual(selected["metadata"]["id"], report["metadata"]["id"])
                with self.assertRaisesRegex(
                    qualification.AiFinopsSustainedLoadError,
                    "ai-finops-sustained-load.report.binding-mismatch",
                ):
                    qualification.verify_report(
                        profile_path=ROOT
                        / "contracts/examples/ai-finops-sustained-load-profile.json",
                        report_path=report_path,
                        image_digest="sha256:" + "f" * 64,
                    )

    def test_explicit_traffic_and_atomic_output_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "report.json"
            with self.assertRaisesRegex(
                qualification.AiFinopsSustainedLoadError,
                "ai-finops-sustained-load.traffic.enable-required",
            ):
                qualification.generate_report(
                    allow_traffic=False,
                    profile_path=ROOT
                    / "contracts/examples/ai-finops-sustained-load-profile.json",
                    report_path=output,
                    database_url="postgresql://iip@127.0.0.1:5432/iip",
                    bedrock_collector="http://127.0.0.1:4318",
                    openai_collector="http://127.0.0.1:4319",
                    receiver="http://127.0.0.1:4320",
                    prometheus="http://127.0.0.1:9090",
                    grafana="http://127.0.0.1:3000",
                    source_revision=REVISION,
                    source_dirty=False,
                    platform_name="linux/arm64",
                    container_runtime_version="29.7.2",
                    application_version="0.84.0",
                    image_digest=IMAGE_DIGEST,
                    collector_image_digest=(
                        qualification.EXPECTED_COLLECTOR_IMAGE_DIGEST
                    ),
                    compose_configuration_valid=True,
                    all_components_healthy=True,
                )
            self.assertFalse(output.exists())

            runner = Mock(return_value=(raw_result(), "18.4"))
            with (
                patch.object(qualification, "_source_identity", return_value=(REVISION, False)),
                patch.object(
                    qualification,
                    "_repository_identity",
                    return_value=("0.84.0", MIGRATION),
                ),
            ):
                report = qualification.generate_report(
                    allow_traffic=True,
                    profile_path=ROOT
                    / "contracts/examples/ai-finops-sustained-load-profile.json",
                    report_path=output,
                    database_url="postgresql://iip@127.0.0.1:5432/iip",
                    bedrock_collector="http://127.0.0.1:4318",
                    openai_collector="http://127.0.0.1:4319",
                    receiver="http://127.0.0.1:4320",
                    prometheus="http://127.0.0.1:9090",
                    grafana="http://127.0.0.1:3000",
                    source_revision=REVISION,
                    source_dirty=False,
                    platform_name="linux/arm64",
                    container_runtime_version="29.7.2",
                    application_version="0.84.0",
                    image_digest=IMAGE_DIGEST,
                    collector_image_digest=(
                        qualification.EXPECTED_COLLECTOR_IMAGE_DIGEST
                    ),
                    compose_configuration_valid=True,
                    all_components_healthy=True,
                    runner=runner,
                )
            self.assertEqual(json.loads(output.read_text()), report)
            self.assertFalse(list(output.parent.glob(f".{output.name}.*")))

            changed_output = Path(temporary) / "changed.json"
            with (
                patch.object(
                    qualification,
                    "_source_identity",
                    side_effect=((REVISION, False), (REVISION, True)),
                ),
                patch.object(
                    qualification,
                    "_repository_identity",
                    return_value=("0.84.0", MIGRATION),
                ),
                self.assertRaisesRegex(
                    qualification.AiFinopsSustainedLoadError,
                    "ai-finops-sustained-load.source.changed-during-run",
                ),
            ):
                qualification.generate_report(
                    allow_traffic=True,
                    profile_path=ROOT
                    / "contracts/examples/ai-finops-sustained-load-profile.json",
                    report_path=changed_output,
                    database_url="postgresql://iip@127.0.0.1:5432/iip",
                    bedrock_collector="http://127.0.0.1:4318",
                    openai_collector="http://127.0.0.1:4319",
                    receiver="http://127.0.0.1:4320",
                    prometheus="http://127.0.0.1:9090",
                    grafana="http://127.0.0.1:3000",
                    source_revision=REVISION,
                    source_dirty=False,
                    platform_name="linux/arm64",
                    container_runtime_version="29.7.2",
                    application_version="0.84.0",
                    image_digest=IMAGE_DIGEST,
                    collector_image_digest=(
                        qualification.EXPECTED_COLLECTOR_IMAGE_DIGEST
                    ),
                    compose_configuration_valid=True,
                    all_components_healthy=True,
                    runner=runner,
                )
            self.assertFalse(changed_output.exists())

    def test_profile_reader_and_path_alias_checks_reject_symlinks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            profile_path = Path(temporary) / "profile.json"
            profile_path.write_text(
                json.dumps(example("ai-finops-sustained-load-profile.json")),
                encoding="utf-8",
            )
            linked = Path(temporary) / "linked.json"
            linked.symlink_to(profile_path)
            with self.assertRaisesRegex(
                qualification.AiFinopsSustainedLoadError,
                "ai-finops-sustained-load.profile.unreadable",
            ):
                qualification.load_profile(linked)
            with self.assertRaisesRegex(
                qualification.AiFinopsSustainedLoadError,
                "ai-finops-sustained-load.paths.overlap",
            ):
                qualification._distinct((profile_path, linked))

    def test_run_does_not_replay_before_exact_original_durability(self) -> None:
        generator = qualification.RawGeneratorResult(
            started_at=STARTED,
            completed_at=STARTED + timedelta(seconds=300),
            actual_duration_milliseconds=300_000,
            target_spans=3000,
            attempts=(
                qualification.ExportAttempt("aws.bedrock", True, 1, b"a"),
                qualification.ExportAttempt("openai", True, 1, b"b"),
            ),
            scheduler_missed_spans=2998,
            scheduler_lags_milliseconds=tuple(0 for _ in range(3000)),
        )
        snapshot = qualification.DatabaseSnapshot(
            usage_records=1,
            attribution_records=1,
            cost_records=1,
            priced_cost_records=1,
            unpriced_cost_records=0,
            provider_counts={"aws.bedrock": (1, 1, 1, 1)},
            attribution_latencies_milliseconds=(1,),
            cost_latencies_milliseconds=(1,),
            content_preserved=False,
            cohort_isolation_preserved=True,
        )

        class Monitor:
            def __init__(self, callback):
                del callback

            def start(self):
                return None

            def stop(self, final):
                del final
                return 0, 0, 1

        with (
            patch.object(qualification, "_database_marker", return_value=STARTED),
            patch.object(qualification, "_database_version", return_value="18.4"),
            patch.object(qualification, "_database_snapshot", return_value=snapshot),
            patch.object(
                qualification,
                "_prometheus_totals",
                side_effect=((0, 0), (1, 1), (1, 1)),
            ),
            patch.object(qualification, "_run_fixed_rate", return_value=generator),
            patch.object(qualification, "_wait_for_usage", return_value=snapshot),
            patch.object(qualification, "_wait_for_pipeline", return_value=snapshot),
            patch.object(qualification, "_content_rejected", return_value=True),
            patch.object(qualification, "_grafana_available", return_value=True),
            patch.object(qualification, "_prohibited_labels_absent", return_value=True),
            patch.object(qualification, "SnapshotMonitor", Monitor),
            patch.object(qualification, "_replay") as replay,
        ):
            result, _version = qualification.run_qualification(
                profile=example("ai-finops-sustained-load-profile.json"),
                database_url="postgresql://iip@127.0.0.1:5432/iip",
                bedrock_collector="http://127.0.0.1:4318",
                openai_collector="http://127.0.0.1:4319",
                receiver="http://127.0.0.1:4320",
                prometheus="http://127.0.0.1:9090",
                grafana="http://127.0.0.1:3000",
                run_key="run",
            )
        replay.assert_not_called()
        self.assertEqual(result.replay_scheduled, 0)
        self.assertEqual(result.replay_attempts, ())
        self.assertEqual(result.usage_before_replay, 1)

    def test_shell_reuses_one_compose_stack_in_required_order(self) -> None:
        shell = (ROOT / "scripts/test_ai_finops_sustained_load.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'IIP_COMPOSE_PROJECT="iip-ai-finops-test-${IIP_RUN_SUFFIX}"', shell
        )
        self.assertIn("IIP_AI_FINOPS_POSTGRES_PORT=0", shell)
        self.assertIn("published_port postgres 5432", shell)
        self.assertIn("down --volumes", shell)
        send = shell.index("scripts/ai_finops_fixture.py send")
        verify = shell.index("scripts/ai_finops_fixture.py verify")
        sustained = shell.index("scripts/qualify_ai_finops_sustained_load.py run")
        self.assertLess(send, verify)
        self.assertLess(verify, sustained)
        self.assertIn("--allow-traffic", shell)
        self.assertIn("--collector-image-digest", shell)
        self.assertGreaterEqual(shell.count("expected_stack_healthy"), 3)
        self.assertIn("checked_cleanup", shell)
        self.assertIn("IIP_AI_FINOPS_PENDING_REPORT", shell)


if __name__ == "__main__":
    unittest.main()
