"""PostgreSQL evidence, investigation, action, session, and audit adapter."""

from __future__ import annotations

import hashlib
from typing import Iterable, Mapping, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from iip.application.investigate import canonical_digest
from iip.application.ports import ActorContext, PersistenceError


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

    def commit_investigation(
        self,
        actor: ActorContext,
        investigation_id: str,
        request: Mapping[str, object],
        report: Mapping[str, object],
    ) -> None:
        self._assert_tenant(actor, request)
        self._assert_tenant(actor, report)
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO iip.investigations (
                        tenant_id, investigation_id, request_digest,
                        request_document, report_document
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        actor.tenant_id,
                        investigation_id,
                        canonical_digest(request),
                        Jsonb(dict(request)),
                        Jsonb(dict(report)),
                    ),
                )
        except psycopg.errors.UniqueViolation:
            raise PersistenceError("storage.conflict") from None
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

    def commit_action_result(
        self, actor: ActorContext, document: Mapping[str, object]
    ) -> None:
        self._assert_tenant(actor, document)
        metadata = document["metadata"]
        assert isinstance(metadata, Mapping)
        self._insert(
            "INSERT INTO iip.action_results (tenant_id, proposal_id, document, completed_at) VALUES (%s, %s, %s, %s)",
            (actor.tenant_id, metadata["id"], Jsonb(dict(document)), metadata["completedAt"]),
        )

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
            return dict(row["document"]) if row is not None else None
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
    def _assert_tenant(actor: ActorContext, document: Mapping[str, object]) -> None:
        metadata = document.get("metadata")
        if not isinstance(metadata, Mapping) or metadata.get("tenantId") != actor.tenant_id:
            raise PersistenceError("storage.input-invalid")
