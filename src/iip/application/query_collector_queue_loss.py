"""Privileged Collector-observed sending-queue depth and send-loss objective.

This is deliberately distinct from ``query_telemetry_export_burn_rate``: the
burn-rate report measures IIP's own local exporter attempts (IIP-to-Collector
reliability) from durable counter samples this process wrote itself. This
service measures the customer's OpenTelemetry Collector's own internal
sending queue for its pipeline *to* IIP's receiver (Collector-to-backend
reliability), sourced by querying the customer's own telemetry backend for
the Collector's self-emitted metrics through the existing backend-neutral
``TelemetryMetricsBackend`` port. IIP never operates or stores the Collector
queue itself; ADR 0080 makes queue capacity, retention, and loss policy an
explicit customer deployment choice.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from iip.application.ports import (
    ActorContext,
    Clock,
    PolicyDecisionPoint,
    TelemetryMetricsBackend,
    TelemetryMetricsQuery,
)


_SIGNALS = ("metrics", "logs")
_METRIC_NAMES = {
    "queue-size": "platform.collector.exporter.queue-size",
    "queue-capacity": "platform.collector.exporter.queue-capacity",
    "sent": {
        "metrics": "platform.collector.exporter.sent-metric-points",
        "logs": "platform.collector.exporter.sent-log-records",
    },
    "send-failed": {
        "metrics": "platform.collector.exporter.send-failed-metric-points",
        "logs": "platform.collector.exporter.send-failed-log-records",
    },
}
# disabled < meeting < no-data < insufficient-data < breached, matching the
# same conservative precedence ADR 0073's export SLO uses: a signal only
# outranks "meeting" once it carries genuine uncertainty or a confirmed
# objective violation, and "disabled" only wins when every signal is
# disabled.
_SEVERITY = {
    "disabled": 0,
    "meeting": 1,
    "no-data": 2,
    "insufficient-data": 3,
    "breached": 4,
}


class CollectorQueueLossAuthorizationError(PermissionError):
    """The actor cannot inspect the Collector queue/loss objective."""


class CollectorQueueLossStateError(RuntimeError):
    """The configured telemetry backend returned an unusable result."""


@dataclass(frozen=True)
class CollectorQueueLossBinding:
    """Deployment-owned pointer to the Collector's self-metrics.

    Non-secret only: which already-configured ``TelemetryMetricsBackend``
    integration exposes the Collector's own metrics, and the exporter
    component name the customer gave their IIP-bound pipeline (Collector
    configuration and metric label conventions are otherwise arbitrary).
    """

    integration_id: str
    exporter_name: str
    signals: tuple[str, ...] = _SIGNALS

    def __post_init__(self) -> None:
        if (
            not isinstance(self.integration_id, str)
            or not 1 <= len(self.integration_id) <= 128
            or not isinstance(self.exporter_name, str)
            or not 1 <= len(self.exporter_name) <= 256
            or not self.signals
            or len(set(self.signals)) != len(self.signals)
            or not set(self.signals).issubset(_SIGNALS)
        ):
            raise ValueError("telemetry.collector-queue-loss.configuration.invalid")


@dataclass(frozen=True)
class CollectorQueueLossObjectives:
    """Deployment-owned single-window Collector queue/loss objective."""

    window_seconds: int = 3_600
    max_loss_basis_points: int = 100
    max_queue_utilization_basis_points: int = 8_000
    minimum_eligible_attempts: int = 20

    def __post_init__(self) -> None:
        values = (
            self.window_seconds,
            self.max_loss_basis_points,
            self.max_queue_utilization_basis_points,
            self.minimum_eligible_attempts,
        )
        if (
            any(isinstance(value, bool) or not isinstance(value, int) for value in values)
            or not 300 <= self.window_seconds <= 2_592_000
            or not 0 <= self.max_loss_basis_points <= 10_000
            or not 0 <= self.max_queue_utilization_basis_points <= 10_000
            or not 1 <= self.minimum_eligible_attempts <= 1_000_000
        ):
            raise ValueError(
                "telemetry.collector-queue-loss.configuration.invalid"
            )


@dataclass(frozen=True)
class GetCollectorQueueLossCommand:
    actor: ActorContext


@dataclass(frozen=True)
class CollectorQueueLossReport:
    document: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return dict(self.document)


class CollectorQueueLossService:
    """Authorize, query, and calculate the Collector queue/loss objective."""

    def __init__(
        self,
        metrics_backend: TelemetryMetricsBackend | None,
        policy: PolicyDecisionPoint,
        clock: Clock,
        binding: CollectorQueueLossBinding | None = None,
        objectives: CollectorQueueLossObjectives | None = None,
    ) -> None:
        self._metrics_backend = metrics_backend
        self._policy = policy
        self._clock = clock
        self._binding = binding
        self._objectives = objectives or CollectorQueueLossObjectives()

    def get(
        self, command: GetCollectorQueueLossCommand
    ) -> CollectorQueueLossReport:
        if "platform-admin" not in command.actor.roles:
            raise CollectorQueueLossAuthorizationError(
                "telemetry.collector-queue-loss.role-required"
            )
        decision = self._policy.decide(
            command.actor,
            "collector-queue-loss:read",
            {
                "tenantId": command.actor.tenant_id,
                "windowSeconds": self._objectives.window_seconds,
                "maxLossBasisPoints": self._objectives.max_loss_basis_points,
                "maxQueueUtilizationBasisPoints": (
                    self._objectives.max_queue_utilization_basis_points
                ),
                "minimumEligibleAttempts": self._objectives.minimum_eligible_attempts,
            },
        )
        if not decision.allowed:
            raise CollectorQueueLossAuthorizationError(decision.reason_code)

        evaluated_at = self._parse_time(self._clock.now())
        window_start = evaluated_at - timedelta(seconds=self._objectives.window_seconds)
        window = {
            "durationSeconds": self._objectives.window_seconds,
            "start": self._format_time(window_start),
            "end": self._format_time(evaluated_at),
        }
        active = self._binding is not None and self._metrics_backend is not None
        signals = [
            self._signal_document(
                command.actor, name, window_start, evaluated_at
            )
            if active
            else self._disabled_signal(name)
            for name in _SIGNALS
        ]

        status = "disabled"
        best = -1
        for item in signals:
            rank = _SEVERITY[item["status"]]
            if rank > best:
                best = rank
                status = item["status"]

        return CollectorQueueLossReport(
            {
                "apiVersion": "iip.platform/v1alpha1",
                "kind": "CollectorQueueLossReport",
                "metadata": {"evaluatedAt": self._format_time(evaluated_at)},
                "spec": {
                    "status": status,
                    "objective": {
                        "maxLossBasisPoints": self._objectives.max_loss_basis_points,
                        "maxQueueUtilizationBasisPoints": (
                            self._objectives.max_queue_utilization_basis_points
                        ),
                        "minimumEligibleAttempts": (
                            self._objectives.minimum_eligible_attempts
                        ),
                    },
                    "window": window,
                    "binding": (
                        {
                            "integrationId": self._binding.integration_id,
                            "exporterName": self._binding.exporter_name,
                        }
                        if active
                        else None
                    ),
                    "signals": signals,
                },
            }
        )

    def _disabled_signal(self, name: str) -> dict[str, object]:
        return {
            "signal": name,
            "status": "disabled",
            "sentDelta": None,
            "failedDelta": None,
            "lossBasisPoints": None,
            "queueSize": None,
            "queueCapacity": None,
            "queueUtilizationBasisPoints": None,
        }

    def _signal_document(
        self,
        actor: ActorContext,
        name: str,
        window_start: datetime,
        window_end: datetime,
    ) -> dict[str, object]:
        assert self._binding is not None and self._metrics_backend is not None
        if name not in self._binding.signals:
            return self._disabled_signal(name)

        sent_points = self._fetch(
            actor, _METRIC_NAMES["sent"][name], window_start, window_end
        )
        failed_points = self._fetch(
            actor, _METRIC_NAMES["send-failed"][name], window_start, window_end
        )
        queue_size_points = self._fetch(
            actor,
            _METRIC_NAMES["queue-size"],
            window_start,
            window_end,
            data_type=name,
        )
        queue_capacity_points = self._fetch(
            actor,
            _METRIC_NAMES["queue-capacity"],
            window_start,
            window_end,
            data_type=name,
        )

        sent_delta = self._counter_delta(sent_points)
        failed_delta = self._counter_delta(failed_points)
        queue_size = self._latest(queue_size_points)
        queue_capacity = self._latest(queue_capacity_points)
        queue_utilization = (
            None
            if queue_size is None or not queue_capacity
            else min(10_000, queue_size * 10_000 // queue_capacity)
        )

        eligible_attempts = (
            None
            if sent_delta is None or failed_delta is None
            else sent_delta + failed_delta
        )
        loss_basis_points = (
            None
            if not eligible_attempts
            else failed_delta * 10_000 // eligible_attempts
        )
        if eligible_attempts is None:
            status = "no-data"
        elif eligible_attempts == 0:
            status = "no-data"
        elif eligible_attempts < self._objectives.minimum_eligible_attempts:
            status = "insufficient-data"
        elif (
            loss_basis_points is not None
            and loss_basis_points > self._objectives.max_loss_basis_points
        ) or (
            queue_utilization is not None
            and queue_utilization
            > self._objectives.max_queue_utilization_basis_points
        ):
            status = "breached"
        else:
            status = "meeting"

        return {
            "signal": name,
            "status": status,
            "sentDelta": sent_delta,
            "failedDelta": failed_delta,
            "lossBasisPoints": loss_basis_points,
            "queueSize": queue_size,
            "queueCapacity": queue_capacity,
            "queueUtilizationBasisPoints": queue_utilization,
        }

    def _fetch(
        self,
        actor: ActorContext,
        metric: str,
        window_start: datetime,
        window_end: datetime,
        *,
        data_type: str | None = None,
    ) -> tuple[float, ...]:
        assert self._binding is not None and self._metrics_backend is not None
        filters = [("exporterName", "eq", self._binding.exporter_name)]
        if data_type is not None:
            filters.append(("dataType", "eq", data_type))
        query = TelemetryMetricsQuery(
            tenant_id=actor.tenant_id,
            actor_id=actor.actor_id,
            request_id="cql_" + secrets.token_hex(16),
            integration_id=self._binding.integration_id,
            resource_uids=(),
            start=self._format_time(window_start),
            end=self._format_time(window_end),
            metric=metric,
            filters=tuple(filters),
            aggregation="max",
            # A resolution proportional to the window (about 60 samples per
            # window), floored at 15s: coarser than that would let a short
            # window miss real, recent counter growth between the only two
            # samples it could return.
            step_seconds=max(15, self._objectives.window_seconds // 60),
            group_by=(),
            max_series=2,
            max_data_points=200,
            max_bytes=131_072,
            deadline=self._format_time(
                self._parse_time(self._clock.now()) + timedelta(seconds=10)
            ),
        )
        try:
            result = self._metrics_backend.query_metrics(query)
        except Exception:
            raise CollectorQueueLossStateError(
                "telemetry.collector-queue-loss.backend-unavailable"
            ) from None
        if result.status == "no-data" or not result.series:
            return ()
        if len(result.series) > 1:
            raise CollectorQueueLossStateError(
                "telemetry.collector-queue-loss.backend-ambiguous"
            )
        points = result.series[0].points
        values = []
        for point in points:
            if not isinstance(point.value, (int, float)) or isinstance(
                point.value, bool
            ):
                raise CollectorQueueLossStateError(
                    "telemetry.collector-queue-loss.backend-invalid"
                )
            values.append(float(point.value))
        return tuple(values)

    @staticmethod
    def _counter_delta(points: tuple[float, ...]) -> int | None:
        if not points:
            return None
        delta = points[-1] - points[0]
        # A monotonic counter can only decrease on a process restart, which
        # invalidates the measurement rather than understating loss.
        if delta < 0:
            return None
        return int(delta)

    @staticmethod
    def _latest(points: tuple[float, ...]) -> int | None:
        if not points:
            return None
        return max(0, int(points[-1]))

    @staticmethod
    def _parse_time(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            raise CollectorQueueLossStateError(
                "telemetry.collector-queue-loss.state-invalid"
            ) from None
        if parsed.tzinfo is None:
            raise CollectorQueueLossStateError(
                "telemetry.collector-queue-loss.state-invalid"
            )
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
