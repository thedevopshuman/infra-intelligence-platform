"""Privileged transport-neutral rolling event-delivery objective."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from iip.application.ports import (
    ActorContext,
    Clock,
    EventDeliverySloState,
    EventOutbox,
    PolicyDecisionPoint,
)


_MAX_COUNTER = 9_007_199_254_740_991


class EventDeliverySloAuthorizationError(PermissionError):
    """The actor cannot inspect the tenant publication objective."""


class EventDeliverySloStateError(RuntimeError):
    """Storage returned inconsistent or unsafe objective facts."""


@dataclass(frozen=True)
class EventDeliverySloObjectives:
    """Deployment-owned rolling window and minimum publication objective."""

    window_seconds: int = 3600
    maximum_delivery_latency_seconds: int = 60
    minimum_attainment_basis_points: int = 9900
    minimum_eligible_events: int = 20

    def __post_init__(self) -> None:
        values = (
            self.window_seconds,
            self.maximum_delivery_latency_seconds,
            self.minimum_attainment_basis_points,
            self.minimum_eligible_events,
        )
        if (
            any(isinstance(value, bool) or not isinstance(value, int) for value in values)
            or not 300 <= self.window_seconds <= 2_592_000
            or not 1 <= self.maximum_delivery_latency_seconds <= 86_400
            or self.maximum_delivery_latency_seconds >= self.window_seconds
            or not 1 <= self.minimum_attainment_basis_points <= 10_000
            or not 1 <= self.minimum_eligible_events <= 1_000_000
        ):
            raise ValueError("event.delivery-slo.configuration.invalid")


@dataclass(frozen=True)
class GetEventDeliverySloCommand:
    actor: ActorContext


@dataclass(frozen=True)
class EventDeliverySloReport:
    document: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return dict(self.document)


class EventDeliverySloService:
    """Authorize, validate, and calculate one exact-tenant SLO report."""

    def __init__(
        self,
        outbox: EventOutbox,
        policy: PolicyDecisionPoint,
        clock: Clock,
        objectives: EventDeliverySloObjectives | None = None,
    ) -> None:
        self._outbox = outbox
        self._policy = policy
        self._clock = clock
        self._objectives = objectives or EventDeliverySloObjectives()

    def get(self, command: GetEventDeliverySloCommand) -> EventDeliverySloReport:
        if "platform-admin" not in command.actor.roles:
            raise EventDeliverySloAuthorizationError(
                "event.delivery-slo.role-required"
            )
        policy_input = {
            "tenantId": command.actor.tenant_id,
            "windowSeconds": self._objectives.window_seconds,
            "maximumDeliveryLatencySeconds": (
                self._objectives.maximum_delivery_latency_seconds
            ),
            "minimumAttainmentBasisPoints": (
                self._objectives.minimum_attainment_basis_points
            ),
            "minimumEligibleEvents": self._objectives.minimum_eligible_events,
        }
        decision = self._policy.decide(
            command.actor,
            "event-delivery-slo:read",
            policy_input,
        )
        if not decision.allowed:
            raise EventDeliverySloAuthorizationError(decision.reason_code)

        evaluated_at = self._parse_time(self._clock.now())
        window_start = evaluated_at - timedelta(
            seconds=self._objectives.window_seconds
        )
        maturity_cutoff = evaluated_at - timedelta(
            seconds=self._objectives.maximum_delivery_latency_seconds
        )
        formatted_start = self._format_time(window_start)
        formatted_end = self._format_time(evaluated_at)
        formatted_cutoff = self._format_time(maturity_cutoff)
        try:
            state = self._outbox.get_event_delivery_slo_state(
                command.actor.tenant_id,
                window_start=formatted_start,
                window_end=formatted_end,
                maturity_cutoff=formatted_cutoff,
                latency_objective_seconds=(
                    self._objectives.maximum_delivery_latency_seconds
                ),
            )
            self._validate_state(
                command,
                state,
                formatted_start,
                formatted_end,
                formatted_cutoff,
            )
        except EventDeliverySloStateError:
            raise
        except Exception:
            raise EventDeliverySloStateError(
                "event.delivery-slo.state-invalid"
            ) from None

        attainment = (
            None
            if state.eligible_events == 0
            else state.within_objective_events * 10_000 // state.eligible_events
        )
        if state.eligible_events == 0:
            status = "no-data"
        elif state.eligible_events < self._objectives.minimum_eligible_events:
            status = "insufficient-data"
        elif attainment is not None and attainment >= (
            self._objectives.minimum_attainment_basis_points
        ):
            status = "meeting"
        else:
            status = "breached"

        return EventDeliverySloReport(
            {
                "apiVersion": "iip.platform/v1alpha1",
                "kind": "EventDeliverySloReport",
                "metadata": {
                    "tenantId": command.actor.tenant_id,
                    "evaluatedAt": formatted_end,
                },
                "spec": {
                    "status": status,
                    "window": {
                        "durationSeconds": self._objectives.window_seconds,
                        "start": formatted_start,
                        "end": formatted_end,
                        "maturityCutoff": formatted_cutoff,
                    },
                    "objective": {
                        "maximumDeliveryLatencySeconds": (
                            self._objectives.maximum_delivery_latency_seconds
                        ),
                        "minimumAttainmentBasisPoints": (
                            self._objectives.minimum_attainment_basis_points
                        ),
                        "minimumEligibleEvents": (
                            self._objectives.minimum_eligible_events
                        ),
                    },
                    "measurement": {
                        "createdEvents": state.created_events,
                        "immatureEvents": state.immature_events,
                        "eligibleEvents": state.eligible_events,
                        "withinObjectiveEvents": state.within_objective_events,
                        "lateDeliveredEvents": state.late_delivered_events,
                        "undeliveredEvents": state.undelivered_events,
                        "quarantinedEvents": state.quarantined_events,
                        "attainmentBasisPoints": attainment,
                    },
                },
            }
        )

    @staticmethod
    def _validate_state(
        command: GetEventDeliverySloCommand,
        state: EventDeliverySloState,
        window_start: str,
        window_end: str,
        maturity_cutoff: str,
    ) -> None:
        counts = (
            state.created_events,
            state.immature_events,
            state.eligible_events,
            state.within_objective_events,
            state.late_delivered_events,
            state.undelivered_events,
            state.quarantined_events,
        )
        if (
            state.tenant_id != command.actor.tenant_id
            or state.window_start != window_start
            or state.window_end != window_end
            or state.maturity_cutoff != maturity_cutoff
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= _MAX_COUNTER
                for value in counts
            )
            or state.created_events
            != state.immature_events + state.eligible_events
            or state.eligible_events
            != (
                state.within_objective_events
                + state.late_delivered_events
                + state.undelivered_events
            )
            or state.quarantined_events > state.undelivered_events
        ):
            raise EventDeliverySloStateError(
                "event.delivery-slo.state-invalid"
            )

    @staticmethod
    def _parse_time(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            raise EventDeliverySloStateError(
                "event.delivery-slo.state-invalid"
            ) from None
        if parsed.tzinfo is None:
            raise EventDeliverySloStateError(
                "event.delivery-slo.state-invalid"
            )
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
