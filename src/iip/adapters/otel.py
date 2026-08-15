"""Optional OpenTelemetry metrics adapter for platform-owned measurements."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any, Mapping
from urllib.parse import urlsplit

from iip.application.ports import IngestionFreshnessMeasurement


_VIOLATIONS = (
    "checkpoint-age-exceeded",
    "clock-skew-detected",
    "ingestion-delay-exceeded",
    "observation-age-exceeded",
    "pending-event-age-exceeded",
)
_ATTRIBUTE_MODES = frozenset({"none", "source", "tenant-source"})
_MAX_ENDPOINT_LENGTH = 2048


class OpenTelemetryConfigurationError(RuntimeError):
    """Fail-closed OTLP configuration error without input disclosure."""


@dataclass(frozen=True)
class OtlpMetricsConfiguration:
    """Validated OTLP/HTTP metrics settings selected at composition time."""

    endpoint: str
    attribute_mode: str = "source"
    service_name: str = "infra-intelligence-api"
    export_interval_millis: int = 60_000
    export_timeout_millis: int = 10_000

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str]
    ) -> "OtlpMetricsConfiguration":
        protocol = environment.get(
            "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
            environment.get("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf"),
        )
        if protocol != "http/protobuf":
            raise OpenTelemetryConfigurationError("telemetry.configuration.invalid")

        endpoint = environment.get("OTEL_EXPORTER_OTLP_METRICS_ENDPOINT")
        if endpoint is None:
            base_endpoint = environment.get("OTEL_EXPORTER_OTLP_ENDPOINT")
            endpoint = (
                f"{base_endpoint.rstrip('/')}/v1/metrics"
                if base_endpoint is not None
                else ""
            )
        configuration = cls(
            endpoint=endpoint,
            attribute_mode=environment.get(
                "IIP_OTEL_INGESTION_ATTRIBUTE_MODE", "source"
            ),
            service_name=environment.get(
                "OTEL_SERVICE_NAME", "infra-intelligence-api"
            ),
            export_interval_millis=cls._integer(
                environment,
                "OTEL_METRIC_EXPORT_INTERVAL",
                60_000,
            ),
            export_timeout_millis=cls._integer(
                environment,
                "OTEL_METRIC_EXPORT_TIMEOUT",
                10_000,
            ),
        )
        configuration.validate()
        return configuration

    def validate(self) -> None:
        try:
            parsed = urlsplit(self.endpoint)
            port = parsed.port
        except (TypeError, ValueError):
            raise OpenTelemetryConfigurationError(
                "telemetry.configuration.invalid"
            ) from None
        if (
            not isinstance(self.endpoint, str)
            or not 1 <= len(self.endpoint) <= _MAX_ENDPOINT_LENGTH
            or parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or (port is not None and not 1 <= port <= 65_535)
            or self.attribute_mode not in _ATTRIBUTE_MODES
            or not isinstance(self.service_name, str)
            or not 1 <= len(self.service_name) <= 128
            or any(character.isspace() for character in self.service_name)
            or not self._bounded_millis(self.export_interval_millis)
            or not self._bounded_millis(self.export_timeout_millis)
        ):
            raise OpenTelemetryConfigurationError("telemetry.configuration.invalid")

    @staticmethod
    def _integer(
        environment: Mapping[str, str], name: str, default: int
    ) -> int:
        raw = environment.get(name)
        if raw is None:
            return default
        try:
            if not raw or any(character not in "0123456789" for character in raw):
                raise ValueError
            return int(raw)
        except (TypeError, ValueError):
            raise OpenTelemetryConfigurationError(
                "telemetry.configuration.invalid"
            ) from None

    @staticmethod
    def _bounded_millis(value: object) -> bool:
        return (
            not isinstance(value, bool)
            and isinstance(value, int)
            and 100 <= value <= 300_000
        )


class OpenTelemetryIngestionSink:
    """Map freshness evaluations to bounded custom OpenTelemetry gauges."""

    def __init__(self, meter: Any, *, attribute_mode: str = "source") -> None:
        if attribute_mode not in _ATTRIBUTE_MODES:
            raise OpenTelemetryConfigurationError("telemetry.configuration.invalid")
        self._attribute_mode = attribute_mode
        self._checkpoint_age = meter.create_gauge(
            "iip.ingestion.checkpoint.age",
            unit="s",
            description="Age of the last completely committed source checkpoint.",
        )
        self._observation_age = meter.create_gauge(
            "iip.ingestion.observation.age",
            unit="s",
            description="Age of the latest accepted provider observation.",
        )
        self._ingestion_delay = meter.create_gauge(
            "iip.ingestion.delay",
            unit="s",
            description="Provider observation to durable platform recording delay.",
        )
        self._accepted_observations = meter.create_gauge(
            "iip.ingestion.accepted_observations",
            unit="1",
            description="Accepted observations retained for the source.",
        )
        self._pending_events = meter.create_gauge(
            "iip.ingestion.pending_events",
            unit="1",
            description="Unpublished transactional-outbox events for the source.",
        )
        self._pending_event_age = meter.create_gauge(
            "iip.ingestion.pending_event.age",
            unit="s",
            description="Age of the oldest unpublished source event.",
        )
        self._within_objective = meter.create_gauge(
            "iip.ingestion.within_objective",
            unit="1",
            description="One when every point-in-time freshness objective is satisfied.",
        )
        self._objective_violation = meter.create_gauge(
            "iip.ingestion.objective.violation",
            unit="1",
            description="One when the named bounded freshness violation is active.",
        )
        self._record_failure = meter.create_counter(
            "iip.telemetry.record.failures",
            unit="1",
            description="Measurements rejected before reaching an exporter.",
        )
        self._failures = 0
        self._failure_lock = Lock()

    @property
    def record_failures(self) -> int:
        with self._failure_lock:
            return self._failures

    def record_ingestion_freshness(
        self, measurement: IngestionFreshnessMeasurement
    ) -> None:
        attributes = self._attributes(measurement)
        try:
            self._checkpoint_age.set(
                measurement.checkpoint_age_seconds, attributes
            )
            if measurement.observation_age_seconds is not None:
                self._observation_age.set(
                    measurement.observation_age_seconds, attributes
                )
            if measurement.ingestion_delay_seconds is not None:
                self._ingestion_delay.set(
                    measurement.ingestion_delay_seconds, attributes
                )
            self._accepted_observations.set(
                measurement.accepted_observation_count, attributes
            )
            self._pending_events.set(measurement.pending_event_count, attributes)
            if measurement.oldest_pending_event_age_seconds is not None:
                self._pending_event_age.set(
                    measurement.oldest_pending_event_age_seconds, attributes
                )
            self._within_objective.set(
                1 if measurement.within_objective else 0, attributes
            )
            violations = set(measurement.violations)
            for violation in _VIOLATIONS:
                violation_attributes = dict(attributes)
                violation_attributes["iip.ingestion.violation"] = violation
                self._objective_violation.set(
                    1 if violation in violations else 0,
                    violation_attributes,
                )
        except Exception:
            with self._failure_lock:
                self._failures += 1
            try:
                self._record_failure.add(1, {"iip.telemetry.signal": "metrics"})
            except Exception:
                pass

    def _attributes(
        self, measurement: IngestionFreshnessMeasurement
    ) -> dict[str, str]:
        attributes = {
            "iip.ingestion.status": (
                "within-objective" if measurement.within_objective else "breached"
            )
        }
        if self._attribute_mode in ("source", "tenant-source"):
            attributes["iip.source.id"] = measurement.source_id
        if self._attribute_mode == "tenant-source":
            attributes["iip.tenant.id"] = measurement.tenant_id
        return attributes


@dataclass(frozen=True)
class OtlpMetricsRuntime:
    """Own the SDK provider so composition can flush and stop its worker."""

    sink: OpenTelemetryIngestionSink
    provider: Any

    def force_flush(self, timeout_millis: int = 10_000) -> bool:
        return bool(self.provider.force_flush(timeout_millis=timeout_millis))

    def shutdown(self, timeout_millis: int = 30_000) -> None:
        self.provider.shutdown(timeout_millis=timeout_millis)


def build_otlp_metrics_runtime(
    configuration: OtlpMetricsConfiguration,
) -> OtlpMetricsRuntime:
    """Compose the official OTLP/HTTP SDK entirely inside the adapter layer."""

    configuration.validate()
    try:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource

        exporter = OTLPMetricExporter(endpoint=configuration.endpoint)
        reader = PeriodicExportingMetricReader(
            exporter,
            export_interval_millis=configuration.export_interval_millis,
            export_timeout_millis=configuration.export_timeout_millis,
        )
        provider = MeterProvider(
            resource=Resource.create(
                {
                    "service.name": configuration.service_name,
                    "service.version": "0.4.0",
                }
            ),
            metric_readers=(reader,),
        )
        meter = provider.get_meter("iip.ingestion", "0.4.0")
        sink = OpenTelemetryIngestionSink(
            meter,
            attribute_mode=configuration.attribute_mode,
        )
        return OtlpMetricsRuntime(sink=sink, provider=provider)
    except OpenTelemetryConfigurationError:
        raise
    except Exception:
        raise OpenTelemetryConfigurationError(
            "telemetry.initialization.failed"
        ) from None
