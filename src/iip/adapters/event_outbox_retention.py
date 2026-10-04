"""Shared storage-boundary checks for published-outbox retention."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

from iip.application.ports import PersistenceError


def retention_cutoff(
    tenant_id: str,
    evaluated_at: str,
    *,
    published_seconds: int,
    limit: int,
    expire: bool,
    policy_digest: str,
) -> datetime:
    """Reject unsafe direct port calls independently of application validation."""
    if (
        not isinstance(tenant_id, str)
        or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", tenant_id) is None
        or type(published_seconds) is not int
        or not 2_592_000 <= published_seconds <= 315_360_000
        or type(limit) is not int or not 1 <= limit <= 1000
        or type(expire) is not bool
        or not isinstance(policy_digest, str)
        or re.fullmatch(r"sha256:[a-f0-9]{64}", policy_digest) is None
    ):
        raise PersistenceError("storage.input-invalid")
    try:
        if not isinstance(evaluated_at, str):
            raise ValueError
        evaluated = datetime.fromisoformat(evaluated_at.replace("Z", "+00:00"))
        if evaluated.tzinfo is None or evaluated.utcoffset() is None:
            raise ValueError
        return evaluated.astimezone(timezone.utc) - timedelta(seconds=published_seconds)
    except (ValueError, OverflowError):
        raise PersistenceError("storage.input-invalid") from None


def retention_audit(
    tenant_id: str,
    evaluated_at: str,
    policy_digest: str,
    expired: int,
    remaining: int,
) -> dict[str, object]:
    """No individual event, outbox identity, destination, or payload is retained."""
    return {
        "apiVersion": "iip.internal/v1alpha1",
        "kind": "EventOutboxRetentionAudit",
        "metadata": {"tenantId": tenant_id, "recordedAt": evaluated_at},
        "spec": {
            "policyDigest": policy_digest,
            "expiredRows": expired,
            "remainingEligibleRows": remaining,
        },
    }
