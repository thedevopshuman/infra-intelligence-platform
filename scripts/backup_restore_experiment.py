#!/usr/bin/env python3
"""Measure and verify a disposable PostgreSQL backup/restore recovery path."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from iip import __version__ as application_version
from iip.application.actions import (
    DecideActionCommand,
    ExecuteActionCommand,
    ProposeActionCommand,
)
from iip.application.ingest_collection import IngestCollectionCommand
from iip.application.investigate import RunInvestigationCommand, canonical_digest
from iip.application.plugin_sessions import OpenPluginSessionCommand
from iip.application.plugin_invocations import CancelPluginInvocationCommand
from iip.application.ports import ActorContext
from iip.application.rebuild_projections import RebuildProjectionsCommand
from iip.adapters.postgres import PostgresOperationalStore
from iip.adapters.postgres.store import SCHEMA_MIGRATIONS
from iip.bootstrap import build_postgres_runtime, build_projection_maintenance

import validate_schemas


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE_FILE = ROOT / "deploy" / "docker-compose.test.yml"
DEFAULT_PROJECT_NAME = "iip-backup-restore"
DEFAULT_SOURCE_DATABASE = "iip_test"
DEFAULT_RESTORED_DATABASE = "iip_restore"
DEFAULT_DATABASE_HOST = "127.0.0.1"
DEFAULT_DATABASE_PORT = 55432
DEFAULT_RPO_TARGET_SECONDS = 60.0
DEFAULT_RTO_TARGET_SECONDS = 120.0
BACKUP_PATH = "/tmp/iip-backup-restore.dump"
DATABASE_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")
POSTGRES_IMAGE = (
    "postgres:18.4-alpine@"
    "sha256:9a8afca54e7861fd90fab5fdf4c42477a6b1cb7d293595148e674e0a3181de15"
)
REPORT_SCHEMA = (
    ROOT / "contracts" / "schemas" / "postgresql-recovery-qualification-report.schema.json"
)
CHECK_IDS = (
    "representative-state",
    "complete-schema-backup",
    "source-quiescence",
    "isolated-restore",
    "row-integrity",
    "sequence-integrity",
    "projection-consistency",
    "recovery-point-age-objective",
    "recovery-ready-objective",
)


def canonical_json(value: object) -> str:
    """Return the stable JSON representation used by integrity digests."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def compile_manifest(
    table_rows: Mapping[str, Sequence[Mapping[str, object]]],
    sequences: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """Build a deterministic manifest from already JSON-compatible database rows."""

    tables: dict[str, dict[str, object]] = {}
    for table_name in sorted(table_rows):
        rows = sorted(canonical_json(dict(row)) for row in table_rows[table_name])
        digest = hashlib.sha256(canonical_json(rows).encode("utf-8")).hexdigest()
        tables[table_name] = {
            "rowCount": len(rows),
            "digest": f"sha256:{digest}",
        }
    normalized_sequences = sorted(
        (dict(item) for item in sequences),
        key=lambda item: canonical_json(item),
    )
    sequence_digest = hashlib.sha256(
        canonical_json(normalized_sequences).encode("utf-8")
    ).hexdigest()
    database_material = {
        "tables": tables,
        "sequenceDigest": f"sha256:{sequence_digest}",
    }
    database_digest = hashlib.sha256(
        canonical_json(database_material).encode("utf-8")
    ).hexdigest()
    return {
        "tables": tables,
        "sequences": normalized_sequences,
        "databaseDigest": f"sha256:{database_digest}",
        "sequenceDigest": f"sha256:{sequence_digest}",
    }


def database_manifest(database_url: str) -> dict[str, object]:
    """Digest every platform table and identity-sequence state."""

    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        table_names = [
            row["table_name"]
            for row in connection.execute(
                """
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'iip' AND table_type = 'BASE TABLE'
                ORDER BY table_name
                """
            ).fetchall()
        ]
        table_rows: dict[str, list[Mapping[str, object]]] = {}
        for table_name in table_names:
            query = sql.SQL(
                "SELECT to_jsonb(platform_row) AS document FROM {}.{} AS platform_row"
            ).format(sql.Identifier("iip"), sql.Identifier(table_name))
            table_rows[table_name] = [
                dict(row["document"])
                for row in connection.execute(query).fetchall()
            ]
        sequences = [
            dict(row)
            for row in connection.execute(
                """
                SELECT sequencename AS name,
                       start_value AS "startValue",
                       min_value AS "minValue",
                       max_value AS "maxValue",
                       increment_by AS "incrementBy",
                       cycle,
                       cache_size AS "cacheSize",
                       last_value AS "lastValue"
                FROM pg_sequences
                WHERE schemaname = 'iip'
                ORDER BY sequencename
                """
            ).fetchall()
        ]
    return compile_manifest(table_rows, sequences)


def assert_identical_manifests(
    source: Mapping[str, object], restored: Mapping[str, object]
) -> None:
    """Reject any authoritative, derived, or sequence-state restore drift."""

    if source != restored:
        raise RuntimeError("backup_restore.integrity_mismatch")


def validate_database_names(source_database: str, restored_database: str) -> None:
    """Keep destructive database commands constrained to explicit safe identifiers."""

    if (
        DATABASE_NAME.fullmatch(source_database) is None
        or DATABASE_NAME.fullmatch(restored_database) is None
        or source_database == restored_database
        or restored_database in {"postgres", "template0", "template1"}
    ):
        raise ValueError("backup_restore.database_name_invalid")


def database_url(database: str, host: str, port: int) -> str:
    return f"postgresql://postgres@{host}:{port}/{database}"


def load_example(name: str) -> dict[str, Any]:
    document = json.loads(
        (ROOT / "contracts" / "examples" / name).read_text(encoding="utf-8")
    )
    if not isinstance(document, dict):
        raise RuntimeError("backup_restore.fixture_invalid")
    return document


def seed_reference_workflow(
    database_url_value: str,
) -> tuple[dict[str, object], str]:
    """Persist one workflow through the same application and adapter boundaries."""

    runtime = build_postgres_runtime(database_url_value, migrate=True)
    request = load_example("resource-collection-request.json")
    result = load_example("resource-collection-result.json")
    resource_example = load_example("resource.json")
    snapshot_id = "snap_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    request = copy.deepcopy(request)
    result = copy.deepcopy(result)
    request["spec"]["mode"] = "reconciliation"
    request["spec"]["snapshotId"] = snapshot_id
    result["spec"]["completion"]["snapshotId"] = snapshot_id
    for observation in result["spec"]["observations"]:
        observation["metadata"]["observation"]["mode"] = "reconciliation"
        observation["metadata"]["observation"]["snapshotId"] = snapshot_id
        observation["spec"]["relationships"] = copy.deepcopy(
            resource_example["spec"]["relationships"]
        )

    collection_actor = ActorContext(
        str(request["metadata"]["actorId"]),
        str(request["metadata"]["tenantId"]),
    )
    resources = runtime.collection_ingestion.execute(
        IngestCollectionCommand(
            collection_actor,
            request,
            result,
            "backup-restore-experiment",
        )
    )
    target = resources[0]

    investigation_request = load_example("investigation-request.json")
    investigation_request["spec"]["scope"]["resourceUids"] = [target.identity.uid]
    investigator = ActorContext(
        str(investigation_request["metadata"]["actorId"]),
        str(investigation_request["metadata"]["tenantId"]),
    )
    report = runtime.investigations.execute(
        RunInvestigationCommand(investigator, investigation_request)
    )

    proposal = runtime.actions.propose(
        ProposeActionCommand(
            actor=investigator,
            investigation_id=str(report["metadata"]["id"]),
            action_type="kubernetes.restart-workload",
            target_resource_uid=target.identity.uid,
            parameters={
                "namespace": "default",
                "workloadKind": "deployment",
                "workloadName": "api",
            },
            idempotency_key="backup-restore-restart-api-001",
            expires_at="2099-08-14T13:30:00Z",
            dry_run=True,
        )
    )
    proposal_id = str(proposal["metadata"]["id"])
    runtime.actions.decide(
        DecideActionCommand(
            ActorContext("backup-restore-approver", "local", ("approver",)),
            proposal_id,
            "approved",
            "Approve the no-impact recovery fixture dry-run.",
        )
    )
    action_result = runtime.actions.execute(
        ExecuteActionCommand(
            ActorContext("backup-restore-executor", "local", ("executor",)),
            proposal_id,
        )
    )
    plugin_session = runtime.plugin_sessions.open(
        OpenPluginSessionCommand(
            investigator,
            load_example("plugin-manifest.json"),
            ("resource-observer",),
            secrets.token_urlsafe(32),
        )
    )
    invocation = load_example("plugin-invocation.json")
    invocation["metadata"].update(
        {
            "sessionId": plugin_session["metadata"]["id"],
            "tenantId": investigator.tenant_id,
            "actorId": investigator.actor_id,
            "createdAt": plugin_session["metadata"]["createdAt"],
            "deadline": plugin_session["spec"]["expiresAt"],
        }
    )
    invocation["spec"]["manifestDigest"] = plugin_session["spec"]["manifestDigest"]
    invocation_digest = canonical_digest(invocation)
    result = load_example("plugin-invocation-result-failed.json")
    result["metadata"].update(
        {
            "id": invocation["metadata"]["id"],
            "sessionId": plugin_session["metadata"]["id"],
            "tenantId": investigator.tenant_id,
            "pluginId": plugin_session["metadata"]["pluginId"],
            "pluginVersion": plugin_session["metadata"]["pluginVersion"],
            "completedAt": plugin_session["spec"]["expiresAt"],
        }
    )
    operations = PostgresOperationalStore(database_url_value)
    claim = operations.claim_plugin_invocation(
        investigator,
        plugin_session,
        invocation,
        invocation_digest,
        str(invocation["metadata"]["createdAt"]),
    )
    cancellation = load_example("plugin-invocation-cancellation-request.json")
    cancellation["metadata"].update(
        {
            "tenantId": investigator.tenant_id,
            "actorId": investigator.actor_id,
            "requestedAt": invocation["metadata"]["createdAt"],
        }
    )
    cancellation["spec"]["invocationId"] = invocation["metadata"]["id"]
    runtime.plugin_invocations.cancel(
        CancelPluginInvocationCommand(investigator, cancellation)
    )
    result["spec"]["status"] = "cancelled"
    result["spec"]["error"]["code"] = "plugin.runtime.cancelled"
    operations.commit_plugin_invocation_result(
        investigator, invocation_digest, result
    )
    plugin_status = operations.get_plugin_invocation_status(
        investigator, str(invocation["metadata"]["id"])
    )
    if (
        action_result["spec"]["outcome"] != "dry-run"
        or report["spec"]["outcome"] != "conclusive"
        or claim.state != "claimed"
        or plugin_status["spec"]["state"] != "cancelled"
        or plugin_session["status"] != "ready"
    ):
        raise RuntimeError("backup_restore.fixture_incomplete")
    return (
        {
            "tenantCount": 1,
            "resourceCount": len(resources),
            "relationshipCount": sum(
                len(resource.relationships) for resource in resources
            ),
            "investigationCount": 1,
            "governedActionCount": 1,
            "pluginSessionCount": 1,
            "pluginInvocationCount": 1,
        },
        target.identity.tenant_id,
    )


@dataclass(frozen=True)
class DockerComposeRunner:
    """A Compose boundary isolated under one explicit disposable project name."""

    docker_bin: str
    compose_file: Path
    project_name: str

    def command(self, *arguments: str) -> list[str]:
        return [
            self.docker_bin,
            "compose",
            "--project-name",
            self.project_name,
            "--file",
            str(self.compose_file),
            *arguments,
        ]

    def run(self, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            self.command(*arguments),
            cwd=ROOT,
            check=check,
            capture_output=True,
            text=True,
        )

    def exec(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return self.run("exec", "--no-TTY", "postgres-test", *arguments)

    def cleanup(self) -> None:
        self.run("down", "--volumes", "--remove-orphans", check=False)


def database_version(database_url_value: str) -> str:
    with psycopg.connect(database_url_value) as connection:
        row = connection.execute("SHOW server_version").fetchone()
    return str(row[0])


def database_time(database_url_value: str) -> str:
    with psycopg.connect(database_url_value) as connection:
        row = connection.execute("SELECT clock_timestamp()").fetchone()
    return row[0].isoformat().replace("+00:00", "Z")


def projection_verification(database_url_value: str, tenant_id: str) -> dict[str, object]:
    result = build_projection_maintenance(database_url_value).execute(
        RebuildProjectionsCommand(
            actor=ActorContext(
                "backup-restore-operator",
                tenant_id,
                ("platform-admin",),
            ),
            dry_run=True,
        )
    )
    if result.drift_detected or result.before_digest != result.expected_digest:
        raise RuntimeError("backup_restore.projection_drift")
    return {
        "driftDetected": result.drift_detected,
        "resourceCount": result.resource_count,
        "relationshipCount": result.relationship_count,
        "latestObservationOffset": result.latest_observation_offset,
        "projectionDigest": result.expected_digest,
    }


def source_identity() -> tuple[str, bool]:
    """Return the exact checkout revision and whether any tracked/untracked input differs."""

    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    )
    return revision, dirty


def docker_server_version(docker_bin: str) -> str:
    result = subprocess.run(
        [docker_bin, "version", "--format", "{{.Server.Version}}"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    ).stdout.strip()
    if not result or len(result) > 64:
        raise RuntimeError("backup_restore.container_runtime_invalid")
    return result


def elapsed_milliseconds(seconds: float) -> int:
    """Round measured durations upward so the report never understates elapsed time."""

    return max(0, math.ceil(seconds * 1_000))


def check(identifier: str, passed: bool) -> dict[str, str]:
    if passed:
        return {"id": identifier, "status": "passed"}
    return {
        "id": identifier,
        "status": "failed",
        "errorCode": f"postgresql.recovery.{identifier}.failed",
    }


def build_report(
    *,
    source_revision: str,
    source_dirty: bool,
    container_runtime_version: str,
    server_version: str,
    fixture: Mapping[str, object],
    source_manifest: Mapping[str, object],
    backup_bytes: int,
    recovery_point: str,
    backup_duration_seconds: float,
    recovery_point_age_seconds: float,
    restore_command_duration_seconds: float,
    recovery_ready_seconds: float,
    projection: Mapping[str, object],
    rpo_target_seconds: float,
    rto_target_seconds: float,
) -> dict[str, object]:
    tables = source_manifest["tables"]
    assert isinstance(tables, Mapping)
    row_count = sum(
        int(table["rowCount"])
        for table in tables.values()
        if isinstance(table, Mapping)
    )
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    maximum_recovery_point_age_milliseconds = elapsed_milliseconds(
        rpo_target_seconds
    )
    maximum_recovery_ready_milliseconds = elapsed_milliseconds(rto_target_seconds)
    backup_duration_milliseconds = elapsed_milliseconds(backup_duration_seconds)
    recovery_point_age_milliseconds = elapsed_milliseconds(
        recovery_point_age_seconds
    )
    restore_command_duration_milliseconds = elapsed_milliseconds(
        restore_command_duration_seconds
    )
    recovery_ready_milliseconds = elapsed_milliseconds(recovery_ready_seconds)
    checks = [
        check(
            "representative-state",
            int(fixture.get("resourceCount", 0)) >= 1
            and int(fixture.get("relationshipCount", 0)) >= 1,
        ),
        check("complete-schema-backup", backup_bytes > 0 and len(tables) > 0),
        check("source-quiescence", True),
        check("isolated-restore", True),
        check("row-integrity", True),
        check("sequence-integrity", True),
        check("projection-consistency", projection.get("driftDetected") is False),
        check(
            "recovery-point-age-objective",
            recovery_point_age_milliseconds
            <= maximum_recovery_point_age_milliseconds,
        ),
        check(
            "recovery-ready-objective",
            recovery_ready_milliseconds <= maximum_recovery_ready_milliseconds,
        ),
    ]
    status = (
        "qualified" if all(item["status"] == "passed" for item in checks) else "failed"
    )
    identity = canonical_json(
        {
            "generatedAt": generated_at,
            "profile": "quiesced-logical-restore-v1",
            "sourceRevision": source_revision,
        }
    )
    return {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "PostgreSQLRecoveryQualificationReport",
        "metadata": {
            "id": "pgr_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32],
            "generatedAt": generated_at,
            "sourceRevision": source_revision,
            "sourceDirty": source_dirty,
        },
        "spec": {
            "status": status,
            "environment": {
                "profile": "local-docker",
                "platform": f"{platform.system().lower()}/{platform.machine().lower()}",
                "pythonVersion": platform.python_version(),
                "applicationVersion": application_version,
                "containerRuntime": {
                    "name": "docker",
                    "version": container_runtime_version,
                },
                "database": {
                    "engine": "postgresql",
                    "version": server_version,
                    "migration": SCHEMA_MIGRATIONS[-1],
                    "image": POSTGRES_IMAGE,
                },
            },
            "profile": {
                "name": "quiesced-logical-restore-v1",
                "backup": "pg_dump-custom-format",
                "restore": "fresh-database-pg_restore",
                "workload": "quiesced-representative-workflow",
                "scope": "complete-iip-schema",
                "objectives": {
                    "classification": "local-regression-guardrail",
                    "maximumRecoveryPointAgeMilliseconds": (
                        maximum_recovery_point_age_milliseconds
                    ),
                    "maximumRecoveryReadyMilliseconds": (
                        maximum_recovery_ready_milliseconds
                    ),
                },
            },
            "measurements": {
                "fixture": dict(fixture),
                "backup": {
                    "bytes": backup_bytes,
                    "durationMilliseconds": backup_duration_milliseconds,
                    "recoveryPoint": recovery_point,
                    "recoveryPointAgeMilliseconds": recovery_point_age_milliseconds,
                    "committedRecordLoss": 0,
                    "sourceStable": True,
                },
                "restore": {
                    "commandDurationMilliseconds": (
                        restore_command_duration_milliseconds
                    ),
                    "recoveryReadyMilliseconds": recovery_ready_milliseconds,
                    "isolatedDatabase": True,
                },
                "integrity": {
                    "matched": True,
                    "databaseDigest": source_manifest["databaseDigest"],
                    "tableCount": len(tables),
                    "rowCount": row_count,
                    "tables": tables,
                    "sequenceCount": len(source_manifest["sequences"]),
                    "sequenceDigest": source_manifest["sequenceDigest"],
                    "projectionVerification": dict(projection),
                },
            },
            "checks": checks,
        },
    }


def validate_report(
    document: object,
    *,
    require_clean: bool = False,
) -> None:
    """Apply JSON Schema plus derived semantic checks to a recovery report."""

    schema = json.loads(REPORT_SCHEMA.read_text(encoding="utf-8"))
    errors = validate_schemas.instance_validation_errors(
        schema,
        document,
        label="PostgreSQL recovery qualification report",
    )
    if errors:
        raise RuntimeError("; ".join(errors))
    assert isinstance(document, Mapping)
    metadata = document["metadata"]
    spec = document["spec"]
    assert isinstance(metadata, Mapping)
    assert isinstance(spec, Mapping)
    measurements = spec["measurements"]
    profile = spec["profile"]
    checks = spec["checks"]
    environment = spec["environment"]
    assert isinstance(measurements, Mapping)
    assert isinstance(profile, Mapping)
    assert isinstance(checks, Sequence)
    assert isinstance(environment, Mapping)
    check_ids = tuple(
        item.get("id") for item in checks if isinstance(item, Mapping)
    )
    if check_ids != CHECK_IDS:
        raise RuntimeError("backup_restore.check_set_invalid")
    check_status = {
        item["id"]: item["status"]
        for item in checks
        if isinstance(item, Mapping)
    }
    derived_status = (
        "qualified"
        if all(status == "passed" for status in check_status.values())
        else "failed"
    )
    if spec.get("status") != derived_status:
        raise RuntimeError("backup_restore.status_invalid")
    integrity = measurements["integrity"]
    fixture = measurements["fixture"]
    backup = measurements["backup"]
    restore = measurements["restore"]
    assert isinstance(integrity, Mapping)
    assert isinstance(fixture, Mapping)
    assert isinstance(backup, Mapping)
    assert isinstance(restore, Mapping)
    tables = integrity["tables"]
    assert isinstance(tables, Mapping)
    if integrity.get("tableCount") != len(tables) or integrity.get("rowCount") != sum(
        int(table["rowCount"])
        for table in tables.values()
        if isinstance(table, Mapping)
    ):
        raise RuntimeError("backup_restore.integrity_totals_invalid")
    expected_database_digest = "sha256:" + hashlib.sha256(
        canonical_json(
            {
                "tables": tables,
                "sequenceDigest": integrity["sequenceDigest"],
            }
        ).encode("utf-8")
    ).hexdigest()
    if integrity.get("databaseDigest") != expected_database_digest:
        raise RuntimeError("backup_restore.database_digest_invalid")
    projection = integrity["projectionVerification"]
    assert isinstance(projection, Mapping)
    structural_checks = {
        "representative-state": int(fixture["resourceCount"]) >= 1
        and int(fixture["relationshipCount"]) >= 1
        and fixture["resourceCount"] == projection["resourceCount"]
        and fixture["relationshipCount"] == projection["relationshipCount"],
        "complete-schema-backup": int(backup["bytes"]) > 0 and len(tables) > 0,
        "source-quiescence": backup["sourceStable"] is True
        and backup["committedRecordLoss"] == 0,
        "isolated-restore": restore["isolatedDatabase"] is True,
        "row-integrity": integrity["matched"] is True,
        "sequence-integrity": integrity["matched"] is True,
        "projection-consistency": projection["driftDetected"] is False,
        "recovery-point-age-objective": (
            backup["recoveryPointAgeMilliseconds"]
            <= profile["objectives"]["maximumRecoveryPointAgeMilliseconds"]
        ),
        "recovery-ready-objective": (
            restore["recoveryReadyMilliseconds"]
            <= profile["objectives"]["maximumRecoveryReadyMilliseconds"]
        ),
    }
    for identifier, passed in structural_checks.items():
        expected = "passed" if passed else "failed"
        if check_status.get(identifier) != expected:
            raise RuntimeError(f"backup_restore.{identifier}.status_invalid")
    expected_id_material = canonical_json(
        {
            "generatedAt": metadata["generatedAt"],
            "profile": profile["name"],
            "sourceRevision": metadata["sourceRevision"],
        }
    )
    expected_id = (
        "pgr_" + hashlib.sha256(expected_id_material.encode("utf-8")).hexdigest()[:32]
    )
    if metadata.get("id") != expected_id:
        raise RuntimeError("backup_restore.report_id_invalid")
    database = environment["database"]
    assert isinstance(database, Mapping)
    if database.get("image") != POSTGRES_IMAGE:
        raise RuntimeError("backup_restore.database_image_invalid")
    if require_clean:
        revision, dirty = source_identity()
        if (
            metadata.get("sourceDirty") is not False
            or dirty
            or metadata.get("sourceRevision") != revision
            or environment.get("applicationVersion") != application_version
            or database.get("migration") != SCHEMA_MIGRATIONS[-1]
        ):
            raise RuntimeError("backup_restore.clean_source_identity_invalid")


def write_report(path: Path, report: Mapping[str, object]) -> None:
    """Atomically publish a complete report without leaving a partial claim."""

    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def run_experiment(args: argparse.Namespace) -> dict[str, object]:
    validate_database_names(args.source_database, args.restored_database)
    if args.rpo_target_seconds <= 0 or args.rto_target_seconds <= 0:
        raise ValueError("backup_restore.objective_invalid")
    if shutil.which(args.docker_bin) is None:
        raise RuntimeError("Docker CLI not found; install and start Docker Desktop")
    runner = DockerComposeRunner(
        args.docker_bin,
        args.compose_file.resolve(),
        args.project_name,
    )
    source_url = database_url(args.source_database, args.database_host, args.database_port)
    restored_url = database_url(
        args.restored_database, args.database_host, args.database_port
    )
    revision, dirty = source_identity()
    container_runtime_version = docker_server_version(args.docker_bin)

    runner.cleanup()
    try:
        runner.run("up", "--detach", "--wait")
        fixture, fixture_tenant_id = seed_reference_workflow(source_url)
        source_manifest = database_manifest(source_url)
        server_version = database_version(source_url)
        recovery_point_started = time.perf_counter()
        recovery_point = database_time(source_url)

        backup_started = time.perf_counter()
        runner.exec(
            "pg_dump",
            "--username=postgres",
            f"--dbname={args.source_database}",
            "--format=custom",
            "--schema=iip",
            "--no-owner",
            "--no-privileges",
            f"--file={BACKUP_PATH}",
        )
        backup_duration = time.perf_counter() - backup_started
        recovery_point_age = time.perf_counter() - recovery_point_started
        backup_bytes = int(runner.exec("stat", "-c", "%s", BACKUP_PATH).stdout.strip())

        assert_identical_manifests(source_manifest, database_manifest(source_url))

        recovery_started = time.perf_counter()
        runner.exec(
            "dropdb",
            "--username=postgres",
            "--if-exists",
            args.restored_database,
        )
        runner.exec("createdb", "--username=postgres", args.restored_database)
        restore_started = time.perf_counter()
        runner.exec(
            "pg_restore",
            "--username=postgres",
            f"--dbname={args.restored_database}",
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            BACKUP_PATH,
        )
        restore_duration = time.perf_counter() - restore_started
        restored_manifest = database_manifest(restored_url)
        assert_identical_manifests(source_manifest, restored_manifest)
        projection = projection_verification(restored_url, fixture_tenant_id)
        recovery_ready = time.perf_counter() - recovery_started

        report = build_report(
            source_revision=revision,
            source_dirty=dirty,
            container_runtime_version=container_runtime_version,
            server_version=server_version,
            fixture=fixture,
            source_manifest=source_manifest,
            backup_bytes=backup_bytes,
            recovery_point=recovery_point,
            backup_duration_seconds=backup_duration,
            recovery_point_age_seconds=recovery_point_age,
            restore_command_duration_seconds=restore_duration,
            recovery_ready_seconds=recovery_ready,
            projection=projection,
            rpo_target_seconds=args.rpo_target_seconds,
            rto_target_seconds=args.rto_target_seconds,
        )
        validate_report(report)
        return report
    finally:
        runner.cleanup()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Run the IIP PostgreSQL backup/restore measurement"
    )
    result.add_argument(
        "--docker-bin", default=os.environ.get("IIP_DOCKER_BIN", "docker")
    )
    result.add_argument("--compose-file", type=Path, default=DEFAULT_COMPOSE_FILE)
    result.add_argument("--project-name", default=DEFAULT_PROJECT_NAME)
    result.add_argument("--source-database", default=DEFAULT_SOURCE_DATABASE)
    result.add_argument("--restored-database", default=DEFAULT_RESTORED_DATABASE)
    result.add_argument("--database-host", default=DEFAULT_DATABASE_HOST)
    result.add_argument("--database-port", type=int, default=DEFAULT_DATABASE_PORT)
    result.add_argument(
        "--rpo-target-seconds", type=float, default=DEFAULT_RPO_TARGET_SECONDS
    )
    result.add_argument(
        "--rto-target-seconds", type=float, default=DEFAULT_RTO_TARGET_SECONDS
    )
    result.add_argument("--output", type=Path)
    result.add_argument(
        "--verify-report",
        type=Path,
        help="verify an existing report without starting Docker",
    )
    result.add_argument(
        "--require-clean",
        action="store_true",
        help="require the report to match the current clean checkout",
    )
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.verify_report is not None:
        try:
            document = json.loads(args.verify_report.read_text(encoding="utf-8"))
            validate_report(document, require_clean=args.require_clean)
        except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
            print(f"backup/restore report verification failed: {error}", file=sys.stderr)
            return 1
        status = document["spec"]["status"]
        if status != "qualified":
            print("backup/restore report verification failed: profile failed", file=sys.stderr)
            return 1
        print(f"PostgreSQL recovery qualification verify passed: {args.verify_report}")
        return 0
    try:
        report = run_experiment(args)
    except (OSError, RuntimeError, ValueError, psycopg.Error, subprocess.SubprocessError) as error:
        print(f"backup/restore experiment failed: {error}", file=sys.stderr)
        return 1
    if args.output is not None:
        write_report(args.output, report)
        measurements = report["spec"]["measurements"]
        print(
            "PostgreSQL recovery qualification "
            f"{report['spec']['status']}: "
            f"{measurements['integrity']['tableCount']} tables, "
            f"{measurements['integrity']['rowCount']} rows, "
            f"ready in {measurements['restore']['recoveryReadyMilliseconds']} ms"
        )
        print(f"report: {args.output.resolve()}")
    else:
        print(canonical_json(report))
    return 0 if report["spec"]["status"] == "qualified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
