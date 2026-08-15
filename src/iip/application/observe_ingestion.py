"""Tenant-scoped ingestion freshness and source-lag evaluation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from iip.application.ports import (
    ActorContext,
    Clock,
    PolicyDecisionPoint,
    SourceIngestionState,
    SourceIngestionTelemetryRepository,
)


_SOURCE_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
_MAX_SECONDS = 315_360_000.0


class IngestionTelemetryInputError(ValueError):
    """A freshness query or configured objective is invalid."""


class IngestionTelemetryAuthorizationError(PermissionError):
    """Policy denied access to tenant-scoped ingestion telemetry."""


class IngestionSourceNotFoundError(LookupError):
    """No committed source checkpoint exists in the actor's tenant."""


class IngestionTelemetryStateError(RuntimeError):
    """An adapter returned internally inconsistent telemetry facts."""


@dataclass(frozen=True)
class IngestionFreshnessObjectives:
    """Trusted SLI thresholds selected at runtime composition."""

    maximum_checkpoint_age_seconds: float = 300.0
    maximum_observation_age_seconds: float = 300.0
    maximum_ingestion_delay_seconds: float = 60.0
    maximum_pending_event_age_seconds: float = 60.0
    maximum_clock_skew_seconds: float = 5.0

    def validate(self) -> None:
        values = (
            self.maximum_checkpoint_age_seconds,
            self.maximum_observation_age_seconds,
            self.maximum_ingestion_delay_seconds,
            self.maximum_pending_event_age_seconds,
            self.maximum_clock_skew_seconds,
        )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 < float(value) <= _MAX_SECONDS
            for value in values
        ):
            raise IngestionTelemetryInputError("ingestion.objective.invalid")

    def to_dict(self) -> dict[str, float]:
        return {
            "maximumCheckpointAgeSeconds": float(
                self.maximum_checkpoint_age_seconds
            ),
            "maximumObservationAgeSeconds": float(
                self.maximum_observation_age_seconds
            ),
            "maximumIngestionDelaySeconds": float(
                self.maximum_ingestion_delay_seconds
            ),
            "maximumPendingEventAgeSeconds": float(
                self.maximum_pending_event_age_seconds
            ),
            "maximumClockSkewSeconds": float(self.maximum_clock_skew_seconds),
        }


@dataclass(frozen=True)
class GetIngestionFreshnessCommand:
    actor: ActorContext
    source_id: str


@dataclass(frozen=True)
class IngestionFreshnessReport:
    """Provider-neutral result rendered by protocol surfaces."""

    document: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return dict(self.document)


class IngestionFreshnessService:
    """Evaluate one committed source against trusted point-in-time objectives."""

    def __init__(
        self,
        repository: SourceIngestionTelemetryRepository,
        policy: PolicyDecisionPoint,
        clock: Clock,
        objectives: IngestionFreshnessObjectives | None = None,
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._clock = clock
        self._objectives = objectives or IngestionFreshnessObjectives()
        self._objectives.validate()

    def get(self, command: GetIngestionFreshnessCommand) -> IngestionFreshnessReport:
        if (
            not isinstance(command.source_id, str)
            or _SOURCE_ID.fullmatch(command.source_id) is None
        ):
            raise IngestionTelemetryInputError("ingestion.source_id.invalid")
        decision = self._policy.decide(
            command.actor,
            "ingestion-telemetry:read",
            {
                "tenantId": command.actor.tenant_id,
                "sourceId": command.source_id,
            },
        )
        if not decision.allowed:
            raise IngestionTelemetryAuthorizationError(decision.reason_code)

        state = self._repository.get_source_ingestion_state(
            command.actor.tenant_id, command.source_id
        )
        if state is None:
            raise IngestionSourceNotFoundError("ingestion.source_not_found")
        self._validate_state(command, state)
        evaluated_at = self._parse_time(self._clock.now())
        violations: set[str] = set()

        checkpoint_at = self._parse_time(state.checkpoint_committed_at)
        checkpoint_age = self._age(evaluated_at, checkpoint_at, violations)
        if checkpoint_age > self._objectives.maximum_checkpoint_age_seconds:
            violations.add("checkpoint-age-exceeded")

        latest_observation: Optional[dict[str, object]] = None
        if state.latest_observed_at is not None:
            assert state.latest_recorded_at is not None
            observed_at = self._parse_time(state.latest_observed_at)
            recorded_at = self._parse_time(state.latest_recorded_at)
            observation_age = self._age(evaluated_at, observed_at, violations)
            self._age(evaluated_at, recorded_at, violations)
            ingestion_delay = self._duration(recorded_at, observed_at, violations)
            if observation_age > self._objectives.maximum_observation_age_seconds:
                violations.add("observation-age-exceeded")
            if ingestion_delay > self._objectives.maximum_ingestion_delay_seconds:
                violations.add("ingestion-delay-exceeded")
            latest_observation = {
                "observedAt": state.latest_observed_at,
                "recordedAt": state.latest_recorded_at,
                "ageSeconds": self._rounded(observation_age),
                "ingestionDelaySeconds": self._rounded(ingestion_delay),
            }

        delivery: dict[str, object] = {"pendingEvents": state.pending_event_count}
        if state.oldest_pending_event_recorded_at is not None:
            oldest_pending_at = self._parse_time(
                state.oldest_pending_event_recorded_at
            )
            pending_age = self._age(evaluated_at, oldest_pending_at, violations)
            delivery["oldestPendingEventAgeSeconds"] = self._rounded(pending_age)
            if pending_age > self._objectives.maximum_pending_event_age_seconds:
                violations.add("pending-event-age-exceeded")

        sorted_violations = sorted(violations)
        spec: dict[str, object] = {
            "status": "breached" if sorted_violations else "within-objective",
            "streamId": state.stream_id,
            "checkpoint": {
                "sequence": state.checkpoint_sequence,
                "committedAt": state.checkpoint_committed_at,
                "ageSeconds": self._rounded(checkpoint_age),
            },
            "acceptedObservationCount": state.accepted_observation_count,
            "delivery": delivery,
            "objectives": self._objectives.to_dict(),
            "violations": sorted_violations,
        }
        if latest_observation is not None:
            spec["latestObservation"] = latest_observation
        return IngestionFreshnessReport(
            {
                "apiVersion": "iip.platform/v1alpha1",
                "kind": "IngestionFreshnessReport",
                "metadata": {
                    "tenantId": state.tenant_id,
                    "sourceId": state.source_id,
                    "evaluatedAt": self._rfc3339(evaluated_at),
                },
                "spec": spec,
            }
        )

    @staticmethod
    def _validate_state(
        command: GetIngestionFreshnessCommand, state: SourceIngestionState
    ) -> None:
        observation_pair = (
            state.latest_observed_at is not None,
            state.latest_recorded_at is not None,
        )
        pending_pair = (
            state.pending_event_count > 0,
            state.oldest_pending_event_recorded_at is not None,
        )
        if (
            state.tenant_id != command.actor.tenant_id
            or state.source_id != command.source_id
            or re.fullmatch(r"obs_[a-f0-9]{32}", state.stream_id) is None
            or state.checkpoint_sequence < 0
            or state.accepted_observation_count < 0
            or state.pending_event_count < 0
            or observation_pair[0] != observation_pair[1]
            or pending_pair[0] != pending_pair[1]
            or (state.accepted_observation_count == 0) != (not observation_pair[0])
        ):
            raise IngestionTelemetryStateError("ingestion.telemetry.invalid_state")

    def _age(
        self,
        evaluated_at: datetime,
        timestamp: datetime,
        violations: set[str],
    ) -> float:
        return self._duration(evaluated_at, timestamp, violations)

    def _duration(
        self,
        later: datetime,
        earlier: datetime,
        violations: set[str],
    ) -> float:
        seconds = (later - earlier).total_seconds()
        if seconds < -self._objectives.maximum_clock_skew_seconds:
            violations.add("clock-skew-detected")
        return max(0.0, seconds)

    @staticmethod
    def _parse_time(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            raise IngestionTelemetryStateError(
                "ingestion.telemetry.invalid_state"
            ) from None
        if parsed.tzinfo is None:
            raise IngestionTelemetryStateError("ingestion.telemetry.invalid_state")
        return parsed

    @staticmethod
    def _rfc3339(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _rounded(value: float) -> float:
        return round(value, 6)
