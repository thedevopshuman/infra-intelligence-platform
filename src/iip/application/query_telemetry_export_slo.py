"""Privileged deployment-wide sampled telemetry export objective."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from iip.application.ports import (
    ActorContext,
    Clock,
    PolicyDecisionPoint,
    TelemetryExportHealthRepository,
    TelemetryExportSloSignalState,
    TelemetryExportSloState,
)


_SIGNALS = ("metrics", "traces")
_MAX_COUNTER = 9_007_199_254_740_991


class TelemetryExportSloAuthorizationError(PermissionError):
    """The actor cannot inspect the deployment telemetry objective."""


class TelemetryExportSloStateError(RuntimeError):
    """Storage returned inconsistent sampled exporter outcomes."""


@dataclass(frozen=True)
class TelemetryExportSloObjectives:
    """Deployment-owned sampled export-attempt objective."""

    window_seconds: int = 3_600
    minimum_attainment_basis_points: int = 9_900
    minimum_eligible_attempts: int = 20

    def __post_init__(self) -> None:
        values = (
            self.window_seconds,
            self.minimum_attainment_basis_points,
            self.minimum_eligible_attempts,
        )
        if (
            any(isinstance(value, bool) or not isinstance(value, int) for value in values)
            or not 300 <= self.window_seconds <= 2_592_000
            or not 1 <= self.minimum_attainment_basis_points <= 10_000
            or not 1 <= self.minimum_eligible_attempts <= 1_000_000
        ):
            raise ValueError("telemetry.export-slo.configuration.invalid")


@dataclass(frozen=True)
class GetTelemetryExportSloCommand:
    actor: ActorContext


@dataclass(frozen=True)
class TelemetryExportSloReport:
    document: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return dict(self.document)


class TelemetryExportSloService:
    """Authorize, validate, and calculate sampled deployment export reliability."""

    def __init__(
        self,
        repository: TelemetryExportHealthRepository,
        policy: PolicyDecisionPoint,
        clock: Clock,
        objectives: TelemetryExportSloObjectives | None = None,
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._clock = clock
        self._objectives = objectives or TelemetryExportSloObjectives()

    def get(self, command: GetTelemetryExportSloCommand) -> TelemetryExportSloReport:
        if "platform-admin" not in command.actor.roles:
            raise TelemetryExportSloAuthorizationError(
                "telemetry.export-slo.role-required"
            )
        policy_input = {
            "tenantId": command.actor.tenant_id,
            "windowSeconds": self._objectives.window_seconds,
            "minimumAttainmentBasisPoints": (
                self._objectives.minimum_attainment_basis_points
            ),
            "minimumEligibleAttempts": self._objectives.minimum_eligible_attempts,
        }
        decision = self._policy.decide(
            command.actor,
            "telemetry-export-slo:read",
            policy_input,
        )
        if not decision.allowed:
            raise TelemetryExportSloAuthorizationError(decision.reason_code)

        evaluated_at = self._parse_time(self._clock.now())
        window_start = evaluated_at - timedelta(
            seconds=self._objectives.window_seconds
        )
        formatted_start = self._format_time(window_start)
        formatted_end = self._format_time(evaluated_at)
        try:
            state = self._repository.get_telemetry_export_slo_state(
                window_start=formatted_start,
                window_end=formatted_end,
            )
            self._validate_state(state, formatted_start, formatted_end)
        except TelemetryExportSloStateError:
            raise
        except Exception:
            raise TelemetryExportSloStateError(
                "telemetry.export-slo.state-invalid"
            ) from None

        by_signal = {item.signal: item for item in state.signals}
        signals = [self._signal_document(by_signal[name]) for name in _SIGNALS]
        statuses = tuple(item["status"] for item in signals)
        if "breached" in statuses:
            status = "breached"
        elif "insufficient-data" in statuses:
            status = "insufficient-data"
        elif "no-data" in statuses:
            status = "no-data"
        elif "meeting" in statuses:
            status = "meeting"
        else:
            status = "disabled"

        return TelemetryExportSloReport(
            {
                "apiVersion": "iip.platform/v1alpha1",
                "kind": "TelemetryExportSloReport",
                "metadata": {"evaluatedAt": formatted_end},
                "spec": {
                    "status": status,
                    "window": {
                        "durationSeconds": self._objectives.window_seconds,
                        "start": formatted_start,
                        "end": formatted_end,
                    },
                    "objective": {
                        "minimumAttainmentBasisPoints": (
                            self._objectives.minimum_attainment_basis_points
                        ),
                        "minimumEligibleAttempts": (
                            self._objectives.minimum_eligible_attempts
                        ),
                    },
                    "observation": {
                        "observedInstances": state.observed_instances,
                        "observedSamples": state.observed_samples,
                    },
                    "signals": signals,
                },
            }
        )

    def _signal_document(
        self, state: TelemetryExportSloSignalState
    ) -> dict[str, object]:
        attainment = (
            None
            if state.eligible_attempts == 0
            else state.successful_attempts * 10_000 // state.eligible_attempts
        )
        if state.enabled_observations == 0:
            status = "disabled"
        elif state.eligible_attempts == 0:
            status = "no-data"
        elif state.eligible_attempts < self._objectives.minimum_eligible_attempts:
            status = "insufficient-data"
        elif attainment is not None and attainment >= (
            self._objectives.minimum_attainment_basis_points
        ):
            status = "meeting"
        else:
            status = "breached"
        return {
            "signal": state.signal,
            "status": status,
            "enabledObservations": state.enabled_observations,
            "eligibleAttempts": state.eligible_attempts,
            "successfulAttempts": state.successful_attempts,
            "failedAttempts": state.failed_attempts,
            "attainmentBasisPoints": attainment,
        }

    @staticmethod
    def _validate_state(
        state: TelemetryExportSloState,
        window_start: str,
        window_end: str,
    ) -> None:
        by_signal = {item.signal: item for item in state.signals}
        counts = (state.observed_instances, state.observed_samples)
        if (
            state.window_start != window_start
            or state.window_end != window_end
            or len(state.signals) != len(by_signal)
            or set(by_signal) != set(_SIGNALS)
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= _MAX_COUNTER
                for value in counts
            )
            or state.observed_instances > state.observed_samples
        ):
            raise TelemetryExportSloStateError(
                "telemetry.export-slo.state-invalid"
            )
        for signal in state.signals:
            values = (
                signal.enabled_observations,
                signal.eligible_attempts,
                signal.successful_attempts,
                signal.failed_attempts,
            )
            if (
                any(
                    isinstance(value, bool)
                    or not isinstance(value, int)
                    or not 0 <= value <= _MAX_COUNTER
                    for value in values
                )
                or signal.enabled_observations > state.observed_samples
                or signal.eligible_attempts
                != signal.successful_attempts + signal.failed_attempts
            ):
                raise TelemetryExportSloStateError(
                    "telemetry.export-slo.state-invalid"
                )

    @staticmethod
    def _parse_time(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            raise TelemetryExportSloStateError(
                "telemetry.export-slo.state-invalid"
            ) from None
        if parsed.tzinfo is None:
            raise TelemetryExportSloStateError(
                "telemetry.export-slo.state-invalid"
            )
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
