"""Safe reference action executors."""

from __future__ import annotations

from typing import Mapping

from iip.application.ports import (
    ActionExecutionOutcome,
    ActionExecutor,
    ActorContext,
    EventOutbox,
)


class KubernetesRestartDryRunExecutor:
    """Validate the narrow Kubernetes restart shape without performing mutation."""

    def execute(
        self,
        actor: ActorContext,
        proposal: Mapping[str, object],
        *,
        approval_id: str,
    ) -> ActionExecutionOutcome:
        del actor, approval_id
        spec = proposal.get("spec")
        if not isinstance(spec, Mapping) or spec.get("dryRun") is not True:
            raise RuntimeError("action.executor.live-disabled")
        parameters = spec.get("parameters")
        if not isinstance(parameters, Mapping):
            raise RuntimeError("action.executor.parameters-invalid")
        namespace = parameters.get("namespace")
        kind = parameters.get("workloadKind")
        name = parameters.get("workloadName")
        if (
            not isinstance(namespace, str)
            or not isinstance(kind, str)
            or not isinstance(name, str)
            or kind not in ("deployment", "statefulset", "daemonset")
        ):
            raise RuntimeError("action.executor.parameters-invalid")
        return ActionExecutionOutcome(
            outcome="dry-run",
            provider="kubernetes",
            operation_ref=f"kubernetes://local/namespaces/{namespace}/{kind}s/{name}",
            verification_status="not-run",
            verification_summary=(
                "The approved restart request passed reference dry-run validation; "
                "no Kubernetes mutation was performed."
            ),
        )


class EventDeliveryReplayExecutor:
    """Apply one approved quarantine-generation replay through the outbox port."""

    ACTION_TYPE = "event-delivery.requeue"

    def __init__(self, outbox: EventOutbox) -> None:
        self._outbox = outbox

    def execute(
        self,
        actor: ActorContext,
        proposal: Mapping[str, object],
        *,
        approval_id: str,
    ) -> ActionExecutionOutcome:
        del approval_id
        metadata = proposal.get("metadata")
        spec = proposal.get("spec")
        if (
            not isinstance(metadata, Mapping)
            or metadata.get("tenantId") != actor.tenant_id
            or not isinstance(spec, Mapping)
            or spec.get("actionType") != self.ACTION_TYPE
            or not isinstance(spec.get("dryRun"), bool)
        ):
            raise RuntimeError("action.executor.parameters-invalid")
        parameters = spec.get("parameters")
        if not isinstance(parameters, Mapping) or set(parameters) != {
            "outboxId",
            "eventId",
            "quarantinedAt",
            "attempts",
        }:
            raise RuntimeError("action.executor.parameters-invalid")
        outbox_id = parameters.get("outboxId")
        event_id = parameters.get("eventId")
        quarantined_at = parameters.get("quarantinedAt")
        attempts = parameters.get("attempts")
        if (
            isinstance(outbox_id, bool)
            or not isinstance(outbox_id, int)
            or outbox_id < 1
            or not isinstance(event_id, str)
            or not isinstance(quarantined_at, str)
            or isinstance(attempts, bool)
            or not isinstance(attempts, int)
            or attempts < 1
        ):
            raise RuntimeError("action.executor.parameters-invalid")
        operation_ref = f"event-delivery://{actor.tenant_id}/outbox/{outbox_id}"
        current = self._outbox.get_quarantined_outbox(actor.tenant_id, outbox_id)
        matches = (
            current is not None
            and current.event_id == event_id
            and current.quarantined_at == quarantined_at
            and current.attempts == attempts
            and current.subject == spec.get("targetResourceUid")
        )
        if spec["dryRun"]:
            if not matches:
                return self._precondition_failed(operation_ref)
            return ActionExecutionOutcome(
                outcome="dry-run",
                provider="iip-event-outbox",
                operation_ref=operation_ref,
                verification_status="not-run",
                verification_summary=(
                    "The approved quarantine generation still matches; no event "
                    "was made eligible for delivery."
                ),
            )
        if not matches or not self._outbox.requeue_quarantined_outbox(
            actor.tenant_id,
            outbox_id,
            expected_event_id=event_id,
            expected_quarantined_at=quarantined_at,
            expected_attempts=attempts,
        ):
            return self._precondition_failed(operation_ref)
        return ActionExecutionOutcome(
            outcome="succeeded",
            provider="iip-event-outbox",
            operation_ref=operation_ref,
            verification_status="passed",
            verification_summary=(
                "The exact approved quarantine generation was requeued with a "
                "fresh bounded delivery-attempt budget."
            ),
        )

    @staticmethod
    def _precondition_failed(operation_ref: str) -> ActionExecutionOutcome:
        return ActionExecutionOutcome(
            outcome="failed",
            provider="iip-event-outbox",
            operation_ref=operation_ref,
            verification_status="failed",
            verification_summary=(
                "The quarantined event no longer matches the immutable approved "
                "generation; no delivery state changed."
            ),
            error_code="event.delivery.replay.precondition-failed",
        )


class ActionExecutorRouter:
    """Route only explicitly supported action types to narrow executors."""

    def __init__(
        self,
        kubernetes: ActionExecutor,
        event_delivery: ActionExecutor,
    ) -> None:
        self._kubernetes = kubernetes
        self._event_delivery = event_delivery

    def execute(
        self,
        actor: ActorContext,
        proposal: Mapping[str, object],
        *,
        approval_id: str,
    ) -> ActionExecutionOutcome:
        spec = proposal.get("spec")
        action_type = spec.get("actionType") if isinstance(spec, Mapping) else None
        if action_type == "kubernetes.restart-workload":
            return self._kubernetes.execute(
                actor,
                proposal,
                approval_id=approval_id,
            )
        if action_type == EventDeliveryReplayExecutor.ACTION_TYPE:
            return self._event_delivery.execute(
                actor,
                proposal,
                approval_id=approval_id,
            )
        raise RuntimeError("action.executor.parameters-invalid")
