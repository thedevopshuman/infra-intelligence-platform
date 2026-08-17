"""Policy-gated, tenant-scoped Evidence artifact retention."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from iip.application.ports import (
    ActorContext,
    Clock,
    EvidenceRetentionState,
    EvidenceRetentionStore,
    PolicyDecisionPoint,
)


_MAX_COUNTER = 9_007_199_254_740_991
_TENANT_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


class EvidenceRetentionAuthorizationError(PermissionError):
    """The actor cannot inspect or execute tenant retention."""


class EvidenceRetentionStateError(RuntimeError):
    """Storage returned inconsistent or unsafe retention state."""


@dataclass(frozen=True)
class EvidenceRetentionPolicy:
    enabled: bool = False
    ephemeral_seconds: int = 86_400
    standard_seconds: int = 2_592_000
    extended_seconds: int = 31_536_000
    batch_size: int = 100

    def __post_init__(self) -> None:
        values = (
            self.ephemeral_seconds,
            self.standard_seconds,
            self.extended_seconds,
            self.batch_size,
        )
        if (
            not isinstance(self.enabled, bool)
            or any(isinstance(value, bool) or not isinstance(value, int) for value in values)
            or not 3_600 <= self.ephemeral_seconds <= 2_592_000
            or not 86_400 <= self.standard_seconds <= 31_536_000
            or not 86_400 <= self.extended_seconds <= 315_360_000
            or not (
                self.ephemeral_seconds
                <= self.standard_seconds
                <= self.extended_seconds
            )
            or not 1 <= self.batch_size <= 1000
        ):
            raise ValueError("evidence.retention.configuration.invalid")

    @property
    def digest(self) -> str:
        payload = {
            "batchSize": self.batch_size,
            "enabled": self.enabled,
            "ephemeralSeconds": self.ephemeral_seconds,
            "extendedSeconds": self.extended_seconds,
            "standardSeconds": self.standard_seconds,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return "sha256:" + hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class GetEvidenceRetentionCommand:
    actor: ActorContext


@dataclass(frozen=True)
class EvidenceRetentionReport:
    document: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return dict(self.document)


class EvidenceRetentionService:
    """Observe retention or expire bytes under explicit deployment policy."""

    def __init__(
        self,
        store: EvidenceRetentionStore,
        policy_decision: PolicyDecisionPoint,
        clock: Clock,
        policy: EvidenceRetentionPolicy | None = None,
    ) -> None:
        self._store = store
        self._policy_decision = policy_decision
        self._clock = clock
        self._policy = policy or EvidenceRetentionPolicy()

    @property
    def enabled(self) -> bool:
        return self._policy.enabled

    def get(self, command: GetEvidenceRetentionCommand) -> EvidenceRetentionReport:
        if "platform-admin" not in command.actor.roles:
            raise EvidenceRetentionAuthorizationError(
                "evidence.retention.role-required"
            )
        return self._evaluate(command.actor, expire=False)

    def expire(self, tenant_id: str) -> EvidenceRetentionReport:
        if not isinstance(tenant_id, str) or _TENANT_ID.fullmatch(tenant_id) is None:
            raise EvidenceRetentionStateError(
                "evidence.retention.tenant-invalid"
            )
        if not self._policy.enabled:
            raise EvidenceRetentionAuthorizationError(
                "evidence.retention.disabled"
            )
        actor = ActorContext(
            "iip-evidence-retention",
            tenant_id,
            ("system-retention",),
        )
        return self._evaluate(actor, expire=True)

    def _evaluate(
        self,
        actor: ActorContext,
        *,
        expire: bool,
    ) -> EvidenceRetentionReport:
        action = "evidence-retention:expire" if expire else "evidence-retention:read"
        policy_input = {
            "tenantId": actor.tenant_id,
            "policyDigest": self._policy.digest,
            "batchSize": self._policy.batch_size,
            "ephemeralSeconds": self._policy.ephemeral_seconds,
            "standardSeconds": self._policy.standard_seconds,
            "extendedSeconds": self._policy.extended_seconds,
        }
        decision = self._policy_decision.decide(actor, action, policy_input)
        if not decision.allowed:
            raise EvidenceRetentionAuthorizationError(decision.reason_code)
        evaluated_at = self._format_time(self._parse_time(self._clock.now()))
        try:
            state = self._store.evaluate_evidence_retention(
                actor.tenant_id,
                evaluated_at,
                ephemeral_seconds=self._policy.ephemeral_seconds,
                standard_seconds=self._policy.standard_seconds,
                extended_seconds=self._policy.extended_seconds,
                limit=self._policy.batch_size,
                expire=expire,
                policy_digest=self._policy.digest,
            )
            self._validate_state(state, actor.tenant_id, evaluated_at, expire)
        except EvidenceRetentionStateError:
            raise
        except Exception:
            raise EvidenceRetentionStateError(
                "evidence.retention.state-invalid"
            ) from None

        if not self._policy.enabled:
            status = "disabled"
        elif state.remaining_eligible_artifacts:
            status = "cleanup-required"
        else:
            status = "current"
        document: dict[str, object] = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "EvidenceRetentionReport",
            "metadata": {
                "tenantId": actor.tenant_id,
                "evaluatedAt": evaluated_at,
            },
            "spec": {
                "status": status,
                "mode": "expire" if expire else "observe",
                "policy": {
                    "enabled": self._policy.enabled,
                    "ephemeralSeconds": self._policy.ephemeral_seconds,
                    "standardSeconds": self._policy.standard_seconds,
                    "extendedSeconds": self._policy.extended_seconds,
                    "batchSize": self._policy.batch_size,
                    "digest": self._policy.digest,
                },
                "artifacts": {
                    "storedBefore": state.stored_artifacts,
                    "eligible": state.eligible_artifacts,
                    "expired": state.expired_artifacts,
                    "remainingEligible": state.remaining_eligible_artifacts,
                    "legalHold": state.legal_hold_artifacts,
                },
            },
        }
        if state.audit_ref is not None:
            spec = document["spec"]
            assert isinstance(spec, dict)
            spec["auditRef"] = state.audit_ref
        return EvidenceRetentionReport(document)

    @staticmethod
    def _validate_state(
        state: EvidenceRetentionState,
        tenant_id: str,
        evaluated_at: str,
        expire: bool,
    ) -> None:
        counts = (
            state.stored_artifacts,
            state.eligible_artifacts,
            state.expired_artifacts,
            state.remaining_eligible_artifacts,
            state.legal_hold_artifacts,
        )
        if (
            state.tenant_id != tenant_id
            or state.evaluated_at != evaluated_at
            or any(
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= _MAX_COUNTER
                for value in counts
            )
            or state.eligible_artifacts
            != state.expired_artifacts + state.remaining_eligible_artifacts
            or state.eligible_artifacts + state.legal_hold_artifacts
            > state.stored_artifacts
            or (state.expired_artifacts > 0 and not expire)
            or (state.expired_artifacts > 0) != (state.audit_ref is not None)
        ):
            raise EvidenceRetentionStateError(
                "evidence.retention.state-invalid"
            )

    @staticmethod
    def _parse_time(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError):
            raise EvidenceRetentionStateError(
                "evidence.retention.state-invalid"
            ) from None
        if parsed.tzinfo is None:
            raise EvidenceRetentionStateError(
                "evidence.retention.state-invalid"
            )
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
