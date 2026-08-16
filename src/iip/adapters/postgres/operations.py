"""PostgreSQL evidence, investigation, action, session, and audit adapter."""

from __future__ import annotations

import hashlib
from typing import Iterable, Mapping, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from iip.application.investigate import canonical_digest
from iip.application.ports import ActionWorkflowRecord, ActorContext, PersistenceError


class PostgresOperationalStore:
    """Durable tenant-scoped operational documents behind application ports."""

    def __init__(self, database_url: str) -> None:
        if not isinstance(database_url, str) or not database_url.strip():
            raise ValueError("database_url must be a non-empty PostgreSQL connection string")
        self._database_url = database_url

    def _connect(self):
        return psycopg.connect(self._database_url, row_factory=dict_row)

    def commit(
        self,
        actor: ActorContext,
        evidence_id: str,
        document: Mapping[str, object],
        decoded_content: bytes,
    ) -> None:
        self._assert_tenant(actor, document)
        content_hash = "sha256:" + hashlib.sha256(decoded_content).hexdigest()
        spec = document.get("spec")
        metadata = document.get("metadata")
        artifact = spec.get("artifact") if isinstance(spec, Mapping) else None
        if (
            not isinstance(metadata, Mapping)
            or not isinstance(artifact, Mapping)
            or artifact.get("contentHash") != content_hash
            or artifact.get("sizeBytes") != len(decoded_content)
        ):
            raise PersistenceError("storage.input-invalid")
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO iip.evidence_artifacts (
                        tenant_id, evidence_id, document, artifact,
                        content_hash, recorded_at
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        actor.tenant_id,
                        evidence_id,
                        Jsonb(dict(document)),
                        decoded_content,
                        content_hash,
                        metadata["recordedAt"],
                    ),
                )
        except psycopg.errors.UniqueViolation:
            raise PersistenceError("storage.conflict") from None
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def get(
        self, actor: ActorContext, evidence_id: str
    ) -> Optional[Mapping[str, object]]:
        return self._one_document(
            "SELECT document FROM iip.evidence_artifacts WHERE tenant_id = %s AND evidence_id = %s",
            (actor.tenant_id, evidence_id),
        )

    def read_artifact(self, actor: ActorContext, evidence_id: str) -> Optional[bytes]:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT artifact FROM iip.evidence_artifacts WHERE tenant_id = %s AND evidence_id = %s",
                    (actor.tenant_id, evidence_id),
                ).fetchone()
            return bytes(row["artifact"]) if row is not None else None
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def list(
        self,
        actor: ActorContext,
        *,
        resource_uids: tuple[str, ...] = (),
        evidence_types: tuple[str, ...] = (),
        limit: int = 100,
    ) -> Iterable[Mapping[str, object]]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        filters = ["tenant_id = %s"]
        parameters: list[object] = [actor.tenant_id]
        if resource_uids:
            filters.append("document->'spec'->'resourceRefs' ?| %s")
            parameters.append(list(resource_uids))
        if evidence_types:
            filters.append("document->'spec'->>'type' = ANY(%s)")
            parameters.append(list(evidence_types))
        parameters.append(limit)
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    f"SELECT document FROM iip.evidence_artifacts WHERE {' AND '.join(filters)} ORDER BY recorded_at DESC, evidence_id LIMIT %s",
                    parameters,
                ).fetchall()
            return tuple(dict(row["document"]) for row in rows)
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def start_investigation(
        self,
        actor: ActorContext,
        investigation_id: str,
        request: Mapping[str, object],
        status: Mapping[str, object],
    ) -> None:
        self._assert_tenant(actor, request)
        self._assert_tenant(actor, status)
        status_spec = status.get("spec")
        if not isinstance(status_spec, Mapping):
            raise PersistenceError("storage.input-invalid")
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO iip.investigations (
                        tenant_id, investigation_id, request_digest,
                        request_document, report_document, state,
                        status_document, lease_expires_at
                    ) VALUES (%s, %s, %s, %s, NULL, 'running', %s, %s)
                    """,
                    (
                        actor.tenant_id,
                        investigation_id,
                        canonical_digest(request),
                        Jsonb(dict(request)),
                        Jsonb(dict(status)),
                        status_spec["leaseExpiresAt"],
                    ),
                )
        except psycopg.errors.UniqueViolation:
            raise PersistenceError("storage.conflict") from None
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

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
        status_spec = status.get("spec")
        report_spec = report.get("spec")
        if not isinstance(status_spec, Mapping) or not isinstance(
            report_spec, Mapping
        ):
            raise PersistenceError("storage.input-invalid")
        request_digest = canonical_digest(request)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    UPDATE iip.investigations
                    SET report_document = %s,
                        status_document = %s,
                        state = %s,
                        lease_expires_at = NULL
                    WHERE tenant_id = %s
                      AND investigation_id = %s
                      AND request_digest = %s
                      AND report_document IS NULL
                      AND (state = 'running' OR %s = 'cancelled')
                    RETURNING investigation_id
                    """,
                    (
                        Jsonb(dict(report)),
                        Jsonb(dict(status)),
                        status_spec["state"],
                        actor.tenant_id,
                        investigation_id,
                        request_digest,
                        report_spec.get("outcome"),
                    ),
                ).fetchone()
                if row is None:
                    existing = connection.execute(
                        """
                        SELECT request_digest, report_document, status_document
                        FROM iip.investigations
                        WHERE tenant_id = %s AND investigation_id = %s
                        """,
                        (actor.tenant_id, investigation_id),
                    ).fetchone()
                    if (
                        existing is None
                        or existing["request_digest"] != request_digest
                        or dict(existing["report_document"] or {}) != dict(report)
                        or dict(existing["status_document"] or {}) != dict(status)
                    ):
                        raise PersistenceError("storage.conflict")
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def get_investigation(
        self, actor: ActorContext, investigation_id: str
    ) -> Optional[Mapping[str, object]]:
        return self._one_document(
            "SELECT report_document AS document FROM iip.investigations WHERE tenant_id = %s AND investigation_id = %s",
            (actor.tenant_id, investigation_id),
        )

    def get_investigation_request(
        self, actor: ActorContext, investigation_id: str
    ) -> Optional[Mapping[str, object]]:
        return self._one_document(
            "SELECT request_document AS document FROM iip.investigations WHERE tenant_id = %s AND investigation_id = %s",
            (actor.tenant_id, investigation_id),
        )

    def get_investigation_status(
        self, actor: ActorContext, investigation_id: str
    ) -> Optional[Mapping[str, object]]:
        return self._one_document(
            "SELECT status_document AS document FROM iip.investigations WHERE tenant_id = %s AND investigation_id = %s",
            (actor.tenant_id, investigation_id),
        )

    def request_investigation_cancellation(
        self,
        actor: ActorContext,
        investigation_id: str,
        status: Mapping[str, object],
    ) -> Mapping[str, object]:
        self._assert_tenant(actor, status)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    UPDATE iip.investigations
                    SET status_document = %s, state = 'cancellation-requested'
                    WHERE tenant_id = %s
                      AND investigation_id = %s
                      AND state = 'running'
                    RETURNING status_document AS document
                    """,
                    (Jsonb(dict(status)), actor.tenant_id, investigation_id),
                ).fetchone()
                if row is None:
                    row = connection.execute(
                        """
                        SELECT status_document AS document
                        FROM iip.investigations
                        WHERE tenant_id = %s AND investigation_id = %s
                        """,
                        (actor.tenant_id, investigation_id),
                    ).fetchone()
            if row is None:
                raise PersistenceError("storage.not-found")
            return dict(row["document"])
        except PersistenceError:
            raise
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def get_proposal_by_key(
        self, actor: ActorContext, idempotency_key: str
    ) -> Optional[Mapping[str, object]]:
        return self._one_document(
            "SELECT document FROM iip.action_proposals WHERE tenant_id = %s AND idempotency_key = %s",
            (actor.tenant_id, idempotency_key),
        )

    def get_proposal(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[Mapping[str, object]]:
        return self._one_document(
            "SELECT document FROM iip.action_proposals WHERE tenant_id = %s AND proposal_id = %s",
            (actor.tenant_id, proposal_id),
        )

    def get_action_workflow(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[ActionWorkflowRecord]:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT p.document AS proposal,
                           a.document AS approval,
                           e.document AS execution_status,
                           r.document AS result
                    FROM iip.action_proposals AS p
                    LEFT JOIN iip.action_approvals AS a
                      ON a.tenant_id = p.tenant_id
                     AND a.proposal_id = p.proposal_id
                    LEFT JOIN iip.action_executions AS e
                      ON e.tenant_id = p.tenant_id
                     AND e.proposal_id = p.proposal_id
                    LEFT JOIN iip.action_results AS r
                      ON r.tenant_id = p.tenant_id
                     AND r.proposal_id = p.proposal_id
                    WHERE p.tenant_id = %s AND p.proposal_id = %s
                    """,
                    (actor.tenant_id, proposal_id),
                ).fetchone()
            return self._workflow_record(row) if row is not None else None
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def list_action_workflows(
        self,
        actor: ActorContext,
        *,
        before_created_at: Optional[str],
        before_proposal_id: Optional[str],
        limit: int,
    ) -> tuple[ActionWorkflowRecord, ...]:
        if limit < 1 or limit > 101:
            raise PersistenceError("storage.input-invalid")
        if (before_created_at is None) != (before_proposal_id is None):
            raise PersistenceError("storage.input-invalid")
        position_clause = ""
        parameters: tuple[object, ...]
        if before_created_at is None:
            parameters = (actor.tenant_id, limit)
        else:
            position_clause = """
              AND (
                    (p.document #>> '{metadata,createdAt}')::timestamptz,
                    p.proposal_id
                  ) < (%s::timestamptz, %s)
            """
            parameters = (
                actor.tenant_id,
                before_created_at,
                before_proposal_id,
                limit,
            )
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT p.document AS proposal,
                           a.document AS approval,
                           e.document AS execution_status,
                           r.document AS result
                    FROM iip.action_proposals AS p
                    LEFT JOIN iip.action_approvals AS a
                      ON a.tenant_id = p.tenant_id
                     AND a.proposal_id = p.proposal_id
                    LEFT JOIN iip.action_executions AS e
                      ON e.tenant_id = p.tenant_id
                     AND e.proposal_id = p.proposal_id
                    LEFT JOIN iip.action_results AS r
                      ON r.tenant_id = p.tenant_id
                     AND r.proposal_id = p.proposal_id
                    WHERE p.tenant_id = %s
                    """
                    + position_clause
                    + """
                    ORDER BY
                      (p.document #>> '{metadata,createdAt}')::timestamptz DESC,
                      p.proposal_id DESC
                    LIMIT %s
                    """,
                    parameters,
                ).fetchall()
            return tuple(self._workflow_record(row) for row in rows)
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def commit_proposal(
        self, actor: ActorContext, document: Mapping[str, object]
    ) -> None:
        self._assert_tenant(actor, document)
        metadata = document["metadata"]
        spec = document["spec"]
        assert isinstance(metadata, Mapping) and isinstance(spec, Mapping)
        self._insert(
            "INSERT INTO iip.action_proposals (tenant_id, proposal_id, idempotency_key, document) VALUES (%s, %s, %s, %s)",
            (actor.tenant_id, metadata["id"], spec["idempotencyKey"], Jsonb(dict(document))),
        )

    def get_approval(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[Mapping[str, object]]:
        return self._one_document(
            "SELECT document FROM iip.action_approvals WHERE tenant_id = %s AND proposal_id = %s",
            (actor.tenant_id, proposal_id),
        )

    def commit_approval(
        self, actor: ActorContext, document: Mapping[str, object]
    ) -> None:
        self._assert_tenant(actor, document)
        metadata = document["metadata"]
        spec = document["spec"]
        assert isinstance(metadata, Mapping) and isinstance(spec, Mapping)
        self._insert(
            "INSERT INTO iip.action_approvals (tenant_id, approval_id, proposal_id, document, decided_at) VALUES (%s, %s, %s, %s, %s)",
            (
                actor.tenant_id,
                metadata["id"],
                spec["proposalId"],
                Jsonb(dict(document)),
                metadata["decidedAt"],
            ),
        )

    def get_action_result(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[Mapping[str, object]]:
        return self._one_document(
            "SELECT document FROM iip.action_results WHERE tenant_id = %s AND proposal_id = %s",
            (actor.tenant_id, proposal_id),
        )

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
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    INSERT INTO iip.action_executions (
                        tenant_id, proposal_id, state, document,
                        lease_expires_at, updated_at
                    ) VALUES (%s, %s, 'executing', %s, %s, %s)
                    ON CONFLICT (tenant_id, proposal_id) DO NOTHING
                    RETURNING proposal_id
                    """,
                    (
                        actor.tenant_id,
                        metadata["id"],
                        Jsonb(dict(document)),
                        spec["leaseExpiresAt"],
                        metadata["updatedAt"],
                    ),
                ).fetchone()
            return row is not None
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def get_action_execution_status(
        self, actor: ActorContext, proposal_id: str
    ) -> Optional[Mapping[str, object]]:
        return self._one_document(
            "SELECT document FROM iip.action_executions WHERE tenant_id = %s AND proposal_id = %s",
            (actor.tenant_id, proposal_id),
        )

    def mark_action_execution_uncertain(
        self,
        actor: ActorContext,
        proposal_id: str,
        observed_at: str,
        document: Mapping[str, object],
    ) -> Mapping[str, object]:
        self._assert_tenant(actor, document)
        metadata = document.get("metadata")
        spec = document.get("spec")
        if not isinstance(metadata, Mapping) or not isinstance(spec, Mapping):
            raise PersistenceError("storage.input-invalid")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    UPDATE iip.action_executions
                    SET state = 'manual-reconciliation-required',
                        document = %s,
                        lease_expires_at = NULL,
                        updated_at = %s
                    WHERE tenant_id = %s
                      AND proposal_id = %s
                      AND state = 'executing'
                      AND lease_expires_at <= %s
                    RETURNING document
                    """,
                    (
                        Jsonb(dict(document)),
                        metadata["updatedAt"],
                        actor.tenant_id,
                        proposal_id,
                        observed_at,
                    ),
                ).fetchone()
                if row is None:
                    row = connection.execute(
                        """
                        SELECT document
                        FROM iip.action_executions
                        WHERE tenant_id = %s AND proposal_id = %s
                        """,
                        (actor.tenant_id, proposal_id),
                    ).fetchone()
            if row is None:
                raise PersistenceError("storage.not-found")
            return dict(row["document"])
        except PersistenceError:
            raise
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

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
        if (
            status_metadata.get("id") != metadata.get("id")
            or not isinstance(status_spec, Mapping)
            or status_spec.get("state") == "executing"
        ):
            raise PersistenceError("storage.input-invalid")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    UPDATE iip.action_executions
                    SET state = %s,
                        document = %s,
                        lease_expires_at = NULL,
                        updated_at = %s
                    WHERE tenant_id = %s
                      AND proposal_id = %s
                      AND state = 'executing'
                    RETURNING proposal_id
                    """,
                    (
                        status_spec["state"],
                        Jsonb(dict(status)),
                        status_metadata["updatedAt"],
                        actor.tenant_id,
                        metadata["id"],
                    ),
                ).fetchone()
                if row is None:
                    existing = connection.execute(
                        """
                        SELECT document
                        FROM iip.action_results
                        WHERE tenant_id = %s AND proposal_id = %s
                        """,
                        (actor.tenant_id, metadata["id"]),
                    ).fetchone()
                    existing_status = connection.execute(
                        """
                        SELECT document
                        FROM iip.action_executions
                        WHERE tenant_id = %s AND proposal_id = %s
                        """,
                        (actor.tenant_id, metadata["id"]),
                    ).fetchone()
                    if (
                        existing is not None
                        and existing_status is not None
                        and dict(existing["document"]) == dict(document)
                        and dict(existing_status["document"]) == dict(status)
                    ):
                        return
                    raise PersistenceError("storage.conflict")
                connection.execute(
                    """
                    INSERT INTO iip.action_results (
                        tenant_id, proposal_id, document, completed_at
                    ) VALUES (%s, %s, %s, %s)
                    """,
                    (
                        actor.tenant_id,
                        metadata["id"],
                        Jsonb(dict(document)),
                        metadata["completedAt"],
                    ),
                )
        except PersistenceError:
            raise
        except psycopg.errors.UniqueViolation:
            raise PersistenceError("storage.conflict") from None
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def commit_plugin_session(
        self, actor: ActorContext, document: Mapping[str, object]
    ) -> None:
        self._assert_tenant(actor, document)
        metadata = document["metadata"]
        spec = document["spec"]
        assert isinstance(metadata, Mapping) and isinstance(spec, Mapping)
        self._insert(
            "INSERT INTO iip.plugin_sessions (tenant_id, session_id, document, expires_at) VALUES (%s, %s, %s, %s)",
            (actor.tenant_id, metadata["id"], Jsonb(dict(document)), spec["expiresAt"]),
        )

    def get_plugin_session(
        self, actor: ActorContext, session_id: str
    ) -> Optional[Mapping[str, object]]:
        return self._one_document(
            "SELECT document FROM iip.plugin_sessions WHERE tenant_id = %s AND session_id = %s",
            (actor.tenant_id, session_id),
        )

    def append_audit(
        self,
        actor: ActorContext,
        category: str,
        document: Mapping[str, object],
    ) -> str:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "INSERT INTO iip.audit_records (tenant_id, category, document) VALUES (%s, %s, %s) RETURNING audit_offset",
                    (actor.tenant_id, category, Jsonb(dict(document))),
                ).fetchone()
            return f"audit://{actor.tenant_id}/records/{row['audit_offset']}"
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def _one_document(
        self, statement: str, parameters: tuple[object, ...]
    ) -> Optional[Mapping[str, object]]:
        try:
            with self._connect() as connection:
                row = connection.execute(statement, parameters).fetchone()
            return (
                dict(row["document"])
                if row is not None and row["document"] is not None
                else None
            )
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def _insert(self, statement: str, parameters: tuple[object, ...]) -> None:
        try:
            with self._connect() as connection:
                connection.execute(statement, parameters)
        except psycopg.errors.UniqueViolation:
            raise PersistenceError("storage.conflict") from None
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    @staticmethod
    def _workflow_record(row: Mapping[str, object]) -> ActionWorkflowRecord:
        proposal = row.get("proposal")
        if not isinstance(proposal, Mapping):
            raise PersistenceError("storage.corrupt")
        related = []
        for key in ("approval", "execution_status", "result"):
            document = row.get(key)
            if document is not None and not isinstance(document, Mapping):
                raise PersistenceError("storage.corrupt")
            related.append(dict(document) if isinstance(document, Mapping) else None)
        return ActionWorkflowRecord(
            proposal=dict(proposal),
            approval=related[0],
            execution_status=related[1],
            result=related[2],
        )

    @staticmethod
    def _assert_tenant(actor: ActorContext, document: Mapping[str, object]) -> None:
        metadata = document.get("metadata")
        if not isinstance(metadata, Mapping) or metadata.get("tenantId") != actor.tenant_id:
            raise PersistenceError("storage.input-invalid")
