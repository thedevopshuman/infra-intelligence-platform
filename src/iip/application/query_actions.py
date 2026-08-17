"""Tenant-scoped governed-action read model and cursor pagination."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from datetime import datetime
from typing import Mapping, Optional

from iip.application.investigate import canonical_digest
from iip.application.ports import (
    ActionRepository,
    ActionWorkflowRecord,
    ActorContext,
    Clock,
    PersistenceError,
    PolicyDecisionPoint,
)


_ACTION_ID = re.compile(r"act_[a-f0-9]{32}")


class ActionQueryError(ValueError):
    """Invalid or unauthorized action workflow query."""


class ActionQueryAuthorizationError(PermissionError):
    """Policy denied a tenant-scoped workflow read."""


class ActionWorkflowNotFoundError(LookupError):
    """The requested workflow is not visible in the actor's tenant."""


class ActionWorkflowQueryService:
    """Build a consistent, reconstructable workflow view from durable records."""

    def __init__(
        self,
        repository: ActionRepository,
        policy: PolicyDecisionPoint,
        clock: Clock,
    ) -> None:
        self._repository = repository
        self._policy = policy
        self._clock = clock

    def get(self, actor: ActorContext, proposal_id: str) -> Mapping[str, object]:
        self._validate_action_id(proposal_id)
        self._authorize(actor, proposal_id)
        record = self._repository.get_action_workflow(actor, proposal_id)
        if record is None:
            raise ActionWorkflowNotFoundError("action.not-found")
        return self._workflow(actor, record)

    def list(
        self,
        actor: ActorContext,
        *,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> Mapping[str, object]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ActionQueryError("request.invalid")
        self._authorize(actor, None)
        before_created_at: Optional[str] = None
        before_proposal_id: Optional[str] = None
        if cursor is not None:
            position = self._decode_cursor(cursor, actor.tenant_id)
            before_created_at = position["createdAt"]
            before_proposal_id = position["proposalId"]
        records = tuple(
            self._repository.list_action_workflows(
                actor,
                before_created_at=before_created_at,
                before_proposal_id=before_proposal_id,
                limit=limit + 1,
            )
        )
        has_more = len(records) > limit
        visible = records[:limit]
        items = [self._workflow(actor, record) for record in visible]
        page: dict[str, object] = {"limit": limit, "hasMore": has_more}
        if has_more and visible:
            metadata = self._proposal_metadata(visible[-1])
            page["nextCursor"] = self._encode_cursor(
                actor.tenant_id,
                str(metadata["createdAt"]),
                str(metadata["id"]),
            )
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "ActionWorkflowPage",
            "metadata": {"tenantId": actor.tenant_id},
            "spec": {"items": items, "page": page},
        }

    def _authorize(self, actor: ActorContext, proposal_id: Optional[str]) -> None:
        resource: dict[str, object] = {"tenantId": actor.tenant_id}
        if proposal_id is not None:
            resource["proposalId"] = proposal_id
        decision = self._policy.decide(actor, "action:read", resource)
        if not decision.allowed:
            raise ActionQueryAuthorizationError(decision.reason_code)

    def _workflow(
        self, actor: ActorContext, record: ActionWorkflowRecord
    ) -> Mapping[str, object]:
        metadata = self._proposal_metadata(record)
        proposal_id = str(metadata["id"])
        self._validate_related(actor, proposal_id, record)
        state = self._state(record, self._clock.now())
        spec: dict[str, object] = {
            "state": state,
            "proposal": dict(record.proposal),
        }
        if record.approval is not None:
            spec["approval"] = dict(record.approval)
        if record.execution_status is not None:
            spec["executionStatus"] = dict(record.execution_status)
        if record.result is not None:
            spec["result"] = dict(record.result)
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "ActionWorkflow",
            "metadata": {"id": proposal_id, "tenantId": actor.tenant_id},
            "spec": spec,
        }

    @staticmethod
    def _proposal_metadata(record: ActionWorkflowRecord) -> Mapping[str, object]:
        metadata = record.proposal.get("metadata")
        if not isinstance(metadata, Mapping):
            raise PersistenceError("storage.corrupt")
        return metadata

    @classmethod
    def _validate_related(
        cls,
        actor: ActorContext,
        proposal_id: str,
        record: ActionWorkflowRecord,
    ) -> None:
        proposal_metadata = record.proposal.get("metadata")
        if (
            record.proposal.get("kind") != "ActionProposal"
            or not isinstance(proposal_metadata, Mapping)
            or proposal_metadata.get("tenantId") != actor.tenant_id
            or proposal_metadata.get("id") != proposal_id
            or _ACTION_ID.fullmatch(proposal_id) is None
        ):
            raise PersistenceError("storage.corrupt")
        if record.approval is not None:
            metadata = record.approval.get("metadata")
            spec = record.approval.get("spec")
            if (
                record.approval.get("kind") != "ActionApproval"
                or not isinstance(metadata, Mapping)
                or metadata.get("tenantId") != actor.tenant_id
                or not isinstance(spec, Mapping)
                or spec.get("proposalId") != proposal_id
                or spec.get("proposalDigest") != canonical_digest(record.proposal)
            ):
                raise PersistenceError("storage.corrupt")
        if record.execution_status is not None and record.approval is None:
            raise PersistenceError("storage.corrupt")
        if record.result is not None and (
            record.approval is None or record.execution_status is None
        ):
            raise PersistenceError("storage.corrupt")
        approval_metadata = (
            record.approval.get("metadata")
            if record.approval is not None
            else None
        )
        approval_id = (
            approval_metadata.get("id")
            if isinstance(approval_metadata, Mapping)
            else None
        )
        proposal_digest = canonical_digest(record.proposal)
        for document, kind in (
            (record.execution_status, "ActionExecutionStatus"),
            (record.result, "ActionResult"),
        ):
            if document is None:
                continue
            metadata = document.get("metadata")
            spec = document.get("spec")
            if (
                document.get("kind") != kind
                or not isinstance(metadata, Mapping)
                or metadata.get("tenantId") != actor.tenant_id
                or metadata.get("id") != proposal_id
                or not isinstance(spec, Mapping)
                or spec.get("proposalDigest") != proposal_digest
                or spec.get("approvalId") != approval_id
            ):
                raise PersistenceError("storage.corrupt")
        if record.result is not None:
            result_spec = record.result.get("spec")
            execution_spec = record.execution_status.get("spec")  # type: ignore[union-attr]
            if (
                not isinstance(result_spec, Mapping)
                or not isinstance(execution_spec, Mapping)
                or result_spec.get("outcome") != execution_spec.get("state")
            ):
                raise PersistenceError("storage.corrupt")

    @classmethod
    def _state(cls, record: ActionWorkflowRecord, observed_at: str) -> str:
        if record.result is not None:
            spec = record.result.get("spec")
            outcome = spec.get("outcome") if isinstance(spec, Mapping) else None
            if outcome in ("dry-run", "succeeded", "failed", "rolled-back"):
                return str(outcome)
            raise PersistenceError("storage.corrupt")
        if record.execution_status is not None:
            spec = record.execution_status.get("spec")
            state = spec.get("state") if isinstance(spec, Mapping) else None
            if state in ("executing", "manual-reconciliation-required"):
                return str(state)
            raise PersistenceError("storage.corrupt")
        if record.approval is not None:
            spec = record.approval.get("spec")
            decision = spec.get("decision") if isinstance(spec, Mapping) else None
            if decision == "rejected":
                return "rejected"
            if decision == "approved":
                return "expired" if cls._proposal_expired(record, observed_at) else "approved"
            raise PersistenceError("storage.corrupt")
        status = record.proposal.get("status")
        if status == "denied":
            return "denied"
        if status == "pending-approval":
            return "expired" if cls._proposal_expired(record, observed_at) else status
        raise PersistenceError("storage.corrupt")

    @staticmethod
    def _proposal_expired(record: ActionWorkflowRecord, observed_at: str) -> bool:
        spec = record.proposal.get("spec")
        expires_at = spec.get("expiresAt") if isinstance(spec, Mapping) else None
        if not isinstance(expires_at, str):
            raise PersistenceError("storage.corrupt")
        try:
            expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise PersistenceError("storage.corrupt") from None
        if expiry.tzinfo is None or observed.tzinfo is None:
            raise PersistenceError("storage.corrupt")
        return expiry <= observed

    @staticmethod
    def _validate_action_id(proposal_id: str) -> None:
        if not isinstance(proposal_id, str) or _ACTION_ID.fullmatch(proposal_id) is None:
            raise ActionQueryError("request.invalid")

    @staticmethod
    def _scope(tenant_id: str) -> str:
        return hashlib.sha256(f"action-workflows\x1f{tenant_id}".encode()).hexdigest()

    @classmethod
    def _encode_cursor(
        cls, tenant_id: str, created_at: str, proposal_id: str
    ) -> str:
        payload = {
            "kind": "action-workflows",
            "position": {"createdAt": created_at, "proposalId": proposal_id},
            "scope": cls._scope(tenant_id),
            "version": 1,
        }
        encoded = base64.urlsafe_b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).decode("ascii")
        return "p1." + encoded.rstrip("=")

    @classmethod
    def _decode_cursor(cls, cursor: str, tenant_id: str) -> dict[str, str]:
        if (
            not isinstance(cursor, str)
            or len(cursor) > 2048
            or re.fullmatch(r"p1\.[A-Za-z0-9_-]+", cursor) is None
        ):
            raise ActionQueryError("pagination.cursor_invalid")
        try:
            token = cursor[3:]
            raw = base64.b64decode(
                token + "=" * (-len(token) % 4), altchars=b"-_", validate=True
            )
            payload = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            raise ActionQueryError("pagination.cursor_invalid") from None
        position = payload.get("position") if isinstance(payload, dict) else None
        if (
            not isinstance(payload, dict)
            or set(payload) != {"kind", "position", "scope", "version"}
            or payload.get("kind") != "action-workflows"
            or payload.get("version") != 1
            or not isinstance(payload.get("scope"), str)
            or not hmac.compare_digest(payload["scope"], cls._scope(tenant_id))
            or not isinstance(position, dict)
            or set(position) != {"createdAt", "proposalId"}
            or not isinstance(position.get("createdAt"), str)
            or not isinstance(position.get("proposalId"), str)
            or _ACTION_ID.fullmatch(position["proposalId"]) is None
            or cls._encode_cursor(
                tenant_id, position["createdAt"], position["proposalId"]
            )
            != cursor
        ):
            raise ActionQueryError("pagination.cursor_invalid")
        try:
            parsed = datetime.fromisoformat(position["createdAt"].replace("Z", "+00:00"))
        except ValueError:
            raise ActionQueryError("pagination.cursor_invalid") from None
        if parsed.tzinfo is None:
            raise ActionQueryError("pagination.cursor_invalid")
        return position
