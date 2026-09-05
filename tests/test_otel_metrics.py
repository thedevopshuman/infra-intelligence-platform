from __future__ import annotations

import json
import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from iip.adapters.auth import HashedBearerAuthenticator
from iip.adapters.otel import (
    OpenTelemetryAiAllocationSink,
    OpenTelemetryConfigurationError,
    OpenTelemetryAiEconomicsSink,
    OpenTelemetryIngestionSink,
    OpenTelemetryQueryAvailabilitySink,
    OtlpMetricsConfiguration,
    OtlpMetricsRuntime,
)
from iip.application.ports import (
    AiAllocationMeasurement,
    AiEconomicsMeasurement,
    AiModelSavingsMeasurement,
    AiRetryMeasurement,
    IngestionFreshnessMeasurement,
    QueryAvailabilityMeasurement,
)
from iip.application.observe_query_availability import (
    RecordQueryAvailabilityCommand,
)
from iip.bootstrap import build_local_runtime, build_runtime_from_env


class RecordingInstrument:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.records: list[tuple[float, dict[str, str]]] = []

    def record(self, value: float, attributes: dict[str, str]) -> None:
        if self.fail:
            raise RuntimeError("instrument failure")
        self.records.append((value, dict(attributes)))

    def set(self, value: float, attributes: dict[str, str]) -> None:
        self.record(value, attributes)

    def add(self, value: float, attributes: dict[str, str]) -> None:
        self.record(value, attributes)


class RecordingMeter:
    def __init__(self, *, failing_name: str | None = None) -> None:
        self.failing_name = failing_name
        self.instruments: dict[str, RecordingInstrument] = {}

    def create_gauge(self, name: str, **kwargs: object) -> RecordingInstrument:
        del kwargs
        instrument = RecordingInstrument(fail=name == self.failing_name)
        self.instruments[name] = instrument
        return instrument

    def create_counter(self, name: str, **kwargs: object) -> RecordingInstrument:
        return self.create_gauge(name, **kwargs)

    def create_histogram(self, name: str, **kwargs: object) -> RecordingInstrument:
        return self.create_gauge(name, **kwargs)


def measurement() -> IngestionFreshnessMeasurement:
    return IngestionFreshnessMeasurement(
        tenant_id="local",
        source_id="kubernetes-local",
        within_objective=False,
        checkpoint_age_seconds=120.0,
        observation_age_seconds=125.0,
        ingestion_delay_seconds=2.0,
        accepted_observation_count=8,
        pending_event_count=2,
        oldest_pending_event_age_seconds=45.0,
        violations=("checkpoint-age-exceeded", "pending-event-age-exceeded"),
    )


def ai_economics_measurement() -> AiEconomicsMeasurement:
    return AiEconomicsMeasurement(
        tenant_id="local",
        profile_id="support-assistant-context",
        provider="aws.bedrock",
        model_id="example.foundation-model-v1:0",
        region="us-east-1",
        service_name="support-assistant",
        deployment_environment="production",
        request_count=2,
        input_tokens=4800,
        input_token_requests=2,
        output_tokens=200,
        output_token_requests=2,
        incomplete_requests=0,
        priced_requests=2,
        unpriced_requests=0,
        ambiguous_requests=0,
        pending_cost_requests=0,
        calculated_cost_subunits=17_400_000,
        currency="USD",
        currency_scale=9,
        baseline_input_tokens_per_request=1200,
        current_input_tokens_per_request=2400,
        context_growth_change_basis_points=10_000,
        evaluation_status="qualified",
        finding_count=1,
        finding_severity="medium",
        potential_savings_subunits=7_200_000,
    )


def ai_retry_measurement() -> AiRetryMeasurement:
    return AiRetryMeasurement(
        tenant_id="local",
        profile_id="support-assistant-retries",
        provider="aws.bedrock",
        model_id="example.foundation-model-v1:0",
        region="us-east-1",
        service_name="support-assistant",
        deployment_environment="production",
        current_operations=2,
        current_retry_fact_operations=2,
        current_retrying_operations=2,
        current_excess_attempts=3,
        baseline_retry_rate_basis_points=0,
        current_retry_rate_basis_points=10_000,
        retry_rate_increase_basis_points=10_000,
        evaluation_status="qualified",
        finding_count=1,
        finding_severity="high",
    )


def ai_model_savings_measurement() -> AiModelSavingsMeasurement:
    return AiModelSavingsMeasurement(
        tenant_id="local",
        profile_id="support-assistant-model-cost",
        provider="aws.bedrock",
        reference_model_id="example.foundation-model-v1:0",
        candidate_model_id="example.efficient-model-v1:0",
        region="us-east-1",
        service_name="support-assistant",
        deployment_environment="production",
        reference_request_count=2,
        candidate_request_count=2,
        reference_cost_per_request_subunits=8_700_000,
        candidate_cost_per_request_subunits=1_300_000,
        cost_increase_basis_points=56_923,
        currency="USD",
        currency_scale=9,
        evaluation_status="qualified",
        finding_count=1,
        finding_severity="high",
        potential_savings_subunits=14_800_000,
    )


def ai_allocation_measurement() -> AiAllocationMeasurement:
    return AiAllocationMeasurement(
        tenant_id="local",
        dimension="application",
        allocation_status="allocated",
        dimension_id="support-experience",
        request_count=2,
        input_tokens=4800,
        input_token_records=2,
        output_tokens=200,
        output_token_records=2,
        priced_requests=2,
        unpriced_requests=0,
        ambiguous_requests=0,
        pending_cost_requests=0,
        calculated_cost_subunits=17_400_000,
        currency="USD",
        currency_scale=9,
    )


class OtlpMetricsConfigurationTests(unittest.TestCase):
    def test_standard_base_endpoint_resolves_metrics_path(self) -> None:
        configuration = OtlpMetricsConfiguration.from_environment(
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.example/otlp",
                "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
                "IIP_OTEL_INGESTION_ATTRIBUTE_MODE": "tenant-source",
                "IIP_OTEL_AI_ECONOMICS_ATTRIBUTE_MODE": "scope",
                "OTEL_SERVICE_NAME": "iip-reference-api",
                "OTEL_METRIC_EXPORT_INTERVAL": "5000",
                "OTEL_METRIC_EXPORT_TIMEOUT": "3000",
            }
        )

        self.assertEqual(
            configuration.endpoint,
            "https://collector.example/otlp/v1/metrics",
        )
        self.assertEqual(configuration.attribute_mode, "tenant-source")
        self.assertEqual(configuration.ai_economics_attribute_mode, "scope")
        self.assertEqual(configuration.export_interval_millis, 5000)
        self.assertEqual(configuration.export_timeout_millis, 3000)

    def test_signal_endpoint_is_used_exactly(self) -> None:
        configuration = OtlpMetricsConfiguration.from_environment(
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://ignored.example/base",
                "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": (
                    "https://metrics.example/custom"
                ),
            }
        )

        self.assertEqual(configuration.endpoint, "https://metrics.example/custom")

    def test_configuration_rejects_missing_unsafe_or_unsupported_values(self) -> None:
        invalid_environments = (
            {},
            {"OTEL_EXPORTER_OTLP_ENDPOINT": "grpc://collector.example:4317"},
            {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://user:secret@example.com"},
            {"OTEL_EXPORTER_OTLP_ENDPOINT": "https://example.com?token=secret"},
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://example.com",
                "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
            },
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://example.com",
                "IIP_OTEL_INGESTION_ATTRIBUTE_MODE": "everything",
            },
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://example.com",
                "IIP_OTEL_AI_ECONOMICS_ATTRIBUTE_MODE": "record-identities",
            },
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://example.com",
                "OTEL_METRIC_EXPORT_INTERVAL": "0",
            },
        )
        for environment in invalid_environments:
            with self.subTest(environment=environment):
                with self.assertRaisesRegex(
                    OpenTelemetryConfigurationError,
                    "telemetry.configuration.invalid",
                ):
                    OtlpMetricsConfiguration.from_environment(environment)


class OpenTelemetryIngestionSinkTests(unittest.TestCase):
    def test_sink_maps_every_measurement_and_bounded_violation_series(self) -> None:
        meter = RecordingMeter()
        sink = OpenTelemetryIngestionSink(
            meter,
            attribute_mode="tenant-source",
        )

        sink.record_ingestion_freshness(measurement())

        attributes = {
            "iip.ingestion.status": "breached",
            "iip.source.id": "kubernetes-local",
            "iip.tenant.id": "local",
        }
        self.assertEqual(
            meter.instruments["iip.ingestion.checkpoint.age"].records,
            [(120.0, attributes)],
        )
        self.assertEqual(
            meter.instruments["iip.ingestion.pending_events"].records,
            [(2, attributes)],
        )
        violation_records = meter.instruments[
            "iip.ingestion.objective.violation"
        ].records
        self.assertEqual(len(violation_records), 5)
        active = {
            item[1]["iip.ingestion.violation"]
            for item in violation_records
            if item[0] == 1
        }
        self.assertEqual(
            active,
            {"checkpoint-age-exceeded", "pending-event-age-exceeded"},
        )
        self.assertEqual(sink.record_failures, 0)

    def test_optional_observation_and_pending_age_are_not_invented(self) -> None:
        meter = RecordingMeter()
        sink = OpenTelemetryIngestionSink(meter, attribute_mode="none")
        empty = IngestionFreshnessMeasurement(
            tenant_id="local",
            source_id="kubernetes-local",
            within_objective=True,
            checkpoint_age_seconds=2.0,
            observation_age_seconds=None,
            ingestion_delay_seconds=None,
            accepted_observation_count=0,
            pending_event_count=0,
            oldest_pending_event_age_seconds=None,
            violations=(),
        )

        sink.record_ingestion_freshness(empty)

        self.assertEqual(
            meter.instruments["iip.ingestion.observation.age"].records, []
        )
        self.assertEqual(meter.instruments["iip.ingestion.delay"].records, [])
        self.assertEqual(
            meter.instruments["iip.ingestion.pending_event.age"].records, []
        )
        self.assertEqual(
            meter.instruments["iip.ingestion.checkpoint.age"].records[0][1],
            {"iip.ingestion.status": "within-objective"},
        )

    def test_instrument_failure_is_counted_and_never_raised(self) -> None:
        meter = RecordingMeter(failing_name="iip.ingestion.checkpoint.age")
        sink = OpenTelemetryIngestionSink(meter)

        sink.record_ingestion_freshness(measurement())

        self.assertEqual(sink.record_failures, 1)
        self.assertEqual(
            meter.instruments["iip.telemetry.record.failures"].records,
            [(1, {"iip.telemetry.signal": "metrics"})],
        )


class OpenTelemetryQueryAvailabilitySinkTests(unittest.TestCase):
    @staticmethod
    def measurement() -> QueryAvailabilityMeasurement:
        return QueryAvailabilityMeasurement(
            operation="runtime-version",
            outcome="success",
            availability="available",
            duration_seconds=0.125,
            objective_window_seconds=3600,
            objective_minimum_availability_basis_points=9990,
            objective_minimum_eligible_requests=100,
        )

    def test_sink_emits_bounded_counter_and_histogram_attributes(self) -> None:
        meter = RecordingMeter()
        sink = OpenTelemetryQueryAvailabilitySink(meter)

        sink.record_query_availability(self.measurement())

        request = meter.instruments["iip.query.requests"].records[0]
        duration = meter.instruments["iip.query.duration"].records[0]
        self.assertEqual(request[0], 1)
        self.assertEqual(duration[0], 0.125)
        self.assertEqual(request[1], duration[1])
        self.assertEqual(request[1]["iip.query.operation"], "runtime-version")
        self.assertEqual(request[1]["iip.query.availability"], "available")
        serialized = json.dumps(request[1])
        for forbidden in ("tenant", "actor", "path", "credential", "status_code"):
            self.assertNotIn(forbidden, serialized)

    def test_instrument_failure_is_local_and_counted(self) -> None:
        meter = RecordingMeter(failing_name="iip.query.requests")
        sink = OpenTelemetryQueryAvailabilitySink(meter)

        sink.record_query_availability(self.measurement())

        self.assertEqual(sink.record_failures, 1)
        self.assertEqual(
            meter.instruments["iip.telemetry.record.failures"].records,
            [
                (
                    1,
                    {
                        "iip.telemetry.signal": "metrics",
                        "iip.telemetry.instrument": "query-availability",
                    },
                )
            ],
        )


class OpenTelemetryAiEconomicsSinkTests(unittest.TestCase):
    def test_sink_exports_bounded_scope_coverage_cost_change_and_saving(self) -> None:
        meter = RecordingMeter()
        sink = OpenTelemetryAiEconomicsSink(
            meter,
            attribute_mode="tenant-scope",
        )

        sink.record_ai_economics(ai_economics_measurement())

        requests = meter.instruments["iip.ai.usage.requests"].records
        self.assertEqual(requests[0][0], 2)
        self.assertEqual(
            requests[0][1],
            {
                "iip.ai.profile.id": "support-assistant-context",
                "gen_ai.provider.name": "aws.bedrock",
                "gen_ai.response.model": "example.foundation-model-v1:0",
                "cloud.region": "us-east-1",
                "service.name": "support-assistant",
                "deployment.environment.name": "production",
                "iip.tenant.id": "local",
            },
        )
        cost = meter.instruments["iip.ai.cost.amount"].records[0]
        self.assertEqual(cost[0], 17_400_000)
        self.assertEqual(cost[1]["iip.ai.currency"], "USD")
        self.assertEqual(cost[1]["iip.ai.currency_scale"], 9)
        self.assertEqual(
            meter.instruments["iip.ai.context_growth.change"].records[0][0],
            10_000,
        )
        self.assertEqual(
            meter.instruments["iip.ai.savings.potential_amount"].records[0][0],
            7_200_000,
        )
        active_status = {
            attributes["iip.ai.savings.status"]
            for value, attributes in meter.instruments[
                "iip.ai.savings.profile_status"
            ].records
            if value == 1
        }
        self.assertEqual(active_status, {"qualified"})
        serialized = json.dumps(meter.instruments, default=lambda value: value.__dict__)
        for forbidden in (
            "aiu_",
            "aic_",
            "aif_",
            "prompt",
            "response content",
            "priceSubunits",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(sink.record_failures, 0)

    def test_retry_snapshot_exports_evidence_without_monetary_savings(self) -> None:
        meter = RecordingMeter()
        sink = OpenTelemetryAiEconomicsSink(
            meter,
            attribute_mode="tenant-scope",
        )

        sink.record_ai_retry(ai_retry_measurement())

        retrying = meter.instruments["iip.ai.retry.operations"].records
        self.assertEqual(
            {
                attributes["iip.ai.retry.status"]: value
                for value, attributes in retrying
            },
            {"retrying": 2, "not-retrying": 0, "fact-missing": 0},
        )
        self.assertEqual(
            meter.instruments["iip.ai.retry.excess_attempts"].records[0][0],
            3,
        )
        self.assertEqual(
            meter.instruments["iip.ai.retry.operation_rate_increase"].records[0][0],
            10_000,
        )
        findings = meter.instruments["iip.ai.savings.findings"].records
        self.assertEqual(findings[0][0], 1)
        self.assertEqual(
            findings[0][1]["iip.ai.savings.rule.id"],
            "retry-amplification",
        )
        self.assertEqual(
            meter.instruments["iip.ai.savings.potential_amount"].records,
            [],
        )
        serialized = json.dumps(meter.instruments, default=lambda value: value.__dict__)
        for forbidden in ("aiu_", "aic_", "aif_", "amountSubunits", "prompt"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(sink.record_failures, 0)

    def test_qualified_model_snapshot_exports_bounded_comparison(self) -> None:
        meter = RecordingMeter()
        sink = OpenTelemetryAiEconomicsSink(
            meter,
            attribute_mode="tenant-scope",
        )

        sink.record_ai_model_savings(ai_model_savings_measurement())

        costs = meter.instruments["iip.ai.model.cost_per_request"].records
        self.assertEqual(
            {
                attributes["iip.ai.model.role"]: value
                for value, attributes in costs
            },
            {"candidate": 1_300_000, "reference": 8_700_000},
        )
        self.assertEqual(
            meter.instruments["iip.ai.model.cost_increase"].records[0][0],
            56_923,
        )
        self.assertEqual(
            meter.instruments["iip.ai.savings.potential_amount"].records[0][0],
            14_800_000,
        )
        serialized = json.dumps(
            meter.instruments,
            default=lambda value: value.__dict__,
        )
        for forbidden in ("ams_", "aiu_", "aic_", "prompt", "resultDigest"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(sink.record_failures, 0)

        sink.record_ai_model_savings(
            replace(
                ai_model_savings_measurement(),
                candidate_cost_per_request_subunits=None,
            )
        )
        self.assertEqual(sink.record_failures, 1)

    def test_scope_mode_omits_tenant_and_invalid_snapshot_fails_locally(self) -> None:
        meter = RecordingMeter()
        sink = OpenTelemetryAiEconomicsSink(meter, attribute_mode="scope")

        sink.record_ai_economics(ai_economics_measurement())
        self.assertNotIn(
            "iip.tenant.id",
            meter.instruments["iip.ai.usage.requests"].records[0][1],
        )
        sink.record_ai_economics(
            replace(ai_economics_measurement(), pending_cost_requests=1)
        )

        self.assertEqual(sink.record_failures, 1)
        self.assertEqual(
            meter.instruments["iip.telemetry.record.failures"].records,
            [
                (
                    1,
                    {
                        "iip.telemetry.signal": "metrics",
                        "iip.telemetry.instrument": "ai-economics",
                    },
                )
            ],
        )


class OpenTelemetryAiAllocationSinkTests(unittest.TestCase):
    def test_snapshot_uses_stable_ids_and_zeros_disappeared_groups(self) -> None:
        meter = RecordingMeter()
        sink = OpenTelemetryAiAllocationSink(meter)

        sink.record_ai_allocation_snapshot("local", (ai_allocation_measurement(),))
        sink.record_ai_allocation_snapshot("local", ())

        requests = meter.instruments["iip.ai.allocation.requests"].records
        self.assertEqual([item[0] for item in requests], [2, 0])
        self.assertEqual(
            requests[0][1],
            {
                "iip.ai.allocation.dimension": "application",
                "iip.ai.allocation.status": "allocated",
                "iip.ai.application.id": "support-experience",
                "iip.tenant.id": "local",
            },
        )
        self.assertNotIn("Support Experience", repr(meter.instruments))
        self.assertEqual(sink.record_failures, 0)

    def test_snapshot_replacement_is_isolated_per_tenant(self) -> None:
        meter = RecordingMeter()
        sink = OpenTelemetryAiAllocationSink(meter)
        local = ai_allocation_measurement()
        secondary = replace(
            local,
            tenant_id="secondary",
            request_count=3,
            priced_requests=3,
        )

        sink.record_ai_allocation_snapshot("local", (local,))
        sink.record_ai_allocation_snapshot("secondary", (secondary,))
        sink.record_ai_allocation_snapshot("local", ())

        requests = meter.instruments["iip.ai.allocation.requests"].records
        self.assertEqual([item[0] for item in requests], [2, 3, 0])
        self.assertEqual(requests[1][1]["iip.tenant.id"], "secondary")
        self.assertEqual(requests[2][1]["iip.tenant.id"], "local")
        self.assertEqual(sink.record_failures, 0)

    def test_invalid_cardinality_input_is_failure_isolated(self) -> None:
        meter = RecordingMeter()
        sink = OpenTelemetryAiAllocationSink(meter)
        invalid = replace(ai_allocation_measurement(), dimension_id="User Supplied")

        sink.record_ai_allocation_snapshot("local", (invalid,))

        self.assertEqual(sink.record_failures, 1)
        self.assertEqual(
            meter.instruments["iip.telemetry.record.failures"].records[0][1][
                "iip.telemetry.instrument"
            ],
            "ai-allocation",
        )

    def test_unpriced_group_does_not_invent_zero_cost_and_clears_prior_cost(
        self,
    ) -> None:
        meter = RecordingMeter()
        sink = OpenTelemetryAiAllocationSink(meter)
        priced = ai_allocation_measurement()
        unpriced = replace(
            priced,
            priced_requests=0,
            unpriced_requests=2,
            calculated_cost_subunits=None,
        )

        sink.record_ai_allocation_snapshot("local", (unpriced,))
        self.assertEqual(
            meter.instruments["iip.ai.allocation.cost_amount"].records,
            [],
        )

        sink.record_ai_allocation_snapshot("local", (priced,))
        sink.record_ai_allocation_snapshot("local", (unpriced,))
        self.assertEqual(
            [
                item[0]
                for item in meter.instruments[
                    "iip.ai.allocation.cost_amount"
                ].records
            ],
            [17_400_000, 0],
        )


class RuntimeTelemetryLifecycleTests(unittest.TestCase):
    def test_runtime_flush_and_close_delegate_only_when_configured(self) -> None:
        class Provider:
            def __init__(self) -> None:
                self.flushes: list[int] = []
                self.shutdowns: list[int] = []

            def force_flush(self, timeout_millis: int) -> bool:
                self.flushes.append(timeout_millis)
                return True

            def shutdown(self, timeout_millis: int) -> None:
                self.shutdowns.append(timeout_millis)

        provider = Provider()
        otel = OtlpMetricsRuntime(
            OpenTelemetryIngestionSink(RecordingMeter()),
            provider,
        )
        runtime = build_local_runtime(
            ingestion_telemetry_sink=otel.sink,
            telemetry_runtime=otel,
        )

        self.assertTrue(runtime.force_flush_telemetry(1234))
        runtime.close()

        self.assertEqual(provider.flushes, [1234])
        self.assertEqual(provider.shutdowns, [30_000])
        self.assertTrue(build_local_runtime().force_flush_telemetry())

    def test_environment_requires_explicit_enablement_and_endpoint(self) -> None:
        token = "otel-reference-token-0123456789abcdef0123456789abcdef"
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
        with patch.dict(
            os.environ,
            {
                "IIP_AUTH_IDENTITIES_JSON": identities,
                "IIP_OTEL_METRICS_ENABLED": "true",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(
                OpenTelemetryConfigurationError,
                "telemetry.configuration.invalid",
            ):
                build_runtime_from_env()

    def test_environment_composes_configured_otlp_runtime(self) -> None:
        token = "otel-reference-token-0123456789abcdef0123456789abcdef"
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

            def shutdown(self, timeout_millis: int) -> None:
                self.shutdowns += 1
                self.timeout_millis = timeout_millis

        provider = Provider()
        query_meter = RecordingMeter()
        otel = OtlpMetricsRuntime(
            OpenTelemetryIngestionSink(RecordingMeter()),
            provider,
            query_sink=OpenTelemetryQueryAvailabilitySink(query_meter),
        )
        with patch.dict(
            os.environ,
            {
                "IIP_AUTH_IDENTITIES_JSON": identities,
                "IIP_OTEL_METRICS_ENABLED": "true",
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.example/base",
            },
            clear=True,
        ), patch(
            "iip.adapters.otel.build_otlp_metrics_runtime",
            return_value=otel,
        ) as build:
            runtime = build_runtime_from_env()

        configuration = build.call_args.args[0]
        self.assertEqual(
            configuration.endpoint,
            "https://collector.example/base/v1/metrics",
        )
        self.assertIs(runtime.telemetry_runtime, otel)
        runtime.query_availability.record(
            RecordQueryAvailabilityCommand(
                operation="runtime-version",
                status_code=200,
                duration_seconds=0.01,
            )
        )
        self.assertEqual(
            query_meter.instruments["iip.query.requests"].records[0][0],
            1,
        )
        runtime.close()
        self.assertEqual(provider.shutdowns, 1)


if __name__ == "__main__":
    unittest.main()
