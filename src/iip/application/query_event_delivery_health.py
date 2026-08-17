"""Privileged tenant-scoped transactional-outbox health query."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone

from iip.application.ports import (
    ActorContext,
    Clock,
    EventDeliveryState,
    EventOutbox,
    PolicyDecisionPoint,
)


_MAX_COUNTER = 9_007_199_254_740_991
_EVENT_TYPE = re.compile(r"^io\.iip\.[a-z0-9.-]+\.v[1-9][0-9]*$")


class EventDeliveryHealthAuthorizationError(PermissionError):
    """The actor cannot inspect tenant event-delivery state."""


class EventDeliveryHealthInputError(ValueError):
    """The requested bounded view is malformed."""


class EventDeliveryHealthStateError(RuntimeError):
    """Storage returned inconsistent or unsafe delivery state."""


@dataclass(frozen=True)
class GetEventDeliveryHealthCommand:
    actor: ActorContext
    limit: int = 50


@dataclass(frozen=True)
class EventDeliveryHealthReport:
    document: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return dict(self.document)


class EventDeliveryHealthService:
    """Authorize and render value-minimized outbox health for one tenant."""

    def __init__(
        self,
        outbox: EventOutbox,
        policy: PolicyDecisionPoint,
        clock: Clock,
    ) -> None:
        self._outbox = outbox
        self._policy = policy
        self._clock = clock

    def get(self, command: GetEventDeliveryHealthCommand) -> EventDeliveryHealthReport:
        if (
            isinstance(command.limit, bool)
            or not isinstance(command.limit, int)
            or not 1 <= command.limit <= 50
        ):
            raise EventDeliveryHealthInputError("request.invalid")
        if "platform-admin" not in command.actor.roles:
            raise EventDeliveryHealthAuthorizationError(
                "event.delivery-health.role-required"
            )
        decision = self._policy.decide(
            command.actor,
            "event-delivery-health:read",
            {"tenantId": command.actor.tenant_id},
        )
        if not decision.allowed:
            raise EventDeliveryHealthAuthorizationError(decision.reason_code)

        evaluated_at = self._parse_time(self._clock.now())
        try:
            state = self._outbox.get_event_delivery_state(
                command.actor.tenant_id,
                quarantine_limit=command.limit,
            )
            self._validate_state(command, state, evaluated_at)
        except EventDeliveryHealthStateError:
            raise
        except Exception:
            raise EventDeliveryHealthStateError(
                "event.delivery-health.state-invalid"
            ) from None

        if state.quarantined_events:
            status = "degraded"
        elif state.pending_events:
            status = "backlogged"
        else:
            status = "healthy"
        delivery: dict[str, object] = {
            "pendingEvents": state.pending_events,
            "inFlightEvents": state.in_flight_events,
            "retryingEvents": state.retrying_events,
            "quarantinedEvents": state.quarantined_events,
        }
        if state.oldest_pending_event_recorded_at is not None:
            oldest = self._parse_time(state.oldest_pending_event_recorded_at)
            delivery["oldestPendingEventAgeSeconds"] = max(
                0,
                int((evaluated_at - oldest).total_seconds()),
            )
        items = [
            {
                "outboxId": record.message_id,
                "eventId": record.event_id,
                "eventSource": record.event_source,
                "eventType": record.event_type,
                "subject": record.subject,
                "attempts": record.attempts,
                "quarantinedAt": record.quarantined_at,
                "lastErrorCode": record.last_error_code,
            }
            for record in state.quarantined
        ]
        return EventDeliveryHealthReport(
            {
                "apiVersion": "iip.platform/v1alpha1",
                "kind": "EventDeliveryHealthReport",
                "metadata": {
                    "tenantId": command.actor.tenant_id,
                    "evaluatedAt": self._format_time(evaluated_at),
                },
                "spec": {
                    "status": status,
                    "delivery": delivery,
                    "quarantine": {
                        "limit": command.limit,
                        "hasMore": state.quarantined_events > len(items),
                        "items": items,
                    },
                },
            }
        )

    @classmethod
    def _validate_state(
        cls,
        command: GetEventDeliveryHealthCommand,
        state: EventDeliveryState,
        evaluated_at: datetime,
    ) -> None:
        counts = (
            state.pending_events,
            state.in_flight_events,
            state.retrying_events,
            state.quarantined_events,
        )
        if (
            state.tenant_id != command.actor.tenant_id
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= _MAX_COUNTER
                for value in counts
            )
            or state.in_flight_events > state.pending_events
            or state.retrying_events > state.pending_events
            or (state.pending_events == 0)
            != (state.oldest_pending_event_recorded_at is None)
            or len(state.quarantined) > command.limit
            or len(state.quarantined)
            != min(state.quarantined_events, command.limit)
        ):
            raise EventDeliveryHealthStateError(
                "event.delivery-health.state-invalid"
            )
        if state.oldest_pending_event_recorded_at is not None:
            oldest = cls._parse_time(state.oldest_pending_event_recorded_at)
            if oldest > evaluated_at:
                raise EventDeliveryHealthStateError(
                    "event.delivery-health.state-invalid"
                )
        seen: set[int] = set()
        previous: tuple[datetime, int] | None = None
        for record in state.quarantined:
            quarantined_at = cls._parse_time(record.quarantined_at)
            if (
                isinstance(record.message_id, bool)
                or not isinstance(record.message_id, int)
                or not 1 <= record.message_id <= _MAX_COUNTER
                or record.message_id in seen
                or record.tenant_id != command.actor.tenant_id
                or not isinstance(record.event_id, str)
                or not 1 <= len(record.event_id) <= 128
                or not isinstance(record.event_source, str)
                or not 1 <= len(record.event_source) <= 512
                or not isinstance(record.event_type, str)
                or not 1 <= len(record.event_type) <= 256
                or _EVENT_TYPE.fullmatch(record.event_type) is None
                or not isinstance(record.subject, str)
                or not 1 <= len(record.subject) <= 512
                or isinstance(record.attempts, bool)
                or not isinstance(record.attempts, int)
                or not 1 <= record.attempts <= 1000
                or quarantined_at > evaluated_at
                or record.last_error_code != "event.publisher.unavailable"
            ):
                raise EventDeliveryHealthStateError(
                    "event.delivery-health.state-invalid"
                )
            order = (quarantined_at, record.message_id)
            if previous is not None and order > previous:
                raise EventDeliveryHealthStateError(
                    "event.delivery-health.state-invalid"
                )
            seen.add(record.message_id)
            previous = order

    @staticmethod
    def _parse_time(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            raise EventDeliveryHealthStateError(
                "event.delivery-health.state-invalid"
            ) from None
        if parsed.tzinfo is None:
            raise EventDeliveryHealthStateError(
                "event.delivery-health.state-invalid"
            )
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
