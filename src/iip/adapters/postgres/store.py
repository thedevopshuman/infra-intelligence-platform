"""PostgreSQL resource, observation, event-log, and outbox adapter."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from functools import wraps
from importlib import resources
from typing import Any, Iterable, Mapping, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from iip.application.ports import (
    OutboxMessage,
    PersistenceError,
    ProjectionRebuildResult,
    ReconciliationSnapshot,
    ResourceObservationRecord,
    ResourceWriteResult,
    SourceCheckpoint,
    StoredEvent,
)
from iip.domain.models import (
    ContractError,
    ObservationDisposition,
    PlatformEvent,
    Resource,
    ResourceRelationshipEdge,
    classify_resource_observation,
    index_resource_relationships,
)


_MIGRATIONS = (
    "0001_resource_event_substrate.sql",
    "0002_resource_relationship_index.sql",
    "0003_operational_workflows.sql",
    "0004_reconciliation_snapshots.sql",
    "0005_projection_rebuild_source.sql",
    "0006_source_checkpoint_provider_cursors.sql",
)


def _translate_database_errors(operation: Any) -> Any:
    """Prevent provider exceptions and SQL details crossing the adapter boundary."""

    @wraps(operation)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return operation(*args, **kwargs)
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    return wrapped


class PostgresResourceStore:
    """Tenant-scoped PostgreSQL adapter with transactional outbox guarantees."""

    def __init__(self, database_url: str) -> None:
        if not isinstance(database_url, str) or not database_url.strip():
            raise ValueError("database_url must be a non-empty PostgreSQL connection string")
        self._database_url = database_url

    @_translate_database_errors
    def migrate(self) -> None:
        """Apply packaged, append-only schema migrations under a database lock."""

        with self._connect() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                ("iip.schema-migrations",),
            )
            connection.execute("CREATE SCHEMA IF NOT EXISTS iip")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS iip.schema_migrations (
                    version text PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT clock_timestamp()
                )
                """
            )
            applied = {
                row["version"]
                for row in connection.execute(
                    "SELECT version FROM iip.schema_migrations"
                ).fetchall()
            }
            migration_root = resources.files("iip.adapters.postgres").joinpath("migrations")
            for version in _MIGRATIONS:
                if version in applied:
                    continue
                sql = migration_root.joinpath(version).read_text(encoding="utf-8")
                connection.execute(sql)
                connection.execute(
                    "INSERT INTO iip.schema_migrations (version) VALUES (%s)",
                    (version,),
                )

    @_translate_database_errors
    def apply(
        self,
        resource: Resource,
        event: PlatformEvent,
        *,
        checkpoint_ready: bool = False,
    ) -> ResourceWriteResult:
        """Apply ordering and all accepted-observation effects in one transaction."""

        self._validate_event(resource, event)
        self._validate_checkpoint_request(resource, checkpoint_ready)
        tenant_id = resource.identity.tenant_id
        resource_uid = resource.identity.uid
        document = resource.to_dict()
        observation_hash = PlatformEvent.canonical_hash(document)

        with self._connect() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock_shared(hashtextextended(%s, 0))",
                (f"iip.projection-maintenance\x1f{tenant_id}",),
            )
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{tenant_id}\x1f{resource_uid}",),
            )
            row = connection.execute(
                """
                SELECT document
                FROM iip.resource_projections
                WHERE tenant_id = %s AND resource_uid = %s
                FOR UPDATE
                """,
                (tenant_id, resource_uid),
            ).fetchone()
            current = Resource.from_dict(row["document"]) if row is not None else None
            disposition = (
                ObservationDisposition.ACCEPTED
                if current is None
                else classify_resource_observation(current, resource)
            )
            if disposition == ObservationDisposition.DUPLICATE:
                return ResourceWriteResult(current, disposition)

            if disposition == ObservationDisposition.ACCEPTED and checkpoint_ready:
                self._validate_checkpoint_advance(connection, resource)

            if disposition == ObservationDisposition.ACCEPTED:
                self._upsert_projection(connection, resource, observation_hash)
                self._replace_relationships(connection, resource)
            self._insert_observation(
                connection,
                resource,
                observation_hash,
                disposition,
            )

            if disposition != ObservationDisposition.ACCEPTED:
                assert current is not None
                return ResourceWriteResult(current, disposition)

            event_offset = self._append_event(connection, event)
            connection.execute(
                """
                INSERT INTO iip.event_outbox (tenant_id, event_offset)
                VALUES (%s, %s)
                """,
                (tenant_id, event_offset),
            )
            if checkpoint_ready:
                self._write_checkpoint(connection, resource)
            return ResourceWriteResult(resource, disposition)

    @_translate_database_errors
    def get(self, tenant_id: str, uid: str) -> Optional[Resource]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT document
                FROM iip.resource_projections
                WHERE tenant_id = %s AND resource_uid = %s
                """,
                (tenant_id, uid),
            ).fetchone()
        return Resource.from_dict(row["document"]) if row is not None else None

    @_translate_database_errors
    def list(self, tenant_id: str) -> Iterable[Resource]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT document
                FROM iip.resource_projections
                WHERE tenant_id = %s
                ORDER BY resource_uid
                """,
                (tenant_id,),
            ).fetchall()
        return tuple(Resource.from_dict(row["document"]) for row in rows)

    @_translate_database_errors
    def rebuild_projections(
        self,
        tenant_id: str,
        *,
        dry_run: bool,
        max_resources: int,
    ) -> ProjectionRebuildResult:
        """Verify or atomically rebuild one tenant's derived serving state."""

        if not isinstance(tenant_id, str) or not 1 <= len(tenant_id) <= 128:
            raise ValueError("tenant_id is invalid")
        if not isinstance(dry_run, bool):
            raise ValueError("dry_run must be a boolean")
        if (
            isinstance(max_resources, bool)
            or not isinstance(max_resources, int)
            or not 1 <= max_resources <= 1_000_000
        ):
            raise ValueError("max_resources is invalid")

        with self._connect() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"iip.projection-maintenance\x1f{tenant_id}",),
            )
            rows = connection.execute(
                """
                SELECT DISTINCT ON (resource_uid)
                       observation_offset, resource_uid,
                       observation_hash, document
                FROM iip.resource_observations
                WHERE tenant_id = %s AND disposition = 'accepted'
                ORDER BY resource_uid, observation_offset DESC
                LIMIT %s
                """,
                (tenant_id, max_resources + 1),
            ).fetchall()
            if len(rows) > max_resources:
                raise PersistenceError("projection.resource_limit_exceeded")

            resources_with_hashes: list[tuple[Resource, str]] = []
            latest_observation_offset = 0
            for row in rows:
                try:
                    resource = Resource.from_dict(row["document"])
                except ContractError:
                    raise PersistenceError("projection.source_invalid") from None
                document_hash = PlatformEvent.canonical_hash(resource.to_dict())
                if (
                    resource.identity.tenant_id != tenant_id
                    or resource.identity.uid != row["resource_uid"]
                    or document_hash != row["observation_hash"]
                ):
                    raise PersistenceError("projection.source_invalid")
                resources_with_hashes.append((resource, document_hash))
                latest_observation_offset = max(
                    latest_observation_offset,
                    row["observation_offset"],
                )

            before_digest = self._stored_projection_digest(connection, tenant_id)
            expected_digest, relationship_count = self._expected_projection_digest(
                resources_with_hashes
            )
            drift_detected = before_digest != expected_digest
            rebuild_performed = not dry_run and drift_detected

            if rebuild_performed:
                connection.execute(
                    "DELETE FROM iip.resource_relationships WHERE tenant_id = %s",
                    (tenant_id,),
                )
                connection.execute(
                    "DELETE FROM iip.resource_projections WHERE tenant_id = %s",
                    (tenant_id,),
                )
                for resource, document_hash in resources_with_hashes:
                    self._upsert_projection(connection, resource, document_hash)
                    self._replace_relationships(connection, resource)

            after_digest = self._stored_projection_digest(connection, tenant_id)
            if rebuild_performed and after_digest != expected_digest:
                raise PersistenceError("projection.rebuild_verification_failed")

        return ProjectionRebuildResult(
            tenant_id=tenant_id,
            dry_run=dry_run,
            drift_detected=drift_detected,
            rebuild_performed=rebuild_performed,
            resource_count=len(resources_with_hashes),
            relationship_count=relationship_count,
            latest_observation_offset=latest_observation_offset,
            before_digest=before_digest,
            expected_digest=expected_digest,
            after_digest=after_digest,
        )

    @_translate_database_errors
    def get_many(self, tenant_id: str, uids: Iterable[str]) -> Iterable[Resource]:
        requested = tuple(sorted(set(uids)))
        if not requested:
            return ()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT document
                FROM iip.resource_projections
                WHERE tenant_id = %s AND resource_uid = ANY(%s)
                ORDER BY resource_uid
                """,
                (tenant_id, list(requested)),
            ).fetchall()
        return tuple(Resource.from_dict(row["document"]) for row in rows)

    @_translate_database_errors
    def history(
        self,
        tenant_id: str,
        uid: str,
        *,
        after_offset: int = 0,
        limit: int = 1000,
    ) -> Iterable[ResourceObservationRecord]:
        self._validate_page(after_offset, limit)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT observation_offset, document, disposition,
                       observation_hash, recorded_at
                FROM iip.resource_observations
                WHERE tenant_id = %s AND resource_uid = %s
                  AND observation_offset > %s
                ORDER BY observation_offset
                LIMIT %s
                """,
                (tenant_id, uid, after_offset, limit),
            ).fetchall()
        return tuple(
            ResourceObservationRecord(
                offset=row["observation_offset"],
                resource=Resource.from_dict(row["document"]),
                disposition=ObservationDisposition(row["disposition"]),
                observation_hash=row["observation_hash"],
                recorded_at=self._rfc3339(row["recorded_at"]),
            )
            for row in rows
        )

    @_translate_database_errors
    def relationships(
        self,
        tenant_id: str,
        uid: str,
        *,
        direction: str = "both",
        relationship_types: tuple[str, ...] = (),
        after_edge_id: Optional[str] = None,
        limit: int = 100,
    ) -> Iterable[ResourceRelationshipEdge]:
        self._validate_page(0, limit)
        if direction not in ("incoming", "outgoing", "both"):
            raise ValueError("direction is invalid")
        direction_clause = {
            "incoming": "target_ref = %s AND target_ref ~ '^res_[a-f0-9]{32}$'",
            "outgoing": "source_ref = %s AND source_ref ~ '^res_[a-f0-9]{32}$'",
            "both": "((source_ref = %s AND source_ref ~ '^res_[a-f0-9]{32}$') OR (target_ref = %s AND target_ref ~ '^res_[a-f0-9]{32}$'))",
        }[direction]
        parameters: list[Any] = [tenant_id]
        if direction == "both":
            parameters.extend((uid, uid))
        else:
            parameters.append(uid)
        filters = [f"({direction_clause})"]
        if relationship_types:
            filters.append("relationship_type = ANY(%s)")
            parameters.append(list(relationship_types))
        if after_edge_id is not None:
            filters.append("edge_id > %s")
            parameters.append(after_edge_id)
        parameters.append(limit)
        where = " AND ".join(filters)
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT edge_id, observed_resource_uid, relationship_type,
                       source_ref, target_ref, attributes
                FROM iip.resource_relationships
                WHERE tenant_id = %s AND {where}
                ORDER BY edge_id
                LIMIT %s
                """,
                parameters,
            ).fetchall()
        return tuple(
            ResourceRelationshipEdge(
                edge_id=row["edge_id"],
                observed_resource_uid=row["observed_resource_uid"],
                relationship_type=row["relationship_type"],
                source_ref=row["source_ref"],
                target_ref=row["target_ref"],
                attributes=dict(row["attributes"]),
            )
            for row in rows
        )

    @_translate_database_errors
    def list_events(
        self,
        tenant_id: str,
        *,
        after_offset: int = 0,
        limit: int = 100,
    ) -> Iterable[StoredEvent]:
        self._validate_page(after_offset, limit)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_offset, document
                FROM iip.event_log
                WHERE tenant_id = %s AND event_offset > %s
                ORDER BY event_offset
                LIMIT %s
                """,
                (tenant_id, after_offset, limit),
            ).fetchall()
        return tuple(
            StoredEvent(row["event_offset"], PlatformEvent.from_dict(row["document"]))
            for row in rows
        )

    @_translate_database_errors
    def claim_outbox(
        self,
        tenant_id: str,
        worker_id: str,
        *,
        limit: int = 100,
        lease_seconds: int = 30,
    ) -> Iterable[OutboxMessage]:
        self._validate_worker(worker_id)
        self._validate_page(0, limit)
        if lease_seconds < 1 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        with self._connect() as connection:
            rows = connection.execute(
                """
                WITH candidates AS (
                    SELECT outbox_id
                    FROM iip.event_outbox
                    WHERE tenant_id = %s
                      AND published_at IS NULL
                      AND available_at <= clock_timestamp()
                      AND (
                          claim_expires_at IS NULL
                          OR claim_expires_at <= clock_timestamp()
                      )
                    ORDER BY outbox_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                ), claimed AS (
                    UPDATE iip.event_outbox AS outbox
                    SET claimed_by = %s,
                        claim_expires_at = clock_timestamp()
                            + make_interval(secs => %s),
                        attempts = outbox.attempts + 1
                    FROM candidates
                    WHERE outbox.outbox_id = candidates.outbox_id
                    RETURNING outbox.outbox_id, outbox.event_offset, outbox.attempts
                )
                SELECT claimed.outbox_id, claimed.attempts, events.document
                FROM claimed
                JOIN iip.event_log AS events
                  ON events.event_offset = claimed.event_offset
                ORDER BY claimed.outbox_id
                """,
                (tenant_id, limit, worker_id, lease_seconds),
            ).fetchall()
        return tuple(
            OutboxMessage(
                message_id=row["outbox_id"],
                event=PlatformEvent.from_dict(row["document"]),
                attempts=row["attempts"],
            )
            for row in rows
        )

    @_translate_database_errors
    def acknowledge_outbox(
        self,
        tenant_id: str,
        worker_id: str,
        message_id: int,
    ) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                UPDATE iip.event_outbox
                SET published_at = clock_timestamp(),
                    claimed_by = NULL,
                    claim_expires_at = NULL
                WHERE tenant_id = %s
                  AND outbox_id = %s
                  AND claimed_by = %s
                  AND claim_expires_at > clock_timestamp()
                  AND published_at IS NULL
                RETURNING outbox_id
                """,
                (tenant_id, message_id, worker_id),
            ).fetchone()
        return row is not None

    @_translate_database_errors
    def release_outbox(
        self,
        tenant_id: str,
        worker_id: str,
        message_id: int,
        error_code: str,
        *,
        retry_after_seconds: int = 0,
    ) -> bool:
        self._validate_error_code(error_code)
        if retry_after_seconds < 0 or retry_after_seconds > 86_400:
            raise ValueError("retry_after_seconds must be between 0 and 86400")
        with self._connect() as connection:
            row = connection.execute(
                """
                UPDATE iip.event_outbox
                SET claimed_by = NULL,
                    claim_expires_at = NULL,
                    available_at = clock_timestamp() + make_interval(secs => %s),
                    last_error_code = %s
                WHERE tenant_id = %s
                  AND outbox_id = %s
                  AND claimed_by = %s
                  AND claim_expires_at > clock_timestamp()
                  AND published_at IS NULL
                RETURNING outbox_id
                """,
                (
                    retry_after_seconds,
                    error_code,
                    tenant_id,
                    message_id,
                    worker_id,
                ),
            ).fetchone()
        return row is not None

    @_translate_database_errors
    def get_checkpoint(self, tenant_id: str, source_id: str) -> Optional[SourceCheckpoint]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT tenant_id, source_id, stream_id, sequence,
                       checkpoint, provider_cursors, committed_at
                FROM iip.source_checkpoints
                WHERE tenant_id = %s AND source_id = %s
                """,
                (tenant_id, source_id),
            ).fetchone()
        if row is None:
            return None
        return SourceCheckpoint(
            tenant_id=row["tenant_id"],
            source_id=row["source_id"],
            stream_id=row["stream_id"],
            sequence=row["sequence"],
            checkpoint=row["checkpoint"],
            committed_at=self._rfc3339(row["committed_at"]),
            provider_cursors=tuple(sorted(row["provider_cursors"].items())),
        )

    @_translate_database_errors
    def get_reconciliation(
        self, tenant_id: str, source_id: str
    ) -> Optional[ReconciliationSnapshot]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT tenant_id, source_id, stream_id, snapshot_id,
                       scope_digest, sequence, checkpoint, result_digest,
                       resource_uids, tombstoned_uids, committed_at
                FROM iip.source_reconciliations
                WHERE tenant_id = %s AND source_id = %s
                """,
                (tenant_id, source_id),
            ).fetchone()
        return self._reconciliation_from_row(row) if row is not None else None

    @_translate_database_errors
    def commit_checkpoint(self, checkpoint: SourceCheckpoint, *, mode: str) -> None:
        """Advance a batch checkpoint after all observations are committed."""

        if mode not in ("incremental", "reconciliation"):
            raise ValueError("checkpoint mode is invalid")
        with self._connect() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{checkpoint.tenant_id}\x1f{checkpoint.source_id}",),
            )
            row = connection.execute(
                """
                SELECT stream_id, sequence, checkpoint, provider_cursors
                FROM iip.source_checkpoints
                WHERE tenant_id = %s AND source_id = %s
                FOR UPDATE
                """,
                (checkpoint.tenant_id, checkpoint.source_id),
            ).fetchone()
            if row is not None and row["stream_id"] == checkpoint.stream_id:
                if checkpoint.sequence < row["sequence"]:
                    raise ValueError("checkpoint sequence cannot move backwards")
                if (
                    checkpoint.sequence == row["sequence"]
                    and (
                        checkpoint.checkpoint != row["checkpoint"]
                        or dict(checkpoint.provider_cursors) != row["provider_cursors"]
                    )
                ):
                    raise ValueError("checkpoint content conflicts at the same sequence")
            elif row is not None and mode != "reconciliation":
                raise ValueError("checkpoint stream reset requires reconciliation")
            connection.execute(
                """
                INSERT INTO iip.source_checkpoints (
                    tenant_id, source_id, stream_id, sequence,
                    checkpoint, provider_cursors, committed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, source_id) DO UPDATE SET
                    stream_id = EXCLUDED.stream_id,
                    sequence = EXCLUDED.sequence,
                    checkpoint = EXCLUDED.checkpoint,
                    provider_cursors = EXCLUDED.provider_cursors,
                    committed_at = EXCLUDED.committed_at
                """,
                (
                    checkpoint.tenant_id,
                    checkpoint.source_id,
                    checkpoint.stream_id,
                    checkpoint.sequence,
                    checkpoint.checkpoint,
                    Jsonb(dict(checkpoint.provider_cursors)),
                    checkpoint.committed_at,
                ),
            )

    @_translate_database_errors
    def commit_reconciliation(
        self,
        snapshot: ReconciliationSnapshot,
        checkpoint: SourceCheckpoint,
    ) -> None:
        """Atomically commit complete membership and its source checkpoint."""

        self._validate_reconciliation(snapshot, checkpoint)
        with self._connect() as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{snapshot.tenant_id}\x1f{snapshot.source_id}",),
            )
            current_row = connection.execute(
                """
                SELECT tenant_id, source_id, stream_id, snapshot_id,
                       scope_digest, sequence, checkpoint, result_digest,
                       resource_uids, tombstoned_uids, committed_at
                FROM iip.source_reconciliations
                WHERE tenant_id = %s AND source_id = %s
                FOR UPDATE
                """,
                (snapshot.tenant_id, snapshot.source_id),
            ).fetchone()
            if current_row is not None:
                current = self._reconciliation_from_row(current_row)
                if current.snapshot_id == snapshot.snapshot_id:
                    if not self._same_reconciliation(current, snapshot):
                        raise ValueError("reconciliation snapshot content conflicts")
                elif current.scope_digest != snapshot.scope_digest:
                    raise ValueError("reconciliation source scope cannot change")

            checkpoint_row = connection.execute(
                """
                SELECT stream_id, sequence, checkpoint, provider_cursors
                FROM iip.source_checkpoints
                WHERE tenant_id = %s AND source_id = %s
                FOR UPDATE
                """,
                (checkpoint.tenant_id, checkpoint.source_id),
            ).fetchone()
            self._validate_checkpoint_row(
                checkpoint_row, checkpoint, mode="reconciliation"
            )
            connection.execute(
                """
                INSERT INTO iip.source_reconciliations (
                    tenant_id, source_id, stream_id, snapshot_id,
                    scope_digest, sequence, checkpoint, result_digest,
                    resource_uids, tombstoned_uids, committed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, source_id) DO UPDATE SET
                    stream_id = EXCLUDED.stream_id,
                    snapshot_id = EXCLUDED.snapshot_id,
                    scope_digest = EXCLUDED.scope_digest,
                    sequence = EXCLUDED.sequence,
                    checkpoint = EXCLUDED.checkpoint,
                    result_digest = EXCLUDED.result_digest,
                    resource_uids = EXCLUDED.resource_uids,
                    tombstoned_uids = EXCLUDED.tombstoned_uids,
                    committed_at = EXCLUDED.committed_at
                """,
                (
                    snapshot.tenant_id,
                    snapshot.source_id,
                    snapshot.stream_id,
                    snapshot.snapshot_id,
                    snapshot.scope_digest,
                    snapshot.sequence,
                    snapshot.checkpoint,
                    snapshot.result_digest,
                    list(snapshot.resource_uids),
                    list(snapshot.tombstoned_uids),
                    snapshot.committed_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO iip.source_checkpoints (
                    tenant_id, source_id, stream_id, sequence,
                    checkpoint, provider_cursors, committed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (tenant_id, source_id) DO UPDATE SET
                    stream_id = EXCLUDED.stream_id,
                    sequence = EXCLUDED.sequence,
                    checkpoint = EXCLUDED.checkpoint,
                    provider_cursors = EXCLUDED.provider_cursors,
                    committed_at = EXCLUDED.committed_at
                """,
                (
                    checkpoint.tenant_id,
                    checkpoint.source_id,
                    checkpoint.stream_id,
                    checkpoint.sequence,
                    checkpoint.checkpoint,
                    Jsonb(dict(checkpoint.provider_cursors)),
                    checkpoint.committed_at,
                ),
            )

    def _connect(self) -> Any:
        return psycopg.connect(self._database_url, row_factory=dict_row)

    @classmethod
    def _reconciliation_from_row(cls, row: Mapping[str, Any]) -> ReconciliationSnapshot:
        return ReconciliationSnapshot(
            tenant_id=row["tenant_id"],
            source_id=row["source_id"],
            stream_id=row["stream_id"],
            snapshot_id=row["snapshot_id"],
            scope_digest=row["scope_digest"],
            sequence=row["sequence"],
            checkpoint=row["checkpoint"],
            result_digest=row["result_digest"],
            resource_uids=tuple(row["resource_uids"]),
            tombstoned_uids=tuple(row["tombstoned_uids"]),
            committed_at=cls._rfc3339(row["committed_at"]),
        )

    @staticmethod
    def _upsert_projection(
        connection: Any,
        resource: Resource,
        observation_hash: str,
    ) -> None:
        cursor = resource.observation
        connection.execute(
            """
            INSERT INTO iip.resource_projections (
                tenant_id, resource_uid, provider, resource_type, external_id,
                observed_at, lifecycle, observation_source_id,
                observation_stream_id, observation_sequence, document_hash, document
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            ON CONFLICT (tenant_id, resource_uid) DO UPDATE SET
                provider = EXCLUDED.provider,
                resource_type = EXCLUDED.resource_type,
                external_id = EXCLUDED.external_id,
                observed_at = EXCLUDED.observed_at,
                lifecycle = EXCLUDED.lifecycle,
                observation_source_id = EXCLUDED.observation_source_id,
                observation_stream_id = EXCLUDED.observation_stream_id,
                observation_sequence = EXCLUDED.observation_sequence,
                document_hash = EXCLUDED.document_hash,
                document = EXCLUDED.document,
                updated_at = clock_timestamp()
            """,
            (
                resource.identity.tenant_id,
                resource.identity.uid,
                resource.identity.provider,
                resource.identity.resource_type,
                resource.identity.external_id,
                resource.observed_at,
                resource.lifecycle,
                cursor.source_id if cursor is not None else None,
                cursor.stream_id if cursor is not None else None,
                cursor.sequence if cursor is not None else None,
                observation_hash,
                Jsonb(resource.to_dict()),
            ),
        )

    @staticmethod
    def _expected_projection_digest(
        resources_with_hashes: Iterable[tuple[Resource, str]],
    ) -> tuple[str, int]:
        resources = []
        relationships = []
        for resource, document_hash in resources_with_hashes:
            resources.append(
                {
                    "resourceUid": resource.identity.uid,
                    "documentHash": document_hash,
                    "document": resource.to_dict(),
                }
            )
            for edge in index_resource_relationships(resource):
                relationships.append(
                    {
                        "edgeId": edge.edge_id,
                        "observedResourceUid": edge.observed_resource_uid,
                        "relationshipType": edge.relationship_type,
                        "sourceRef": edge.source_ref,
                        "targetRef": edge.target_ref,
                        "attributes": dict(edge.attributes),
                    }
                )
        material = {
            "resources": sorted(resources, key=lambda item: item["resourceUid"]),
            "relationships": sorted(
                relationships,
                key=lambda item: item["edgeId"],
            ),
        }
        return PostgresResourceStore._projection_digest(material), len(relationships)

    @staticmethod
    def _stored_projection_digest(connection: Any, tenant_id: str) -> str:
        projection_rows = connection.execute(
            """
            SELECT resource_uid, document_hash, document
            FROM iip.resource_projections
            WHERE tenant_id = %s
            ORDER BY resource_uid
            """,
            (tenant_id,),
        ).fetchall()
        relationship_rows = connection.execute(
            """
            SELECT edge_id, observed_resource_uid, relationship_type,
                   source_ref, target_ref, attributes
            FROM iip.resource_relationships
            WHERE tenant_id = %s
            ORDER BY edge_id
            """,
            (tenant_id,),
        ).fetchall()
        material = {
            "resources": [
                {
                    "resourceUid": row["resource_uid"],
                    "documentHash": row["document_hash"],
                    "document": dict(row["document"]),
                }
                for row in projection_rows
            ],
            "relationships": [
                {
                    "edgeId": row["edge_id"],
                    "observedResourceUid": row["observed_resource_uid"],
                    "relationshipType": row["relationship_type"],
                    "sourceRef": row["source_ref"],
                    "targetRef": row["target_ref"],
                    "attributes": dict(row["attributes"]),
                }
                for row in relationship_rows
            ],
        }
        return PostgresResourceStore._projection_digest(material)

    @staticmethod
    def _projection_digest(material: Mapping[str, Any]) -> str:
        encoded = json.dumps(
            material,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _insert_observation(
        connection: Any,
        resource: Resource,
        observation_hash: str,
        disposition: ObservationDisposition,
    ) -> None:
        cursor = resource.observation
        connection.execute(
            """
            INSERT INTO iip.resource_observations (
                tenant_id, resource_uid, observation_hash, disposition,
                observed_at, observation_source_id, observation_stream_id,
                observation_sequence, document
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, resource_uid, observation_hash) DO NOTHING
            """,
            (
                resource.identity.tenant_id,
                resource.identity.uid,
                observation_hash,
                disposition.value,
                resource.observed_at,
                cursor.source_id if cursor is not None else None,
                cursor.stream_id if cursor is not None else None,
                cursor.sequence if cursor is not None else None,
                Jsonb(resource.to_dict()),
            ),
        )

    @staticmethod
    def _replace_relationships(connection: Any, resource: Resource) -> None:
        tenant_id = resource.identity.tenant_id
        resource_uid = resource.identity.uid
        connection.execute(
            """
            DELETE FROM iip.resource_relationships
            WHERE tenant_id = %s AND observed_resource_uid = %s
            """,
            (tenant_id, resource_uid),
        )
        for edge in index_resource_relationships(resource):
            connection.execute(
                """
                INSERT INTO iip.resource_relationships (
                    tenant_id, edge_id, observed_resource_uid,
                    relationship_type, source_ref, target_ref,
                    attributes, observed_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_id,
                    edge.edge_id,
                    resource_uid,
                    edge.relationship_type,
                    edge.source_ref,
                    edge.target_ref,
                    Jsonb(dict(edge.attributes)),
                    resource.observed_at,
                ),
            )

    @staticmethod
    def _append_event(connection: Any, event: PlatformEvent) -> int:
        row = connection.execute(
            """
            INSERT INTO iip.event_log (
                tenant_id, event_source, event_id, event_type,
                subject, event_time, document
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING event_offset
            """,
            (
                event.tenant_id,
                event.source,
                event.event_id,
                event.event_type,
                event.subject,
                event.time,
                Jsonb(event.to_dict()),
            ),
        ).fetchone()
        return row["event_offset"]

    @staticmethod
    def _validate_checkpoint_advance(connection: Any, resource: Resource) -> None:
        cursor = resource.observation
        assert cursor is not None and cursor.checkpoint is not None
        row = connection.execute(
            """
            SELECT stream_id, sequence, checkpoint
            FROM iip.source_checkpoints
            WHERE tenant_id = %s AND source_id = %s
            FOR UPDATE
            """,
            (resource.identity.tenant_id, cursor.source_id),
        ).fetchone()
        if row is None:
            return
        if row["stream_id"] == cursor.stream_id:
            if cursor.sequence < row["sequence"]:
                raise ValueError("checkpoint sequence cannot move backwards")
            if cursor.sequence == row["sequence"] and cursor.checkpoint != row["checkpoint"]:
                raise ValueError("checkpoint content conflicts at the same sequence")
            return
        if cursor.mode != "reconciliation":
            raise ValueError("checkpoint stream reset requires reconciliation")

    @staticmethod
    def _validate_checkpoint_row(
        row: Optional[Mapping[str, Any]],
        checkpoint: SourceCheckpoint,
        *,
        mode: str,
    ) -> None:
        if row is None:
            return
        if row["stream_id"] == checkpoint.stream_id:
            if checkpoint.sequence < row["sequence"]:
                raise ValueError("checkpoint sequence cannot move backwards")
            if (
                checkpoint.sequence == row["sequence"]
                and (
                    checkpoint.checkpoint != row["checkpoint"]
                    or dict(checkpoint.provider_cursors) != row["provider_cursors"]
                )
            ):
                raise ValueError("checkpoint content conflicts at the same sequence")
            return
        if mode != "reconciliation":
            raise ValueError("checkpoint stream reset requires reconciliation")

    @staticmethod
    def _validate_reconciliation(
        snapshot: ReconciliationSnapshot,
        checkpoint: SourceCheckpoint,
    ) -> None:
        if (
            snapshot.tenant_id != checkpoint.tenant_id
            or snapshot.source_id != checkpoint.source_id
            or snapshot.stream_id != checkpoint.stream_id
            or snapshot.sequence != checkpoint.sequence
            or snapshot.checkpoint != checkpoint.checkpoint
            or snapshot.committed_at != checkpoint.committed_at
            or snapshot.resource_uids != tuple(sorted(set(snapshot.resource_uids)))
            or snapshot.tombstoned_uids
            != tuple(sorted(set(snapshot.tombstoned_uids)))
            or set(snapshot.resource_uids).intersection(snapshot.tombstoned_uids)
            or len(snapshot.resource_uids) > 10_000
            or len(snapshot.tombstoned_uids) > 10_000
            or snapshot.sequence < 0
            or snapshot.sequence > 9_007_199_254_740_991
            or not re.fullmatch(r"snap_[a-f0-9]{32}", snapshot.snapshot_id)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", snapshot.scope_digest)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", snapshot.result_digest)
            or any(
                not re.fullmatch(r"res_[a-f0-9]{32}", uid)
                for uid in snapshot.resource_uids + snapshot.tombstoned_uids
            )
        ):
            raise ValueError("reconciliation state is invalid")

    @staticmethod
    def _same_reconciliation(
        current: ReconciliationSnapshot,
        incoming: ReconciliationSnapshot,
    ) -> bool:
        return (
            current.tenant_id,
            current.source_id,
            current.stream_id,
            current.snapshot_id,
            current.scope_digest,
            current.sequence,
            current.checkpoint,
            current.result_digest,
            current.resource_uids,
            current.tombstoned_uids,
        ) == (
            incoming.tenant_id,
            incoming.source_id,
            incoming.stream_id,
            incoming.snapshot_id,
            incoming.scope_digest,
            incoming.sequence,
            incoming.checkpoint,
            incoming.result_digest,
            incoming.resource_uids,
            incoming.tombstoned_uids,
        )

    @staticmethod
    def _write_checkpoint(connection: Any, resource: Resource) -> None:
        cursor = resource.observation
        assert cursor is not None and cursor.checkpoint is not None
        connection.execute(
            """
            INSERT INTO iip.source_checkpoints (
                tenant_id, source_id, stream_id, sequence, checkpoint,
                provider_cursors
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, source_id) DO UPDATE SET
                stream_id = EXCLUDED.stream_id,
                sequence = EXCLUDED.sequence,
                checkpoint = EXCLUDED.checkpoint,
                provider_cursors = EXCLUDED.provider_cursors,
                committed_at = clock_timestamp()
            """,
            (
                resource.identity.tenant_id,
                cursor.source_id,
                cursor.stream_id,
                cursor.sequence,
                cursor.checkpoint,
                Jsonb({}),
            ),
        )

    @staticmethod
    def _validate_event(resource: Resource, event: PlatformEvent) -> None:
        if event.tenant_id != resource.identity.tenant_id:
            raise ValueError("event tenant does not match resource tenant")
        if event.subject != resource.identity.uid:
            raise ValueError("event subject does not match resource identity")

    @staticmethod
    def _validate_checkpoint_request(resource: Resource, checkpoint_ready: bool) -> None:
        if checkpoint_ready and (
            resource.observation is None or resource.observation.checkpoint is None
        ):
            raise ValueError("checkpoint-ready write requires an observation checkpoint")

    @staticmethod
    def _validate_page(after_offset: int, limit: int) -> None:
        if after_offset < 0:
            raise ValueError("after_offset must not be negative")
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")

    @staticmethod
    def _validate_worker(worker_id: str) -> None:
        if not isinstance(worker_id, str) or not re.fullmatch(
            r"[a-zA-Z0-9._:-]{1,128}", worker_id
        ):
            raise ValueError("worker_id is invalid")

    @staticmethod
    def _validate_error_code(error_code: str) -> None:
        if not isinstance(error_code, str) or not re.fullmatch(
            r"[a-z][a-z0-9_.-]{2,127}", error_code
        ):
            raise ValueError("error_code must be stable and non-sensitive")

    @staticmethod
    def _rfc3339(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
