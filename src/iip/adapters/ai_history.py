"""Defensive, provider-neutral helpers for AI history storage adapters."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from iip.application.ports import (
    ActorContext,
    AiHistoryAvailabilityQuery,
    PersistenceError,
)


_ACTOR_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:/@-]{0,255}")
_TENANT_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}")
_ROLE = re.compile(r"[a-z][a-z0-9._:-]{0,63}")
_MAX_AVAILABILITY_INTERVAL = timedelta(days=31)


def validate_ai_history_availability_query(
    actor: ActorContext,
    query: AiHistoryAvailabilityQuery,
) -> tuple[datetime, datetime]:
    """Validate exact-tenant interval bounds again at the storage boundary."""

    try:
        if (
            not isinstance(actor, ActorContext)
            or not isinstance(actor.actor_id, str)
            or _ACTOR_ID.fullmatch(actor.actor_id) is None
            or actor.actor_id == "anonymous"
            or not isinstance(actor.tenant_id, str)
            or _TENANT_ID.fullmatch(actor.tenant_id) is None
            or not isinstance(actor.roles, tuple)
            or len(actor.roles) > 64
            or any(
                not isinstance(role, str) or _ROLE.fullmatch(role) is None
                for role in actor.roles
            )
            or len(set(actor.roles)) != len(actor.roles)
            or not isinstance(query, AiHistoryAvailabilityQuery)
            or not isinstance(query.start, str)
            or not isinstance(query.end, str)
        ):
            raise ValueError
        start, end = parse_ai_history_interval(query.start, query.end)
        if (
            _canonical_timestamp(start) != query.start
            or _canonical_timestamp(end) != query.end
            or end - start > _MAX_AVAILABILITY_INTERVAL
        ):
            raise ValueError
        return start, end
    except (TypeError, ValueError, OverflowError):
        raise PersistenceError("storage.request.invalid") from None


def ai_invocation_correlation_digest(
    tenant_id: str,
    trace_id: str,
    span_id: str,
) -> str:
    """Return the public observation's value-minimized correlation digest."""

    encoded = json.dumps(
        {"tenantId": tenant_id, "traceId": trace_id, "spanId": span_id},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def parse_ai_history_interval(start: str, end: str) -> tuple[datetime, datetime]:
    """Parse a non-empty aware interval already bounded by its owning query."""

    try:
        if not isinstance(start, str) or not isinstance(end, str):
            raise ValueError
        first = _timestamp(start)
        last = _timestamp(end)
        if first >= last:
            raise ValueError
        return first, last
    except (TypeError, ValueError, OverflowError):
        raise PersistenceError("storage.request.invalid") from None


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    return parsed


def _canonical_timestamp(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
        .replace(".000000Z", "Z")
    )
