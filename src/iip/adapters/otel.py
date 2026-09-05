"""Optional OpenTelemetry adapters for platform-owned metrics and traces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from threading import Lock
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from iip.application.ports import (
    AiAllocationMeasurement,
    AiEconomicsMeasurement,
    AiRetryMeasurement,
    IngestionFreshnessMeasurement,
    InvestigationExecutionMeasurement,
    OtlpReceiverMeasurement,
    QueryAvailabilityMeasurement,
    TelemetryExportSignalState,
)


_VIOLATIONS = (
    "checkpoint-age-exceeded",
    "clock-skew-detected",
    "ingestion-delay-exceeded",
    "observation-age-exceeded",
    "pending-event-age-exceeded",
)
_ATTRIBUTE_MODES = frozenset({"none", "source", "tenant-source"})
_AI_ECONOMICS_ATTRIBUTE_MODES = frozenset({"scope", "tenant-scope"})
_AI_ECONOMICS_EVALUATION_STATUSES = (
    "qualified",
    "insufficient",
    "unresolved",
    "unsupported",
    "below-threshold",
)
_INVESTIGATION_ATTRIBUTE_MODES = frozenset(
    {"none", "investigation", "tenant-investigation"}
)
_MAX_ENDPOINT_LENGTH = 2048
_MAX_COUNTER = 9_007_199_254_740_991
_SAFE_AI_DIMENSION_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_SIGNALS = ("metrics", "traces")


class OpenTelemetryConfigurationError(RuntimeError):
    """Fail-closed OTLP configuration error without input disclosure."""


class TelemetryExportHealthState:
    """Thread-safe, bounded delivery outcomes for backend-neutral OTLP signals."""

    def __init__(self, enabled_signals: Iterable[str] = ()) -> None:
        enabled = frozenset(enabled_signals)
        if not enabled.issubset(_SIGNALS):
            raise ValueError("telemetry.export-health.signal-invalid")
        self._lock = Lock()
        self._states: dict[str, dict[str, Any]] = {
            signal: {
                "enabled": signal in enabled,
                "attempts": 0,
                "successes": 0,
                "failures": 0,
                "consecutive_failures": 0,
                "last_attempt_at": None,
                "last_success_at": None,
                "last_failure_at": None,
                "last_failure_code": None,
                "healthy": False,
            }
            for signal in _SIGNALS
        }

    def record_success(self, signal: str) -> None:
        observed_at = self._now()
        with self._lock:
            state = self._enabled(signal)
            if state["attempts"] < _MAX_COUNTER:
                state["attempts"] += 1
                state["successes"] += 1
            state["consecutive_failures"] = 0
            state["last_attempt_at"] = observed_at
            state["last_success_at"] = observed_at
            state["healthy"] = True

    def record_failure(
        self,
        signal: str,
        code: str = "telemetry.export.failed",
    ) -> None:
        if code not in (
            "telemetry.export.exception",
            "telemetry.export.rejected",
        ):
            code = "telemetry.export.failed"
        observed_at = self._now()
        with self._lock:
            state = self._enabled(signal)
            if state["attempts"] < _MAX_COUNTER:
                state["attempts"] += 1
                state["failures"] += 1
            state["consecutive_failures"] = min(
                state["consecutive_failures"] + 1,
                state["failures"],
            )
            state["last_attempt_at"] = observed_at
            state["last_failure_at"] = observed_at
            state["last_failure_code"] = code
            state["healthy"] = False

    def read_export_health(self) -> tuple[TelemetryExportSignalState, ...]:
        with self._lock:
            result = []
            for signal in _SIGNALS:
                state = self._states[signal]
                status = (
                    "disabled"
                    if not state["enabled"]
                    else "awaiting-first-attempt"
                    if state["attempts"] == 0
                    else "healthy"
                    if state["healthy"]
                    else "degraded"
                )
                result.append(
                    TelemetryExportSignalState(
                        signal=signal,
                        enabled=state["enabled"],
                        status=status,
                        attempts=state["attempts"],
                        successes=state["successes"],
                        failures=state["failures"],
                        consecutive_failures=state["consecutive_failures"],
                        last_attempt_at=state["last_attempt_at"],
                        last_success_at=state["last_success_at"],
                        last_failure_at=state["last_failure_at"],
                        last_failure_code=state["last_failure_code"],
                    )
                )
            return tuple(result)

    def _enabled(self, signal: str) -> dict[str, Any]:
        state = self._states.get(signal)
        if state is None or not state["enabled"]:
            raise ValueError("telemetry.export-health.signal-disabled")
        return state

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class DisabledTelemetryExportHealthReader(TelemetryExportHealthState):
    """Report explicit disabled state when no exporter is composed."""

    def __init__(self) -> None:
        super().__init__()


class TrackingMetricExporter:
    """Decorate an OpenTelemetry metric exporter with delivery outcomes."""

    def __init__(self, delegate: Any, health: TelemetryExportHealthState) -> None:
        self._delegate = delegate
        self._health = health
        self._preferred_temporality = getattr(
            delegate, "_preferred_temporality", None
        )
        self._preferred_aggregation = getattr(
            delegate, "_preferred_aggregation", None
        )

    def export(
        self,
        metrics_data: Any,
        timeout_millis: float = 10_000,
        **kwargs: Any,
    ) -> Any:
        try:
            result = self._delegate.export(
                metrics_data,
                timeout_millis=timeout_millis,
                **kwargs,
            )
        except Exception:
            self._health.record_failure("metrics", "telemetry.export.exception")
            raise
        if getattr(result, "name", None) == "SUCCESS":
            self._health.record_success("metrics")
        else:
            self._health.record_failure("metrics", "telemetry.export.rejected")
        return result

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return bool(self._delegate.force_flush(timeout_millis=timeout_millis))

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: Any) -> None:
        self._delegate.shutdown(timeout_millis=timeout_millis, **kwargs)


class TrackingSpanExporter:
    """Decorate an OpenTelemetry span exporter with delivery outcomes."""

    def __init__(self, delegate: Any, health: TelemetryExportHealthState) -> None:
        self._delegate = delegate
        self._health = health

    def export(self, spans: Any) -> Any:
        try:
            result = self._delegate.export(spans)
        except Exception:
            self._health.record_failure("traces", "telemetry.export.exception")
            raise
        if getattr(result, "name", None) == "SUCCESS":
            self._health.record_success("traces")
        else:
            self._health.record_failure("traces", "telemetry.export.rejected")
        return result

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return bool(self._delegate.force_flush(timeout_millis=timeout_millis))

    def shutdown(self) -> None:
        self._delegate.shutdown()


@dataclass(frozen=True)
class OtlpMetricsConfiguration:
    """Validated OTLP/HTTP metrics settings selected at composition time."""

    endpoint: str
    attribute_mode: str = "source"
    ai_economics_attribute_mode: str = "tenant-scope"
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
            ai_economics_attribute_mode=environment.get(
                "IIP_OTEL_AI_ECONOMICS_ATTRIBUTE_MODE", "tenant-scope"
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
            or self.ai_economics_attribute_mode
            not in _AI_ECONOMICS_ATTRIBUTE_MODES
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


@dataclass(frozen=True)
class OtlpTracesConfiguration:
    """Validated OTLP/HTTP trace settings selected at composition time."""

    endpoint: str
    attribute_mode: str = "none"
    service_name: str = "infra-intelligence-api"
    schedule_delay_millis: int = 5_000
    export_timeout_millis: int = 10_000
    max_queue_size: int = 2_048
    max_export_batch_size: int = 512

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str]
    ) -> "OtlpTracesConfiguration":
        protocol = environment.get(
            "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
            environment.get("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf"),
        )
        if protocol != "http/protobuf":
            raise OpenTelemetryConfigurationError("telemetry.configuration.invalid")
        endpoint = environment.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        if endpoint is None:
            base_endpoint = environment.get("OTEL_EXPORTER_OTLP_ENDPOINT")
            endpoint = (
                f"{base_endpoint.rstrip('/')}/v1/traces"
                if base_endpoint is not None
                else ""
            )
        configuration = cls(
            endpoint=endpoint,
            attribute_mode=environment.get(
                "IIP_OTEL_INVESTIGATION_ATTRIBUTE_MODE", "none"
            ),
            service_name=environment.get(
                "OTEL_SERVICE_NAME", "infra-intelligence-api"
            ),
            schedule_delay_millis=cls._integer(
                environment, "OTEL_BSP_SCHEDULE_DELAY", 5_000
            ),
            export_timeout_millis=cls._integer(
                environment, "OTEL_BSP_EXPORT_TIMEOUT", 10_000
            ),
            max_queue_size=cls._integer(
                environment, "OTEL_BSP_MAX_QUEUE_SIZE", 2_048
            ),
            max_export_batch_size=cls._integer(
                environment, "OTEL_BSP_MAX_EXPORT_BATCH_SIZE", 512
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
            or self.attribute_mode not in _INVESTIGATION_ATTRIBUTE_MODES
            or not isinstance(self.service_name, str)
            or not 1 <= len(self.service_name) <= 128
            or any(character.isspace() for character in self.service_name)
            or not self._bounded_millis(self.schedule_delay_millis)
            or not self._bounded_millis(self.export_timeout_millis)
            or not self._bounded_count(self.max_queue_size)
            or not self._bounded_count(self.max_export_batch_size)
            or self.max_export_batch_size > self.max_queue_size
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

    @staticmethod
    def _bounded_count(value: object) -> bool:
        return (
            not isinstance(value, bool)
            and isinstance(value, int)
            and 1 <= value <= 65_536
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


class OpenTelemetryQueryAvailabilitySink:
    """Map closed query outcomes to backend-neutral OTLP metrics."""

    def __init__(self, meter: Any) -> None:
        self._requests = meter.create_counter(
            "iip.query.requests",
            unit="1",
            description="Recognized control-plane query attempts by availability class.",
        )
        self._duration = meter.create_histogram(
            "iip.query.duration",
            unit="s",
            description="Monotonic serving time for recognized control-plane queries.",
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

    def record_query_availability(
        self, measurement: QueryAvailabilityMeasurement
    ) -> None:
        attributes: dict[str, str | int] = {
            "iip.query.operation": measurement.operation,
            "iip.query.outcome": measurement.outcome,
            "iip.query.availability": measurement.availability,
            "iip.query.objective.window_seconds": (
                measurement.objective_window_seconds
            ),
            "iip.query.objective.minimum_availability_basis_points": (
                measurement.objective_minimum_availability_basis_points
            ),
            "iip.query.objective.minimum_eligible_requests": (
                measurement.objective_minimum_eligible_requests
            ),
        }
        try:
            self._requests.add(1, attributes)
            self._duration.record(measurement.duration_seconds, attributes)
        except Exception:
            with self._failure_lock:
                self._failures += 1
            try:
                self._record_failure.add(
                    1,
                    {
                        "iip.telemetry.signal": "metrics",
                        "iip.telemetry.instrument": "query-availability",
                    },
                )
            except Exception:
                pass


class OpenTelemetryOtlpReceiverSink:
    """Map closed intake outcomes to privacy-bounded OTLP metrics."""

    def __init__(self, meter: Any) -> None:
        self._requests = meter.create_counter(
            "iip.otlp.receiver.requests",
            unit="1",
            description="Completed OTLP intake attempts by availability class.",
        )
        self._duration = meter.create_histogram(
            "iip.otlp.receiver.duration",
            unit="s",
            description="Monotonic serving time for completed OTLP intake attempts.",
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

    def record_otlp_receiver(self, measurement: OtlpReceiverMeasurement) -> None:
        attributes: dict[str, str | int] = {
            "iip.otlp.receiver.signal": measurement.signal,
            "iip.otlp.receiver.outcome": measurement.outcome,
            "iip.otlp.receiver.availability": measurement.availability,
            "iip.otlp.receiver.objective.window_seconds": (
                measurement.objective_window_seconds
            ),
            "iip.otlp.receiver.objective.minimum_availability_basis_points": (
                measurement.objective_minimum_availability_basis_points
            ),
            "iip.otlp.receiver.objective.minimum_eligible_requests": (
                measurement.objective_minimum_eligible_requests
            ),
        }
        try:
            self._requests.add(1, attributes)
            self._duration.record(measurement.duration_seconds, attributes)
        except Exception:
            with self._failure_lock:
                self._failures += 1
            try:
                self._record_failure.add(
                    1,
                    {
                        "iip.telemetry.signal": "metrics",
                        "iip.telemetry.instrument": "otlp-receiver-availability",
                    },
                )
            except Exception:
                pass


class OpenTelemetryAiEconomicsSink:
    """Map one protected profile snapshot to bounded OTLP gauges."""

    def __init__(self, meter: Any, *, attribute_mode: str = "tenant-scope") -> None:
        if attribute_mode not in _AI_ECONOMICS_ATTRIBUTE_MODES:
            raise OpenTelemetryConfigurationError("telemetry.configuration.invalid")
        self._attribute_mode = attribute_mode
        self._requests = meter.create_gauge(
            "iip.ai.usage.requests",
            unit="{request}",
            description="Successful observed AI invocations in the profile window.",
        )
        self._input_tokens = meter.create_gauge(
            "iip.ai.usage.input_tokens",
            unit="{token}",
            description="Known input tokens in the profile window.",
        )
        self._output_tokens = meter.create_gauge(
            "iip.ai.usage.output_tokens",
            unit="{token}",
            description="Known output tokens in the profile window.",
        )
        self._metered_requests = meter.create_gauge(
            "iip.ai.usage.metered_requests",
            unit="{request}",
            description="Requests with the named token total present.",
        )
        self._incomplete_requests = meter.create_gauge(
            "iip.ai.usage.incomplete_requests",
            unit="{request}",
            description="Requests whose provider usage meters are incomplete.",
        )
        self._cost_requests = meter.create_gauge(
            "iip.ai.cost.requests",
            unit="{request}",
            description="Requests grouped by exact calculation status.",
        )
        self._cost_amount = meter.create_gauge(
            "iip.ai.cost.amount",
            unit="{currency-subunit}",
            description="Calculated-estimate cost for priced requests.",
        )
        self._input_tokens_per_request = meter.create_gauge(
            "iip.ai.usage.input_tokens_per_request",
            unit="{token}/{request}",
            description="Mean input tokens per request for a comparison window.",
        )
        self._context_growth = meter.create_gauge(
            "iip.ai.context_growth.change",
            unit="1",
            description="Current input-token mean change in basis points.",
        )
        self._retry_operations = meter.create_gauge(
            "iip.ai.retry.operations",
            unit="{operation}",
            description="Operations grouped by normalized retry-fact status.",
        )
        self._retry_excess_attempts = meter.create_gauge(
            "iip.ai.retry.excess_attempts",
            unit="{attempt}",
            description="Reported retry attempts beyond the initial operation attempt.",
        )
        self._retry_rate = meter.create_gauge(
            "iip.ai.retry.operation_rate",
            unit="1",
            description="Operations reporting retries, expressed in basis points.",
        )
        self._retry_rate_increase = meter.create_gauge(
            "iip.ai.retry.operation_rate_increase",
            unit="1",
            description="Absolute retrying-operation rate increase in basis points.",
        )
        self._evaluation_status = meter.create_gauge(
            "iip.ai.savings.profile_status",
            unit="1",
            description="One for the current deterministic rule status, otherwise zero.",
        )
        self._findings = meter.create_gauge(
            "iip.ai.savings.findings",
            unit="{finding}",
            description="Committed evidence-backed savings findings.",
        )
        self._potential_savings = meter.create_gauge(
            "iip.ai.savings.potential_amount",
            unit="{currency-subunit}",
            description="Evidence-backed potential saving for the current window.",
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

    def record_ai_economics(self, measurement: AiEconomicsMeasurement) -> None:
        try:
            self._validate(measurement)
            common = self._attributes(measurement)
            self._requests.set(measurement.request_count, common)
            self._input_tokens.set(measurement.input_tokens, common)
            self._output_tokens.set(measurement.output_tokens, common)
            for meter_name, value in (
                ("input", measurement.input_token_requests),
                ("output", measurement.output_token_requests),
            ):
                attributes = dict(common)
                attributes["iip.ai.usage.meter"] = meter_name
                self._metered_requests.set(value, attributes)
            self._incomplete_requests.set(measurement.incomplete_requests, common)
            for status, value in (
                ("priced", measurement.priced_requests),
                ("unpriced", measurement.unpriced_requests),
                ("ambiguous", measurement.ambiguous_requests),
                ("pending", measurement.pending_cost_requests),
            ):
                attributes = dict(common)
                attributes["iip.ai.cost.status"] = status
                self._cost_requests.set(value, attributes)
            if measurement.calculated_cost_subunits is not None:
                self._cost_amount.set(
                    measurement.calculated_cost_subunits,
                    self._money_attributes(common, measurement),
                )
            for comparison, value in (
                ("baseline", measurement.baseline_input_tokens_per_request),
                ("current", measurement.current_input_tokens_per_request),
            ):
                if value is not None:
                    attributes = dict(common)
                    attributes["iip.ai.comparison.window"] = comparison
                    self._input_tokens_per_request.set(value, attributes)
            if measurement.context_growth_change_basis_points is not None:
                self._context_growth.set(
                    measurement.context_growth_change_basis_points,
                    common,
                )
            for status in _AI_ECONOMICS_EVALUATION_STATUSES:
                attributes = dict(common)
                attributes["iip.ai.savings.status"] = status
                self._evaluation_status.set(
                    1 if status == measurement.evaluation_status else 0,
                    attributes,
                )
            finding_attributes = dict(common)
            finding_attributes.update(
                {
                    "iip.ai.savings.rule.id": "context-growth",
                    "iip.ai.savings.rule.version": "1.0.0",
                    "iip.ai.savings.severity": (
                        measurement.finding_severity or "none"
                    ),
                }
            )
            self._findings.set(measurement.finding_count, finding_attributes)
            if measurement.potential_savings_subunits is not None:
                finding_attributes.update(
                    self._money_attributes(common, measurement)
                )
                self._potential_savings.set(
                    measurement.potential_savings_subunits,
                    finding_attributes,
                )
        except Exception:
            with self._failure_lock:
                self._failures += 1
            try:
                self._record_failure.add(
                    1,
                    {
                        "iip.telemetry.signal": "metrics",
                        "iip.telemetry.instrument": "ai-economics",
                    },
                )
            except Exception:
                pass

    def record_ai_retry(self, measurement: AiRetryMeasurement) -> None:
        try:
            self._validate_retry(measurement)
            common = self._retry_attributes(measurement)
            status_values = (
                ("retrying", measurement.current_retrying_operations),
                (
                    "not-retrying",
                    measurement.current_retry_fact_operations
                    - measurement.current_retrying_operations,
                ),
                (
                    "fact-missing",
                    measurement.current_operations
                    - measurement.current_retry_fact_operations,
                ),
            )
            for status, value in status_values:
                attributes = dict(common)
                attributes["iip.ai.retry.status"] = status
                self._retry_operations.set(value, attributes)
            self._retry_excess_attempts.set(
                measurement.current_excess_attempts,
                common,
            )
            for comparison, value in (
                ("baseline", measurement.baseline_retry_rate_basis_points),
                ("current", measurement.current_retry_rate_basis_points),
            ):
                if value is not None:
                    attributes = dict(common)
                    attributes["iip.ai.comparison.window"] = comparison
                    self._retry_rate.set(value, attributes)
            if measurement.retry_rate_increase_basis_points is not None:
                self._retry_rate_increase.set(
                    measurement.retry_rate_increase_basis_points,
                    common,
                )
            for status in _AI_ECONOMICS_EVALUATION_STATUSES:
                attributes = dict(common)
                attributes["iip.ai.savings.status"] = status
                self._evaluation_status.set(
                    1 if status == measurement.evaluation_status else 0,
                    attributes,
                )
            finding_attributes = dict(common)
            finding_attributes.update(
                {
                    "iip.ai.savings.rule.id": "retry-amplification",
                    "iip.ai.savings.rule.version": "1.0.0",
                    "iip.ai.savings.severity": (
                        measurement.finding_severity or "none"
                    ),
                }
            )
            self._findings.set(measurement.finding_count, finding_attributes)
        except Exception:
            with self._failure_lock:
                self._failures += 1
            try:
                self._record_failure.add(
                    1,
                    {
                        "iip.telemetry.signal": "metrics",
                        "iip.telemetry.instrument": "ai-retry",
                    },
                )
            except Exception:
                pass

    def _attributes(
        self,
        measurement: AiEconomicsMeasurement,
    ) -> dict[str, str | int]:
        attributes: dict[str, str | int] = {
            "iip.ai.profile.id": measurement.profile_id,
            "gen_ai.provider.name": measurement.provider,
            "gen_ai.response.model": measurement.model_id,
            "cloud.region": measurement.region,
            "service.name": measurement.service_name,
            "deployment.environment.name": measurement.deployment_environment,
        }
        if self._attribute_mode == "tenant-scope":
            attributes["iip.tenant.id"] = measurement.tenant_id
        return attributes

    def _retry_attributes(
        self,
        measurement: AiRetryMeasurement,
    ) -> dict[str, str | int]:
        attributes: dict[str, str | int] = {
            "iip.ai.profile.id": measurement.profile_id,
            "gen_ai.provider.name": measurement.provider,
            "gen_ai.response.model": measurement.model_id,
            "cloud.region": measurement.region,
            "service.name": measurement.service_name,
            "deployment.environment.name": measurement.deployment_environment,
        }
        if self._attribute_mode == "tenant-scope":
            attributes["iip.tenant.id"] = measurement.tenant_id
        return attributes

    @staticmethod
    def _money_attributes(
        common: Mapping[str, str | int],
        measurement: AiEconomicsMeasurement,
    ) -> dict[str, str | int]:
        if measurement.currency is None or measurement.currency_scale is None:
            raise ValueError
        return {
            **common,
            "iip.ai.currency": measurement.currency,
            "iip.ai.currency_scale": measurement.currency_scale,
            "iip.ai.cost.basis": "calculated-estimate",
        }

    @staticmethod
    def _validate(measurement: AiEconomicsMeasurement) -> None:
        if not isinstance(measurement, AiEconomicsMeasurement):
            raise ValueError
        text_values = (
            (measurement.tenant_id, 128),
            (measurement.profile_id, 128),
            (measurement.provider, 64),
            (measurement.model_id, 256),
            (measurement.region, 64),
            (measurement.service_name, 256),
            (measurement.deployment_environment, 128),
        )
        if any(
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in value
            )
            for value, maximum in text_values
        ):
            raise ValueError
        counts = (
            measurement.request_count,
            measurement.input_tokens,
            measurement.input_token_requests,
            measurement.output_tokens,
            measurement.output_token_requests,
            measurement.incomplete_requests,
            measurement.priced_requests,
            measurement.unpriced_requests,
            measurement.ambiguous_requests,
            measurement.pending_cost_requests,
            measurement.finding_count,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= _MAX_COUNTER
            for value in counts
        ):
            raise ValueError
        if (
            measurement.input_token_requests > measurement.request_count
            or measurement.output_token_requests > measurement.request_count
            or measurement.incomplete_requests > measurement.request_count
            or sum(
                (
                    measurement.priced_requests,
                    measurement.unpriced_requests,
                    measurement.ambiguous_requests,
                    measurement.pending_cost_requests,
                )
            )
            != measurement.request_count
            or measurement.evaluation_status
            not in _AI_ECONOMICS_EVALUATION_STATUSES
            or measurement.finding_count not in (0, 1)
        ):
            raise ValueError
        optional_non_negative_integers = (
            measurement.calculated_cost_subunits,
            measurement.baseline_input_tokens_per_request,
            measurement.current_input_tokens_per_request,
            measurement.potential_savings_subunits,
        )
        if any(
            value is not None
            and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= _MAX_COUNTER
            )
            for value in optional_non_negative_integers
        ) or (
            measurement.context_growth_change_basis_points is not None
            and (
                isinstance(measurement.context_growth_change_basis_points, bool)
                or not isinstance(
                    measurement.context_growth_change_basis_points, int
                )
                or not -_MAX_COUNTER
                <= measurement.context_growth_change_basis_points
                <= _MAX_COUNTER
            )
        ):
            raise ValueError
        has_money = (
            measurement.calculated_cost_subunits is not None
            or measurement.potential_savings_subunits is not None
        )
        if (
            has_money
            and (
                not isinstance(measurement.currency, str)
                or len(measurement.currency) != 3
                or not measurement.currency.isupper()
                or measurement.currency_scale not in (6, 9, 12)
            )
        ):
            raise ValueError
        if (
            measurement.finding_count == 0
            and (
                measurement.finding_severity is not None
                or measurement.potential_savings_subunits is not None
            )
        ) or (
            measurement.finding_count == 1
            and (
                measurement.finding_severity not in {"low", "medium", "high"}
                or measurement.potential_savings_subunits is None
            )
        ):
            raise ValueError

    @staticmethod
    def _validate_retry(measurement: AiRetryMeasurement) -> None:
        if not isinstance(measurement, AiRetryMeasurement):
            raise ValueError
        text_values = (
            (measurement.tenant_id, 128),
            (measurement.profile_id, 128),
            (measurement.provider, 64),
            (measurement.model_id, 256),
            (measurement.region, 64),
            (measurement.service_name, 256),
            (measurement.deployment_environment, 128),
        )
        if any(
            not isinstance(value, str)
            or not 1 <= len(value) <= maximum
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in value
            )
            for value, maximum in text_values
        ):
            raise ValueError
        counts = (
            measurement.current_operations,
            measurement.current_retry_fact_operations,
            measurement.current_retrying_operations,
            measurement.current_excess_attempts,
            measurement.finding_count,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= _MAX_COUNTER
            for value in counts
        ):
            raise ValueError
        if (
            measurement.current_retry_fact_operations
            > measurement.current_operations
            or measurement.current_retrying_operations
            > measurement.current_retry_fact_operations
            or measurement.finding_count not in (0, 1)
            or measurement.evaluation_status
            not in _AI_ECONOMICS_EVALUATION_STATUSES
            or measurement.finding_severity
            not in (None, "low", "medium", "high")
            or (measurement.finding_count == 0)
            != (measurement.finding_severity is None)
        ):
            raise ValueError
        rates = (
            measurement.baseline_retry_rate_basis_points,
            measurement.current_retry_rate_basis_points,
            measurement.retry_rate_increase_basis_points,
        )
        if any(
            value is not None
            and (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not -10_000 <= value <= 10_000
            )
            for value in rates
        ):
            raise ValueError
        if (
            measurement.baseline_retry_rate_basis_points is not None
            and measurement.baseline_retry_rate_basis_points < 0
            or measurement.current_retry_rate_basis_points is not None
            and measurement.current_retry_rate_basis_points < 0
        ):
            raise ValueError


class OpenTelemetryAiAllocationSink:
    """Export complete, bounded allocation snapshots using protected IDs only."""

    def __init__(self, meter: Any, *, attribute_mode: str = "tenant-scope") -> None:
        if attribute_mode not in _AI_ECONOMICS_ATTRIBUTE_MODES:
            raise OpenTelemetryConfigurationError("telemetry.configuration.invalid")
        self._attribute_mode = attribute_mode
        self._requests = meter.create_gauge(
            "iip.ai.allocation.requests",
            unit="{request}",
            description="Observed invocations grouped by protected allocation.",
        )
        self._input_tokens = meter.create_gauge(
            "iip.ai.allocation.input_tokens",
            unit="{token}",
            description="Known input tokens grouped by protected allocation.",
        )
        self._output_tokens = meter.create_gauge(
            "iip.ai.allocation.output_tokens",
            unit="{token}",
            description="Known output tokens grouped by protected allocation.",
        )
        self._metered_requests = meter.create_gauge(
            "iip.ai.allocation.metered_requests",
            unit="{request}",
            description="Requests with the named token total present.",
        )
        self._cost_requests = meter.create_gauge(
            "iip.ai.allocation.cost_requests",
            unit="{request}",
            description="Allocation requests grouped by exact cost coverage.",
        )
        self._cost_amount = meter.create_gauge(
            "iip.ai.allocation.cost_amount",
            unit="{currency-subunit}",
            description="Calculated-estimate priced cost grouped by allocation.",
        )
        self._record_failure = meter.create_counter(
            "iip.telemetry.record.failures",
            unit="1",
            description="Measurements rejected before reaching an exporter.",
        )
        self._previous: dict[
            tuple[str, str, str, str | None, str, int], AiAllocationMeasurement
        ] = {}
        self._snapshot_lock = Lock()
        self._failures = 0

    @property
    def record_failures(self) -> int:
        with self._snapshot_lock:
            return self._failures

    def record_ai_allocation_snapshot(
        self,
        tenant_id: str,
        measurements: tuple[AiAllocationMeasurement, ...],
    ) -> None:
        try:
            if (
                not isinstance(tenant_id, str)
                or not 1 <= len(tenant_id) <= 128
                or not isinstance(measurements, tuple)
                or len(measurements) > 2004
            ):
                raise ValueError
            current: dict[
                tuple[str, str, str, str | None, str, int],
                AiAllocationMeasurement,
            ] = {}
            for measurement in measurements:
                self._validate(measurement)
                if measurement.tenant_id != tenant_id:
                    raise ValueError
                key = self._key(measurement)
                if key in current:
                    raise ValueError
                current[key] = measurement
            with self._snapshot_lock:
                previous = {
                    key: value
                    for key, value in self._previous.items()
                    if key[0] == tenant_id
                }
                self._previous = {
                    key: value
                    for key, value in self._previous.items()
                    if key[0] != tenant_id
                }
                self._previous.update(current)
            for key, measurement in current.items():
                prior = previous.get(key)
                self._record(measurement)
                if (
                    prior is not None
                    and prior.calculated_cost_subunits is not None
                    and measurement.calculated_cost_subunits is None
                ):
                    self._record_cost_zero(prior)
                previous.pop(key, None)
            for measurement in previous.values():
                self._record(self._zero(measurement))
        except Exception:
            with self._snapshot_lock:
                self._failures += 1
            try:
                self._record_failure.add(
                    1,
                    {
                        "iip.telemetry.signal": "metrics",
                        "iip.telemetry.instrument": "ai-allocation",
                    },
                )
            except Exception:
                pass

    def _record(self, measurement: AiAllocationMeasurement) -> None:
        common = self._attributes(measurement)
        self._requests.set(measurement.request_count, common)
        self._input_tokens.set(measurement.input_tokens, common)
        self._output_tokens.set(measurement.output_tokens, common)
        for meter_name, value in (
            ("input", measurement.input_token_records),
            ("output", measurement.output_token_records),
        ):
            attributes = dict(common)
            attributes["iip.ai.usage.meter"] = meter_name
            self._metered_requests.set(value, attributes)
        for status, value in (
            ("priced", measurement.priced_requests),
            ("unpriced", measurement.unpriced_requests),
            ("ambiguous", measurement.ambiguous_requests),
            ("pending", measurement.pending_cost_requests),
        ):
            attributes = dict(common)
            attributes["iip.ai.cost.status"] = status
            self._cost_requests.set(value, attributes)
        if measurement.calculated_cost_subunits is not None:
            self._cost_amount.set(
                measurement.calculated_cost_subunits,
                self._money_attributes(common, measurement),
            )

    def _record_cost_zero(self, measurement: AiAllocationMeasurement) -> None:
        self._cost_amount.set(
            0,
            self._money_attributes(self._attributes(measurement), measurement),
        )

    @staticmethod
    def _money_attributes(
        common: Mapping[str, str | int],
        measurement: AiAllocationMeasurement,
    ) -> dict[str, str | int]:
        return {
            **common,
            "iip.ai.currency": measurement.currency,
            "iip.ai.currency_scale": measurement.currency_scale,
            "iip.ai.cost.basis": "calculated-estimate",
        }

    def _attributes(
        self, measurement: AiAllocationMeasurement
    ) -> dict[str, str | int]:
        attributes: dict[str, str | int] = {
            "iip.ai.allocation.dimension": measurement.dimension,
            "iip.ai.allocation.status": measurement.allocation_status,
        }
        if measurement.dimension_id is not None:
            attributes[
                f"iip.ai.{measurement.dimension}.id"
            ] = measurement.dimension_id
        if self._attribute_mode == "tenant-scope":
            attributes["iip.tenant.id"] = measurement.tenant_id
        return attributes

    @staticmethod
    def _key(
        measurement: AiAllocationMeasurement,
    ) -> tuple[str, str, str, str | None, str, int]:
        return (
            measurement.tenant_id,
            measurement.dimension,
            measurement.allocation_status,
            measurement.dimension_id,
            measurement.currency,
            measurement.currency_scale,
        )

    @staticmethod
    def _zero(measurement: AiAllocationMeasurement) -> AiAllocationMeasurement:
        return AiAllocationMeasurement(
            tenant_id=measurement.tenant_id,
            dimension=measurement.dimension,
            allocation_status=measurement.allocation_status,
            dimension_id=measurement.dimension_id,
            request_count=0,
            input_tokens=0,
            input_token_records=0,
            output_tokens=0,
            output_token_records=0,
            priced_requests=0,
            unpriced_requests=0,
            ambiguous_requests=0,
            pending_cost_requests=0,
            calculated_cost_subunits=(
                0
                if measurement.calculated_cost_subunits is not None
                else None
            ),
            currency=measurement.currency,
            currency_scale=measurement.currency_scale,
        )

    @staticmethod
    def _validate(measurement: AiAllocationMeasurement) -> None:
        if not isinstance(measurement, AiAllocationMeasurement):
            raise ValueError
        if (
            not isinstance(measurement.tenant_id, str)
            or not 1 <= len(measurement.tenant_id) <= 128
            or measurement.dimension not in {"application", "team"}
            or measurement.allocation_status
            not in {"allocated", "unallocated", "pending"}
            or (measurement.allocation_status == "allocated")
            != (measurement.dimension_id is not None)
            or (
                measurement.dimension_id is not None
                and _SAFE_AI_DIMENSION_ID.fullmatch(measurement.dimension_id) is None
            )
            or not isinstance(measurement.currency, str)
            or len(measurement.currency) != 3
            or not measurement.currency.isupper()
            or measurement.currency_scale not in (6, 9, 12)
        ):
            raise ValueError
        counts = (
            measurement.request_count,
            measurement.input_tokens,
            measurement.input_token_records,
            measurement.output_tokens,
            measurement.output_token_records,
            measurement.priced_requests,
            measurement.unpriced_requests,
            measurement.ambiguous_requests,
            measurement.pending_cost_requests,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= _MAX_COUNTER
            for value in counts
        ) or (
            measurement.input_token_records > measurement.request_count
            or measurement.output_token_records > measurement.request_count
            or sum(counts[5:]) != measurement.request_count
            or (
                measurement.calculated_cost_subunits is not None
                and (
                    isinstance(measurement.calculated_cost_subunits, bool)
                    or not isinstance(measurement.calculated_cost_subunits, int)
                    or not 0
                    <= measurement.calculated_cost_subunits
                    <= _MAX_COUNTER
                )
            )
        ):
            raise ValueError


class OpenTelemetryInvestigationSink:
    """Emit one bounded span from each durable terminal investigation report."""

    def __init__(self, tracer: Any, *, attribute_mode: str = "none") -> None:
        if attribute_mode not in _INVESTIGATION_ATTRIBUTE_MODES:
            raise OpenTelemetryConfigurationError("telemetry.configuration.invalid")
        self._tracer = tracer
        self._attribute_mode = attribute_mode
        self._failures = 0
        self._failure_lock = Lock()

    @property
    def record_failures(self) -> int:
        with self._failure_lock:
            return self._failures

    def record_investigation_execution(
        self, measurement: InvestigationExecutionMeasurement
    ) -> None:
        try:
            start_time = self._unix_nanos(measurement.started_at)
            end_time = self._unix_nanos(measurement.completed_at)
            if end_time < start_time:
                raise ValueError
            attributes: dict[str, str | int | float] = {
                "iip.investigation.outcome": measurement.outcome,
                "iip.investigation.terminal_reason": measurement.terminal_reason,
                "iip.investigation.wall_time": measurement.wall_time_seconds,
                "iip.investigation.tool_calls": measurement.tool_calls,
                "iip.investigation.evidence_items": measurement.evidence_items,
            }
            if self._attribute_mode in (
                "investigation",
                "tenant-investigation",
            ):
                attributes["iip.investigation.id"] = measurement.investigation_id
            if self._attribute_mode == "tenant-investigation":
                attributes["iip.tenant.id"] = measurement.tenant_id
            span = self._tracer.start_span(
                "iip.investigation.execute",
                attributes=attributes,
                start_time=start_time,
            )
            if measurement.outcome == "failed":
                from opentelemetry.trace import Status, StatusCode

                span.set_status(
                    Status(StatusCode.ERROR, measurement.terminal_reason)
                )
            span.end(end_time=end_time)
        except Exception:
            with self._failure_lock:
                self._failures += 1

    @staticmethod
    def _unix_nanos(value: str) -> int:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        return int(parsed.timestamp() * 1_000_000_000)


@dataclass(frozen=True)
class OtlpMetricsRuntime:
    """Own the SDK provider so composition can flush and stop its worker."""

    sink: OpenTelemetryIngestionSink
    provider: Any
    health: TelemetryExportHealthState | None = None
    query_sink: OpenTelemetryQueryAvailabilitySink | None = None
    receiver_sink: OpenTelemetryOtlpReceiverSink | None = None
    ai_economics_sink: OpenTelemetryAiEconomicsSink | None = None
    ai_allocation_sink: OpenTelemetryAiAllocationSink | None = None

    def force_flush(self, timeout_millis: int = 10_000) -> bool:
        return bool(self.provider.force_flush(timeout_millis=timeout_millis))

    def shutdown(self, timeout_millis: int = 30_000) -> None:
        self.provider.shutdown(timeout_millis=timeout_millis)

    def read_export_health(self) -> tuple[TelemetryExportSignalState, ...]:
        health = self.health or TelemetryExportHealthState(("metrics",))
        return health.read_export_health()


@dataclass(frozen=True)
class OtlpTracesRuntime:
    """Own the trace provider and its bounded asynchronous export queue."""

    sink: OpenTelemetryInvestigationSink
    provider: Any
    health: TelemetryExportHealthState | None = None

    def force_flush(self, timeout_millis: int = 10_000) -> bool:
        return bool(self.provider.force_flush(timeout_millis=timeout_millis))

    def shutdown(self, timeout_millis: int = 30_000) -> None:
        del timeout_millis
        self.provider.shutdown()

    def read_export_health(self) -> tuple[TelemetryExportSignalState, ...]:
        health = self.health or TelemetryExportHealthState(("traces",))
        return health.read_export_health()


@dataclass(frozen=True)
class CompositeTelemetryRuntime:
    """Manage independently enabled OTLP signal providers as one runtime."""

    parts: tuple[Any, ...]

    def force_flush(self, timeout_millis: int = 10_000) -> bool:
        succeeded = True
        for part in self.parts:
            try:
                succeeded = bool(part.force_flush(timeout_millis)) and succeeded
            except Exception:
                succeeded = False
        return succeeded

    def shutdown(self, timeout_millis: int = 30_000) -> None:
        for part in self.parts:
            try:
                part.shutdown(timeout_millis)
            except Exception:
                continue

    def read_export_health(self) -> tuple[TelemetryExportSignalState, ...]:
        selected: dict[str, TelemetryExportSignalState] = {}
        for part in self.parts:
            for state in part.read_export_health():
                existing = selected.get(state.signal)
                if existing is None or (state.enabled and not existing.enabled):
                    selected[state.signal] = state
        return tuple(selected[signal] for signal in _SIGNALS)


def build_otlp_metrics_runtime(
    configuration: OtlpMetricsConfiguration,
    health: TelemetryExportHealthState | None = None,
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

        export_health = health or TelemetryExportHealthState(("metrics",))
        exporter = TrackingMetricExporter(
            OTLPMetricExporter(endpoint=configuration.endpoint),
            export_health,
        )
        reader = PeriodicExportingMetricReader(
            exporter,
            export_interval_millis=configuration.export_interval_millis,
            export_timeout_millis=configuration.export_timeout_millis,
        )
        provider = MeterProvider(
            resource=Resource.create(
                {
                    "service.name": configuration.service_name,
                    "service.version": "0.67.0",
                }
            ),
            metric_readers=(reader,),
        )
        ingestion_meter = provider.get_meter("iip.ingestion", "0.67.0")
        query_meter = provider.get_meter("iip.query", "0.67.0")
        receiver_meter = provider.get_meter("iip.otlp.receiver", "0.67.0")
        ai_economics_meter = provider.get_meter("iip.ai.economics", "0.67.0")
        sink = OpenTelemetryIngestionSink(
            ingestion_meter,
            attribute_mode=configuration.attribute_mode,
        )
        return OtlpMetricsRuntime(
            sink=sink,
            provider=provider,
            health=export_health,
            query_sink=OpenTelemetryQueryAvailabilitySink(query_meter),
            receiver_sink=OpenTelemetryOtlpReceiverSink(receiver_meter),
            ai_economics_sink=OpenTelemetryAiEconomicsSink(
                ai_economics_meter,
                attribute_mode=configuration.ai_economics_attribute_mode,
            ),
            ai_allocation_sink=OpenTelemetryAiAllocationSink(
                ai_economics_meter,
                attribute_mode=configuration.ai_economics_attribute_mode,
            ),
        )
    except OpenTelemetryConfigurationError:
        raise
    except Exception:
        raise OpenTelemetryConfigurationError(
            "telemetry.initialization.failed"
        ) from None


def build_otlp_traces_runtime(
    configuration: OtlpTracesConfiguration,
    health: TelemetryExportHealthState | None = None,
) -> OtlpTracesRuntime:
    """Compose the official OTLP/HTTP trace SDK inside the adapter layer."""

    configuration.validate()
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        export_health = health or TelemetryExportHealthState(("traces",))
        exporter = TrackingSpanExporter(
            OTLPSpanExporter(endpoint=configuration.endpoint),
            export_health,
        )
        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": configuration.service_name,
                    "service.version": "0.67.0",
                }
            )
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                exporter,
                schedule_delay_millis=configuration.schedule_delay_millis,
                export_timeout_millis=configuration.export_timeout_millis,
                max_queue_size=configuration.max_queue_size,
                max_export_batch_size=configuration.max_export_batch_size,
            )
        )
        tracer = provider.get_tracer("iip.investigation", "0.67.0")
        sink = OpenTelemetryInvestigationSink(
            tracer,
            attribute_mode=configuration.attribute_mode,
        )
        return OtlpTracesRuntime(
            sink=sink,
            provider=provider,
            health=export_health,
        )
    except OpenTelemetryConfigurationError:
        raise
    except Exception:
        raise OpenTelemetryConfigurationError(
            "telemetry.initialization.failed"
        ) from None
