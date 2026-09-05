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

from iip.adapters.ai_cost_store import (
    prepare_ai_cost_writes,
    prepare_ai_price_catalog,
    validate_ai_cost_actor,
    validate_ai_cost_usage_binding,
)
from iip.adapters.ai_attribution_store import (
    prepare_ai_attribution_policy,
    prepare_ai_attribution_writes,
    validate_ai_attribution_actor,
)
from iip.adapters.ai_savings_store import (
    prepare_ai_savings_writes,
    validate_ai_savings_query,
)
from iip.adapters.ai_usage_store import prepare_ai_usage_writes
from iip.application.evaluate_ai_savings import (
    InvalidAiSavingsInputError,
    validate_context_growth_source_binding,
)
from iip.application.attribute_ai_usage import (
    InvalidAiAttributionInputError,
    validate_ai_attribution_source_binding,
)
from iip.application.ports import (
    ActorContext,
    AiAllocationLedgerQuery,
    AiSavingsCohortQuery,
    EventDeliverySloState,
    EventDeliveryState,
    OutboxMessage,
    PersistenceError,
    ProjectionRebuildResult,
    QuarantinedOutboxMessage,
    ReconciliationSnapshot,
    ResourceObservationRecord,
    ResourceWriteResult,
    SourceCheckpoint,
    SourceIngestionState,
    StoredEvent,
)
from iip.application.query_ai_allocations import validate_ai_allocation_ledger_query
from iip.domain.models import (
    ContractError,
    ObservationDisposition,
    PlatformEvent,
    Resource,
    ResourceRelationshipEdge,
    classify_resource_observation,
    index_resource_relationships,
)


SCHEMA_MIGRATIONS = (
    "0001_resource_event_substrate.sql",
    "0002_resource_relationship_index.sql",
    "0003_operational_workflows.sql",
    "0004_reconciliation_snapshots.sql",
    "0005_projection_rebuild_source.sql",
    "0006_source_checkpoint_provider_cursors.sql",
    "0007_investigation_lifecycle.sql",
    "0008_action_execution_lifecycle.sql",
    "0009_investigation_jobs.sql",
    "0010_event_outbox_quarantine.sql",
    "0011_event_outbox_slo_window.sql",
    "0012_investigation_job_slo_window.sql",
    "0013_evidence_artifact_retention.sql",
    "0014_durable_plugin_invocations.sql",
    "0015_plugin_invocation_lifecycle.sql",
    "0016_telemetry_export_health.sql",
    "0017_telemetry_export_slo_samples.sql",
    "0018_ai_usage_ledger.sql",
    "0019_ai_cost_ledger.sql",
    "0020_ai_savings_ledger.sql",
    "0021_ai_attribution_ledger.sql",
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
            for version in SCHEMA_MIGRATIONS:
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
    def commit_usage_batch(
        self,
        actor: ActorContext,
        records: tuple[Mapping[str, object], ...],
        events: tuple[PlatformEvent, ...],
    ) -> tuple[Mapping[str, object], ...]:
        """Commit new AI usage, CloudEvents, and outbox rows in one transaction."""

        prepared = prepare_ai_usage_writes(actor, records, events)
        with self._connect() as connection:
            lock_names = sorted(
                {
                    name
                    for item in prepared
                    for name in (
                        f"iip.ai-usage\x1f{item.tenant_id}\x1f{item.deduplication_key}",
                        f"iip.ai-usage-id\x1f{item.tenant_id}\x1f{item.usage_record_id}",
                    )
                }
            )
            for name in lock_names:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (name,),
                )

            stored: dict[tuple[str, str], Mapping[str, object]] = {}
            new_items = []
            for item in prepared:
                row = connection.execute(
                    """
                    SELECT usage_record_id, deduplication_key,
                           document_hash, document
                    FROM iip.ai_usage_records
                    WHERE tenant_id = %s
                      AND (deduplication_key = %s OR usage_record_id = %s)
                    FOR UPDATE
                    """,
                    (
                        item.tenant_id,
                        item.deduplication_key,
                        item.usage_record_id,
                    ),
                ).fetchone()
                if row is None:
                    new_items.append(item)
                    continue
                if (
                    row["usage_record_id"] != item.usage_record_id
                    or row["deduplication_key"] != item.deduplication_key
                    or row["document_hash"] != item.document_hash
                ):
                    raise PersistenceError("storage.conflict")
                stored[(item.tenant_id, item.deduplication_key)] = row["document"]

            for item in new_items:
                connection.execute(
                    """
                    INSERT INTO iip.ai_usage_records (
                        tenant_id, usage_record_id, deduplication_key,
                        document_hash, provider, model_id, service_name,
                        invocation_started_at, recorded_at, document
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        item.tenant_id,
                        item.usage_record_id,
                        item.deduplication_key,
                        item.document_hash,
                        item.provider,
                        item.model_id,
                        item.service_name,
                        item.invocation_started_at,
                        item.recorded_at,
                        Jsonb(dict(item.document)),
                    ),
                )
                event_offset = self._append_event(connection, item.event)
                connection.execute(
                    """
                    INSERT INTO iip.event_outbox (tenant_id, event_offset)
                    VALUES (%s, %s)
                    """,
                    (item.tenant_id, event_offset),
                )
                stored[(item.tenant_id, item.deduplication_key)] = item.document

            return tuple(
                stored[(item.tenant_id, item.deduplication_key)]
                for item in prepared
            )

    @_translate_database_errors
    def register_attribution_policy(
        self,
        actor: ActorContext,
        policy: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Idempotently register one immutable tenant attribution snapshot."""

        prepared = prepare_ai_attribution_policy(actor, policy)
        with self._connect() as connection:
            for name in sorted(
                (
                    "iip.ai-attribution-policy-id\x1f"
                    f"{prepared.tenant_id}\x1f{prepared.policy_id}",
                    "iip.ai-attribution-policy-version\x1f"
                    f"{prepared.tenant_id}\x1f{prepared.policy_version}",
                )
            ):
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (name,),
                )
            row = connection.execute(
                """
                SELECT policy_id, policy_version, document_hash, document
                FROM iip.ai_attribution_policies
                WHERE tenant_id = %s
                  AND (policy_id = %s OR policy_version = %s)
                FOR UPDATE
                """,
                (
                    prepared.tenant_id,
                    prepared.policy_id,
                    prepared.policy_version,
                ),
            ).fetchone()
            if row is not None:
                if (
                    row["policy_id"] != prepared.policy_id
                    or row["policy_version"] != prepared.policy_version
                    or row["document_hash"] != prepared.document_hash
                ):
                    raise PersistenceError("storage.conflict")
                return row["document"]
            connection.execute(
                """
                INSERT INTO iip.ai_attribution_policies (
                    tenant_id, policy_id, policy_version, document_hash,
                    source_hash, published_at, document
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    prepared.tenant_id,
                    prepared.policy_id,
                    prepared.policy_version,
                    prepared.document_hash,
                    prepared.source_hash,
                    prepared.published_at,
                    Jsonb(dict(prepared.document)),
                ),
            )
            return prepared.document

    @_translate_database_errors
    def list_usage_without_attribution(
        self,
        actor: ActorContext,
        policy_id: str,
        engine_version: str,
        *,
        limit: int = 100,
    ) -> tuple[Mapping[str, object], ...]:
        """List bounded exact-tenant usage not resolved by this generation."""

        validate_ai_attribution_actor(
            actor,
            policy_id=policy_id,
            engine_version=engine_version,
            limit=limit,
        )
        with self._connect() as connection:
            policy_exists = connection.execute(
                """
                SELECT 1 FROM iip.ai_attribution_policies
                WHERE tenant_id = %s AND policy_id = %s
                """,
                (actor.tenant_id, policy_id),
            ).fetchone()
            if policy_exists is None:
                raise PersistenceError("storage.request.invalid")
            rows = connection.execute(
                """
                SELECT usage.document
                FROM iip.ai_usage_records AS usage
                WHERE usage.tenant_id = %s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM iip.ai_usage_attributions AS attribution
                      WHERE attribution.tenant_id = usage.tenant_id
                        AND attribution.usage_record_id = usage.usage_record_id
                        AND attribution.policy_id = %s
                        AND attribution.engine_version = %s
                  )
                ORDER BY usage.invocation_started_at, usage.usage_record_id
                LIMIT %s
                """,
                (actor.tenant_id, policy_id, engine_version, limit),
            ).fetchall()
        return tuple(row["document"] for row in rows)

    @_translate_database_errors
    def commit_usage_attribution_batch(
        self,
        actor: ActorContext,
        records: tuple[Mapping[str, object], ...],
        events: tuple[PlatformEvent, ...],
    ) -> tuple[Mapping[str, object], ...]:
        """Commit attribution facts, CloudEvents, and outbox rows atomically."""

        prepared = prepare_ai_attribution_writes(actor, records, events)
        with self._connect() as connection:
            for name in sorted(
                {
                    "iip.ai-attribution\x1f"
                    f"{item.tenant_id}\x1f{item.usage_record_id}\x1f"
                    f"{item.policy_id}\x1f{item.engine_version}"
                    for item in prepared
                }
            ):
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (name,),
                )
            stored: dict[tuple[str, str], Mapping[str, object]] = {}
            new_items = []
            for item in prepared:
                policy = connection.execute(
                    """
                    SELECT policy_version, source_hash, document
                    FROM iip.ai_attribution_policies
                    WHERE tenant_id = %s AND policy_id = %s
                    """,
                    (item.tenant_id, item.policy_id),
                ).fetchone()
                usage = connection.execute(
                    """
                    SELECT document FROM iip.ai_usage_records
                    WHERE tenant_id = %s AND usage_record_id = %s
                    """,
                    (item.tenant_id, item.usage_record_id),
                ).fetchone()
                if (
                    policy is None
                    or usage is None
                    or policy["policy_version"] != item.policy_version
                    or policy["source_hash"] != item.policy_source_hash
                ):
                    raise PersistenceError("storage.request.invalid")
                try:
                    validate_ai_attribution_source_binding(
                        item.document,
                        policy["document"],
                        usage["document"],
                    )
                except InvalidAiAttributionInputError:
                    raise PersistenceError("storage.request.invalid") from None
                row = connection.execute(
                    """
                    SELECT attribution_record_id, document_hash, document
                    FROM iip.ai_usage_attributions
                    WHERE tenant_id = %s
                      AND (
                          attribution_record_id = %s
                          OR (
                              usage_record_id = %s
                              AND policy_id = %s
                              AND engine_version = %s
                          )
                      )
                    FOR UPDATE
                    """,
                    (
                        item.tenant_id,
                        item.attribution_record_id,
                        item.usage_record_id,
                        item.policy_id,
                        item.engine_version,
                    ),
                ).fetchone()
                if row is None:
                    new_items.append(item)
                    continue
                if (
                    row["attribution_record_id"] != item.attribution_record_id
                    or row["document_hash"] != item.document_hash
                ):
                    raise PersistenceError("storage.conflict")
                stored[(item.tenant_id, item.attribution_record_id)] = row["document"]

            for item in new_items:
                connection.execute(
                    """
                    INSERT INTO iip.ai_usage_attributions (
                        tenant_id, attribution_record_id, usage_record_id,
                        policy_id, policy_version, policy_source_hash,
                        engine_version, document_hash, status,
                        application_id, team_id, effective_at, resolved_at,
                        document
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        item.tenant_id,
                        item.attribution_record_id,
                        item.usage_record_id,
                        item.policy_id,
                        item.policy_version,
                        item.policy_source_hash,
                        item.engine_version,
                        item.document_hash,
                        item.status,
                        item.application_id,
                        item.team_id,
                        item.effective_at,
                        item.resolved_at,
                        Jsonb(dict(item.document)),
                    ),
                )
                event_offset = self._append_event(connection, item.event)
                connection.execute(
                    """
                    INSERT INTO iip.event_outbox (tenant_id, event_offset)
                    VALUES (%s, %s)
                    """,
                    (item.tenant_id, event_offset),
                )
                stored[(item.tenant_id, item.attribution_record_id)] = item.document

            return tuple(
                stored[(item.tenant_id, item.attribution_record_id)]
                for item in prepared
            )

    @_translate_database_errors
    def register_price_catalog(
        self,
        actor: ActorContext,
        catalog: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Idempotently register one immutable tenant price catalog."""

        prepared = prepare_ai_price_catalog(actor, catalog)
        with self._connect() as connection:
            lock_names = sorted(
                (
                    "iip.ai-price-catalog-id\x1f"
                    f"{prepared.tenant_id}\x1f{prepared.catalog_id}",
                    "iip.ai-price-catalog-version\x1f"
                    f"{prepared.tenant_id}\x1f{prepared.catalog_version}",
                )
            )
            for name in lock_names:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (name,),
                )
            row = connection.execute(
                """
                SELECT catalog_id, catalog_version, document_hash, document
                FROM iip.ai_price_catalogs
                WHERE tenant_id = %s
                  AND (catalog_id = %s OR catalog_version = %s)
                FOR UPDATE
                """,
                (
                    prepared.tenant_id,
                    prepared.catalog_id,
                    prepared.catalog_version,
                ),
            ).fetchone()
            if row is not None:
                if (
                    row["catalog_id"] != prepared.catalog_id
                    or row["catalog_version"] != prepared.catalog_version
                    or row["document_hash"] != prepared.document_hash
                ):
                    raise PersistenceError("storage.conflict")
                return row["document"]
            connection.execute(
                """
                INSERT INTO iip.ai_price_catalogs (
                    tenant_id, catalog_id, catalog_version, document_hash,
                    source_hash, published_at, document
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    prepared.tenant_id,
                    prepared.catalog_id,
                    prepared.catalog_version,
                    prepared.document_hash,
                    prepared.source_hash,
                    prepared.published_at,
                    Jsonb(dict(prepared.document)),
                ),
            )
            return prepared.document

    @_translate_database_errors
    def list_usage_without_cost(
        self,
        actor: ActorContext,
        catalog_id: str,
        engine_version: str,
        *,
        limit: int = 100,
    ) -> tuple[Mapping[str, object], ...]:
        validate_ai_cost_actor(
            actor,
            catalog_id=catalog_id,
            engine_version=engine_version,
            limit=limit,
        )
        with self._connect() as connection:
            catalog_exists = connection.execute(
                """
                SELECT 1
                FROM iip.ai_price_catalogs
                WHERE tenant_id = %s AND catalog_id = %s
                """,
                (actor.tenant_id, catalog_id),
            ).fetchone()
            if catalog_exists is None:
                raise PersistenceError("storage.request.invalid")
            rows = connection.execute(
                """
                SELECT usage.document
                FROM iip.ai_usage_records AS usage
                WHERE usage.tenant_id = %s
                  AND NOT EXISTS (
                      SELECT 1
                      FROM iip.ai_cost_records AS cost
                      WHERE cost.tenant_id = usage.tenant_id
                        AND cost.usage_record_id = usage.usage_record_id
                        AND cost.catalog_id = %s
                        AND cost.engine_version = %s
                  )
                ORDER BY usage.invocation_started_at, usage.usage_record_id
                LIMIT %s
                """,
                (actor.tenant_id, catalog_id, engine_version, limit),
            ).fetchall()
        return tuple(row["document"] for row in rows)

    @_translate_database_errors
    def commit_cost_batch(
        self,
        actor: ActorContext,
        records: tuple[Mapping[str, object], ...],
        events: tuple[PlatformEvent, ...],
    ) -> tuple[Mapping[str, object], ...]:
        """Commit cost facts, CloudEvents, and outbox rows atomically."""

        prepared = prepare_ai_cost_writes(actor, records, events)
        with self._connect() as connection:
            lock_names = sorted(
                {
                    "iip.ai-cost\x1f"
                    f"{item.tenant_id}\x1f{item.usage_record_id}\x1f"
                    f"{item.catalog_id}\x1f{item.engine_version}"
                    for item in prepared
                }
            )
            for name in lock_names:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (name,),
                )

            stored: dict[tuple[str, str], Mapping[str, object]] = {}
            new_items = []
            for item in prepared:
                catalog = connection.execute(
                    """
                    SELECT catalog_version, source_hash,
                           document->'spec'->'source'->>'kind' AS source_kind,
                           document->'spec'->>'currency' AS currency,
                           (document->'spec'->>'currencyScale')::smallint
                               AS currency_scale
                    FROM iip.ai_price_catalogs
                    WHERE tenant_id = %s AND catalog_id = %s
                    """,
                    (item.tenant_id, item.catalog_id),
                ).fetchone()
                usage = connection.execute(
                    """
                    SELECT document FROM iip.ai_usage_records
                    WHERE tenant_id = %s AND usage_record_id = %s
                    """,
                    (item.tenant_id, item.usage_record_id),
                ).fetchone()
                if (
                    catalog is None
                    or usage is None
                    or catalog["catalog_version"] != item.catalog_version
                    or catalog["source_hash"] != item.catalog_source_hash
                    or (
                        "test-fixture-pricing" in item.warnings
                    ) != (catalog["source_kind"] == "test-fixture")
                    or (
                        item.cost_status == "priced"
                        and (
                            item.currency != catalog["currency"]
                            or item.currency_scale != catalog["currency_scale"]
                        )
                    )
                ):
                    raise PersistenceError("storage.request.invalid")
                validate_ai_cost_usage_binding(item, usage["document"])
                row = connection.execute(
                    """
                    SELECT cost_record_id, document_hash, document
                    FROM iip.ai_cost_records
                    WHERE tenant_id = %s
                      AND (
                          cost_record_id = %s
                          OR (
                              usage_record_id = %s
                              AND catalog_id = %s
                              AND engine_version = %s
                          )
                      )
                    FOR UPDATE
                    """,
                    (
                        item.tenant_id,
                        item.cost_record_id,
                        item.usage_record_id,
                        item.catalog_id,
                        item.engine_version,
                    ),
                ).fetchone()
                if row is None:
                    new_items.append(item)
                    continue
                if (
                    row["cost_record_id"] != item.cost_record_id
                    or row["document_hash"] != item.document_hash
                ):
                    raise PersistenceError("storage.conflict")
                stored[(item.tenant_id, item.cost_record_id)] = row["document"]

            for item in new_items:
                connection.execute(
                    """
                    INSERT INTO iip.ai_cost_records (
                        tenant_id, cost_record_id, usage_record_id,
                        catalog_id, catalog_version, catalog_source_hash,
                        engine_version,
                        document_hash, cost_status, currency, currency_scale,
                        total_subunits, calculated_at, document
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        item.tenant_id,
                        item.cost_record_id,
                        item.usage_record_id,
                        item.catalog_id,
                        item.catalog_version,
                        item.catalog_source_hash,
                        item.engine_version,
                        item.document_hash,
                        item.cost_status,
                        item.currency,
                        item.currency_scale,
                        item.total_subunits,
                        item.calculated_at,
                        Jsonb(dict(item.document)),
                    ),
                )
                event_offset = self._append_event(connection, item.event)
                connection.execute(
                    """
                    INSERT INTO iip.event_outbox (tenant_id, event_offset)
                    VALUES (%s, %s)
                    """,
                    (item.tenant_id, event_offset),
                )
                stored[(item.tenant_id, item.cost_record_id)] = item.document

            return tuple(
                stored[(item.tenant_id, item.cost_record_id)]
                for item in prepared
            )

    @_translate_database_errors
    def list_ai_savings_cohort(
        self,
        actor: ActorContext,
        query: AiSavingsCohortQuery,
    ) -> tuple[tuple[Mapping[str, object], Mapping[str, object] | None], ...]:
        """Read exact-scope successful usage with one requested calculation."""

        validate_ai_savings_query(actor, query)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT usage.document AS usage_document,
                       cost.document AS cost_document
                FROM iip.ai_usage_records AS usage
                LEFT JOIN iip.ai_cost_records AS cost
                  ON cost.tenant_id = usage.tenant_id
                 AND cost.usage_record_id = usage.usage_record_id
                 AND cost.catalog_id = %s
                 AND cost.engine_version = %s
                WHERE usage.tenant_id = %s
                  AND usage.provider = %s
                  AND usage.model_id = %s
                  AND usage.service_name = %s
                  AND usage.document->'spec'->'invocation'->>'region' = %s
                  AND usage.document->'spec'->'invocation'->>'outcome' = 'success'
                  AND usage.document->'spec'->'attribution'
                        ->>'deploymentEnvironment' = %s
                  AND usage.invocation_started_at >= %s
                  AND usage.invocation_started_at < %s
                ORDER BY usage.invocation_started_at, usage.usage_record_id
                LIMIT %s
                """,
                (
                    query.catalog_id,
                    query.engine_version,
                    actor.tenant_id,
                    query.provider,
                    query.model_id,
                    query.service_name,
                    query.region,
                    query.deployment_environment,
                    query.start,
                    query.end,
                    query.limit,
                ),
            ).fetchall()
        return tuple(
            (row["usage_document"], row["cost_document"])
            for row in rows
        )

    @_translate_database_errors
    def list_ai_allocation_rows(
        self,
        actor: ActorContext,
        query: AiAllocationLedgerQuery,
    ) -> tuple[
        tuple[
            Mapping[str, object],
            Mapping[str, object] | None,
            Mapping[str, object] | None,
        ],
        ...,
    ]:
        """Read a tenant interval with exact attribution and cost generations."""

        validate_ai_allocation_ledger_query(actor, query)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT usage.document AS usage_document,
                       attribution.document AS attribution_document,
                       cost.document AS cost_document
                FROM iip.ai_usage_records AS usage
                LEFT JOIN iip.ai_usage_attributions AS attribution
                  ON attribution.tenant_id = usage.tenant_id
                 AND attribution.usage_record_id = usage.usage_record_id
                 AND attribution.policy_id = %s
                 AND attribution.engine_version = %s
                LEFT JOIN iip.ai_cost_records AS cost
                  ON cost.tenant_id = usage.tenant_id
                 AND cost.usage_record_id = usage.usage_record_id
                 AND cost.catalog_id = %s
                 AND cost.engine_version = %s
                WHERE usage.tenant_id = %s
                  AND usage.invocation_started_at >= %s
                  AND usage.invocation_started_at < %s
                ORDER BY usage.invocation_started_at, usage.usage_record_id
                LIMIT %s
                """,
                (
                    query.policy_id,
                    query.attribution_engine_version,
                    query.catalog_id,
                    query.cost_engine_version,
                    actor.tenant_id,
                    query.start,
                    query.end,
                    query.limit,
                ),
            ).fetchall()
        return tuple(
            (
                row["usage_document"],
                row["attribution_document"],
                row["cost_document"],
            )
            for row in rows
        )

    @_translate_database_errors
    def commit_ai_savings_batch(
        self,
        actor: ActorContext,
        findings: tuple[Mapping[str, object], ...],
        events: tuple[PlatformEvent, ...],
    ) -> tuple[Mapping[str, object], ...]:
        """Commit source-bound savings findings, events, and outbox atomically."""

        prepared = prepare_ai_savings_writes(actor, findings, events)
        with self._connect() as connection:
            for item in prepared:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"iip.ai-savings\x1f{item.tenant_id}\x1f{item.finding_id}",),
                )
            stored: dict[tuple[str, str], Mapping[str, object]] = {}
            new_items = []
            for item in prepared:
                usage_rows = connection.execute(
                    """
                    SELECT document
                    FROM iip.ai_usage_records
                    WHERE tenant_id = %s
                      AND provider = %s
                      AND model_id = %s
                      AND service_name = %s
                      AND document->'spec'->'invocation'->>'region' = %s
                      AND document->'spec'->'invocation'->>'outcome' = 'success'
                      AND document->'spec'->'attribution'
                            ->>'deploymentEnvironment' = %s
                      AND invocation_started_at >= %s
                      AND invocation_started_at < %s
                    ORDER BY invocation_started_at, usage_record_id
                    FOR SHARE
                    """,
                    (
                        item.tenant_id,
                        item.provider,
                        item.model_id,
                        item.service_name,
                        item.region,
                        item.deployment_environment,
                        item.baseline_start,
                        item.current_end,
                    ),
                ).fetchall()
                usage_documents = tuple(row["document"] for row in usage_rows)
                usage_ids = {
                    document["metadata"]["id"]
                    for document in usage_documents
                }
                if usage_ids != set(item.usage_record_ids):
                    raise PersistenceError("storage.request.invalid")
                cost_rows = connection.execute(
                    """
                    SELECT document
                    FROM iip.ai_cost_records
                    WHERE tenant_id = %s
                      AND cost_record_id = ANY(%s)
                    ORDER BY cost_record_id
                    FOR SHARE
                    """,
                    (item.tenant_id, list(item.cost_record_ids)),
                ).fetchall()
                cost_documents = tuple(row["document"] for row in cost_rows)
                if len(cost_documents) != len(item.cost_record_ids):
                    raise PersistenceError("storage.request.invalid")
                try:
                    validate_context_growth_source_binding(
                        item.document,
                        usage_documents,
                        cost_documents,
                    )
                except InvalidAiSavingsInputError:
                    raise PersistenceError("storage.request.invalid") from None
                row = connection.execute(
                    """
                    SELECT document_hash, document
                    FROM iip.ai_savings_findings
                    WHERE tenant_id = %s AND finding_id = %s
                    FOR UPDATE
                    """,
                    (item.tenant_id, item.finding_id),
                ).fetchone()
                if row is not None:
                    if row["document_hash"] != item.document_hash:
                        raise PersistenceError("storage.conflict")
                    stored[(item.tenant_id, item.finding_id)] = row["document"]
                    continue
                new_items.append(item)

            for item in new_items:
                connection.execute(
                    """
                    INSERT INTO iip.ai_savings_findings (
                        tenant_id, finding_id, document_hash, rule_id,
                        rule_version, severity, provider, model_id, region,
                        service_name, deployment_environment, baseline_start,
                        baseline_end, current_start, current_end, evaluated_at,
                        document
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    """,
                    (
                        item.tenant_id,
                        item.finding_id,
                        item.document_hash,
                        item.rule_id,
                        item.rule_version,
                        item.severity,
                        item.provider,
                        item.model_id,
                        item.region,
                        item.service_name,
                        item.deployment_environment,
                        item.baseline_start,
                        item.baseline_end,
                        item.current_start,
                        item.current_end,
                        item.evaluated_at,
                        Jsonb(dict(item.document)),
                    ),
                )
                event_offset = self._append_event(connection, item.event)
                connection.execute(
                    """
                    INSERT INTO iip.event_outbox (tenant_id, event_offset)
                    VALUES (%s, %s)
                    """,
                    (item.tenant_id, event_offset),
                )
                stored[(item.tenant_id, item.finding_id)] = item.document
            return tuple(
                stored[(item.tenant_id, item.finding_id)]
                for item in prepared
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
                      AND quarantined_at IS NULL
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
                  AND quarantined_at IS NULL
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
                  AND quarantined_at IS NULL
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
    def quarantine_outbox(
        self,
        tenant_id: str,
        worker_id: str,
        message_id: int,
        error_code: str,
    ) -> bool:
        self._validate_error_code(error_code)
        with self._connect() as connection:
            row = connection.execute(
                """
                UPDATE iip.event_outbox
                SET claimed_by = NULL,
                    claim_expires_at = NULL,
                    quarantined_at = clock_timestamp(),
                    last_error_code = %s
                WHERE tenant_id = %s
                  AND outbox_id = %s
                  AND claimed_by = %s
                  AND claim_expires_at > clock_timestamp()
                  AND published_at IS NULL
                  AND quarantined_at IS NULL
                RETURNING outbox_id
                """,
                (error_code, tenant_id, message_id, worker_id),
            ).fetchone()
        return row is not None

    @_translate_database_errors
    def get_event_delivery_state(
        self,
        tenant_id: str,
        *,
        quarantine_limit: int = 50,
    ) -> EventDeliveryState:
        if (
            isinstance(quarantine_limit, bool)
            or not isinstance(quarantine_limit, int)
            or not 1 <= quarantine_limit <= 50
        ):
            raise ValueError("quarantine_limit must be between 1 and 50")
        with self._connect() as connection:
            summary = connection.execute(
                """
                SELECT
                    count(*) FILTER (
                        WHERE published_at IS NULL AND quarantined_at IS NULL
                    ) AS pending_events,
                    count(*) FILTER (
                        WHERE published_at IS NULL
                          AND quarantined_at IS NULL
                          AND claim_expires_at > clock_timestamp()
                    ) AS in_flight_events,
                    count(*) FILTER (
                        WHERE published_at IS NULL
                          AND quarantined_at IS NULL
                          AND last_error_code IS NOT NULL
                    ) AS retrying_events,
                    count(*) FILTER (
                        WHERE quarantined_at IS NOT NULL
                    ) AS quarantined_events,
                    min(created_at) FILTER (
                        WHERE published_at IS NULL AND quarantined_at IS NULL
                    ) AS oldest_pending_event_recorded_at
                FROM iip.event_outbox
                WHERE tenant_id = %s
                """,
                (tenant_id,),
            ).fetchone()
            rows = connection.execute(
                """
                SELECT outbox.outbox_id, outbox.tenant_id, outbox.attempts,
                       outbox.quarantined_at, outbox.last_error_code,
                       events.document->>'id' AS event_id,
                       events.document->>'source' AS event_source,
                       events.document->>'type' AS event_type,
                       events.document->>'subject' AS subject
                FROM iip.event_outbox AS outbox
                JOIN iip.event_log AS events
                  ON events.tenant_id = outbox.tenant_id
                 AND events.event_offset = outbox.event_offset
                WHERE outbox.tenant_id = %s
                  AND outbox.quarantined_at IS NOT NULL
                ORDER BY outbox.quarantined_at DESC, outbox.outbox_id DESC
                LIMIT %s
                """,
                (tenant_id, quarantine_limit),
            ).fetchall()
        return EventDeliveryState(
            tenant_id=tenant_id,
            pending_events=summary["pending_events"],
            in_flight_events=summary["in_flight_events"],
            retrying_events=summary["retrying_events"],
            quarantined_events=summary["quarantined_events"],
            oldest_pending_event_recorded_at=(
                self._rfc3339(summary["oldest_pending_event_recorded_at"])
                if summary["oldest_pending_event_recorded_at"] is not None
                else None
            ),
            quarantined=tuple(
                QuarantinedOutboxMessage(
                    message_id=row["outbox_id"],
                    tenant_id=row["tenant_id"],
                    event_id=row["event_id"],
                    event_source=row["event_source"],
                    event_type=row["event_type"],
                    subject=row["subject"],
                    attempts=row["attempts"],
                    quarantined_at=self._rfc3339(row["quarantined_at"]),
                    last_error_code=row["last_error_code"],
                )
                for row in rows
            ),
        )

    @_translate_database_errors
    def get_event_delivery_slo_state(
        self,
        tenant_id: str,
        *,
        window_start: str,
        window_end: str,
        maturity_cutoff: str,
        latency_objective_seconds: int,
    ) -> EventDeliverySloState:
        if (
            isinstance(latency_objective_seconds, bool)
            or not isinstance(latency_objective_seconds, int)
            or not 1 <= latency_objective_seconds <= 86_400
        ):
            raise ValueError("latency_objective_seconds is invalid")
        with self._connect() as connection:
            row = connection.execute(
                """
                WITH cohort AS (
                    SELECT created_at, published_at, quarantined_at,
                           created_at <= %s::timestamptz AS eligible,
                           created_at + make_interval(secs => %s) AS deadline
                    FROM iip.event_outbox
                    WHERE tenant_id = %s
                      AND created_at >= %s::timestamptz
                      AND created_at <= %s::timestamptz
                )
                SELECT
                    count(*) AS created_events,
                    count(*) FILTER (WHERE NOT eligible) AS immature_events,
                    count(*) FILTER (WHERE eligible) AS eligible_events,
                    count(*) FILTER (
                        WHERE eligible
                          AND published_at IS NOT NULL
                          AND published_at <= deadline
                    ) AS within_objective_events,
                    count(*) FILTER (
                        WHERE eligible
                          AND published_at IS NOT NULL
                          AND published_at > deadline
                    ) AS late_delivered_events,
                    count(*) FILTER (
                        WHERE eligible AND published_at IS NULL
                    ) AS undelivered_events,
                    count(*) FILTER (
                        WHERE eligible
                          AND published_at IS NULL
                          AND quarantined_at IS NOT NULL
                    ) AS quarantined_events
                FROM cohort
                """,
                (
                    maturity_cutoff,
                    latency_objective_seconds,
                    tenant_id,
                    window_start,
                    window_end,
                ),
            ).fetchone()
        assert row is not None
        return EventDeliverySloState(
            tenant_id=tenant_id,
            window_start=window_start,
            window_end=window_end,
            maturity_cutoff=maturity_cutoff,
            created_events=row["created_events"],
            immature_events=row["immature_events"],
            eligible_events=row["eligible_events"],
            within_objective_events=row["within_objective_events"],
            late_delivered_events=row["late_delivered_events"],
            undelivered_events=row["undelivered_events"],
            quarantined_events=row["quarantined_events"],
        )

    @_translate_database_errors
    def get_quarantined_outbox(
        self,
        tenant_id: str,
        message_id: int,
    ) -> Optional[QuarantinedOutboxMessage]:
        if (
            isinstance(message_id, bool)
            or not isinstance(message_id, int)
            or message_id < 1
        ):
            raise ValueError("message_id must be a positive integer")
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT outbox.outbox_id, outbox.tenant_id, outbox.attempts,
                       outbox.quarantined_at, outbox.last_error_code,
                       events.document->>'id' AS event_id,
                       events.document->>'source' AS event_source,
                       events.document->>'type' AS event_type,
                       events.document->>'subject' AS subject
                FROM iip.event_outbox AS outbox
                JOIN iip.event_log AS events
                  ON events.tenant_id = outbox.tenant_id
                 AND events.event_offset = outbox.event_offset
                WHERE outbox.tenant_id = %s
                  AND outbox.outbox_id = %s
                  AND outbox.quarantined_at IS NOT NULL
                """,
                (tenant_id, message_id),
            ).fetchone()
        return self._quarantined_message_from_row(row) if row is not None else None

    @_translate_database_errors
    def requeue_quarantined_outbox(
        self,
        tenant_id: str,
        message_id: int,
        *,
        expected_event_id: str,
        expected_quarantined_at: str,
        expected_attempts: int,
    ) -> bool:
        if (
            isinstance(message_id, bool)
            or not isinstance(message_id, int)
            or message_id < 1
            or not isinstance(expected_event_id, str)
            or not isinstance(expected_quarantined_at, str)
            or isinstance(expected_attempts, bool)
            or not isinstance(expected_attempts, int)
        ):
            raise ValueError("event delivery replay preconditions are invalid")
        with self._connect() as connection:
            row = connection.execute(
                """
                UPDATE iip.event_outbox AS outbox
                SET attempts = 0,
                    claimed_by = NULL,
                    claim_expires_at = NULL,
                    available_at = clock_timestamp(),
                    last_error_code = NULL,
                    quarantined_at = NULL
                FROM iip.event_log AS events
                WHERE outbox.tenant_id = %s
                  AND outbox.outbox_id = %s
                  AND outbox.quarantined_at = %s::timestamptz
                  AND outbox.attempts = %s
                  AND outbox.published_at IS NULL
                  AND events.tenant_id = outbox.tenant_id
                  AND events.event_offset = outbox.event_offset
                  AND events.document->>'id' = %s
                RETURNING outbox.outbox_id
                """,
                (
                    tenant_id,
                    message_id,
                    expected_quarantined_at,
                    expected_attempts,
                    expected_event_id,
                ),
            ).fetchone()
        return row is not None

    @classmethod
    def _quarantined_message_from_row(
        cls,
        row: Mapping[str, object],
    ) -> QuarantinedOutboxMessage:
        return QuarantinedOutboxMessage(
            message_id=row["outbox_id"],  # type: ignore[arg-type]
            tenant_id=row["tenant_id"],  # type: ignore[arg-type]
            event_id=row["event_id"],  # type: ignore[arg-type]
            event_source=row["event_source"],  # type: ignore[arg-type]
            event_type=row["event_type"],  # type: ignore[arg-type]
            subject=row["subject"],  # type: ignore[arg-type]
            attempts=row["attempts"],  # type: ignore[arg-type]
            quarantined_at=cls._rfc3339(row["quarantined_at"]),
            last_error_code=row["last_error_code"],  # type: ignore[arg-type]
        )

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
    def get_source_ingestion_state(
        self, tenant_id: str, source_id: str
    ) -> Optional[SourceIngestionState]:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT checkpoint.tenant_id,
                       checkpoint.source_id,
                       checkpoint.stream_id,
                       checkpoint.sequence,
                       checkpoint.committed_at,
                       latest.observed_at AS latest_observed_at,
                       latest.recorded_at AS latest_recorded_at,
                       observations.accepted_observation_count,
                       pending.pending_event_count,
                       pending.oldest_pending_event_recorded_at
                FROM iip.source_checkpoints AS checkpoint
                LEFT JOIN LATERAL (
                    SELECT observed_at, recorded_at
                    FROM iip.resource_observations
                    WHERE tenant_id = checkpoint.tenant_id
                      AND observation_source_id = checkpoint.source_id
                      AND disposition = 'accepted'
                    ORDER BY observation_offset DESC
                    LIMIT 1
                ) AS latest ON true
                CROSS JOIN LATERAL (
                    SELECT count(*)::bigint AS accepted_observation_count
                    FROM iip.resource_observations
                    WHERE tenant_id = checkpoint.tenant_id
                      AND observation_source_id = checkpoint.source_id
                      AND disposition = 'accepted'
                ) AS observations
                CROSS JOIN LATERAL (
                    SELECT count(*)::bigint AS pending_event_count,
                           min(outbox.created_at) AS oldest_pending_event_recorded_at
                    FROM iip.event_outbox AS outbox
                    JOIN iip.event_log AS events
                      ON events.tenant_id = outbox.tenant_id
                     AND events.event_offset = outbox.event_offset
                    WHERE outbox.tenant_id = checkpoint.tenant_id
                      AND outbox.published_at IS NULL
                      AND outbox.quarantined_at IS NULL
                      AND events.document->'data'->'observation'->>'sourceId'
                          = checkpoint.source_id
                ) AS pending
                WHERE checkpoint.tenant_id = %s
                  AND checkpoint.source_id = %s
                """,
                (tenant_id, source_id),
            ).fetchone()
        if row is None:
            return None
        return SourceIngestionState(
            tenant_id=row["tenant_id"],
            source_id=row["source_id"],
            stream_id=row["stream_id"],
            checkpoint_sequence=row["sequence"],
            checkpoint_committed_at=self._rfc3339(row["committed_at"]),
            latest_observed_at=(
                self._rfc3339(row["latest_observed_at"])
                if row["latest_observed_at"] is not None
                else None
            ),
            latest_recorded_at=(
                self._rfc3339(row["latest_recorded_at"])
                if row["latest_recorded_at"] is not None
                else None
            ),
            accepted_observation_count=row["accepted_observation_count"],
            pending_event_count=row["pending_event_count"],
            oldest_pending_event_recorded_at=(
                self._rfc3339(row["oldest_pending_event_recorded_at"])
                if row["oldest_pending_event_recorded_at"] is not None
                else None
            ),
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
            if (
                row is not None
                and row["stream_id"] == checkpoint.stream_id
                and row["sequence"] == checkpoint.sequence
                and row["checkpoint"] == checkpoint.checkpoint
                and row["provider_cursors"] == dict(checkpoint.provider_cursors)
            ):
                return
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
