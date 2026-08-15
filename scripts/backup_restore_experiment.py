#!/usr/bin/env python3
"""Measure and verify a disposable PostgreSQL backup/restore recovery path."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
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

from iip.application.actions import (
    DecideActionCommand,
    ExecuteActionCommand,
    ProposeActionCommand,
)
from iip.application.ingest_collection import IngestCollectionCommand
from iip.application.investigate import RunInvestigationCommand
from iip.application.plugin_sessions import OpenPluginSessionCommand
from iip.application.ports import ActorContext
from iip.application.rebuild_projections import RebuildProjectionsCommand
from iip.bootstrap import build_postgres_runtime, build_projection_maintenance


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
    material = {"tables": tables, "sequences": normalized_sequences}
    database_digest = hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()
    return {
        **material,
        "databaseDigest": f"sha256:{database_digest}",
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


def seed_reference_workflow(database_url_value: str) -> dict[str, object]:
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
    return {
        "actionOutcome": action_result["spec"]["outcome"],
        "investigationOutcome": report["spec"]["outcome"],
        "pluginSessionStatus": plugin_session["status"],
        "resourceCount": len(resources),
        "tenantId": target.identity.tenant_id,
    }


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


def build_report(
    *,
    server_version: str,
    seed: Mapping[str, object],
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
    objectives_met = (
        recovery_point_age_seconds <= rpo_target_seconds
        and recovery_ready_seconds <= rto_target_seconds
    )
    return {
        "apiVersion": "iip.platform/operations/v1alpha1",
        "kind": "PostgreSQLBackupRestoreMeasurement",
        "metadata": {
            "capturedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "environment": "docker-desktop-local",
            "hostArchitecture": platform.machine(),
        },
        "spec": {
            "database": {
                "engine": "PostgreSQL",
                "serverVersion": server_version,
                "containerImage": "postgres:18.4-alpine",
            },
            "method": {
                "backup": "pg_dump-custom-format",
                "restore": "fresh-database-pg_restore",
                "workload": "quiesced-reference-workflow",
                "scope": "complete-iip-schema",
            },
            "objectives": {
                "maximumRecoveryPointAgeSeconds": rpo_target_seconds,
                "maximumRecoveryReadySeconds": rto_target_seconds,
                "classification": "local-experiment-guardrail",
            },
        },
        "status": {
            "backup": {
                "bytes": backup_bytes,
                "durationSeconds": round(backup_duration_seconds, 6),
                "recoveryPoint": recovery_point,
                "recoveryPointAgeSeconds": round(recovery_point_age_seconds, 6),
                "committedRecordLoss": 0,
            },
            "restore": {
                "commandDurationSeconds": round(restore_command_duration_seconds, 6),
                "recoveryReadySeconds": round(recovery_ready_seconds, 6),
            },
            "integrity": {
                "matched": True,
                "databaseDigest": source_manifest["databaseDigest"],
                "tableCount": len(tables),
                "rowCount": row_count,
                "tables": tables,
                "sequenceCount": len(source_manifest["sequences"]),
                "projectionVerification": dict(projection),
            },
            "seed": dict(seed),
            "objectivesMet": objectives_met,
        },
    }


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

    runner.cleanup()
    try:
        runner.run("up", "--detach", "--wait")
        seed = seed_reference_workflow(source_url)
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
        projection = projection_verification(restored_url, str(seed["tenantId"]))
        recovery_ready = time.perf_counter() - recovery_started

        report = build_report(
            server_version=server_version,
            seed=seed,
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
        if not report["status"]["objectivesMet"]:
            raise RuntimeError("backup_restore.objective_missed")
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
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        report = run_experiment(args)
    except (OSError, RuntimeError, ValueError, psycopg.Error, subprocess.SubprocessError) as error:
        print(f"backup/restore experiment failed: {error}", file=sys.stderr)
        return 1
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(canonical_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
