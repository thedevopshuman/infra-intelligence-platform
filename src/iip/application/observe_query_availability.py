"""Failure-isolated query-availability classification and measurement."""

from __future__ import annotations

import math
from dataclasses import dataclass

from iip.application.ports import (
    QueryAvailabilityMeasurement,
    QueryAvailabilitySink,
)


QUERY_OPERATIONS = frozenset(
    {
        "console-authentication",
        "session",
        "runtime-version",
        "resources-list",
        "resource-neighborhood",
        "resource-timeline",
        "ingestion-freshness",
        "telemetry-export-health",
        "telemetry-deployment-export-health",
        "telemetry-export-slo",
        "telemetry-export-burn-rate",
        "collector-queue-loss",
        "event-delivery-health",
        "event-delivery-slo",
        "investigation-completion-slo",
        "evidence-retention",
        "evidence-get",
        "investigation-get",
        "investigation-status",
        "investigation-job-get",
        "actions-list",
        "ai-allocation-report",
        "action-get",
        "action-workflow-get",
        "plugin-session-get",
        "plugin-invocation-status",
    }
)
_MAX_DURATION_SECONDS = 86_400.0


@dataclass(frozen=True)
class QueryAvailabilityObjectives:
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
            raise ValueError("query.availability.configuration.invalid")


@dataclass(frozen=True)
class RecordQueryAvailabilityCommand:
    operation: str
    status_code: int
    duration_seconds: float
    uncaught_failure: bool = False


class QueryAvailabilityService:
    """Classify one recognized query and offer a privacy-bounded measurement."""

    def __init__(
        self,
        sink: QueryAvailabilitySink | None = None,
        objectives: QueryAvailabilityObjectives | None = None,
    ) -> None:
        self._sink = sink
        self._objectives = objectives or QueryAvailabilityObjectives()

    def record(self, command: RecordQueryAvailabilityCommand) -> None:
        if self._sink is None:
            return
        if (
            command.operation not in QUERY_OPERATIONS
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
        elif status == 401:
            outcome, availability = "unauthenticated", "excluded"
        elif status == 403:
            outcome, availability = "denied", "excluded"
        elif status == 404:
            outcome, availability = "not-found", "available"
        elif status == 409:
            outcome, availability = "conflict", "available"
        elif 400 <= status < 500:
            outcome, availability = "invalid", "excluded"
        elif status >= 500:
            outcome = "unavailable" if status in (502, 503, 504) else "internal-error"
            availability = "unavailable"
        else:
            outcome, availability = "success", "available"

        measurement = QueryAvailabilityMeasurement(
            operation=command.operation,
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
            self._sink.record_query_availability(measurement)
        except Exception:
            # Optional observability cannot change the owning HTTP result.
            return
