"""PostgreSQL resource, observation, event-log, and outbox adapter."""

from __future__ import annotations

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
    ResourceObservationRecord,
    ResourceWriteResult,
    SourceCheckpoint,
    StoredEvent,
)
from iip.domain.models import (
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
                       checkpoint, committed_at
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
        )

    def _connect(self) -> Any:
        return psycopg.connect(self._database_url, row_factory=dict_row)

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
    def _write_checkpoint(connection: Any, resource: Resource) -> None:
        cursor = resource.observation
        assert cursor is not None and cursor.checkpoint is not None
        connection.execute(
            """
            INSERT INTO iip.source_checkpoints (
                tenant_id, source_id, stream_id, sequence, checkpoint
            ) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, source_id) DO UPDATE SET
                stream_id = EXCLUDED.stream_id,
                sequence = EXCLUDED.sequence,
                checkpoint = EXCLUDED.checkpoint,
                committed_at = clock_timestamp()
            """,
            (
                resource.identity.tenant_id,
                cursor.source_id,
                cursor.stream_id,
                cursor.sequence,
                cursor.checkpoint,
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
