from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from iip.adapters.auth import HashedBearerAuthenticator
from iip.adapters.otel import (
    OpenTelemetryConfigurationError,
    OpenTelemetryIngestionSink,
    OpenTelemetryQueryAvailabilitySink,
    OtlpMetricsConfiguration,
    OtlpMetricsRuntime,
)
from iip.application.ports import (
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


class OtlpMetricsConfigurationTests(unittest.TestCase):
    def test_standard_base_endpoint_resolves_metrics_path(self) -> None:
        configuration = OtlpMetricsConfiguration.from_environment(
            {
                "OTEL_EXPORTER_OTLP_ENDPOINT": "https://collector.example/otlp",
                "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
                "IIP_OTEL_INGESTION_ATTRIBUTE_MODE": "tenant-source",
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
