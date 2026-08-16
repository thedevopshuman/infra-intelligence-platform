"""Thread-safe operational document adapters for the local runtime."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime
from threading import RLock
from typing import Mapping, Optional

from iip.application.ports import ActorContext, PersistenceError


class InMemoryOperationalStore:
    """Tenant-partitioned investigations, actions, sessions, and audit records."""

    def __init__(self) -> None:
        self._investigations: dict[
            tuple[str, str],
            tuple[dict[str, object], Optional[dict[str, object]], dict[str, object]],
        ] = {}
        self._proposals: dict[tuple[str, str], dict[str, object]] = {}
        self._proposal_keys: dict[tuple[str, str], str] = {}
        self._approvals: dict[tuple[str, str], dict[str, object]] = {}
        self._results: dict[tuple[str, str], dict[str, object]] = {}
        self._action_executions: dict[tuple[str, str], dict[str, object]] = {}
        self._sessions: dict[tuple[str, str], dict[str, object]] = {}
        self._audit: list[tuple[str, str, dict[str, object]]] = []
        self._lock = RLock()

    def start_investigation(
        self,
        actor: ActorContext,
        investigation_id: str,
        request: Mapping[str, object],
        status: Mapping[str, object],
    ) -> None:
        self._assert_tenant(actor, request)
        self._assert_tenant(actor, status)
        key = (actor.tenant_id, investigation_id)
        value = (
            copy.deepcopy(dict(request)),
            None,
            copy.deepcopy(dict(status)),
        )
        with self._lock:
            if key in self._investigations:
                raise PersistenceError("storage.conflict")
            self._investigations[key] = value

    def commit_investigation(
        self,
        actor: ActorContext,
        investigation_id: str,
        request: Mapping[str, object],
        report: Mapping[str, object],
        status: Mapping[str, object],
    ) -> None:
        self._assert_tenant(actor, request)
        self._assert_tenant(actor, report)
        self._assert_tenant(actor, status)
        key = (actor.tenant_id, investigation_id)
        value = (
            copy.deepcopy(dict(request)),
            copy.deepcopy(dict(report)),
            copy.deepcopy(dict(status)),
        )
        with self._lock:
            current = self._investigations.get(key)
            if current is not None and current[0] != value[0]:
                raise PersistenceError("storage.conflict")
            if current is not None and current[1] is not None and current != value:
                raise PersistenceError("storage.conflict")
            current_spec = current[2].get("spec") if current is not None else None
            report_spec = report.get("spec")
            if (
                isinstance(current_spec, Mapping)
                and current_spec.get("state") == "cancellation-requested"
                and (
                    not isinstance(report_spec, Mapping)
                    or report_spec.get("outcome") != "cancelled"
                )
            ):
                raise PersistenceError("storage.conflict")
            self._investigations[key] = value

    def get_investigation(
        self, actor: ActorContext, investigation_id: str
    ) -> Optional[Mapping[str, object]]:
        with self._lock:
            value = self._investigations.get((actor.tenant_id, investigation_id))
            return (
                copy.deepcopy(value[1])
                if value is not None and value[1] is not None
                else None
            )

    def get_investigation_request(
        self, actor: ActorContext, investigation_id: str
    ) -> Optional[Mapping[str, object]]:
        with self._lock:
            value = self._investigations.get((actor.tenant_id, investigation_id))
            return copy.deepcopy(value[0]) if value is not None else None

    def get_investigation_status(
        self, actor: ActorContext, investigation_id: str
    ) -> Optional[Mapping[str, object]]:
        with self._lock:
            value = self._investigations.get((actor.tenant_id, investigation_id))
            return copy.deepcopy(value[2]) if value is not None else None

    def request_investigation_cancellation(
        self,
        actor: ActorContext,
        investigation_id: str,
        status: Mapping[str, object],
    ) -> Mapping[str, object]:
        self._assert_tenant(actor, status)
        key = (actor.tenant_id, investigation_id)
        with self._lock:
            current = self._investigations.get(key)
            if current is None:
                raise PersistenceError("storage.not-found")
            current_spec = current[2].get("spec")
            state = current_spec.get("state") if isinstance(current_spec, Mapping) else None
            if state != "running":
                return copy.deepcopy(current[2])
            updated = copy.deepcopy(dict(status))
            self._investigations[key] = (current[0], current[1], updated)
            return copy.deepcopy(updated)

    def get_proposal_by_key(
        self, actor: ActorContext, idempotency_key: str
    ) -> Optional[Mapping[str, object]]:
        with self._lock:
            proposal_id = self._proposal_keys.get((actor.tenant_id, idempotency_key))
            value = self._proposals.get((actor.tenant_id, proposal_id or ""))
            return copy.deepcopy(value) if value is not None else None

    def get_proposal(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[Mapping[str, object]]:
        with self._lock:
            value = self._proposals.get((actor.tenant_id, proposal_id))
            return copy.deepcopy(value) if value is not None else None

    def commit_proposal(
        self, actor: ActorContext, document: Mapping[str, object]
    ) -> None:
        self._assert_tenant(actor, document)
        metadata = document["metadata"]
        spec = document["spec"]
        assert isinstance(metadata, Mapping) and isinstance(spec, Mapping)
        key = (actor.tenant_id, str(metadata["id"]))
        idempotency = (actor.tenant_id, str(spec["idempotencyKey"]))
        value = copy.deepcopy(dict(document))
        with self._lock:
            existing_id = self._proposal_keys.get(idempotency)
            if existing_id is not None and existing_id != key[1]:
                raise PersistenceError("storage.conflict")
            current = self._proposals.get(key)
            if current is not None and current != value:
                raise PersistenceError("storage.conflict")
            self._proposals[key] = value
            self._proposal_keys[idempotency] = key[1]

    def get_approval(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[Mapping[str, object]]:
        with self._lock:
            value = self._approvals.get((actor.tenant_id, proposal_id))
            return copy.deepcopy(value) if value is not None else None

    def commit_approval(
        self, actor: ActorContext, document: Mapping[str, object]
    ) -> None:
        self._assert_tenant(actor, document)
        spec = document["spec"]
        assert isinstance(spec, Mapping)
        key = (actor.tenant_id, str(spec["proposalId"]))
        value = copy.deepcopy(dict(document))
        with self._lock:
            current = self._approvals.get(key)
            if current is not None and current != value:
                raise PersistenceError("storage.conflict")
            self._approvals[key] = value

    def get_action_result(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[Mapping[str, object]]:
        with self._lock:
            value = self._results.get((actor.tenant_id, proposal_id))
            return copy.deepcopy(value) if value is not None else None

    def claim_action_execution(
        self, actor: ActorContext, document: Mapping[str, object]
    ) -> bool:
        self._assert_tenant(actor, document)
        metadata = document.get("metadata")
        spec = document.get("spec")
        if (
            not isinstance(metadata, Mapping)
            or not isinstance(spec, Mapping)
            or spec.get("state") != "executing"
        ):
            raise PersistenceError("storage.input-invalid")
        key = (actor.tenant_id, str(metadata.get("id")))
        value = copy.deepcopy(dict(document))
        with self._lock:
            if key in self._action_executions:
                return False
            self._action_executions[key] = value
            return True

    def get_action_execution_status(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[Mapping[str, object]]:
        with self._lock:
            value = self._action_executions.get((actor.tenant_id, proposal_id))
            return copy.deepcopy(value) if value is not None else None

    def mark_action_execution_uncertain(
        self,
        actor: ActorContext,
        proposal_id: str,
        observed_at: str,
        document: Mapping[str, object],
    ) -> Mapping[str, object]:
        self._assert_tenant(actor, document)
        key = (actor.tenant_id, proposal_id)
        with self._lock:
            current = self._action_executions.get(key)
            if current is None:
                raise PersistenceError("storage.not-found")
            current_spec = current.get("spec")
            if not isinstance(current_spec, Mapping):
                raise PersistenceError("storage.input-invalid")
            if (
                current_spec.get("state") == "executing"
                and self._parse_time(str(current_spec.get("leaseExpiresAt")))
                <= self._parse_time(observed_at)
            ):
                self._action_executions[key] = copy.deepcopy(dict(document))
            return copy.deepcopy(self._action_executions[key])

    def commit_action_result(
        self,
        actor: ActorContext,
        document: Mapping[str, object],
        status: Mapping[str, object],
    ) -> None:
        self._assert_tenant(actor, document)
        self._assert_tenant(actor, status)
        metadata = document["metadata"]
        status_metadata = status.get("metadata")
        status_spec = status.get("spec")
        assert isinstance(metadata, Mapping) and isinstance(status_metadata, Mapping)
        key = (actor.tenant_id, str(metadata["id"]))
        if (
            status_metadata.get("id") != key[1]
            or not isinstance(status_spec, Mapping)
            or status_spec.get("state") == "executing"
        ):
            raise PersistenceError("storage.input-invalid")
        value = copy.deepcopy(dict(document))
        status_value = copy.deepcopy(dict(status))
        with self._lock:
            current = self._results.get(key)
            current_status = self._action_executions.get(key)
            if current is not None:
                if current != value or current_status != status_value:
                    raise PersistenceError("storage.conflict")
                return
            current_spec = (
                current_status.get("spec")
                if isinstance(current_status, Mapping)
                else None
            )
            if not isinstance(current_spec, Mapping) or current_spec.get("state") != "executing":
                raise PersistenceError("storage.conflict")
            self._results[key] = value
            self._action_executions[key] = status_value

    @staticmethod
    def _parse_time(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            raise PersistenceError("storage.input-invalid") from None
        if parsed.tzinfo is None:
            raise PersistenceError("storage.input-invalid")
        return parsed

    def commit_plugin_session(
        self, actor: ActorContext, document: Mapping[str, object]
    ) -> None:
        self._assert_tenant(actor, document)
        metadata = document["metadata"]
        assert isinstance(metadata, Mapping)
        key = (actor.tenant_id, str(metadata["id"]))
        with self._lock:
            if key in self._sessions:
                raise PersistenceError("storage.conflict")
            self._sessions[key] = copy.deepcopy(dict(document))

    def get_plugin_session(
        self, actor: ActorContext, session_id: str
    ) -> Optional[Mapping[str, object]]:
        with self._lock:
            value = self._sessions.get((actor.tenant_id, session_id))
            return copy.deepcopy(value) if value is not None else None

    def append_audit(
        self,
        actor: ActorContext,
        category: str,
        document: Mapping[str, object],
    ) -> str:
        encoded = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
        digest = hashlib.sha256(encoded).hexdigest()[:32]
        reference = f"audit://{actor.tenant_id}/{category}/{digest}"
        with self._lock:
            self._audit.append((actor.tenant_id, category, copy.deepcopy(dict(document))))
        return reference

    @staticmethod
    def _assert_tenant(actor: ActorContext, document: Mapping[str, object]) -> None:
        metadata = document.get("metadata")
        if not isinstance(metadata, Mapping) or metadata.get("tenantId") != actor.tenant_id:
            raise PersistenceError("storage.input-invalid")
