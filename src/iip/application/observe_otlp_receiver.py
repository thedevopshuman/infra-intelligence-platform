"""Failure-isolated OTLP receiver availability classification."""

from __future__ import annotations

import math
from dataclasses import dataclass

from iip.application.ports import (
    OtlpReceiverMeasurement,
    OtlpReceiverTelemetrySink,
)


OTLP_RECEIVER_SIGNALS = frozenset({"metrics", "logs"})
_MAX_DURATION_SECONDS = 86_400.0


@dataclass(frozen=True)
class OtlpReceiverObjectives:
    """Deployment-owned aggregation objective carried with each observation."""

    window_seconds: int = 3600
    minimum_availability_basis_points: int = 9990
    minimum_eligible_requests: int = 100

    def __post_init__(self) -> None:
        values = (
            self.window_seconds,
            self.minimum_availability_basis_points,
            self.minimum_eligible_requests,
        )
        if (
            any(isinstance(value, bool) or not isinstance(value, int) for value in values)
            or not 300 <= self.window_seconds <= 2_592_000
            or not 1 <= self.minimum_availability_basis_points <= 10_000
            or not 1 <= self.minimum_eligible_requests <= 1_000_000
        ):
            raise ValueError("otlp.receiver.availability.configuration.invalid")


@dataclass(frozen=True)
class RecordOtlpReceiverCommand:
    signal: str
    status_code: int
    duration_seconds: float
    uncaught_failure: bool = False


class OtlpReceiverTelemetryService:
    """Classify one OTLP request and offer a privacy-bounded measurement."""

    def __init__(
        self,
        sink: OtlpReceiverTelemetrySink | None = None,
        objectives: OtlpReceiverObjectives | None = None,
    ) -> None:
        self._sink = sink
        self._objectives = objectives or OtlpReceiverObjectives()

    def record(self, command: RecordOtlpReceiverCommand) -> None:
        if self._sink is None:
            return
        if (
            command.signal not in OTLP_RECEIVER_SIGNALS
            or isinstance(command.status_code, bool)
            or not isinstance(command.status_code, int)
            or not 200 <= command.status_code <= 599
            or isinstance(command.duration_seconds, bool)
            or not isinstance(command.duration_seconds, (int, float))
            or not math.isfinite(float(command.duration_seconds))
        ):
            return

        status = command.status_code
        if command.uncaught_failure:
            outcome, availability = "internal-error", "unavailable"
        elif 200 <= status < 300:
            outcome, availability = "success", "available"
        elif status == 401:
            outcome, availability = "unauthenticated", "excluded"
        elif status == 403:
            outcome, availability = "denied", "excluded"
        elif status == 404:
            outcome, availability = "disabled", "excluded"
        elif status == 429:
            outcome, availability = "rate-limited", "unavailable"
        elif status in (400, 413, 415):
            outcome, availability = "invalid", "excluded"
        elif status in (502, 503, 504):
            outcome, availability = "unavailable", "unavailable"
        elif status >= 500:
            outcome, availability = "internal-error", "unavailable"
        else:
            outcome, availability = "invalid", "excluded"

        measurement = OtlpReceiverMeasurement(
            signal=command.signal,
            outcome=outcome,
            availability=availability,
            duration_seconds=min(
                _MAX_DURATION_SECONDS,
                max(0.0, float(command.duration_seconds)),
            ),
            objective_window_seconds=self._objectives.window_seconds,
            objective_minimum_availability_basis_points=(
                self._objectives.minimum_availability_basis_points
            ),
            objective_minimum_eligible_requests=(
                self._objectives.minimum_eligible_requests
            ),
        )
        try:
            self._sink.record_otlp_receiver(measurement)
        except Exception:
            # Optional observability cannot change the owning HTTP result.
            return
