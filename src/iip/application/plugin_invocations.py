"""Tenant-scoped plugin invocation lifecycle, cancellation, and reconciliation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Mapping

from iip.application.ports import (
    ActorContext,
    Clock,
    PersistenceError,
    PluginInvocationLedger,
    PolicyDecisionPoint,
)


_REQUEST_ID = re.compile(r"pin_[a-f0-9]{32}")
_CANCELLATION_ID = re.compile(r"pcn_[a-f0-9]{32}")
_RECONCILIATION_ID = re.compile(r"prc_[a-f0-9]{32}")
_CANCELLATION_REASONS = {"operator-requested", "session-superseded", "shutdown"}
_RECONCILIATION_REASONS = {"runner-lost", "deadline-elapsed"}
_TERMINAL_STATES = {"succeeded", "failed", "cancelled"}


class PluginInvocationInputError(ValueError):
    """Malformed lifecycle request with a stable public code."""


class PluginInvocationNotFoundError(LookupError):
    """Exact-tenant invocation was not found."""


class PluginInvocationAuthorizationError(PermissionError):
    """Actor lacks policy or role authority for the operation."""


class PluginInvocationConflictError(RuntimeError):
    """Lifecycle transition is not currently safe."""


@dataclass(frozen=True)
class GetPluginInvocationStatusCommand:
    actor: ActorContext
    invocation_id: str


@dataclass(frozen=True)
class CancelPluginInvocationCommand:
    actor: ActorContext
    request: Mapping[str, object]


@dataclass(frozen=True)
class ReconcilePluginInvocationCommand:
    actor: ActorContext
    request: Mapping[str, object]


class PluginInvocationLifecycleService:
    """Expose durable lifecycle facts and fail-closed recovery operations."""

    def __init__(
        self,
        repository: PluginInvocationLedger,
        policy: PolicyDecisionPoint,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._clock = clock

    def get(
        self, command: GetPluginInvocationStatusCommand
    ) -> Mapping[str, object]:
        invocation_id = self._invocation_id(command.invocation_id)
        self._authorize(command.actor, "plugin:get-invocation", invocation_id)
        status = self._repository.get_plugin_invocation_status(
            command.actor, invocation_id
        )
        if status is None:
            raise PluginInvocationNotFoundError("plugin.invocation.not-found")
        return status

    def cancel(
        self, command: CancelPluginInvocationCommand
    ) -> Mapping[str, object]:
        request = self._validate_request(
            command.actor,
            command.request,
            kind="PluginInvocationCancellationRequest",
            identifier_pattern=_CANCELLATION_ID,
            reason_codes=_CANCELLATION_REASONS,
        )
        metadata = request["metadata"]
        spec = request["spec"]
        assert isinstance(metadata, Mapping) and isinstance(spec, Mapping)
        invocation_id = str(spec["invocationId"])
        self._authorize(command.actor, "plugin:cancel-invocation", invocation_id)
        current = self._required(command.actor, invocation_id)
        current_metadata, current_spec = self._parts(current)
        if current_spec["state"] in _TERMINAL_STATES:
            return current
        requested_at = str(metadata["requestedAt"])
        if self._time(requested_at) < self._state_time(current_spec["claimedAt"]):
            raise PluginInvocationInputError("plugin.cancellation.invalid")
        cancellation = {
            "requestedBy": command.actor.actor_id,
            "requestedAt": requested_at,
            "reasonCode": spec["reasonCode"],
        }
        updated = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "PluginInvocationStatus",
            "metadata": {**dict(current_metadata), "updatedAt": requested_at},
            "spec": {
                **dict(current_spec),
                "state": "cancellation-requested",
                "cancellation": cancellation,
            },
        }
        audit = self._audit(
            command.actor,
            invocation_id,
            current_spec["requestDigest"],
            "plugin-invocation-cancellation-requested",
            requested_at,
            str(spec["reasonCode"]),
        )
        return self._repository.request_plugin_invocation_cancellation(
            command.actor, invocation_id, updated, audit
        )

    def reconcile(
        self, command: ReconcilePluginInvocationCommand
    ) -> Mapping[str, object]:
        request = self._validate_request(
            command.actor,
            command.request,
            kind="PluginInvocationReconciliationRequest",
            identifier_pattern=_RECONCILIATION_ID,
            reason_codes=_RECONCILIATION_REASONS,
        )
        metadata = request["metadata"]
        spec = request["spec"]
        assert isinstance(metadata, Mapping) and isinstance(spec, Mapping)
        invocation_id = str(spec["invocationId"])
        if "platform-admin" not in command.actor.roles:
            raise PluginInvocationAuthorizationError("plugin.reconciliation.role-required")
        self._authorize(command.actor, "plugin:reconcile-invocation", invocation_id)
        requested_at = str(metadata["requestedAt"])
        for attempt in range(2):
            current = self._required(command.actor, invocation_id)
            try:
                return self._reconcile_current(
                    command.actor,
                    current,
                    invocation_id,
                    requested_at,
                    str(spec["reasonCode"]),
                )
            except PersistenceError as error:
                if (
                    str(error) != "plugin.request.cancellation-pending"
                    or attempt > 0
                ):
                    raise
        raise PluginInvocationConflictError("plugin.invocation.state-invalid")

    def _reconcile_current(
        self,
        actor: ActorContext,
        current: Mapping[str, object],
        invocation_id: str,
        requested_at: str,
        reason_code: str,
    ) -> Mapping[str, object]:
        current_metadata, current_spec = self._parts(current)
        if current_spec["state"] in _TERMINAL_STATES:
            return current
        if self._time(requested_at) < self._state_time(current_spec["deadline"]):
            raise PluginInvocationConflictError("plugin.reconciliation.deadline-live")
        cancellation = current_spec.get("cancellation")
        terminal_state = "cancelled" if isinstance(cancellation, Mapping) else "failed"
        result = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "PluginInvocationResult",
            "metadata": {
                "id": invocation_id,
                "sessionId": current_metadata["sessionId"],
                "tenantId": actor.tenant_id,
                "pluginId": current_metadata["pluginId"],
                "pluginVersion": current_metadata["pluginVersion"],
                "completedAt": requested_at,
            },
            "spec": {
                "status": terminal_state,
                "error": {"code": "plugin.execution.outcome-unknown"},
                "usage": {"wallTimeMillis": 0, "outputBytes": 0},
            },
        }
        terminal_spec = {
            **dict(current_spec),
            "state": terminal_state,
            "completedAt": requested_at,
            "resultRef": self._result_ref(
                actor.tenant_id,
                str(current_metadata["sessionId"]),
                invocation_id,
            ),
        }
        status = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "PluginInvocationStatus",
            "metadata": {**dict(current_metadata), "updatedAt": requested_at},
            "spec": terminal_spec,
        }
        audit = self._audit(
            actor,
            invocation_id,
            current_spec["requestDigest"],
            "plugin-invocation-reconciled",
            requested_at,
            reason_code,
        )
        return self._repository.reconcile_plugin_invocation(
            actor,
            invocation_id,
            requested_at,
            result,
            status,
            audit,
        )

    def _required(
        self, actor: ActorContext, invocation_id: str
    ) -> Mapping[str, object]:
        status = self._repository.get_plugin_invocation_status(actor, invocation_id)
        if status is None:
            raise PluginInvocationNotFoundError("plugin.invocation.not-found")
        return status

    @staticmethod
    def _parts(
        status: Mapping[str, object],
    ) -> tuple[Mapping[str, object], Mapping[str, object]]:
        metadata = status.get("metadata")
        spec = status.get("spec")
        if not isinstance(metadata, Mapping) or not isinstance(spec, Mapping):
            raise PluginInvocationConflictError("plugin.invocation.state-invalid")
        if spec.get("state") not in {
            "claimed",
            "cancellation-requested",
            *_TERMINAL_STATES,
        }:
            raise PluginInvocationConflictError("plugin.invocation.state-invalid")
        return metadata, spec

    def _validate_request(
        self,
        actor: ActorContext,
        request: Mapping[str, object],
        *,
        kind: str,
        identifier_pattern: re.Pattern[str],
        reason_codes: set[str],
    ) -> Mapping[str, object]:
        if (
            not isinstance(request, Mapping)
            or set(request) != {"apiVersion", "kind", "metadata", "spec"}
            or request.get("apiVersion") != "iip.platform/v1alpha1"
            or request.get("kind") != kind
        ):
            raise PluginInvocationInputError("plugin.lifecycle.request.invalid")
        metadata = request.get("metadata")
        spec = request.get("spec")
        if (
            not isinstance(metadata, Mapping)
            or set(metadata) != {"id", "tenantId", "actorId", "requestedAt"}
            or not isinstance(metadata.get("id"), str)
            or identifier_pattern.fullmatch(str(metadata["id"])) is None
            or metadata.get("tenantId") != actor.tenant_id
            or metadata.get("actorId") != actor.actor_id
            or not isinstance(spec, Mapping)
            or set(spec) != {"invocationId", "reasonCode"}
            or not isinstance(spec.get("invocationId"), str)
            or _REQUEST_ID.fullmatch(str(spec["invocationId"])) is None
            or spec.get("reasonCode") not in reason_codes
        ):
            raise PluginInvocationInputError("plugin.lifecycle.request.invalid")
        requested_at = self._time(metadata.get("requestedAt"))
        now = self._time(self._clock.now())
        if requested_at > now + timedelta(minutes=5):
            raise PluginInvocationInputError("plugin.lifecycle.request.invalid")
        return request

    def _authorize(
        self, actor: ActorContext, action: str, invocation_id: str
    ) -> None:
        decision = self._policy.decide(
            actor,
            action,
            {"tenantId": actor.tenant_id, "invocationId": invocation_id},
        )
        if not decision.allowed:
            raise PluginInvocationAuthorizationError("plugin.lifecycle.policy-denied")

    @staticmethod
    def _invocation_id(value: object) -> str:
        if not isinstance(value, str) or _REQUEST_ID.fullmatch(value) is None:
            raise PluginInvocationNotFoundError("plugin.invocation.not-found")
        return value

    @staticmethod
    def _time(value: object) -> datetime:
        if not isinstance(value, str) or not value.endswith("Z"):
            raise PluginInvocationInputError("plugin.lifecycle.request.invalid")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise PluginInvocationInputError("plugin.lifecycle.request.invalid") from None
        if parsed.tzinfo is None:
            raise PluginInvocationInputError("plugin.lifecycle.request.invalid")
        return parsed.astimezone(timezone.utc)

    @classmethod
    def _state_time(cls, value: object) -> datetime:
        try:
            return cls._time(value)
        except PluginInvocationInputError:
            raise PluginInvocationConflictError(
                "plugin.invocation.state-invalid"
            ) from None

    @staticmethod
    def _result_ref(tenant_id: str, session_id: str, invocation_id: str) -> str:
        return f"plugin-result://{tenant_id}/sessions/{session_id}/invocations/{invocation_id}"

    @staticmethod
    def _audit(
        actor: ActorContext,
        invocation_id: str,
        request_digest: object,
        category: str,
        recorded_at: str,
        reason_code: str,
    ) -> Mapping[str, object]:
        return {
            "apiVersion": "iip.platform/audit/v1alpha1",
            "kind": "PluginInvocationLifecycleAudit",
            "metadata": {
                "tenantId": actor.tenant_id,
                "actorId": actor.actor_id,
                "recordedAt": recorded_at,
            },
            "spec": {
                "category": category,
                "invocationId": invocation_id,
                "requestDigest": request_digest,
                "reasonCode": reason_code,
            },
        }
