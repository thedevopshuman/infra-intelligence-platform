"""Opt-in, policy-gated retention of delivered event transport state only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re

from iip.application.ports import (
    ActorContext, Clock, EventOutboxRetentionState,
    EventOutboxRetentionStore, PolicyDecisionPoint,
)


MIN_PUBLISHED_SECONDS = 2_592_000  # Every supported event-delivery SLO window.
MAX_PUBLISHED_SECONDS = 315_360_000
_MAX_COUNTER = 9_007_199_254_740_991
_TENANT = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}")
_ACTOR = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:/@-]{0,255}")


class EventOutboxRetentionAuthorizationError(PermissionError):
    """The caller cannot observe or execute this tenant's retention policy."""


class EventOutboxRetentionStateError(RuntimeError):
    """No safe aggregate retention observation is available."""


@dataclass(frozen=True)
class EventOutboxRetentionPolicy:
    enabled: bool = False
    published_seconds: int = MIN_PUBLISHED_SECONDS
    batch_size: int = 100

    def __post_init__(self) -> None:
        if (
            not isinstance(self.enabled, bool)
            or type(self.published_seconds) is not int
            or not MIN_PUBLISHED_SECONDS <= self.published_seconds <= MAX_PUBLISHED_SECONDS
            or type(self.batch_size) is not int
            or not 1 <= self.batch_size <= 1000
        ):
            raise ValueError("event.outbox-retention.configuration.invalid")

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "publishedSeconds": self.published_seconds,
            "batchSize": self.batch_size,
        }

    @property
    def digest(self) -> str:
        encoded = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True)
class GetEventOutboxRetentionCommand:
    actor: ActorContext


@dataclass(frozen=True)
class EventOutboxRetentionReport:
    document: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return dict(self.document)


class EventOutboxRetentionService:
    def __init__(
        self,
        store: EventOutboxRetentionStore,
        policy_decision: PolicyDecisionPoint,
        clock: Clock,
        policy: EventOutboxRetentionPolicy | None = None,
    ) -> None:
        self._store = store
        self._decision = policy_decision
        self._clock = clock
        self._policy = policy or EventOutboxRetentionPolicy()

    @property
    def enabled(self) -> bool:
        return self._policy.enabled

    def get(self, command: GetEventOutboxRetentionCommand) -> EventOutboxRetentionReport:
        actor = command.actor
        if (
            not isinstance(actor, ActorContext)
            or not isinstance(actor.actor_id, str)
            or actor.actor_id == "anonymous"
            or _ACTOR.fullmatch(actor.actor_id) is None
            or not isinstance(actor.tenant_id, str)
            or _TENANT.fullmatch(actor.tenant_id) is None
            or not isinstance(actor.roles, tuple)
            or "platform-admin" not in actor.roles
        ):
            raise EventOutboxRetentionAuthorizationError("event.outbox-retention.role-required")
        return self._evaluate(actor, expire=False)

    def expire(self, tenant_id: str) -> EventOutboxRetentionReport:
        if not self.enabled:
            raise EventOutboxRetentionAuthorizationError("event.outbox-retention.disabled")
        if not isinstance(tenant_id, str) or _TENANT.fullmatch(tenant_id) is None:
            raise EventOutboxRetentionStateError("event.outbox-retention.tenant-invalid")
        return self._evaluate(
            ActorContext("iip-event-outbox-retention", tenant_id, ("system-retention",)),
            expire=True,
        )

    def _evaluate(self, actor: ActorContext, *, expire: bool) -> EventOutboxRetentionReport:
        action = "event-outbox-retention:expire" if expire else "event-outbox-retention:read"
        try:
            decision = self._decision.decide(actor, action, {
                "tenantId": actor.tenant_id,
                "policyDigest": self._policy.digest,
                "publishedSeconds": self._policy.published_seconds,
                "batchSize": self._policy.batch_size,
            })
            if decision.allowed is not True:
                raise EventOutboxRetentionAuthorizationError("policy.denied")
        except EventOutboxRetentionAuthorizationError:
            raise
        except Exception:
            raise EventOutboxRetentionAuthorizationError("policy.denied") from None
        try:
            timestamp = datetime.fromisoformat(self._clock.now().replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError
            evaluated_at = timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            state = self._store.evaluate_event_outbox_retention(
                actor.tenant_id, evaluated_at,
                published_seconds=self._policy.published_seconds,
                limit=self._policy.batch_size, expire=expire,
                policy_digest=self._policy.digest,
            )
            self._validate_state(state, actor.tenant_id, evaluated_at, expire)
        except Exception:
            raise EventOutboxRetentionStateError("event.outbox-retention.state-invalid") from None
        status = (
            "disabled" if not self.enabled else
            "cleanup-required" if state.remaining_eligible_rows else "current"
        )
        spec: dict[str, object] = {
            "status": status, "mode": "expire" if expire else "observe",
            "policy": {**self._policy.to_dict(), "digest": self._policy.digest},
            "rows": {
                "storedBefore": state.stored_rows,
                "published": state.published_rows,
                "eligible": state.eligible_rows,
                "expired": state.expired_rows,
                "remainingEligible": state.remaining_eligible_rows,
                "protected": state.protected_rows,
            },
        }
        if state.audit_ref is not None:
            spec["auditRef"] = state.audit_ref
        return EventOutboxRetentionReport({
            "apiVersion": "iip.platform/v1alpha1", "kind": "EventOutboxRetentionReport",
            "metadata": {"tenantId": actor.tenant_id, "evaluatedAt": evaluated_at},
            "spec": spec,
        })

    def _validate_state(
        self, state: EventOutboxRetentionState, tenant: str, evaluated_at: str, expire: bool,
    ) -> None:
        counts = (
            state.stored_rows, state.published_rows, state.eligible_rows,
            state.expired_rows, state.remaining_eligible_rows, state.protected_rows,
        )
        if (
            state.tenant_id != tenant or state.evaluated_at != evaluated_at
            or any(type(value) is not int or not 0 <= value <= _MAX_COUNTER for value in counts)
            or state.eligible_rows + state.protected_rows != state.stored_rows
            or not state.eligible_rows <= state.published_rows <= state.stored_rows
            or state.expired_rows + state.remaining_eligible_rows != state.eligible_rows
            or state.expired_rows > self._policy.batch_size
            or (state.expired_rows != 0 and not expire)
            or (state.expired_rows > 0) != (state.audit_ref is not None)
            or (state.audit_ref is not None and (
                not isinstance(state.audit_ref, str)
                or re.fullmatch(r"audit://" + re.escape(tenant) + r"/[a-zA-Z0-9._/-]{1,255}", state.audit_ref) is None
            ))
        ):
            raise EventOutboxRetentionStateError("event.outbox-retention.state-invalid")
