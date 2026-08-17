"""PostgreSQL evidence, investigation, action, session, and audit adapter."""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime
from typing import Iterable, Mapping, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from iip.application.investigate import canonical_digest
from iip.application.ports import (
    ActionExecutionTransition,
    ActionWorkflowRecord,
    ActorContext,
    EvidenceRetentionState,
    InvestigationCompletionSloState,
    InvestigationJobClaim,
    PersistenceError,
    PluginInvocationClaim,
    TelemetryExportInstanceState,
    TelemetryExportSignalState,
    TelemetryExportSloSignalState,
    TelemetryExportSloState,
)


class PostgresOperationalStore:
    """Durable tenant-scoped operational documents behind application ports."""

    def __init__(self, database_url: str) -> None:
        if not isinstance(database_url, str) or not database_url.strip():
            raise ValueError("database_url must be a non-empty PostgreSQL connection string")
        self._database_url = database_url

    def _connect(self):
        return psycopg.connect(self._database_url, row_factory=dict_row)

    def record_telemetry_export_health(
        self,
        state: TelemetryExportInstanceState,
        *,
        expire_before: str,
        sample_expire_before: str,
    ) -> None:
        signals = [
            {
                "signal": signal.signal,
                "enabled": signal.enabled,
                "status": signal.status,
                "attempts": signal.attempts,
                "successes": signal.successes,
                "failures": signal.failures,
                "consecutiveFailures": signal.consecutive_failures,
                **(
                    {"lastAttemptAt": signal.last_attempt_at}
                    if signal.last_attempt_at is not None
                    else {}
                ),
                **(
                    {"lastSuccessAt": signal.last_success_at}
                    if signal.last_success_at is not None
                    else {}
                ),
                **(
                    {"lastFailureAt": signal.last_failure_at}
                    if signal.last_failure_at is not None
                    else {}
                ),
                **(
                    {"lastFailureCode": signal.last_failure_code}
                    if signal.last_failure_code is not None
                    else {}
                ),
            }
            for signal in state.signals
        ]
        try:
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM iip.telemetry_export_health WHERE last_reported_at < %s",
                    (expire_before,),
                )
                connection.execute(
                    "DELETE FROM iip.telemetry_export_health_samples WHERE observed_at < %s",
                    (sample_expire_before,),
                )
                connection.execute(
                    """
                    INSERT INTO iip.telemetry_export_health (
                        instance_id, component, started_at, last_reported_at, signals
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (instance_id) DO UPDATE SET
                        component = EXCLUDED.component,
                        started_at = EXCLUDED.started_at,
                        last_reported_at = EXCLUDED.last_reported_at,
                        signals = EXCLUDED.signals
                    WHERE iip.telemetry_export_health.started_at = EXCLUDED.started_at
                      AND iip.telemetry_export_health.last_reported_at <= EXCLUDED.last_reported_at
                    """,
                    (
                        state.instance_id,
                        state.component,
                        state.started_at,
                        state.last_reported_at,
                        Jsonb(signals),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO iip.telemetry_export_health_samples (
                        instance_id, component, started_at, observed_at, signals
                    ) VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (instance_id, observed_at) DO NOTHING
                    """,
                    (
                        state.instance_id,
                        state.component,
                        state.started_at,
                        state.last_reported_at,
                        Jsonb(signals),
                    ),
                )
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def list_telemetry_export_health(
        self,
        *,
        reported_since: str,
        limit: int,
    ) -> tuple[TelemetryExportInstanceState, ...]:
        if not 1 <= limit <= 1_001:
            raise PersistenceError("storage.input-invalid")
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT instance_id, component, started_at, last_reported_at, signals
                    FROM iip.telemetry_export_health
                    WHERE last_reported_at >= %s
                    ORDER BY component, instance_id
                    LIMIT %s
                    """,
                    (reported_since, limit),
                ).fetchall()
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None
        return tuple(self._telemetry_export_state(row) for row in rows)

    def retire_telemetry_export_health(self, instance_id: str) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    "DELETE FROM iip.telemetry_export_health WHERE instance_id = %s",
                    (instance_id,),
                )
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def get_telemetry_export_slo_state(
        self,
        *,
        window_start: str,
        window_end: str,
    ) -> TelemetryExportSloState:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    WITH before_window AS (
                        SELECT DISTINCT ON (instance_id)
                               instance_id, started_at, observed_at, signals
                        FROM iip.telemetry_export_health_samples
                        WHERE observed_at < %s::timestamptz
                        ORDER BY instance_id, observed_at DESC
                    ),
                    window_samples AS (
                        SELECT instance_id, started_at, observed_at, signals
                        FROM iip.telemetry_export_health_samples
                        WHERE observed_at >= %s::timestamptz
                          AND observed_at <= %s::timestamptz
                    ),
                    selected AS (
                        SELECT * FROM before_window
                        UNION ALL
                        SELECT * FROM window_samples
                    ),
                    expanded AS (
                        SELECT selected.instance_id,
                               selected.started_at,
                               selected.observed_at,
                               signal->>'signal' AS signal,
                               (signal->>'enabled')::boolean AS enabled,
                               (signal->>'attempts')::bigint AS attempts,
                               (signal->>'successes')::bigint AS successes,
                               (signal->>'failures')::bigint AS failures
                        FROM selected
                        CROSS JOIN LATERAL jsonb_array_elements(signals) AS signal
                    ),
                    sequenced AS (
                        SELECT *,
                               lag(attempts) OVER sequence AS prior_attempts,
                               lag(successes) OVER sequence AS prior_successes,
                               lag(failures) OVER sequence AS prior_failures
                        FROM expanded
                        WINDOW sequence AS (
                            PARTITION BY instance_id, signal ORDER BY observed_at
                        )
                    ),
                    deltas AS (
                        SELECT *,
                               CASE
                                   WHEN observed_at < %s::timestamptz THEN 0
                                   WHEN prior_attempts IS NOT NULL
                                       THEN attempts - prior_attempts
                                   WHEN started_at >= %s::timestamptz THEN attempts
                                   ELSE 0
                               END AS attempt_delta,
                               CASE
                                   WHEN observed_at < %s::timestamptz THEN 0
                                   WHEN prior_successes IS NOT NULL
                                       THEN successes - prior_successes
                                   WHEN started_at >= %s::timestamptz THEN successes
                                   ELSE 0
                               END AS success_delta,
                               CASE
                                   WHEN observed_at < %s::timestamptz THEN 0
                                   WHEN prior_failures IS NOT NULL
                                       THEN failures - prior_failures
                                   WHEN started_at >= %s::timestamptz THEN failures
                                   ELSE 0
                               END AS failure_delta
                        FROM sequenced
                    ),
                    signals(signal) AS (VALUES ('metrics'), ('traces')),
                    window_counts AS (
                        SELECT count(DISTINCT instance_id) AS observed_instances,
                               count(*) AS observed_samples
                        FROM window_samples
                    )
                    SELECT signals.signal,
                           window_counts.observed_instances,
                           window_counts.observed_samples,
                           count(deltas.instance_id) FILTER (
                               WHERE deltas.observed_at >= %s::timestamptz
                           ) AS signal_observations,
                           count(deltas.instance_id) FILTER (
                               WHERE deltas.observed_at >= %s::timestamptz
                                 AND deltas.enabled
                           ) AS enabled_observations,
                           coalesce(sum(deltas.attempt_delta) FILTER (
                               WHERE deltas.observed_at >= %s::timestamptz
                           ), 0) AS eligible_attempts,
                           coalesce(sum(deltas.success_delta) FILTER (
                               WHERE deltas.observed_at >= %s::timestamptz
                           ), 0) AS successful_attempts,
                           coalesce(sum(deltas.failure_delta) FILTER (
                               WHERE deltas.observed_at >= %s::timestamptz
                           ), 0) AS failed_attempts,
                           count(deltas.instance_id) FILTER (
                               WHERE deltas.observed_at >= %s::timestamptz
                                 AND (
                                     deltas.attempts <> deltas.successes + deltas.failures
                                     OR deltas.attempt_delta < 0
                                     OR deltas.success_delta < 0
                                     OR deltas.failure_delta < 0
                                     OR deltas.attempt_delta
                                        <> deltas.success_delta + deltas.failure_delta
                                 )
                           ) AS invalid_observations
                    FROM signals
                    CROSS JOIN window_counts
                    LEFT JOIN deltas ON deltas.signal = signals.signal
                    GROUP BY signals.signal,
                             window_counts.observed_instances,
                             window_counts.observed_samples
                    ORDER BY signals.signal
                    """,
                    (
                        window_start,
                        window_start,
                        window_end,
                        window_start,
                        window_start,
                        window_start,
                        window_start,
                        window_start,
                        window_start,
                        window_start,
                        window_start,
                        window_start,
                        window_start,
                        window_start,
                        window_start,
                    ),
                ).fetchall()
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None
        if (
            len(rows) != 2
            or any(row["invalid_observations"] for row in rows)
            or any(
                row["signal_observations"] != row["observed_samples"]
                for row in rows
            )
        ):
            raise PersistenceError("storage.corrupt")
        observed_instances = rows[0]["observed_instances"]
        observed_samples = rows[0]["observed_samples"]
        if any(
            row["observed_instances"] != observed_instances
            or row["observed_samples"] != observed_samples
            for row in rows
        ):
            raise PersistenceError("storage.corrupt")
        return TelemetryExportSloState(
            window_start=window_start,
            window_end=window_end,
            observed_instances=observed_instances,
            observed_samples=observed_samples,
            signals=tuple(
                TelemetryExportSloSignalState(
                    signal=row["signal"],
                    enabled_observations=row["enabled_observations"],
                    eligible_attempts=int(row["eligible_attempts"]),
                    successful_attempts=int(row["successful_attempts"]),
                    failed_attempts=int(row["failed_attempts"]),
                )
                for row in rows
            ),
        )

    @staticmethod
    def _telemetry_export_state(
        row: Mapping[str, object],
    ) -> TelemetryExportInstanceState:
        signals = row["signals"]
        if not isinstance(signals, list):
            raise PersistenceError("storage.corrupt")
        try:
            decoded = tuple(
                TelemetryExportSignalState(
                    signal=item["signal"],
                    enabled=item["enabled"],
                    status=item["status"],
                    attempts=item["attempts"],
                    successes=item["successes"],
                    failures=item["failures"],
                    consecutive_failures=item["consecutiveFailures"],
                    last_attempt_at=item.get("lastAttemptAt"),
                    last_success_at=item.get("lastSuccessAt"),
                    last_failure_at=item.get("lastFailureAt"),
                    last_failure_code=item.get("lastFailureCode"),
                )
                for item in signals
                if isinstance(item, Mapping)
            )
            if len(decoded) != len(signals):
                raise ValueError
            return TelemetryExportInstanceState(
                instance_id=str(row["instance_id"]),
                component=str(row["component"]),
                started_at=row["started_at"].isoformat().replace("+00:00", "Z"),
                last_reported_at=row["last_reported_at"].isoformat().replace(
                    "+00:00", "Z"
                ),
                signals=decoded,
            )
        except (KeyError, TypeError, ValueError):
            raise PersistenceError("storage.corrupt") from None

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
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"evidence-artifacts:{actor.tenant_id}",),
                )
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
            return (
                bytes(row["artifact"])
                if row is not None and row["artifact"] is not None
                else None
            )
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

    def evaluate_evidence_retention(
        self,
        tenant_id: str,
        evaluated_at: str,
        *,
        ephemeral_seconds: int,
        standard_seconds: int,
        extended_seconds: int,
        limit: int,
        expire: bool,
        policy_digest: str,
    ) -> EvidenceRetentionState:
        if (
            not isinstance(tenant_id, str)
            or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}", tenant_id) is None
            or not isinstance(expire, bool)
            or any(
                isinstance(value, bool) or not isinstance(value, int)
                for value in (
                    ephemeral_seconds,
                    standard_seconds,
                    extended_seconds,
                    limit,
                )
            )
            or not (
                3_600 <= ephemeral_seconds <= 2_592_000
                and 86_400 <= standard_seconds <= 31_536_000
                and 86_400 <= extended_seconds <= 315_360_000
                and ephemeral_seconds <= standard_seconds <= extended_seconds
            )
            or not 1 <= limit <= 1000
            or re.fullmatch(r"sha256:[a-f0-9]{64}", policy_digest) is None
        ):
            raise PersistenceError("storage.input-invalid")

        count_statement = """
            WITH classified AS (
                SELECT
                    document->'spec'->'handling'->>'retentionClass' AS class,
                    CASE
                        WHEN document->'spec'->'handling'->>'retentionClass'
                             = 'legal-hold' THEN NULL
                        ELSE COALESCE(
                            NULLIF(
                                document->'spec'->'handling'->>'expiresAt',
                                ''
                            )::timestamptz,
                            recorded_at + CASE
                                WHEN document->'spec'->'handling'->>'retentionClass'
                                     = 'ephemeral'
                                    THEN make_interval(secs => %s)
                                WHEN document->'spec'->'handling'->>'retentionClass'
                                     = 'standard'
                                    THEN make_interval(secs => %s)
                                WHEN document->'spec'->'handling'->>'retentionClass'
                                     = 'extended'
                                    THEN make_interval(secs => %s)
                                ELSE NULL
                            END
                        )
                    END AS effective_expiry
                FROM iip.evidence_artifacts
                WHERE tenant_id = %s AND artifact IS NOT NULL
            )
            SELECT
                count(*) AS stored,
                count(*) FILTER (WHERE class = 'legal-hold') AS legal_hold,
                count(*) FILTER (WHERE effective_expiry <= %s) AS eligible,
                count(*) FILTER (
                    WHERE class IS NULL OR class NOT IN (
                        'ephemeral', 'standard', 'extended', 'legal-hold'
                    )
                ) AS invalid
            FROM classified
        """
        count_parameters = (
            ephemeral_seconds,
            standard_seconds,
            extended_seconds,
            tenant_id,
            evaluated_at,
        )
        try:
            with self._connect() as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"evidence-artifacts:{tenant_id}",),
                )
                before = connection.execute(
                    count_statement, count_parameters
                ).fetchone()
                if before is None or int(before["invalid"]):
                    raise PersistenceError("storage.corrupt")
                eligible = int(before["eligible"])
                expired = 0
                audit_ref = None
                if expire and eligible:
                    updated = connection.execute(
                        """
                        WITH candidates AS (
                            SELECT evidence_id
                            FROM iip.evidence_artifacts
                            WHERE tenant_id = %s
                              AND artifact IS NOT NULL
                              AND document->'spec'->'handling'->>'retentionClass'
                                  IN ('ephemeral', 'standard', 'extended')
                              AND COALESCE(
                                  NULLIF(
                                      document->'spec'->'handling'->>'expiresAt',
                                      ''
                                  )::timestamptz,
                                  recorded_at + CASE
                                      WHEN document->'spec'->'handling'->>'retentionClass'
                                           = 'ephemeral'
                                          THEN make_interval(secs => %s)
                                      WHEN document->'spec'->'handling'->>'retentionClass'
                                           = 'standard'
                                          THEN make_interval(secs => %s)
                                      ELSE make_interval(secs => %s)
                                  END
                              ) <= %s
                            ORDER BY recorded_at, evidence_id
                            FOR UPDATE SKIP LOCKED
                            LIMIT %s
                        )
                        UPDATE iip.evidence_artifacts AS evidence
                        SET artifact = NULL, artifact_deleted_at = %s
                        FROM candidates
                        WHERE evidence.tenant_id = %s
                          AND evidence.evidence_id = candidates.evidence_id
                        RETURNING evidence.evidence_id
                        """,
                        (
                            tenant_id,
                            ephemeral_seconds,
                            standard_seconds,
                            extended_seconds,
                            evaluated_at,
                            limit,
                            evaluated_at,
                            tenant_id,
                        ),
                    ).fetchall()
                    expired = len(updated)
                    if expired:
                        audit = {
                            "apiVersion": "iip.internal/v1alpha1",
                            "kind": "EvidenceRetentionAudit",
                            "metadata": {
                                "tenantId": tenant_id,
                                "recordedAt": evaluated_at,
                            },
                            "spec": {
                                "policyDigest": policy_digest,
                                "expiredArtifacts": expired,
                                "remainingEligibleArtifacts": eligible - expired,
                            },
                        }
                        row = connection.execute(
                            """
                            INSERT INTO iip.audit_records (
                                tenant_id, category, document
                            ) VALUES (%s, 'evidence-retention-expired', %s)
                            RETURNING audit_offset
                            """,
                            (tenant_id, Jsonb(audit)),
                        ).fetchone()
                        if row is None:
                            raise PersistenceError("storage.unavailable")
                        audit_ref = (
                            f"audit://{tenant_id}/records/{row['audit_offset']}"
                        )
                return EvidenceRetentionState(
                    tenant_id=tenant_id,
                    evaluated_at=evaluated_at,
                    stored_artifacts=int(before["stored"]),
                    eligible_artifacts=eligible,
                    expired_artifacts=expired,
                    remaining_eligible_artifacts=eligible - expired,
                    legal_hold_artifacts=int(before["legal_hold"]),
                    audit_ref=audit_ref,
                )
        except PersistenceError:
            raise
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

    def enqueue_investigation_job(
        self,
        actor: ActorContext,
        investigation_id: str,
        request: Mapping[str, object],
        status: Mapping[str, object],
        *,
        max_outstanding_jobs_per_tenant: int = 1000,
    ) -> Mapping[str, object]:
        self._assert_tenant(actor, request)
        self._assert_tenant(actor, status)
        metadata = status.get("metadata")
        spec = status.get("spec")
        if not isinstance(metadata, Mapping) or not isinstance(spec, Mapping):
            raise PersistenceError("storage.input-invalid")
        if (
            isinstance(max_outstanding_jobs_per_tenant, bool)
            or not isinstance(max_outstanding_jobs_per_tenant, int)
            or not 1 <= max_outstanding_jobs_per_tenant <= 100_000
        ):
            raise PersistenceError("storage.input-invalid")
        request_digest = canonical_digest(request)
        try:
            with self._connect() as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (actor.tenant_id,),
                )
                existing = connection.execute(
                    """
                    SELECT request_digest, status_document AS document
                    FROM iip.investigation_jobs
                    WHERE tenant_id = %s AND investigation_id = %s
                    """,
                    (actor.tenant_id, investigation_id),
                ).fetchone()
                if existing is not None:
                    if existing["request_digest"] != request_digest:
                        raise PersistenceError("storage.conflict")
                    return dict(existing["document"])
                outstanding = connection.execute(
                    """
                    SELECT count(*) AS outstanding
                    FROM iip.investigation_jobs
                    WHERE tenant_id = %s
                      AND state IN ('queued', 'running', 'cancellation-requested')
                    """,
                    (actor.tenant_id,),
                ).fetchone()
                if (
                    outstanding is not None
                    and int(outstanding["outstanding"])
                    >= max_outstanding_jobs_per_tenant
                ):
                    raise PersistenceError("storage.capacity-exceeded")
                row = connection.execute(
                    """
                    INSERT INTO iip.investigation_jobs (
                        tenant_id, investigation_id, actor_id, actor_roles,
                        request_digest, request_document, status_document,
                        state, attempts, available_at, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'queued', 0, %s, %s, %s)
                    ON CONFLICT (tenant_id, investigation_id) DO NOTHING
                    RETURNING status_document AS document
                    """,
                    (
                        actor.tenant_id,
                        investigation_id,
                        actor.actor_id,
                        Jsonb(list(actor.roles)),
                        request_digest,
                        Jsonb(dict(request)),
                        Jsonb(dict(status)),
                        spec["availableAt"],
                        spec["queuedAt"],
                        metadata["updatedAt"],
                    ),
                ).fetchone()
                if row is None:
                    existing = connection.execute(
                        """
                        SELECT request_digest, status_document AS document
                        FROM iip.investigation_jobs
                        WHERE tenant_id = %s AND investigation_id = %s
                        """,
                        (actor.tenant_id, investigation_id),
                    ).fetchone()
                    if existing is None or existing["request_digest"] != request_digest:
                        raise PersistenceError("storage.conflict")
                    row = existing
            return dict(row["document"])
        except PersistenceError:
            raise
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def get_investigation_job(
        self, actor: ActorContext, investigation_id: str
    ) -> Optional[Mapping[str, object]]:
        return self._one_document(
            "SELECT status_document AS document FROM iip.investigation_jobs WHERE tenant_id = %s AND investigation_id = %s",
            (actor.tenant_id, investigation_id),
        )

    def get_investigation_completion_slo_state(
        self,
        tenant_id: str,
        *,
        window_start: str,
        window_end: str,
        maturity_cutoff: str,
        completion_objective_seconds: int,
    ) -> InvestigationCompletionSloState:
        if (
            isinstance(completion_objective_seconds, bool)
            or not isinstance(completion_objective_seconds, int)
            or not 1 <= completion_objective_seconds <= 86_400
        ):
            raise ValueError("completion_objective_seconds is invalid")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    WITH cohort AS (
                        SELECT state, created_at, updated_at,
                               created_at <= %s::timestamptz AS eligible,
                               created_at + make_interval(secs => %s) AS deadline,
                               updated_at <= %s::timestamptz AS observed_at_end
                        FROM iip.investigation_jobs
                        WHERE tenant_id = %s
                          AND created_at >= %s::timestamptz
                          AND created_at <= %s::timestamptz
                    )
                    SELECT
                        count(*) AS accepted_jobs,
                        count(*) FILTER (
                            WHERE state IN ('completed', 'failed', 'cancelled')
                              AND updated_at < created_at
                        ) AS invalid_jobs,
                        count(*) FILTER (WHERE NOT eligible) AS immature_jobs,
                        count(*) FILTER (WHERE eligible) AS eligible_jobs,
                        count(*) FILTER (
                            WHERE eligible AND observed_at_end
                              AND state = 'completed' AND updated_at <= deadline
                        ) AS within_objective_jobs,
                        count(*) FILTER (
                            WHERE eligible AND observed_at_end
                              AND state = 'completed' AND updated_at > deadline
                        ) AS late_completed_jobs,
                        count(*) FILTER (
                            WHERE eligible AND observed_at_end AND state = 'failed'
                        ) AS failed_jobs,
                        count(*) FILTER (
                            WHERE eligible AND observed_at_end AND state = 'cancelled'
                        ) AS cancelled_jobs,
                        count(*) FILTER (
                            WHERE eligible AND (
                                state IN ('queued', 'running', 'cancellation-requested')
                                OR (
                                    state IN ('completed', 'failed', 'cancelled')
                                    AND NOT observed_at_end
                                )
                            )
                        ) AS unfinished_jobs
                    FROM cohort
                    """,
                    (
                        maturity_cutoff,
                        completion_objective_seconds,
                        window_end,
                        tenant_id,
                        window_start,
                        window_end,
                    ),
                ).fetchone()
            assert row is not None
            if row["invalid_jobs"]:
                raise PersistenceError("storage.corrupt")
            return InvestigationCompletionSloState(
                tenant_id=tenant_id,
                window_start=window_start,
                window_end=window_end,
                maturity_cutoff=maturity_cutoff,
                accepted_jobs=row["accepted_jobs"],
                immature_jobs=row["immature_jobs"],
                eligible_jobs=row["eligible_jobs"],
                within_objective_jobs=row["within_objective_jobs"],
                late_completed_jobs=row["late_completed_jobs"],
                failed_jobs=row["failed_jobs"],
                cancelled_jobs=row["cancelled_jobs"],
                unfinished_jobs=row["unfinished_jobs"],
            )
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def claim_investigation_job(
        self,
        tenant_id: str,
        worker_id: str,
        now: str,
        lease_expires_at: str,
        *,
        max_tenant_concurrency: int = 1,
    ) -> Optional[InvestigationJobClaim]:
        if (
            isinstance(max_tenant_concurrency, bool)
            or not isinstance(max_tenant_concurrency, int)
            or not 1 <= max_tenant_concurrency <= 64
        ):
            raise PersistenceError("storage.input-invalid")
        claim_token = secrets.token_hex(32)
        try:
            with self._connect() as connection:
                # Serialize admission only within this tenant. Different tenants
                # retain independent claim throughput across worker replicas.
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (tenant_id,),
                )
                active = connection.execute(
                    """
                    SELECT count(*) AS active
                    FROM iip.investigation_jobs
                    WHERE tenant_id = %s
                      AND state IN ('running', 'cancellation-requested')
                      AND lease_expires_at > %s
                    """,
                    (tenant_id, now),
                ).fetchone()
                if (
                    active is not None
                    and int(active["active"]) >= max_tenant_concurrency
                ):
                    return None
                row = connection.execute(
                    """
                    SELECT investigation_id, actor_id, actor_roles,
                           request_document, status_document, attempts
                    FROM iip.investigation_jobs
                    WHERE tenant_id = %s
                      AND (
                        (state = 'queued' AND available_at <= %s)
                        OR
                        (state IN ('running', 'cancellation-requested') AND lease_expires_at <= %s)
                      )
                    ORDER BY created_at, investigation_id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                    """,
                    (tenant_id, now, now),
                ).fetchone()
                if row is None:
                    return None
                status = dict(row["status_document"])
                metadata = dict(status["metadata"])
                spec = dict(status["spec"])
                attempts = int(row["attempts"]) + 1
                metadata["updatedAt"] = now
                if spec.get("state") == "queued":
                    spec["state"] = "running"
                    spec["startedAt"] = now
                    spec.pop("availableAt", None)
                spec["attempts"] = attempts
                spec["heartbeatAt"] = now
                spec["leaseExpiresAt"] = lease_expires_at
                status["metadata"] = metadata
                status["spec"] = spec
                updated = connection.execute(
                    """
                    UPDATE iip.investigation_jobs
                    SET status_document = %s,
                        state = %s,
                        attempts = %s,
                        available_at = NULL,
                        lease_owner = %s,
                        claim_token = %s,
                        lease_expires_at = %s,
                        updated_at = %s
                    WHERE tenant_id = %s AND investigation_id = %s
                    RETURNING investigation_id
                    """,
                    (
                        Jsonb(status),
                        spec["state"],
                        attempts,
                        worker_id,
                        claim_token,
                        lease_expires_at,
                        now,
                        tenant_id,
                        row["investigation_id"],
                    ),
                ).fetchone()
                if updated is None:
                    return None
            roles = row["actor_roles"]
            return InvestigationJobClaim(
                actor=ActorContext(
                    str(row["actor_id"]),
                    tenant_id,
                    tuple(str(role) for role in roles),
                ),
                investigation_id=str(row["investigation_id"]),
                request=dict(row["request_document"]),
                claim_token=claim_token,
                attempts=attempts,
            )
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def heartbeat_investigation_job(
        self,
        tenant_id: str,
        investigation_id: str,
        worker_id: str,
        claim_token: str,
        status: Mapping[str, object],
    ) -> bool:
        spec = status.get("spec")
        metadata = status.get("metadata")
        if not isinstance(spec, Mapping) or not isinstance(metadata, Mapping):
            raise PersistenceError("storage.input-invalid")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    UPDATE iip.investigation_jobs
                    SET status_document = %s, lease_expires_at = %s, updated_at = %s
                    WHERE tenant_id = %s AND investigation_id = %s
                      AND lease_owner = %s AND claim_token = %s
                      AND state = %s
                    RETURNING investigation_id
                    """,
                    (
                        Jsonb(dict(status)),
                        spec["leaseExpiresAt"],
                        metadata["updatedAt"],
                        tenant_id,
                        investigation_id,
                        worker_id,
                        claim_token,
                        spec["state"],
                    ),
                ).fetchone()
            return row is not None
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def release_investigation_job(
        self,
        tenant_id: str,
        investigation_id: str,
        worker_id: str,
        claim_token: str,
        status: Mapping[str, object],
        available_at: str,
        error_code: str,
    ) -> bool:
        metadata = status.get("metadata")
        if not isinstance(metadata, Mapping):
            raise PersistenceError("storage.input-invalid")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    UPDATE iip.investigation_jobs
                    SET status_document = %s, state = 'queued',
                        available_at = %s, lease_owner = NULL,
                        claim_token = NULL, lease_expires_at = NULL,
                        last_error_code = %s, updated_at = %s
                    WHERE tenant_id = %s AND investigation_id = %s
                      AND lease_owner = %s AND claim_token = %s
                      AND state <> 'cancellation-requested'
                    RETURNING investigation_id
                    """,
                    (
                        Jsonb(dict(status)),
                        available_at,
                        error_code,
                        metadata["updatedAt"],
                        tenant_id,
                        investigation_id,
                        worker_id,
                        claim_token,
                    ),
                ).fetchone()
            return row is not None
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def finish_investigation_job(
        self,
        tenant_id: str,
        investigation_id: str,
        worker_id: str,
        claim_token: str,
        status: Mapping[str, object],
    ) -> bool:
        metadata = status.get("metadata")
        spec = status.get("spec")
        if not isinstance(metadata, Mapping) or not isinstance(spec, Mapping):
            raise PersistenceError("storage.input-invalid")
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    UPDATE iip.investigation_jobs
                    SET status_document = %s, state = %s,
                        available_at = NULL, lease_owner = NULL,
                        claim_token = NULL, lease_expires_at = NULL,
                        last_error_code = %s, updated_at = %s
                    WHERE tenant_id = %s AND investigation_id = %s
                      AND lease_owner = %s AND claim_token = %s
                      AND (state <> 'cancellation-requested' OR %s = 'cancelled')
                    RETURNING investigation_id
                    """,
                    (
                        Jsonb(dict(status)),
                        spec["state"],
                        spec.get("lastErrorCode"),
                        metadata["updatedAt"],
                        tenant_id,
                        investigation_id,
                        worker_id,
                        claim_token,
                        spec["state"],
                    ),
                ).fetchone()
            return row is not None
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def request_investigation_job_cancellation(
        self,
        actor: ActorContext,
        investigation_id: str,
        status: Mapping[str, object],
    ) -> Mapping[str, object]:
        self._assert_tenant(actor, status)
        metadata = status.get("metadata")
        spec = status.get("spec")
        if not isinstance(metadata, Mapping) or not isinstance(spec, Mapping):
            raise PersistenceError("storage.input-invalid")
        terminal = spec.get("state") == "cancelled"
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    UPDATE iip.investigation_jobs
                    SET status_document = %s,
                        state = %s,
                        available_at = CASE WHEN %s THEN NULL ELSE available_at END,
                        lease_owner = CASE WHEN %s THEN NULL ELSE lease_owner END,
                        claim_token = CASE WHEN %s THEN NULL ELSE claim_token END,
                        lease_expires_at = CASE WHEN %s THEN NULL ELSE lease_expires_at END,
                        updated_at = %s
                    WHERE tenant_id = %s AND investigation_id = %s
                      AND state NOT IN ('completed', 'failed', 'cancelled')
                    RETURNING status_document AS document
                    """,
                    (
                        Jsonb(dict(status)),
                        spec["state"],
                        terminal,
                        terminal,
                        terminal,
                        terminal,
                        metadata["updatedAt"],
                        actor.tenant_id,
                        investigation_id,
                    ),
                ).fetchone()
                if row is None:
                    row = connection.execute(
                        """
                        SELECT status_document AS document
                        FROM iip.investigation_jobs
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

    def list_expired_action_executions(
        self,
        actor: ActorContext,
        observed_at: str,
        *,
        limit: int,
    ) -> tuple[Mapping[str, object], ...]:
        if isinstance(limit, bool) or not 1 <= limit <= 500:
            raise PersistenceError("storage.input-invalid")
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT document
                    FROM iip.action_executions
                    WHERE tenant_id = %s
                      AND state = 'executing'
                      AND lease_expires_at <= %s
                    ORDER BY lease_expires_at, proposal_id
                    LIMIT %s
                    """,
                    (actor.tenant_id, observed_at, limit),
                ).fetchall()
            return tuple(dict(row["document"]) for row in rows)
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def reconcile_expired_action_execution(
        self,
        actor: ActorContext,
        proposal_id: str,
        observed_at: str,
        document: Mapping[str, object],
        audit_document: Mapping[str, object],
    ) -> ActionExecutionTransition:
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
                transitioned = row is not None
                if transitioned:
                    connection.execute(
                        """
                        INSERT INTO iip.audit_records (tenant_id, category, document)
                        VALUES (%s, 'action-execution-reconciliation-required', %s)
                        """,
                        (actor.tenant_id, Jsonb(dict(audit_document))),
                    )
                else:
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
            return ActionExecutionTransition(
                status=dict(row["document"]),
                transitioned=transitioned,
            )
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

    def claim_plugin_invocation(
        self,
        actor: ActorContext,
        session: Mapping[str, object],
        invocation: Mapping[str, object],
        request_digest: str,
        claimed_at: str,
    ) -> PluginInvocationClaim:
        self._assert_tenant(actor, session)
        self._assert_tenant(actor, invocation)
        session_metadata = session.get("metadata")
        session_spec = session.get("spec")
        invocation_metadata = invocation.get("metadata")
        limits = session_spec.get("limits") if isinstance(session_spec, Mapping) else None
        created_at = (
            invocation_metadata.get("createdAt")
            if isinstance(invocation_metadata, Mapping)
            else None
        )
        deadline = (
            invocation_metadata.get("deadline")
            if isinstance(invocation_metadata, Mapping)
            else None
        )
        expires_at = session_spec.get("expiresAt") if isinstance(session_spec, Mapping) else None
        if (
            not isinstance(session_metadata, Mapping)
            or not isinstance(invocation_metadata, Mapping)
            or not isinstance(limits, Mapping)
            or re.fullmatch(r"psn_[a-f0-9]{32}", str(session_metadata.get("id")))
            is None
            or re.fullmatch(r"pin_[a-f0-9]{32}", str(invocation_metadata.get("id")))
            is None
            or invocation_metadata.get("sessionId") != session_metadata.get("id")
            or canonical_digest(invocation) != request_digest
            or re.fullmatch(r"sha256:[a-f0-9]{64}", request_digest) is None
            or isinstance(limits.get("maxRequests"), bool)
            or not isinstance(limits.get("maxRequests"), int)
            or not 1 <= limits["maxRequests"] <= 10_000
            or not self._valid_timestamp(claimed_at)
            or not self._valid_timestamp(created_at)
            or not self._valid_timestamp(deadline)
            or not self._valid_timestamp(expires_at)
        ):
            raise PersistenceError("storage.input-invalid")
        session_id = str(session_metadata["id"])
        request_id = str(invocation_metadata["id"])
        claim_status = self._plugin_claim_status(
            actor,
            session_metadata,
            invocation_metadata,
            request_digest,
            claimed_at,
        )
        try:
            with self._connect() as connection:
                stored_session = connection.execute(
                    """
                    SELECT document
                    FROM iip.plugin_sessions
                    WHERE tenant_id = %s AND session_id = %s
                    FOR UPDATE
                    """,
                    (actor.tenant_id, session_id),
                ).fetchone()
                if stored_session is None:
                    raise PersistenceError("storage.not-found")
                if dict(stored_session["document"]) != dict(session):
                    raise PersistenceError("storage.conflict")
                existing = connection.execute(
                    """
                    SELECT session_id, request_digest, state, result_document,
                           status_document, cancellation_document
                    FROM iip.plugin_invocations
                    WHERE tenant_id = %s AND request_id = %s
                    """,
                    (actor.tenant_id, request_id),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["session_id"] != session_id
                        or existing["request_digest"] != request_digest
                    ):
                        raise PersistenceError("storage.conflict")
                    result = existing["result_document"]
                    if existing["state"] == "completed" and isinstance(result, Mapping):
                        return PluginInvocationClaim("completed", dict(result))
                    if existing["state"] == "claimed" and result is None:
                        status = existing["status_document"]
                        status_spec = status.get("spec") if isinstance(status, Mapping) else None
                        if (
                            isinstance(status_spec, Mapping)
                            and status_spec.get("state") == "cancellation-requested"
                            and existing["cancellation_document"] is not None
                        ):
                            return PluginInvocationClaim("cancellation-requested")
                        return PluginInvocationClaim("in-progress")
                    raise PersistenceError("storage.corrupt")
                assert isinstance(created_at, str)
                assert isinstance(deadline, str)
                assert isinstance(expires_at, str)
                if not (
                    self._parse_timestamp(created_at)
                    <= self._parse_timestamp(claimed_at)
                    <= self._parse_timestamp(deadline)
                    <= self._parse_timestamp(expires_at)
                ):
                    raise PersistenceError("plugin.request.expired")
                count = connection.execute(
                    """
                    SELECT count(*) AS request_count
                    FROM iip.plugin_invocations
                    WHERE tenant_id = %s AND session_id = %s
                    """,
                    (actor.tenant_id, session_id),
                ).fetchone()
                if count is None:
                    raise PersistenceError("storage.corrupt")
                if int(count["request_count"]) >= limits["maxRequests"]:
                    raise PersistenceError("plugin.request.limit-exceeded")
                connection.execute(
                    """
                    INSERT INTO iip.plugin_invocations (
                        tenant_id, request_id, session_id, request_digest,
                        invocation_document, state, claimed_at, status_document
                    ) VALUES (%s, %s, %s, %s, %s, 'claimed', %s, %s)
                    """,
                    (
                        actor.tenant_id,
                        request_id,
                        session_id,
                        request_digest,
                        Jsonb(dict(invocation)),
                        claimed_at,
                        Jsonb(claim_status),
                    ),
                )
                return PluginInvocationClaim("claimed")
        except PersistenceError:
            raise
        except psycopg.errors.UniqueViolation:
            raise PersistenceError("storage.conflict") from None
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def commit_plugin_invocation_result(
        self,
        actor: ActorContext,
        request_digest: str,
        result: Mapping[str, object],
    ) -> None:
        self._assert_tenant(actor, result)
        metadata = result.get("metadata")
        spec = result.get("spec")
        if (
            not isinstance(metadata, Mapping)
            or not isinstance(spec, Mapping)
            or re.fullmatch(r"pin_[a-f0-9]{32}", str(metadata.get("id"))) is None
            or re.fullmatch(r"psn_[a-f0-9]{32}", str(metadata.get("sessionId")))
            is None
            or re.fullmatch(r"sha256:[a-f0-9]{64}", request_digest) is None
            or spec.get("status") not in ("succeeded", "failed", "cancelled")
            or not self._valid_timestamp(metadata.get("completedAt"))
        ):
            raise PersistenceError("storage.input-invalid")
        request_id = str(metadata["id"])
        session_id = str(metadata["sessionId"])
        value = dict(result)
        try:
            with self._connect() as connection:
                existing = connection.execute(
                    """
                    SELECT session_id, request_digest, state, result_document,
                           status_document, cancellation_document
                    FROM iip.plugin_invocations
                    WHERE tenant_id = %s AND request_id = %s
                    FOR UPDATE
                    """,
                    (actor.tenant_id, request_id),
                ).fetchone()
                if existing is None:
                    raise PersistenceError("storage.not-found")
                if (
                    existing["session_id"] != session_id
                    or existing["request_digest"] != request_digest
                ):
                    raise PersistenceError("storage.conflict")
                if existing["state"] == "completed":
                    current = existing["result_document"]
                    if isinstance(current, Mapping) and dict(current) == value:
                        return
                    raise PersistenceError("storage.conflict")
                if existing["state"] != "claimed" or existing["result_document"] is not None:
                    raise PersistenceError("storage.corrupt")
                if (
                    existing["cancellation_document"] is not None
                    and spec.get("status") != "cancelled"
                ):
                    raise PersistenceError("plugin.request.cancellation-pending")
                terminal_status = self._terminal_plugin_status(
                    existing["status_document"], value
                )
                updated = connection.execute(
                    """
                    UPDATE iip.plugin_invocations
                    SET state = 'completed', result_document = %s,
                        status_document = %s, completed_at = %s
                    WHERE tenant_id = %s AND request_id = %s AND state = 'claimed'
                    RETURNING request_id
                    """,
                    (
                        Jsonb(value),
                        Jsonb(terminal_status),
                        metadata["completedAt"],
                        actor.tenant_id,
                        request_id,
                    ),
                ).fetchone()
                if updated is None:
                    raise PersistenceError("storage.conflict")
        except PersistenceError:
            raise
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def get_plugin_invocation_status(
        self, actor: ActorContext, request_id: str
    ) -> Optional[Mapping[str, object]]:
        return self._one_document(
            "SELECT status_document AS document FROM iip.plugin_invocations WHERE tenant_id = %s AND request_id = %s",
            (actor.tenant_id, request_id),
        )

    def request_plugin_invocation_cancellation(
        self,
        actor: ActorContext,
        request_id: str,
        status: Mapping[str, object],
        audit_document: Mapping[str, object],
    ) -> Mapping[str, object]:
        self._assert_tenant(actor, status)
        self._assert_tenant(actor, audit_document)
        status_metadata = status.get("metadata")
        status_spec = status.get("spec")
        cancellation = (
            status_spec.get("cancellation") if isinstance(status_spec, Mapping) else None
        )
        if (
            not isinstance(status_metadata, Mapping)
            or status_metadata.get("id") != request_id
            or not isinstance(status_spec, Mapping)
            or status_spec.get("state") != "cancellation-requested"
            or not isinstance(cancellation, Mapping)
        ):
            raise PersistenceError("storage.input-invalid")
        try:
            with self._connect() as connection:
                existing = connection.execute(
                    """
                    SELECT request_digest, state, result_document, status_document
                    FROM iip.plugin_invocations
                    WHERE tenant_id = %s AND request_id = %s
                    FOR UPDATE
                    """,
                    (actor.tenant_id, request_id),
                ).fetchone()
                if existing is None:
                    raise PersistenceError("storage.not-found")
                current = existing["status_document"]
                current_spec = current.get("spec") if isinstance(current, Mapping) else None
                if not isinstance(current_spec, Mapping):
                    raise PersistenceError("storage.corrupt")
                if current_spec.get("state") != "claimed":
                    return dict(current)
                if (
                    existing["state"] != "claimed"
                    or existing["result_document"] is not None
                    or status_spec.get("requestDigest") != existing["request_digest"]
                ):
                    raise PersistenceError("storage.conflict")
                updated = connection.execute(
                    """
                    UPDATE iip.plugin_invocations
                    SET status_document = %s, cancellation_document = %s
                    WHERE tenant_id = %s AND request_id = %s
                      AND state = 'claimed' AND result_document IS NULL
                    RETURNING status_document
                    """,
                    (
                        Jsonb(dict(status)),
                        Jsonb(dict(cancellation)),
                        actor.tenant_id,
                        request_id,
                    ),
                ).fetchone()
                if updated is None:
                    raise PersistenceError("storage.conflict")
                connection.execute(
                    "INSERT INTO iip.audit_records (tenant_id, category, document) VALUES (%s, %s, %s)",
                    (
                        actor.tenant_id,
                        "plugin-invocation-cancellation",
                        Jsonb(dict(audit_document)),
                    ),
                )
                return dict(updated["status_document"])
        except PersistenceError:
            raise
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def plugin_invocation_cancellation_requested(
        self, actor: ActorContext, request_id: str, request_digest: str
    ) -> bool:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    """
                    SELECT request_digest, status_document
                    FROM iip.plugin_invocations
                    WHERE tenant_id = %s AND request_id = %s
                    """,
                    (actor.tenant_id, request_id),
                ).fetchone()
            if row is None or row["request_digest"] != request_digest:
                raise PersistenceError("storage.not-found")
            status = row["status_document"]
            spec = status.get("spec") if isinstance(status, Mapping) else None
            if not isinstance(spec, Mapping):
                raise PersistenceError("storage.corrupt")
            return spec.get("state") == "cancellation-requested"
        except PersistenceError:
            raise
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

    def reconcile_plugin_invocation(
        self,
        actor: ActorContext,
        request_id: str,
        observed_at: str,
        result: Mapping[str, object],
        status: Mapping[str, object],
        audit_document: Mapping[str, object],
    ) -> Mapping[str, object]:
        self._assert_tenant(actor, result)
        self._assert_tenant(actor, status)
        self._assert_tenant(actor, audit_document)
        result_metadata = result.get("metadata")
        result_spec = result.get("spec")
        status_metadata = status.get("metadata")
        status_spec = status.get("spec")
        if (
            not isinstance(result_metadata, Mapping)
            or result_metadata.get("id") != request_id
            or not isinstance(result_spec, Mapping)
            or not isinstance(status_metadata, Mapping)
            or status_metadata.get("id") != request_id
            or not isinstance(status_spec, Mapping)
            or status_spec.get("state") not in ("failed", "cancelled")
            or not self._valid_timestamp(observed_at)
        ):
            raise PersistenceError("storage.input-invalid")
        try:
            with self._connect() as connection:
                existing = connection.execute(
                    """
                    SELECT request_digest, state, result_document,
                           invocation_document, status_document
                    FROM iip.plugin_invocations
                    WHERE tenant_id = %s AND request_id = %s
                    FOR UPDATE
                    """,
                    (actor.tenant_id, request_id),
                ).fetchone()
                if existing is None:
                    raise PersistenceError("storage.not-found")
                current = existing["status_document"]
                current_spec = current.get("spec") if isinstance(current, Mapping) else None
                if not isinstance(current_spec, Mapping):
                    raise PersistenceError("storage.corrupt")
                if current_spec.get("state") in ("succeeded", "failed", "cancelled"):
                    return dict(current)
                expected_state = (
                    "cancelled"
                    if current_spec.get("state") == "cancellation-requested"
                    else "failed"
                )
                if (
                    status_spec.get("state") != expected_state
                    or result_spec.get("status") != expected_state
                ):
                    raise PersistenceError("plugin.request.cancellation-pending")
                invocation = existing["invocation_document"]
                invocation_metadata = (
                    invocation.get("metadata") if isinstance(invocation, Mapping) else None
                )
                deadline = (
                    invocation_metadata.get("deadline")
                    if isinstance(invocation_metadata, Mapping)
                    else None
                )
                if (
                    not isinstance(deadline, str)
                    or self._parse_timestamp(observed_at)
                    < self._parse_timestamp(deadline)
                ):
                    raise PersistenceError("plugin.reconciliation.deadline-live")
                if (
                    existing["state"] != "claimed"
                    or existing["result_document"] is not None
                    or status_spec.get("requestDigest") != existing["request_digest"]
                ):
                    raise PersistenceError("storage.conflict")
                updated = connection.execute(
                    """
                    UPDATE iip.plugin_invocations
                    SET state = 'completed', result_document = %s,
                        status_document = %s, completed_at = %s
                    WHERE tenant_id = %s AND request_id = %s
                      AND state = 'claimed' AND result_document IS NULL
                    RETURNING status_document
                    """,
                    (
                        Jsonb(dict(result)),
                        Jsonb(dict(status)),
                        observed_at,
                        actor.tenant_id,
                        request_id,
                    ),
                ).fetchone()
                if updated is None:
                    raise PersistenceError("storage.conflict")
                connection.execute(
                    "INSERT INTO iip.audit_records (tenant_id, category, document) VALUES (%s, %s, %s)",
                    (
                        actor.tenant_id,
                        "plugin-invocation-reconciliation",
                        Jsonb(dict(audit_document)),
                    ),
                )
                return dict(updated["status_document"])
        except PersistenceError:
            raise
        except psycopg.Error:
            raise PersistenceError("storage.unavailable") from None

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

    @staticmethod
    def _valid_timestamp(value: object) -> bool:
        if not isinstance(value, str) or not value.endswith("Z"):
            return False
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return parsed.tzinfo is not None

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    @staticmethod
    def _plugin_claim_status(
        actor: ActorContext,
        session_metadata: Mapping[str, object],
        invocation_metadata: Mapping[str, object],
        request_digest: str,
        claimed_at: str,
    ) -> dict[str, object]:
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "PluginInvocationStatus",
            "metadata": {
                "id": invocation_metadata["id"],
                "sessionId": session_metadata["id"],
                "tenantId": actor.tenant_id,
                "pluginId": session_metadata["pluginId"],
                "pluginVersion": session_metadata["pluginVersion"],
                "updatedAt": claimed_at,
            },
            "spec": {
                "requestDigest": request_digest,
                "state": "claimed",
                "claimedAt": claimed_at,
                "deadline": invocation_metadata["deadline"],
            },
        }

    @staticmethod
    def _terminal_plugin_status(
        current: object, result: Mapping[str, object]
    ) -> dict[str, object]:
        if not isinstance(current, Mapping):
            raise PersistenceError("storage.corrupt")
        current_metadata = current.get("metadata")
        current_spec = current.get("spec")
        result_metadata = result.get("metadata")
        result_spec = result.get("spec")
        if not all(
            isinstance(value, Mapping)
            for value in (current_metadata, current_spec, result_metadata, result_spec)
        ):
            raise PersistenceError("storage.corrupt")
        terminal_spec = {
            **dict(current_spec),
            "state": result_spec["status"],
            "completedAt": result_metadata["completedAt"],
            "resultRef": (
                f"plugin-result://{current_metadata['tenantId']}/sessions/"
                f"{current_metadata['sessionId']}/invocations/{current_metadata['id']}"
            ),
        }
        return {
            "apiVersion": "iip.platform/v1alpha1",
            "kind": "PluginInvocationStatus",
            "metadata": {
                **dict(current_metadata),
                "updatedAt": result_metadata["completedAt"],
            },
            "spec": terminal_spec,
        }
