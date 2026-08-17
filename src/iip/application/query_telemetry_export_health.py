"""Privileged process-local telemetry exporter delivery health query."""

from __future__ import annotations

from dataclasses import dataclass

from iip.application.ports import (
    ActorContext,
    Clock,
    PolicyDecisionPoint,
    TelemetryExportHealthReader,
    TelemetryExportSignalState,
)


_SIGNALS = ("metrics", "traces")
_STATUSES = frozenset({"disabled", "awaiting-first-attempt", "healthy", "degraded"})
_MAX_COUNTER = 9_007_199_254_740_991


class TelemetryExportHealthAuthorizationError(PermissionError):
    """The actor cannot inspect process-wide exporter delivery state."""


class TelemetryExportHealthStateError(RuntimeError):
    """The health reader returned inconsistent or unsafe state."""


@dataclass(frozen=True)
class GetTelemetryExportHealthCommand:
    actor: ActorContext


@dataclass(frozen=True)
class TelemetryExportHealthReport:
    document: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return dict(self.document)


class TelemetryExportHealthService:
    """Authorize and render process-local exporter state without backend details."""

    def __init__(
        self,
        reader: TelemetryExportHealthReader,
        policy: PolicyDecisionPoint,
        clock: Clock,
    ) -> None:
        self._reader = reader
        self._policy = policy
        self._clock = clock

    def get(
        self, command: GetTelemetryExportHealthCommand
    ) -> TelemetryExportHealthReport:
        if "platform-admin" not in command.actor.roles:
            raise TelemetryExportHealthAuthorizationError(
                "telemetry.export-health.role-required"
            )
        decision = self._policy.decide(
            command.actor,
            "telemetry-export-health:read",
            {"tenantId": command.actor.tenant_id},
        )
        if not decision.allowed:
            raise TelemetryExportHealthAuthorizationError(decision.reason_code)

        states = tuple(self._reader.read_export_health())
        by_signal = {state.signal: state for state in states}
        if len(states) != len(by_signal) or set(by_signal) != set(_SIGNALS):
            raise TelemetryExportHealthStateError(
                "telemetry.export-health.state-invalid"
            )
        self.validate_states(states)
        enabled = tuple(state for state in states if state.enabled)
        if not enabled:
            status = "disabled"
        elif any(state.status == "degraded" for state in enabled):
            status = "degraded"
        elif any(state.status == "awaiting-first-attempt" for state in enabled):
            status = "awaiting-first-attempt"
        else:
            status = "healthy"

        return TelemetryExportHealthReport(
            {
                "apiVersion": "iip.platform/v1alpha1",
                "kind": "TelemetryExportHealthReport",
                "metadata": {"evaluatedAt": self._clock.now()},
                "spec": {
                    "status": status,
                    "signals": [
                        self._signal_document(by_signal[signal])
                        for signal in _SIGNALS
                    ],
                },
            }
        )

    @staticmethod
    def validate_states(states: tuple[TelemetryExportSignalState, ...]) -> None:
        for state in states:
            counts = (
                state.attempts,
                state.successes,
                state.failures,
                state.consecutive_failures,
            )
            timestamps = (
                state.last_attempt_at,
                state.last_success_at,
                state.last_failure_at,
            )
            if (
                state.signal not in _SIGNALS
                or state.status not in _STATUSES
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not 0 <= value <= _MAX_COUNTER
                    for value in counts
                )
                or state.attempts != state.successes + state.failures
                or state.consecutive_failures > state.failures
                or any(
                    value is not None and not isinstance(value, str)
                    for value in timestamps
                )
                or state.last_failure_code
                not in (
                    None,
                    "telemetry.export.exception",
                    "telemetry.export.failed",
                    "telemetry.export.rejected",
                )
                or (
                    state.attempts == 0
                    and any(value is not None for value in timestamps)
                )
                or (state.attempts > 0 and state.last_attempt_at is None)
                or (state.successes == 0) != (state.last_success_at is None)
                or (state.failures == 0) != (state.last_failure_at is None)
                or (not state.enabled and state.status != "disabled")
                or (not state.enabled and any(counts))
                or (state.status == "awaiting-first-attempt" and state.attempts != 0)
                or (state.status == "healthy" and state.last_success_at is None)
                or (state.status == "degraded" and state.last_failure_at is None)
                or (state.last_failure_code is None) != (state.last_failure_at is None)
            ):
                raise TelemetryExportHealthStateError(
                    "telemetry.export-health.state-invalid"
                )

    @staticmethod
    def _signal_document(state: TelemetryExportSignalState) -> dict[str, object]:
        result: dict[str, object] = {
            "signal": state.signal,
            "enabled": state.enabled,
            "status": state.status,
            "attempts": state.attempts,
            "successes": state.successes,
            "failures": state.failures,
            "consecutiveFailures": state.consecutive_failures,
        }
        optional = (
            ("lastAttemptAt", state.last_attempt_at),
            ("lastSuccessAt", state.last_success_at),
            ("lastFailureAt", state.last_failure_at),
            ("lastFailureCode", state.last_failure_code),
        )
        result.update({name: value for name, value in optional if value is not None})
        return result
