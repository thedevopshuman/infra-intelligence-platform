"""Tenant-scoped, bounded read model for durable AI savings findings."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Mapping

from iip.application.evaluate_ai_savings import (
    InvalidAiSavingsInputError,
    validate_ai_savings_finding,
)
from iip.application.ports import (
    ActorContext,
    AiEconomicsLedger,
    AiSavingsFindingLedgerQuery,
    Clock,
    PersistenceError,
    PolicyDecisionPoint,
)


DEFAULT_LIMIT = 20
MAX_LIMIT = 100
MAX_INTERVAL = timedelta(days=31)
_ACTOR_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._:/@-]{0,255}")
_TENANT_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}")
_ROLE = re.compile(r"[a-z][a-z0-9._:-]{0,63}")
_FINDING_ID = re.compile(r"aif_[a-f0-9]{32}")
_CURSOR = re.compile(r"p1\.[A-Za-z0-9_-]+")


class AiSavingsFindingQueryError(ValueError):
    """A public finding query violates a closed scope or cursor invariant."""


class AiSavingsFindingAuthorizationError(PermissionError):
    """Policy denied a finding read for the authenticated tenant."""


class AiSavingsFindingQueryService:
    """Authorize and page immutable findings without evaluating rules inline."""

    def __init__(
        self,
        ledger: AiEconomicsLedger,
        policy: PolicyDecisionPoint,
        clock: Clock,
    ) -> None:
        self._ledger = ledger
        self._policy = policy
        self._clock = clock

    def list(
        self,
        actor: ActorContext,
        *,
        start: str,
        end: str,
        limit: int = DEFAULT_LIMIT,
        cursor: str | None = None,
    ) -> Mapping[str, object]:
        _validate_actor(actor)
        start_value, start_text = _timestamp(start)
        end_value, end_text = _timestamp(end)
        if (
            start != start_text
            or end != end_text
            or end_value <= start_value
            or end_value - start_value > MAX_INTERVAL
            or isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= MAX_LIMIT
        ):
            raise AiSavingsFindingQueryError("request.invalid")

        decision = self._policy.decide(
            actor,
            "ai-economics:read",
            {"tenantId": actor.tenant_id, "start": start_text, "end": end_text},
        )
        if not decision.allowed:
            raise AiSavingsFindingAuthorizationError(decision.reason_code)

        before_evaluated_at: str | None = None
        before_finding_id: str | None = None
        if cursor is not None:
            position = self._decode_cursor(
                cursor,
                actor.tenant_id,
                start_text,
                end_text,
            )
            before_evaluated_at = position[0]
            before_finding_id = position[1]

        query = AiSavingsFindingLedgerQuery(
            start=start_text,
            end=end_text,
            before_evaluated_at=before_evaluated_at,
            before_finding_id=before_finding_id,
            limit=limit + 1,
        )
        records = self._ledger.list_ai_savings_findings(actor, query)
        if not isinstance(records, tuple) or len(records) > limit + 1:
            raise PersistenceError("storage.state.invalid")
        items = self._validated_items(actor, query, records)
        visible = items[:limit]
        has_more = len(items) > limit
        page: dict[str, object] = {"limit": limit, "hasMore": has_more}
        if has_more and visible:
            metadata = visible[-1]["metadata"]
            assert isinstance(metadata, Mapping)
            page["nextCursor"] = self._encode_cursor(
                actor.tenant_id,
                start_text,
                end_text,
                str(metadata["evaluatedAt"]),
                str(metadata["id"]),
            )
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "AiSavingsFindingPage",
            "metadata": {
                "tenantId": actor.tenant_id,
                "generatedAt": _timestamp(self._clock.now())[1],
            },
            "spec": {
                "scope": {"start": start_text, "end": end_text},
                "items": visible,
                "page": page,
            },
        }

    @staticmethod
    def _validated_items(
        actor: ActorContext,
        query: AiSavingsFindingLedgerQuery,
        records: tuple[Mapping[str, object], ...],
    ) -> list[Mapping[str, object]]:
        start_value = _timestamp(query.start)[0]
        end_value = _timestamp(query.end)[0]
        before = (
            (_timestamp(query.before_evaluated_at)[0], query.before_finding_id)
            if query.before_evaluated_at is not None
            and query.before_finding_id is not None
            else None
        )
        previous = before
        seen: set[str] = set()
        validated: list[Mapping[str, object]] = []
        try:
            for document in records:
                item = validate_ai_savings_finding(document)
                metadata = item["metadata"]
                assert isinstance(metadata, Mapping)
                finding_id = metadata["id"]
                evaluated_at = metadata["evaluatedAt"]
                if not isinstance(finding_id, str) or not isinstance(evaluated_at, str):
                    raise ValueError
                evaluated_value, canonical = _timestamp(evaluated_at)
                key = (evaluated_value, finding_id)
                if (
                    metadata["tenantId"] != actor.tenant_id
                    or canonical != evaluated_at
                    or _FINDING_ID.fullmatch(finding_id) is None
                    or finding_id in seen
                    or not start_value <= evaluated_value < end_value
                    or previous is not None
                    and key >= previous
                ):
                    raise ValueError
                seen.add(finding_id)
                previous = key
                validated.append(item)
        except (
            AssertionError,
            KeyError,
            TypeError,
            ValueError,
            OverflowError,
            InvalidAiSavingsInputError,
        ):
            raise PersistenceError("storage.state.invalid") from None
        return validated

    @staticmethod
    def _scope(tenant_id: str, start: str, end: str) -> str:
        return hashlib.sha256(
            f"ai-savings-findings\x1f{tenant_id}\x1f{start}\x1f{end}".encode()
        ).hexdigest()

    @classmethod
    def _encode_cursor(
        cls,
        tenant_id: str,
        start: str,
        end: str,
        evaluated_at: str,
        finding_id: str,
    ) -> str:
        payload = {
            "kind": "ai-savings-findings",
            "position": {"evaluatedAt": evaluated_at, "findingId": finding_id},
            "scope": cls._scope(tenant_id, start, end),
            "version": 1,
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).decode("ascii")
        return "p1." + encoded.rstrip("=")

    @classmethod
    def _decode_cursor(
        cls,
        cursor: str,
        tenant_id: str,
        start: str,
        end: str,
    ) -> tuple[str, str]:
        if (
            not isinstance(cursor, str)
            or len(cursor) > 2048
            or _CURSOR.fullmatch(cursor) is None
        ):
            raise AiSavingsFindingQueryError("pagination.cursor_invalid")
        try:
            token = cursor[3:]
            raw = base64.b64decode(
                token + "=" * (-len(token) % 4), altchars=b"-_", validate=True
            )
            payload = json.loads(raw.decode("utf-8"))
            position = payload.get("position") if isinstance(payload, dict) else None
            evaluated_at = position.get("evaluatedAt") if isinstance(position, dict) else None
            finding_id = position.get("findingId") if isinstance(position, dict) else None
            canonical = _timestamp(evaluated_at)[1]
        except (
            TypeError,
            ValueError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            OverflowError,
        ):
            raise AiSavingsFindingQueryError("pagination.cursor_invalid") from None
        if (
            not isinstance(payload, dict)
            or set(payload) != {"kind", "position", "scope", "version"}
            or payload.get("kind") != "ai-savings-findings"
            or payload.get("version") != 1
            or not isinstance(payload.get("scope"), str)
            or not hmac.compare_digest(
                payload["scope"], cls._scope(tenant_id, start, end)
            )
            or not isinstance(position, dict)
            or set(position) != {"evaluatedAt", "findingId"}
            or not isinstance(evaluated_at, str)
            or canonical != evaluated_at
            or not isinstance(finding_id, str)
            or _FINDING_ID.fullmatch(finding_id) is None
            or cls._encode_cursor(
                tenant_id, start, end, evaluated_at, finding_id
            ) != cursor
        ):
            raise AiSavingsFindingQueryError("pagination.cursor_invalid")
        return evaluated_at, finding_id


def validate_ai_savings_finding_ledger_query(
    actor: ActorContext,
    query: AiSavingsFindingLedgerQuery,
) -> tuple[datetime, datetime, tuple[datetime, str] | None]:
    """Reject unsafe storage bounds before an adapter performs I/O."""

    try:
        _validate_actor(actor)
        start, start_text = _timestamp(query.start)
        end, end_text = _timestamp(query.end)
        cursor_values = (query.before_evaluated_at, query.before_finding_id)
        if (cursor_values[0] is None) != (cursor_values[1] is None):
            raise ValueError
        before = None
        if cursor_values[0] is not None and cursor_values[1] is not None:
            before_time, before_text = _timestamp(cursor_values[0])
            if (
                before_text != cursor_values[0]
                or _FINDING_ID.fullmatch(cursor_values[1]) is None
                or not start <= before_time < end
            ):
                raise ValueError
            before = (before_time, cursor_values[1])
        if (
            not isinstance(query, AiSavingsFindingLedgerQuery)
            or start_text != query.start
            or end_text != query.end
            or end <= start
            or end - start > MAX_INTERVAL
            or isinstance(query.limit, bool)
            or not isinstance(query.limit, int)
            or not 1 <= query.limit <= MAX_LIMIT + 1
        ):
            raise ValueError
    except (AttributeError, TypeError, ValueError, OverflowError):
        raise AiSavingsFindingQueryError("request.invalid") from None
    return start, end, before


def _validate_actor(actor: ActorContext) -> None:
    if (
        not isinstance(actor, ActorContext)
        or not isinstance(actor.actor_id, str)
        or actor.actor_id == "anonymous"
        or _ACTOR_ID.fullmatch(actor.actor_id) is None
        or not isinstance(actor.tenant_id, str)
        or _TENANT_ID.fullmatch(actor.tenant_id) is None
        or not isinstance(actor.roles, tuple)
        or len(actor.roles) > 64
        or len(set(actor.roles)) != len(actor.roles)
        or any(not isinstance(role, str) or _ROLE.fullmatch(role) is None for role in actor.roles)
    ):
        raise AiSavingsFindingQueryError("request.invalid")


def _timestamp(value: object) -> tuple[datetime, str]:
    if not isinstance(value, str):
        raise ValueError
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError
    normalized = parsed.astimezone(timezone.utc)
    return normalized, normalized.isoformat().replace("+00:00", "Z")


__all__ = [
    "AiSavingsFindingAuthorizationError",
    "AiSavingsFindingQueryError",
    "AiSavingsFindingQueryService",
    "validate_ai_savings_finding_ledger_query",
]
