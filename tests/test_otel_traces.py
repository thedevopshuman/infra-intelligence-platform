from __future__ import annotations

import copy
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from iip.adapters.auth import HashedBearerAuthenticator
from iip.adapters.evidence import (
    InMemoryEvidenceStore,
    ResourceStateEvidenceProvider,
    StructuredTextRedactor,
    UuidEvidenceIdGenerator,
)
from iip.adapters.memory import AllowTenantPolicy, InMemoryResourceStore
from iip.adapters.operations import InMemoryOperationalStore
from iip.adapters.otel import (
    OpenTelemetryConfigurationError,
    OpenTelemetryInvestigationSink,
    OtlpTracesConfiguration,
    OtlpTracesRuntime,
)
from iip.application.collect_evidence import EvidenceCollectionService
from iip.application.ingest_resource import IngestResourceCommand, ResourceIngestionService
from iip.application.investigate import (
    DeterministicInvestigationService,
    RunInvestigationCommand,
)
from iip.application.ports import ActorContext, InvestigationExecutionMeasurement
from iip.bootstrap import build_runtime_from_env


ROOT = Path(__file__).resolve().parents[1]


def example(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "examples" / name).read_text())


def iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def measurement(*, outcome: str = "conclusive") -> InvestigationExecutionMeasurement:
    return InvestigationExecutionMeasurement(
        tenant_id="local",
        investigation_id="inv_0123456789abcdef0123456789abcdef",
        outcome=outcome,
        terminal_reason=(
            "runtime-error" if outcome == "failed" else "sufficient-evidence"
        ),
        started_at="2026-08-17T10:00:00Z",
        completed_at="2026-08-17T10:00:02.5Z",
        wall_time_seconds=2.5,
        tool_calls=3,
        evidence_items=2,
    )


class OtlpTracesConfigurationTests(unittest.TestCase):
    def test_standard_endpoint_and_bounded_queue_configuration(self) -> None:
        configuration = OtlpTracesConfiguration.from_environment(
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.example/otlp",
                "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
                "IIP_OTEL_INVESTIGATION_ATTRIBUTE_MODE": "tenant-investigation",
                "OTEL_BSP_SCHEDULE_DELAY": "1000",
                "OTEL_BSP_EXPORT_TIMEOUT": "3000",
                "OTEL_BSP_MAX_QUEUE_SIZE": "128",
                "OTEL_BSP_MAX_EXPORT_BATCH_SIZE": "32",
            }
        )

        self.assertEqual(
            configuration.endpoint, "https://collector.example/otlp/v1/traces"
        )
        self.assertEqual(configuration.attribute_mode, "tenant-investigation")
        self.assertEqual(configuration.max_queue_size, 128)
        self.assertEqual(configuration.max_export_batch_size, 32)

    def test_signal_endpoint_is_exact_and_unsafe_values_fail_closed(self) -> None:
        exact = OtlpTracesConfiguration.from_environment(
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://ignored.example/base",
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "https://trace.example/custom",
            }
        )
        self.assertEqual(exact.endpoint, "https://trace.example/custom")

        invalid_environments = (
            {},
            {"OTEL_EXPORTER_OTLP_ENDPOINT": "grpc://collector.example:4317"},
            {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://user:secret@example.com"},
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://example.com",
                "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "grpc",
            },
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://example.com",
                "IIP_OTEL_INVESTIGATION_ATTRIBUTE_MODE": "everything",
            },
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://example.com",
                "OTEL_BSP_MAX_QUEUE_SIZE": "16",
                "OTEL_BSP_MAX_EXPORT_BATCH_SIZE": "32",
            },
        )
        for environment in invalid_environments:
            with self.subTest(environment=environment):
                with self.assertRaisesRegex(
                    OpenTelemetryConfigurationError,
                    "telemetry.configuration.invalid",
                ):
                    OtlpTracesConfiguration.from_environment(environment)


class RecordingSpan:
    def __init__(self, attributes: dict[str, object], start_time: int) -> None:
        self.attributes = dict(attributes)
        self.start_time = start_time
        self.status = None
        self.end_time = None

    def set_status(self, status: object) -> None:
        self.status = status

    def end(self, *, end_time: int) -> None:
        self.end_time = end_time


class RecordingTracer:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.spans: list[RecordingSpan] = []

    def start_span(
        self, name: str, *, attributes: dict[str, object], start_time: int
    ) -> RecordingSpan:
        if self.fail:
            raise RuntimeError("trace failure")
        if name != "iip.investigation.execute":
            raise AssertionError(name)
        span = RecordingSpan(attributes, start_time)
        self.spans.append(span)
        return span


class OpenTelemetryInvestigationSinkTests(unittest.TestCase):
    def test_sink_emits_only_bounded_terminal_facts(self) -> None:
        tracer = RecordingTracer()
        sink = OpenTelemetryInvestigationSink(
            tracer, attribute_mode="tenant-investigation"
        )

        sink.record_investigation_execution(measurement())

        self.assertEqual(len(tracer.spans), 1)
        span = tracer.spans[0]
        self.assertEqual(span.end_time - span.start_time, 2_500_000_000)
        self.assertEqual(
            span.attributes,
            {
                "iip.investigation.outcome": "conclusive",
                "iip.investigation.terminal_reason": "sufficient-evidence",
                "iip.investigation.wall_time": 2.5,
                "iip.investigation.tool_calls": 3,
                "iip.investigation.evidence_items": 2,
                "iip.investigation.id": "inv_0123456789abcdef0123456789abcdef",
                "iip.tenant.id": "local",
            },
        )
        self.assertEqual(sink.record_failures, 0)

    def test_failure_is_counted_and_never_raised(self) -> None:
        sink = OpenTelemetryInvestigationSink(RecordingTracer(fail=True))

        sink.record_investigation_execution(measurement())

        self.assertEqual(sink.record_failures, 1)


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)

    def now(self) -> str:
        current = self.value
        self.value += timedelta(seconds=2.5)
        return iso(current)


class InvestigationTelemetryBoundaryTests(unittest.TestCase):
    def test_durable_terminal_report_drives_one_failure_isolated_measurement(self) -> None:
        scenario = example("evaluation-scenario.json")
        actor = ActorContext("evaluation-runner", "evaluation")
        resources = InMemoryResourceStore()
        policy = AllowTenantPolicy()
        ingestion = ResourceIngestionService(resources, policy)
        for resource in scenario["spec"]["fixtures"]["graph"]["resources"]:
            ingestion.execute(IngestResourceCommand(actor, resource))
        clock = MutableClock()
        evidence_store = InMemoryEvidenceStore()
        evidence = EvidenceCollectionService(
            resources,
            {"resource-state": ResourceStateEvidenceProvider(resources)},
            evidence_store,
            StructuredTextRedactor(),
            policy,
            UuidEvidenceIdGenerator(),
            clock,
        )

        class RecordingSink:
            def __init__(self) -> None:
                self.measurements: list[InvestigationExecutionMeasurement] = []

            def record_investigation_execution(
                self, item: InvestigationExecutionMeasurement
            ) -> None:
                self.measurements.append(item)

        sink = RecordingSink()
        service = DeterministicInvestigationService(
            resources,
            evidence,
            InMemoryOperationalStore(),
            clock,
            telemetry_sink=sink,
        )
        request = copy.deepcopy(scenario["spec"]["request"])
        request["metadata"]["requestedAt"] = "2026-08-17T10:00:00Z"

        report = service.execute(RunInvestigationCommand(actor, request))
        replay = service.execute(RunInvestigationCommand(actor, request))

        self.assertEqual(replay, report)
        self.assertEqual(len(sink.measurements), 1)
        self.assertEqual(sink.measurements[0].outcome, report["spec"]["outcome"])
        self.assertEqual(
            sink.measurements[0].wall_time_seconds,
            report["spec"]["usage"]["wallTimeSeconds"],
        )
        self.assertGreater(report["spec"]["usage"]["wallTimeSeconds"], 0)

        class FailingSink:
            def record_investigation_execution(
                self, item: InvestigationExecutionMeasurement
            ) -> None:
                del item
                raise RuntimeError("observability unavailable")

        failing_service = DeterministicInvestigationService(
            resources,
            evidence,
            InMemoryOperationalStore(),
            clock,
            telemetry_sink=FailingSink(),
        )
        failing_request = copy.deepcopy(request)
        failing_request["metadata"]["id"] = (
            "inv_fedcba9876543210fedcba9876543210"
        )
        failed_export_report = failing_service.execute(
            RunInvestigationCommand(actor, failing_request)
        )
        self.assertEqual(failed_export_report["spec"]["outcome"], "conclusive")


class RuntimeTraceCompositionTests(unittest.TestCase):
    def test_environment_composes_trace_runtime_independently(self) -> None:
        token = "trace-reference-token-0123456789abcdef0123456789abcdef"
        identities = json.dumps(
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

        class Provider:
            def __init__(self) -> None:
                self.shutdowns = 0

            def force_flush(self, timeout_millis: int) -> bool:
                del timeout_millis
                return True

            def shutdown(self) -> None:
                self.shutdowns += 1

        provider = Provider()
        trace_runtime = OtlpTracesRuntime(
            OpenTelemetryInvestigationSink(RecordingTracer()), provider
        )
        with patch.dict(
            os.environ,
            {
                "IIP_AUTH_IDENTITIES_JSON": identities,
                "IIP_OTEL_TRACES_ENABLED": "true",
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.example/base",
            },
            clear=True,
        ), patch(
            "iip.adapters.otel.build_otlp_traces_runtime",
            return_value=trace_runtime,
        ) as build:
            runtime = build_runtime_from_env()

        self.assertEqual(
            build.call_args.args[0].endpoint,
            "https://collector.example/base/v1/traces",
        )
        self.assertIs(runtime.telemetry_runtime, trace_runtime)
        runtime.close()
        self.assertEqual(provider.shutdowns, 1)


if __name__ == "__main__":
    unittest.main()
