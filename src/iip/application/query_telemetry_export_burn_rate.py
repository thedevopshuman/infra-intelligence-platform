"""Privileged multi-window deployment telemetry export burn-rate objective."""

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
# Shared conservatism order for combining short/long windows within one signal
# and for combining signals into the deployment status: a signal or window
# only outranks "sustainable" once it carries either genuine uncertainty
# (no-data, insufficient-data) or a confirmed trend (elevated, critical).
# "disabled" is the lowest rank, so it only wins when every candidate is
# disabled, matching the ADR 0073 "disabled only when both signals are
# disabled" rule applied uniformly here.
_SEVERITY = {
    "disabled": 0,
    "sustainable": 1,
    "no-data": 2,
    "insufficient-data": 3,
    "elevated": 4,
    "critical": 5,
}


class TelemetryExportBurnRateAuthorizationError(PermissionError):
    """The actor cannot inspect the deployment telemetry burn-rate objective."""


class TelemetryExportBurnRateStateError(RuntimeError):
    """Storage returned inconsistent sampled exporter outcomes."""


@dataclass(frozen=True)
class TelemetryExportBurnRateObjectives:
    """Deployment-owned multi-window sampled export-attempt burn-rate objective."""

    short_window_seconds: int = 3_600
    long_window_seconds: int = 21_600
    minimum_attainment_basis_points: int = 9_900
    minimum_eligible_attempts: int = 20
    critical_burn_rate_hundredths: int = 600

    def __post_init__(self) -> None:
        values = (
            self.short_window_seconds,
            self.long_window_seconds,
            self.minimum_attainment_basis_points,
            self.minimum_eligible_attempts,
            self.critical_burn_rate_hundredths,
        )
        if (
            any(isinstance(value, bool) or not isinstance(value, int) for value in values)
            or not 300 <= self.short_window_seconds <= 2_592_000
            or not 300 <= self.long_window_seconds <= 2_592_000
            or self.short_window_seconds >= self.long_window_seconds
            # A 10000 (100%) objective leaves a zero-width error budget, which
            # would make the burn-rate ratio below undefined.
            or not 1 <= self.minimum_attainment_basis_points <= 9_999
            or not 1 <= self.minimum_eligible_attempts <= 1_000_000
            or not 101 <= self.critical_burn_rate_hundredths <= 1_000_000
        ):
            raise ValueError("telemetry.export-burn-rate.configuration.invalid")


@dataclass(frozen=True)
class GetTelemetryExportBurnRateCommand:
    actor: ActorContext


@dataclass(frozen=True)
class TelemetryExportBurnRateReport:
    document: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return dict(self.document)


class TelemetryExportBurnRateService:
    """Authorize, validate, and calculate multi-window sampled burn rate."""

    def __init__(
        self,
        repository: TelemetryExportHealthRepository,
        policy: PolicyDecisionPoint,
        clock: Clock,
        objectives: TelemetryExportBurnRateObjectives | None = None,
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._clock = clock
        self._objectives = objectives or TelemetryExportBurnRateObjectives()

    def get(
        self, command: GetTelemetryExportBurnRateCommand
    ) -> TelemetryExportBurnRateReport:
        if "platform-admin" not in command.actor.roles:
            raise TelemetryExportBurnRateAuthorizationError(
                "telemetry.export-burn-rate.role-required"
            )
        policy_input = {
            "tenantId": command.actor.tenant_id,
            "shortWindowSeconds": self._objectives.short_window_seconds,
            "longWindowSeconds": self._objectives.long_window_seconds,
            "minimumAttainmentBasisPoints": (
                self._objectives.minimum_attainment_basis_points
            ),
            "minimumEligibleAttempts": self._objectives.minimum_eligible_attempts,
            "criticalBurnRateHundredths": (
                self._objectives.critical_burn_rate_hundredths
            ),
        }
        decision = self._policy.decide(
            command.actor,
            "telemetry-export-burn-rate:read",
            policy_input,
        )
        if not decision.allowed:
            raise TelemetryExportBurnRateAuthorizationError(decision.reason_code)

        evaluated_at = self._parse_time(self._clock.now())
        short_window = self._window(
            evaluated_at, self._objectives.short_window_seconds
        )
        long_window = self._window(
            evaluated_at, self._objectives.long_window_seconds
        )
        short_state = self._fetch_state(*short_window)
        long_state = self._fetch_state(*long_window)

        by_signal_short = {item.signal: item for item in short_state.signals}
        by_signal_long = {item.signal: item for item in long_state.signals}
        signals = []
        for name in _SIGNALS:
            short_observation = self._window_document(by_signal_short[name])
            long_observation = self._window_document(by_signal_long[name])
            signals.append(
                {
                    "signal": name,
                    "status": self._combine(
                        short_observation["status"], long_observation["status"]
                    ),
                    "short": short_observation,
                    "long": long_observation,
                }
            )

        status = "disabled"
        best = -1
        for item in signals:
            rank = _SEVERITY[item["status"]]
            if rank > best:
                best = rank
                status = item["status"]

        return TelemetryExportBurnRateReport(
            {
                "apiVersion": "iip.platform/v1alpha1",
                "kind": "TelemetryExportBurnRateReport",
                "metadata": {"evaluatedAt": self._format_time(evaluated_at)},
                "spec": {
                    "status": status,
                    "objective": {
                        "minimumAttainmentBasisPoints": (
                            self._objectives.minimum_attainment_basis_points
                        ),
                        "minimumEligibleAttempts": (
                            self._objectives.minimum_eligible_attempts
                        ),
                        "criticalBurnRateHundredths": (
                            self._objectives.critical_burn_rate_hundredths
                        ),
                    },
                    "windows": {
                        "short": {
                            "durationSeconds": self._objectives.short_window_seconds,
                            "start": self._format_time(short_window[0]),
                            "end": self._format_time(short_window[1]),
                        },
                        "long": {
                            "durationSeconds": self._objectives.long_window_seconds,
                            "start": self._format_time(long_window[0]),
                            "end": self._format_time(long_window[1]),
                        },
                    },
                    "signals": signals,
                },
            }
        )

    def _fetch_state(
        self, window_start: datetime, window_end: datetime
    ) -> TelemetryExportSloState:
        formatted_start = self._format_time(window_start)
        formatted_end = self._format_time(window_end)
        try:
            state = self._repository.get_telemetry_export_slo_state(
                window_start=formatted_start,
                window_end=formatted_end,
            )
            self._validate_state(state, formatted_start, formatted_end)
        except TelemetryExportBurnRateStateError:
            raise
        except Exception:
            raise TelemetryExportBurnRateStateError(
                "telemetry.export-burn-rate.state-invalid"
            ) from None
        return state

    def _window(
        self, evaluated_at: datetime, duration_seconds: int
    ) -> tuple[datetime, datetime]:
        return evaluated_at - timedelta(seconds=duration_seconds), evaluated_at

    def _window_document(
        self, state: TelemetryExportSloSignalState
    ) -> dict[str, object]:
        error_budget_basis_points = 10_000 - (
            self._objectives.minimum_attainment_basis_points
        )
        attainment = (
            None
            if state.eligible_attempts == 0
            else state.successful_attempts * 10_000 // state.eligible_attempts
        )
        burn_rate = (
            None
            if state.eligible_attempts == 0
            else (
                (state.failed_attempts * 10_000 // state.eligible_attempts)
                * 100
                // error_budget_basis_points
            )
        )
        if state.enabled_observations == 0:
            status = "disabled"
        elif state.eligible_attempts == 0:
            status = "no-data"
        elif state.eligible_attempts < self._objectives.minimum_eligible_attempts:
            status = "insufficient-data"
        elif burn_rate is not None and burn_rate >= (
            self._objectives.critical_burn_rate_hundredths
        ):
            status = "critical"
        elif burn_rate is not None and burn_rate > 100:
            status = "elevated"
        else:
            status = "sustainable"
        return {
            "status": status,
            "enabledObservations": state.enabled_observations,
            "eligibleAttempts": state.eligible_attempts,
            "successfulAttempts": state.successful_attempts,
            "failedAttempts": state.failed_attempts,
            "attainmentBasisPoints": attainment,
            "burnRateHundredths": burn_rate,
        }

    @staticmethod
    def _combine(short_status: str, long_status: str) -> str:
        effective_short = "elevated" if short_status == "critical" else short_status
        effective_long = "elevated" if long_status == "critical" else long_status
        if short_status == "critical" and long_status == "critical":
            return "critical"
        return (
            effective_short
            if _SEVERITY[effective_short] >= _SEVERITY[effective_long]
            else effective_long
        )

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
            raise TelemetryExportBurnRateStateError(
                "telemetry.export-burn-rate.state-invalid"
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
                raise TelemetryExportBurnRateStateError(
                    "telemetry.export-burn-rate.state-invalid"
                )

    @staticmethod
    def _parse_time(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            raise TelemetryExportBurnRateStateError(
                "telemetry.export-burn-rate.state-invalid"
            ) from None
        if parsed.tzinfo is None:
            raise TelemetryExportBurnRateStateError(
                "telemetry.export-burn-rate.state-invalid"
            )
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
