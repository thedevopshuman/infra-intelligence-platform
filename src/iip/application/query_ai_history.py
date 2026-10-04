"""Read-only availability of locally committed AI usage history."""

from __future__ import annotations

from datetime import timedelta
import re
from typing import Mapping

from iip.application.ports import (
    ActorContext,
    AiHistoryAvailabilityQuery,
    AiHistoryAvailabilityState,
    AiHistoryAvailabilityStore,
    Clock,
    PersistenceError,
    PolicyDecisionPoint,
)
from iip.application.query_ai_allocations import canonical_ai_economics_timestamp


class AiHistoryQueryError(ValueError):
    """The closed history interval or authenticated context is invalid."""


class AiHistoryAuthorizationError(PermissionError):
    """The caller is not authorized to inspect this tenant's history."""


class AiHistoryAvailabilityService:
    """Observe availability, without choosing or executing a retention policy."""

    def __init__(
        self,
        store: AiHistoryAvailabilityStore,
        policy: PolicyDecisionPoint,
        clock: Clock,
    ) -> None:
        self._store = store
        self._policy = policy
        self._clock = clock

    def get(self, actor: ActorContext, *, start: str, end: str) -> Mapping[str, object]:
        try:
            if (
                not isinstance(actor, ActorContext)
                or not isinstance(actor.actor_id, str)
                or actor.actor_id == "anonymous"
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/@-]{0,255}", actor.actor_id) is None
                or not isinstance(actor.tenant_id, str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", actor.tenant_id) is None
                or not isinstance(actor.roles, tuple)
                or len(actor.roles) > 64
                or any(
                    not isinstance(role, str)
                    or re.fullmatch(r"[a-z][a-z0-9._:-]{0,63}", role) is None
                    for role in actor.roles
                )
                or len(set(actor.roles)) != len(actor.roles)
            ):
                raise ValueError
            first, start_text = canonical_ai_economics_timestamp(start)
            last, end_text = canonical_ai_economics_timestamp(end)
            if (
                start_text != start or end_text != end
                or not timedelta(0) < last - first <= timedelta(days=31)
            ):
                raise ValueError
        except (AttributeError, TypeError, ValueError, OverflowError):
            raise AiHistoryQueryError("request.invalid") from None
        decision = self._policy.decide(
            actor,
            "ai-economics:read",
            {"tenantId": actor.tenant_id, "start": start, "end": end},
        )
        if decision.allowed is not True:
            raise AiHistoryAuthorizationError("policy.denied")
        state = self._store.get_ai_history_availability(
            actor, AiHistoryAvailabilityQuery(start, end)
        )
        if not isinstance(state, AiHistoryAvailabilityState) or any(
            type(count) is not int or not 0 <= count <= 9_007_199_254_740_991
            for count in (state.retained_usage_records, state.retired_usage_records)
        ):
            raise PersistenceError("storage.state.invalid")
        try:
            generated_at = canonical_ai_economics_timestamp(self._clock.now())[1]
        except (TypeError, ValueError, OverflowError):
            raise PersistenceError("storage.state.invalid") from None
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "AiHistoryAvailabilityReport",
            "metadata": {"tenantId": actor.tenant_id, "generatedAt": generated_at},
            "spec": {
                "scope": {"start": start, "end": end},
                "status": "history-retired" if state.retired_usage_records else "available",
                "coverage": {
                    "retainedUsageRecords": state.retained_usage_records,
                    "retiredUsageRecords": state.retired_usage_records,
                },
            },
        }
