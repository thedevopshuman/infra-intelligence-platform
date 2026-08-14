"""Governed proposal, separation-of-duties approval, and execution workflow."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from iip.application.investigate import canonical_digest
from iip.application.ports import (
    ActionExecutor,
    ActionRepository,
    ActorContext,
    AuditSink,
    Clock,
    PolicyDecisionPoint,
    ResourceRepository,
)


class ActionWorkflowError(RuntimeError):
    """Governed workflow failure carrying one stable external code."""


@dataclass(frozen=True)
class ProposeActionCommand:
    actor: ActorContext
    investigation_id: str
    action_type: str
    target_resource_uid: str
    parameters: Mapping[str, object]
    idempotency_key: str
    expires_at: str
    dry_run: bool = True


@dataclass(frozen=True)
class DecideActionCommand:
    actor: ActorContext
    proposal_id: str
    decision: str
    rationale: str


@dataclass(frozen=True)
class ExecuteActionCommand:
    actor: ActorContext
    proposal_id: str


class GovernedActionService:
    """Enforce policy, approval, idempotency, expiry, execution, and audit."""

    SUPPORTED_ACTION = "kubernetes.restart-workload"

    def __init__(
        self,
        resources: ResourceRepository,
        policy: PolicyDecisionPoint,
        repository: ActionRepository,
        executor: ActionExecutor,
        audit: AuditSink,
        clock: Clock,
    ) -> None:
        self._resources = resources
        self._policy = policy
        self._repository = repository
        self._executor = executor
        self._audit = audit
        self._clock = clock

    def propose(self, command: ProposeActionCommand) -> Mapping[str, object]:
        if command.action_type != self.SUPPORTED_ACTION:
            raise ActionWorkflowError("action.type.unsupported")
        if not 8 <= len(command.idempotency_key) <= 128:
            raise ActionWorkflowError("action.idempotency-key.invalid")
        target = self._resources.get(
            command.actor.tenant_id, command.target_resource_uid
        )
        if target is None:
            raise ActionWorkflowError("action.target.unavailable")
        if self._parse_time(command.expires_at) <= self._parse_time(self._clock.now()):
            raise ActionWorkflowError("action.expiry.invalid")

        existing = self._repository.get_proposal_by_key(
            command.actor, command.idempotency_key
        )
        intended = {
            "investigationId": command.investigation_id,
            "actionType": command.action_type,
            "targetResourceUid": command.target_resource_uid,
            "parameters": dict(command.parameters),
            "dryRun": command.dry_run,
        }
        if existing is not None:
            spec = existing["spec"]
            assert isinstance(spec, Mapping)
            if any(spec.get(key) != value for key, value in intended.items()):
                raise ActionWorkflowError("action.idempotency-key.conflict")
            return existing

        decision = self._policy.decide(
            command.actor,
            "action:propose",
            {
                "tenantId": command.actor.tenant_id,
                "actionType": command.action_type,
                "targetResourceUid": command.target_resource_uid,
                "dryRun": command.dry_run,
            },
        )
        material = f"{command.actor.tenant_id}\x1f{command.idempotency_key}".encode()
        proposal_id = "act_" + hashlib.sha256(material).hexdigest()[:32]
        proposal: dict[str, object] = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "ActionProposal",
            "metadata": {
                "id": proposal_id,
                "tenantId": command.actor.tenant_id,
                "actorId": command.actor.actor_id,
                "createdAt": self._clock.now(),
            },
            "spec": {
                **intended,
                "risk": "low" if command.dry_run else "medium",
                "reversible": True,
                "idempotencyKey": command.idempotency_key,
                "expiresAt": command.expires_at,
                "policyDecision": {
                    "allowed": decision.allowed,
                    "reasonCode": decision.reason_code,
                    "policySnapshotRef": (
                        f"policy://{command.actor.tenant_id}/snapshots/action-proposal-v1"
                    ),
                },
            },
            "status": "pending-approval" if decision.allowed else "denied",
        }
        self._repository.commit_proposal(command.actor, proposal)
        self._audit.append_audit(command.actor, "action-proposed", proposal)
        return proposal

    def decide(self, command: DecideActionCommand) -> Mapping[str, object]:
        if command.decision not in ("approved", "rejected") or not command.rationale:
            raise ActionWorkflowError("action.approval.invalid")
        if "approver" not in command.actor.roles:
            raise ActionWorkflowError("action.approval.role-required")
        proposal = self._repository.get_proposal(command.actor, command.proposal_id)
        if proposal is None:
            raise ActionWorkflowError("action.proposal.not-found")
        metadata = proposal["metadata"]
        spec = proposal["spec"]
        assert isinstance(metadata, Mapping) and isinstance(spec, Mapping)
        if metadata.get("actorId") == command.actor.actor_id:
            raise ActionWorkflowError("action.approval.self-denied")
        if proposal.get("status") != "pending-approval":
            raise ActionWorkflowError("action.proposal.not-approvable")
        if self._parse_time(str(spec["expiresAt"])) <= self._parse_time(self._clock.now()):
            raise ActionWorkflowError("action.proposal.expired")
        policy = self._policy.decide(
            command.actor,
            "action:approve",
            {"tenantId": command.actor.tenant_id, "proposalId": command.proposal_id},
        )
        if not policy.allowed:
            raise ActionWorkflowError("action.approval.policy-denied")

        existing = self._repository.get_approval(command.actor, command.proposal_id)
        if existing is not None:
            existing_spec = existing["spec"]
            assert isinstance(existing_spec, Mapping)
            if (
                existing_spec.get("decision") != command.decision
                or existing_spec.get("rationale") != command.rationale
            ):
                raise ActionWorkflowError("action.approval.conflict")
            return existing
        material = (
            f"{command.actor.tenant_id}\x1f{command.proposal_id}\x1f"
            f"{command.actor.actor_id}\x1f{command.decision}"
        ).encode()
        approval: dict[str, object] = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "ActionApproval",
            "metadata": {
                "id": "apr_" + hashlib.sha256(material).hexdigest()[:32],
                "tenantId": command.actor.tenant_id,
                "approverId": command.actor.actor_id,
                "decidedAt": self._clock.now(),
            },
            "spec": {
                "proposalId": command.proposal_id,
                "decision": command.decision,
                "rationale": command.rationale,
                "policySnapshotRef": (
                    f"policy://{command.actor.tenant_id}/snapshots/action-approval-v1"
                ),
            },
        }
        self._repository.commit_approval(command.actor, approval)
        self._audit.append_audit(command.actor, "action-decided", approval)
        return approval

    def execute(self, command: ExecuteActionCommand) -> Mapping[str, object]:
        if "executor" not in command.actor.roles:
            raise ActionWorkflowError("action.execution.role-required")
        existing = self._repository.get_action_result(command.actor, command.proposal_id)
        if existing is not None:
            return existing
        proposal = self._repository.get_proposal(command.actor, command.proposal_id)
        approval = self._repository.get_approval(command.actor, command.proposal_id)
        if proposal is None or approval is None:
            raise ActionWorkflowError("action.execution.not-approved")
        proposal_spec = proposal["spec"]
        approval_metadata = approval["metadata"]
        approval_spec = approval["spec"]
        assert all(
            isinstance(value, Mapping)
            for value in (proposal_spec, approval_metadata, approval_spec)
        )
        if approval_spec.get("decision") != "approved":
            raise ActionWorkflowError("action.execution.not-approved")
        if self._parse_time(str(proposal_spec["expiresAt"])) <= self._parse_time(
            self._clock.now()
        ):
            raise ActionWorkflowError("action.proposal.expired")
        policy = self._policy.decide(
            command.actor,
            "action:execute",
            {"tenantId": command.actor.tenant_id, "proposalId": command.proposal_id},
        )
        if not policy.allowed:
            raise ActionWorkflowError("action.execution.policy-denied")

        outcome = self._executor.execute(
            command.actor,
            proposal,
            approval_id=str(approval_metadata["id"]),
        )
        audit_ref = self._audit.append_audit(
            command.actor,
            "action-executed",
            {
                "proposalId": command.proposal_id,
                "approvalId": approval_metadata["id"],
                "outcome": outcome.outcome,
            },
        )
        result: dict[str, object] = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "ActionResult",
            "metadata": {
                "id": command.proposal_id,
                "tenantId": command.actor.tenant_id,
                "completedAt": self._clock.now(),
            },
            "spec": {
                "proposalDigest": canonical_digest(proposal),
                "approvalId": approval_metadata["id"],
                "outcome": outcome.outcome,
                "idempotencyKey": proposal_spec["idempotencyKey"],
                "execution": {
                    "provider": outcome.provider,
                    "operationRef": outcome.operation_ref,
                },
                "verification": {
                    "status": outcome.verification_status,
                    "summary": outcome.verification_summary,
                },
                "auditRef": audit_ref,
            },
        }
        self._repository.commit_action_result(command.actor, result)
        return result

    @staticmethod
    def _parse_time(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ActionWorkflowError("action.time.invalid") from None
        if parsed.tzinfo is None:
            raise ActionWorkflowError("action.time.invalid")
        return parsed
