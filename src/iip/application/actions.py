"""Governed proposal, separation-of-duties approval, and execution workflow."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping

from iip.application.action_reconciliation import build_uncertain_execution_status
from iip.application.investigate import canonical_digest
from iip.application.ports import (
    ActionExecutor,
    ActionRepository,
    ActorContext,
    AuditSink,
    Clock,
    EventOutbox,
    InvestigationRepository,
    PersistenceError,
    PolicyDecisionPoint,
    ResourceRepository,
)
from iip.domain.models import Resource


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

    KUBERNETES_RESTART = "kubernetes.restart-workload"
    EVENT_DELIVERY_REQUEUE = "event-delivery.requeue"
    SUPPORTED_ACTIONS = {KUBERNETES_RESTART, EVENT_DELIVERY_REQUEUE}
    EXECUTION_LEASE_SECONDS = 300
    _DNS_LABEL = r"[a-z0-9](?:[-a-z0-9]{0,61}[a-z0-9])?"
    _DNS_SUBDOMAIN = re.compile(rf"{_DNS_LABEL}(?:\.{_DNS_LABEL})*")
    _PROVIDER = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
    _INTEGRATION_ID = re.compile(r"[a-z][a-z0-9._-]{2,127}")
    _PROVIDER_OBJECT_UID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,252}")
    _WORKLOAD_TYPES = {
        "deployment": "apps/deployment",
        "statefulset": "apps/statefulset",
        "daemonset": "apps/daemonset",
    }

    def __init__(
        self,
        resources: ResourceRepository,
        policy: PolicyDecisionPoint,
        repository: ActionRepository,
        executor: ActionExecutor,
        audit: AuditSink,
        clock: Clock,
        investigations: InvestigationRepository,
        outbox: EventOutbox | None = None,
    ) -> None:
        self._resources = resources
        self._policy = policy
        self._repository = repository
        self._executor = executor
        self._audit = audit
        self._clock = clock
        self._investigations = investigations
        self._outbox = outbox

    def propose(self, command: ProposeActionCommand) -> Mapping[str, object]:
        if command.action_type not in self.SUPPORTED_ACTIONS:
            raise ActionWorkflowError("action.type.unsupported")
        if (
            command.action_type == self.EVENT_DELIVERY_REQUEUE
            and "platform-admin" not in command.actor.roles
        ):
            raise ActionWorkflowError("action.replay.role-required")
        if not 8 <= len(command.idempotency_key) <= 128:
            raise ActionWorkflowError("action.idempotency-key.invalid")
        target = self._resources.get(
            command.actor.tenant_id, command.target_resource_uid
        )
        if target is None:
            raise ActionWorkflowError("action.target.unavailable")
        parameters = self._validated_parameters(
            command.actor,
            command.action_type,
            command.parameters,
            target,
        )
        integration_id: str | None = None
        provider_object_uid: str | None = None
        if command.action_type == self.KUBERNETES_RESTART:
            integration_id, provider_object_uid = self._target_binding(target)
        investigation_request = self._investigations.get_investigation_request(
            command.actor, command.investigation_id
        )
        investigation_report = self._investigations.get_investigation(
            command.actor, command.investigation_id
        )
        self._validate_investigation_binding(
            investigation_request,
            investigation_report,
            command.target_resource_uid,
        )
        if self._parse_time(command.expires_at) <= self._parse_time(self._clock.now()):
            raise ActionWorkflowError("action.expiry.invalid")

        existing = self._repository.get_proposal_by_key(
            command.actor, command.idempotency_key
        )
        intended = {
            "investigationId": command.investigation_id,
            "actionType": command.action_type,
            "targetResourceUid": command.target_resource_uid,
            "parameters": parameters,
            "dryRun": command.dry_run,
            "expiresAt": command.expires_at,
        }
        if integration_id is not None:
            intended["integrationId"] = integration_id
        if provider_object_uid is not None:
            intended["providerObjectUid"] = provider_object_uid
        if existing is not None:
            self._validate_proposal_replay(existing, command, intended)
            return existing

        target_digest = canonical_digest(target.to_dict())
        investigation_digest = canonical_digest(investigation_report)
        policy_input: dict[str, object] = {
            "tenantId": command.actor.tenant_id,
            "investigationDigest": investigation_digest,
            "actionType": command.action_type,
            "targetResourceUid": command.target_resource_uid,
            "targetDigest": target_digest,
            "parameters": parameters,
            "dryRun": command.dry_run,
        }
        if integration_id is not None:
            policy_input["integrationId"] = integration_id
        if provider_object_uid is not None:
            policy_input["providerObjectUid"] = provider_object_uid
        decision = self._policy.decide(command.actor, "action:propose", policy_input)
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
                "investigationDigest": investigation_digest,
                "targetDigest": target_digest,
                "risk": (
                    "low"
                    if command.dry_run
                    else "high"
                    if command.action_type == self.EVENT_DELIVERY_REQUEUE
                    else "medium"
                ),
                "reversible": command.action_type == self.KUBERNETES_RESTART,
                "idempotencyKey": command.idempotency_key,
                "expiresAt": command.expires_at,
                "policyDecision": {
                    "allowed": decision.allowed,
                    "reasonCode": decision.reason_code,
                    "policySnapshotRef": self._policy_snapshot(
                        decision,
                        f"policy://{command.actor.tenant_id}/snapshots/action-proposal-v1",
                    ),
                    "inputDigest": canonical_digest(policy_input),
                },
            },
            "status": "pending-approval" if decision.allowed else "denied",
        }
        try:
            self._repository.commit_proposal(command.actor, proposal)
        except PersistenceError as error:
            if str(error) != "storage.conflict":
                raise
            existing = self._repository.get_proposal_by_key(
                command.actor, command.idempotency_key
            )
            if existing is None:
                raise
            self._validate_proposal_replay(existing, command, intended)
            return existing
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
        proposal_digest = canonical_digest(proposal)
        policy_input = {
            "tenantId": command.actor.tenant_id,
            "proposalId": command.proposal_id,
            "proposalDigest": proposal_digest,
            "decision": command.decision,
        }
        policy = self._policy.decide(command.actor, "action:approve", policy_input)
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
                "proposalDigest": proposal_digest,
                "decision": command.decision,
                "rationale": command.rationale,
                "policySnapshotRef": self._policy_snapshot(
                    policy,
                    f"policy://{command.actor.tenant_id}/snapshots/action-approval-v1",
                ),
                "policyInputDigest": canonical_digest(policy_input),
            },
        }
        try:
            self._repository.commit_approval(command.actor, approval)
        except PersistenceError as error:
            if str(error) != "storage.conflict":
                raise
            existing = self._repository.get_approval(
                command.actor, command.proposal_id
            )
            if existing is None:
                raise
            existing_spec = existing.get("spec")
            if (
                not isinstance(existing_spec, Mapping)
                or existing_spec.get("decision") != command.decision
                or existing_spec.get("rationale") != command.rationale
            ):
                raise ActionWorkflowError("action.approval.conflict") from None
            return existing
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
        proposal_digest = canonical_digest(proposal)
        approved_digest = approval_spec.get("proposalDigest")
        if approved_digest is not None and approved_digest != proposal_digest:
            raise ActionWorkflowError("action.approval.proposal-mismatch")
        if self._parse_time(str(proposal_spec["expiresAt"])) <= self._parse_time(
            self._clock.now()
        ):
            raise ActionWorkflowError("action.proposal.expired")
        target_uid = str(proposal_spec["targetResourceUid"])
        target = self._resources.get(command.actor.tenant_id, target_uid)
        action_type = proposal_spec.get("actionType")
        if (
            target is None
            or action_type not in self.SUPPORTED_ACTIONS
            or (
                action_type == self.KUBERNETES_RESTART
                and target.lifecycle != "active"
            )
        ):
            raise ActionWorkflowError("action.target.unavailable")
        self._validated_parameters(
            command.actor,
            str(action_type),
            proposal_spec.get("parameters"),
            target,
        )
        policy_input = {
            "tenantId": command.actor.tenant_id,
            "proposalId": command.proposal_id,
            "proposalDigest": proposal_digest,
            "approvalId": approval_metadata["id"],
            "actionType": proposal_spec["actionType"],
            "targetResourceUid": target_uid,
            "proposalTargetDigest": proposal_spec.get("targetDigest"),
            "currentTargetDigest": canonical_digest(target.to_dict()),
            "parameters": proposal_spec["parameters"],
            "dryRun": proposal_spec["dryRun"],
        }
        policy = self._policy.decide(command.actor, "action:execute", policy_input)
        if not policy.allowed:
            raise ActionWorkflowError("action.execution.policy-denied")

        started_at = self._clock.now()
        lease_expires_at = self._format_time(
            self._parse_time(started_at)
            + timedelta(seconds=self.EXECUTION_LEASE_SECONDS)
        )
        execution_status: dict[str, object] = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "ActionExecutionStatus",
            "metadata": {
                "id": command.proposal_id,
                "tenantId": command.actor.tenant_id,
                "updatedAt": started_at,
            },
            "spec": {
                "proposalDigest": proposal_digest,
                "approvalId": approval_metadata["id"],
                "state": "executing",
                "attempt": 1,
                "executorActorId": command.actor.actor_id,
                "startedAt": started_at,
                "leaseExpiresAt": lease_expires_at,
                "policyDecision": {
                    "allowed": True,
                    "reasonCode": policy.reason_code,
                    "policySnapshotRef": self._policy_snapshot(
                        policy,
                        f"policy://{command.actor.tenant_id}/snapshots/action-execution-v1",
                    ),
                    "inputDigest": canonical_digest(policy_input),
                },
            },
        }
        if not self._repository.claim_action_execution(
            command.actor, execution_status
        ):
            result = self._repository.get_action_result(
                command.actor, command.proposal_id
            )
            if result is not None:
                return result
            return self._resolve_existing_execution(command)

        self._audit.append_audit(
            command.actor,
            "action-execution-started",
            {
                "proposalId": command.proposal_id,
                "proposalDigest": proposal_digest,
                "approvalId": approval_metadata["id"],
                "leaseExpiresAt": lease_expires_at,
                "policyInputDigest": canonical_digest(policy_input),
            },
        )

        outcome = self._executor.execute(
            command.actor,
            proposal,
            approval_id=str(approval_metadata["id"]),
        )
        self._validate_outcome(outcome)
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
                "executionStatusRef": (
                    f"action://{command.actor.tenant_id}/{command.proposal_id}/execution"
                ),
                "executionPolicyInputDigest": canonical_digest(policy_input),
            },
        }
        result_spec = result["spec"]
        assert isinstance(result_spec, dict)
        if outcome.error_code is not None:
            result_spec["errorCode"] = outcome.error_code
        if outcome.rollback_status is not None:
            result_spec["rollback"] = {
                "status": outcome.rollback_status,
                "summary": outcome.rollback_summary,
            }
        completed_at = str(result["metadata"]["completedAt"])  # type: ignore[index]
        terminal_status: dict[str, object] = {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "ActionExecutionStatus",
            "metadata": {
                "id": command.proposal_id,
                "tenantId": command.actor.tenant_id,
                "updatedAt": completed_at,
            },
            "spec": {
                **dict(execution_status["spec"]),  # type: ignore[arg-type]
                "state": outcome.outcome,
                "completedAt": completed_at,
                "operationRef": outcome.operation_ref,
                "summary": outcome.verification_summary,
            },
        }
        del terminal_status["spec"]["leaseExpiresAt"]  # type: ignore[index]
        self._repository.commit_action_result(
            command.actor, result, terminal_status
        )
        return result

    def _resolve_existing_execution(
        self,
        command: ExecuteActionCommand,
    ) -> Mapping[str, object]:
        current = self._repository.get_action_execution_status(
            command.actor, command.proposal_id
        )
        if current is None:
            raise ActionWorkflowError("action.execution.state-unavailable")
        spec = current.get("spec")
        if not isinstance(spec, Mapping):
            raise ActionWorkflowError("action.execution.state-unavailable")
        if spec.get("state") != "executing":
            result = self._repository.get_action_result(
                command.actor, command.proposal_id
            )
            if result is not None:
                return result
            raise ActionWorkflowError("action.execution.reconciliation-required")
        now = self._clock.now()
        if self._parse_time(str(spec.get("leaseExpiresAt"))) > self._parse_time(now):
            raise ActionWorkflowError("action.execution.in-progress")
        uncertain = build_uncertain_execution_status(current, now)
        transition = self._repository.reconcile_expired_action_execution(
            command.actor,
            command.proposal_id,
            now,
            uncertain,
            {
                "proposalId": command.proposal_id,
                "proposalDigest": spec.get("proposalDigest"),
                "observedAt": now,
                "workerActorId": command.actor.actor_id,
                "reasonCode": "action.execution.lease-expired",
            },
        )
        resolved_spec = transition.status.get("spec")
        if isinstance(resolved_spec, Mapping) and resolved_spec.get("state") == "executing":
            raise ActionWorkflowError("action.execution.in-progress")
        if (
            isinstance(resolved_spec, Mapping)
            and resolved_spec.get("state") != "manual-reconciliation-required"
        ):
            result = self._repository.get_action_result(
                command.actor, command.proposal_id
            )
            if result is not None:
                return result
        raise ActionWorkflowError("action.execution.reconciliation-required")

    @staticmethod
    def _validate_proposal_replay(
        existing: Mapping[str, object],
        command: ProposeActionCommand,
        intended: Mapping[str, object],
    ) -> None:
        metadata = existing.get("metadata")
        spec = existing.get("spec")
        if (
            not isinstance(metadata, Mapping)
            or metadata.get("actorId") != command.actor.actor_id
            or not isinstance(spec, Mapping)
            or any(spec.get(key) != value for key, value in intended.items())
        ):
            raise ActionWorkflowError("action.idempotency-key.conflict")

    @classmethod
    def _validate_outcome(cls, outcome: object) -> None:
        if (
            getattr(outcome, "outcome", None)
            not in ("dry-run", "succeeded", "failed", "rolled-back")
            or not isinstance(getattr(outcome, "provider", None), str)
            or cls._PROVIDER.fullmatch(outcome.provider) is None
            or not isinstance(getattr(outcome, "operation_ref", None), str)
            or not 1 <= len(outcome.operation_ref) <= 2048
            or getattr(outcome, "verification_status", None)
            not in ("not-run", "passed", "failed")
            or not isinstance(getattr(outcome, "verification_summary", None), str)
            or not 1 <= len(outcome.verification_summary) <= 4096
            or (
                getattr(outcome, "error_code", None) is not None
                and (
                    not isinstance(outcome.error_code, str)
                    or re.fullmatch(r"[a-z][a-z0-9_.-]{2,127}", outcome.error_code)
                    is None
                )
            )
            or (
                getattr(outcome, "rollback_status", None) is not None
                and outcome.rollback_status not in ("succeeded", "failed")
            )
            or (
                getattr(outcome, "rollback_status", None) is not None
                and (
                    not isinstance(getattr(outcome, "rollback_summary", None), str)
                    or not 1 <= len(outcome.rollback_summary) <= 4096
                )
            )
            or (
                getattr(outcome, "rollback_status", None) is None
                and getattr(outcome, "rollback_summary", None) is not None
            )
            or (
                getattr(outcome, "outcome", None) == "rolled-back"
                and getattr(outcome, "rollback_status", None) != "succeeded"
            )
        ):
            raise ActionWorkflowError("action.executor.output-invalid")

    def _validated_parameters(
        self,
        actor: ActorContext,
        action_type: str,
        parameters: object,
        target: Resource,
    ) -> dict[str, object]:
        if action_type == self.EVENT_DELIVERY_REQUEUE:
            return self._validated_replay_parameters(actor, parameters, target)
        if action_type != self.KUBERNETES_RESTART:
            raise ActionWorkflowError("action.type.unsupported")
        if not isinstance(parameters, Mapping) or set(parameters) != {
            "namespace",
            "workloadKind",
            "workloadName",
        }:
            raise ActionWorkflowError("action.parameters.invalid")
        namespace = parameters.get("namespace")
        workload_kind = parameters.get("workloadKind")
        workload_name = parameters.get("workloadName")
        if (
            not isinstance(namespace, str)
            or len(namespace) > 253
            or self._DNS_SUBDOMAIN.fullmatch(namespace) is None
            or not isinstance(workload_name, str)
            or len(workload_name) > 253
            or self._DNS_SUBDOMAIN.fullmatch(workload_name) is None
            or workload_kind not in self._WORKLOAD_TYPES
        ):
            raise ActionWorkflowError("action.parameters.invalid")
        external_parts = target.identity.external_id.split("/")
        if (
            target.identity.provider != "kubernetes"
            or target.identity.resource_type != self._WORKLOAD_TYPES[workload_kind]
            or len(external_parts) != 3
            or external_parts[1] != namespace
            or external_parts[2] != workload_name
        ):
            raise ActionWorkflowError("action.parameters.target-mismatch")
        return {
            "namespace": namespace,
            "workloadKind": workload_kind,
            "workloadName": workload_name,
        }

    def _validated_replay_parameters(
        self,
        actor: ActorContext,
        parameters: object,
        target: Resource,
    ) -> dict[str, object]:
        if self._outbox is None:
            raise ActionWorkflowError("action.target.unavailable")
        if not isinstance(parameters, Mapping) or set(parameters) != {
            "outboxId",
            "eventId",
            "quarantinedAt",
            "attempts",
        }:
            raise ActionWorkflowError("action.parameters.invalid")
        outbox_id = parameters.get("outboxId")
        event_id = parameters.get("eventId")
        quarantined_at = parameters.get("quarantinedAt")
        attempts = parameters.get("attempts")
        if (
            isinstance(outbox_id, bool)
            or not isinstance(outbox_id, int)
            or not 1 <= outbox_id <= 9_007_199_254_740_991
            or not isinstance(event_id, str)
            or not 1 <= len(event_id) <= 128
            or not isinstance(quarantined_at, str)
            or isinstance(attempts, bool)
            or not isinstance(attempts, int)
            or not 1 <= attempts <= 1000
        ):
            raise ActionWorkflowError("action.parameters.invalid")
        self._parse_time(quarantined_at)
        quarantine = self._outbox.get_quarantined_outbox(
            actor.tenant_id,
            outbox_id,
        )
        if quarantine is None:
            raise ActionWorkflowError("action.target.unavailable")
        if (
            quarantine.event_id != event_id
            or quarantine.quarantined_at != quarantined_at
            or quarantine.attempts != attempts
            or quarantine.subject != target.identity.uid
        ):
            raise ActionWorkflowError("action.parameters.target-mismatch")
        return {
            "outboxId": outbox_id,
            "eventId": event_id,
            "quarantinedAt": quarantined_at,
            "attempts": attempts,
        }

    @classmethod
    def _target_binding(cls, target: Resource) -> tuple[str, str | None]:
        integration_id = (
            target.observation.source_id if target.observation is not None else None
        )
        provider_object_uid = target.attributes.get("providerUid")
        if (
            not isinstance(integration_id, str)
            or cls._INTEGRATION_ID.fullmatch(integration_id) is None
        ):
            raise ActionWorkflowError("action.target.integration-unavailable")
        if provider_object_uid is not None and (
            not isinstance(provider_object_uid, str)
            or cls._PROVIDER_OBJECT_UID.fullmatch(provider_object_uid) is None
        ):
            raise ActionWorkflowError("action.target.provider-uid-invalid")
        return integration_id, provider_object_uid

    @staticmethod
    def _validate_investigation_binding(
        request: object,
        report: object,
        target_resource_uid: str,
    ) -> None:
        if not isinstance(request, Mapping) or not isinstance(report, Mapping):
            raise ActionWorkflowError("action.investigation.unavailable")
        request_spec = request.get("spec")
        report_spec = report.get("spec")
        if not isinstance(request_spec, Mapping) or not isinstance(
            report_spec, Mapping
        ):
            raise ActionWorkflowError("action.investigation.unavailable")
        request_scope = request_spec.get("scope")
        report_scope = report_spec.get("scope")
        if (
            request_spec.get("maxAuthority") != "propose"
            or not isinstance(request_scope, Mapping)
            or not isinstance(report_scope, Mapping)
            or target_resource_uid not in request_scope.get("resourceUids", ())
            or target_resource_uid not in report_scope.get("resourceUids", ())
            or report_spec.get("requestDigest") != canonical_digest(request)
            or report_spec.get("outcome") in ("failed", "cancelled")
        ):
            raise ActionWorkflowError("action.investigation.not-authorized")

    @staticmethod
    def _parse_time(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise ActionWorkflowError("action.time.invalid") from None
        if parsed.tzinfo is None:
            raise ActionWorkflowError("action.time.invalid")
        return parsed

    @staticmethod
    def _format_time(value: datetime) -> str:
        return value.isoformat().replace("+00:00", "Z")

    @staticmethod
    def _policy_snapshot(decision: object, fallback: str) -> str:
        snapshot = getattr(decision, "policy_snapshot_ref", None)
        return snapshot if isinstance(snapshot, str) and snapshot else fallback
