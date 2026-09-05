#!/usr/bin/env python3
"""Qualify disposable PostgreSQL physical continuity mechanics with Docker."""

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
from typing import Mapping, Sequence

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from iip import __version__ as application_version
from iip.adapters.postgres.store import SCHEMA_MIGRATIONS
from iip.application.ingest_resource import IngestResourceCommand
from iip.application.ports import ActorContext
from iip.bootstrap import build_postgres_runtime

import validate_schemas
from backup_restore_experiment import (
    POSTGRES_IMAGE,
    canonical_json,
    database_manifest,
    database_version,
    docker_server_version,
    projection_verification,
    seed_reference_workflow,
    source_identity,
    write_report,
)


ROOT = Path(__file__).resolve().parents[1]
REPORT_SCHEMA = (
    ROOT
    / "contracts"
    / "schemas"
    / "postgresql-continuity-qualification-report.schema.json"
)
DEFAULT_PROJECT_NAME = "iip-pg-continuity"
DEFAULT_DATABASE = "iip_continuity"
DEFAULT_CATCHUP_TARGET_SECONDS = 60.0
DEFAULT_FAILOVER_TARGET_SECONDS = 120.0
DEFAULT_PITR_TARGET_SECONDS = 180.0
RESTORE_POINT_NAME = "iip_pitr_target"
REPLICATION_ROLE = "iip_replication"
REPLICATION_SLOT = "iip_standby"
PROJECT_NAME = re.compile(r"iip-pg-continuity(?:-[a-z0-9]{1,12})?")
DATABASE_NAME = re.compile(r"[a-z][a-z0-9_]{0,62}")
CHECK_IDS = (
    "representative-state",
    "physical-base-backups",
    "streaming-standby",
    "zero-lag-cutover",
    "replica-row-integrity",
    "replica-sequence-safety",
    "primary-stopped",
    "standby-promoted",
    "failover-row-integrity",
    "failover-sequence-safety",
    "failover-projection-consistency",
    "wal-archive",
    "named-restore-target",
    "pitr-boundary",
    "pitr-row-integrity",
    "pitr-sequence-safety",
    "pitr-projection-consistency",
    "catchup-objective",
    "failover-ready-objective",
    "pitr-ready-objective",
)


def elapsed_milliseconds(seconds: float) -> int:
    """Round upward so qualification evidence never understates elapsed time."""

    return max(0, math.ceil(seconds * 1_000))


def validate_names(project_name: str, database_name: str) -> None:
    """Constrain every disposable Docker and database name before cleanup."""

    if PROJECT_NAME.fullmatch(project_name) is None:
        raise ValueError("postgresql_continuity.project_name_invalid")
    if DATABASE_NAME.fullmatch(database_name) is None or database_name in {
        "postgres",
        "template0",
        "template1",
    }:
        raise ValueError("postgresql_continuity.database_name_invalid")


@dataclass(frozen=True)
class DockerRunner:
    """Exact-name Docker boundary with ownership labels and redacted failures."""

    docker_bin: str
    project_name: str

    @property
    def label(self) -> str:
        return f"iip.continuity.project={self.project_name}"

    def name(self, suffix: str) -> str:
        return f"{self.project_name}-{suffix}"

    def run(
        self,
        *arguments: str,
        action: str,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        timeout: int = 600,
    ) -> subprocess.CompletedProcess[str]:
        process_env = os.environ.copy()
        if env is not None:
            process_env.update(env)
        result = subprocess.run(
            [self.docker_bin, *arguments],
            cwd=ROOT,
            env=process_env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if check and result.returncode != 0:
            raise RuntimeError(f"postgresql_continuity.{action}_failed")
        return result

    def _owned(self, resource_type: str, name: str) -> bool | None:
        command = (
            ("inspect", "--format", "{{index .Config.Labels \"iip.continuity.project\"}}", name)
            if resource_type == "container"
            else (
                resource_type,
                "inspect",
                "--format",
                "{{index .Labels \"iip.continuity.project\"}}",
                name,
            )
        )
        result = self.run(
            *command,
            action=f"inspect_{resource_type}",
            check=False,
            timeout=15,
        )
        if result.returncode != 0:
            return None
        return result.stdout.strip() == self.project_name

    def assert_available_or_owned(self) -> None:
        for suffix in ("primary", "standby", "pitr"):
            name = self.name(suffix)
            owned = self._owned("container", name)
            if owned is False:
                raise RuntimeError("postgresql_continuity.resource_collision")
        for suffix in ("primary-data", "standby-data", "pitr-data", "wal-archive"):
            name = self.name(suffix)
            owned = self._owned("volume", name)
            if owned is False:
                raise RuntimeError("postgresql_continuity.resource_collision")
        network = self.name("network")
        owned = self._owned("network", network)
        if owned is False:
            raise RuntimeError("postgresql_continuity.resource_collision")

    def cleanup(self) -> None:
        """Remove only exact resources carrying this experiment's ownership label."""

        for suffix in ("pitr", "standby", "primary"):
            name = self.name(suffix)
            if self._owned("container", name):
                self.run(
                    "rm",
                    "--force",
                    "--volumes",
                    name,
                    action="cleanup_container",
                    check=False,
                    timeout=60,
                )
        network = self.name("network")
        if self._owned("network", network):
            self.run(
                "network",
                "rm",
                network,
                action="cleanup_network",
                check=False,
                timeout=60,
            )
        for suffix in ("primary-data", "standby-data", "pitr-data", "wal-archive"):
            name = self.name(suffix)
            if self._owned("volume", name):
                self.run(
                    "volume",
                    "rm",
                    name,
                    action="cleanup_volume",
                    check=False,
                    timeout=60,
                )

    def create_storage(self) -> None:
        network = self.name("network")
        self.run(
            "network",
            "create",
            "--label",
            self.label,
            network,
            action="create_network",
        )
        for suffix in ("primary-data", "standby-data", "pitr-data", "wal-archive"):
            self.run(
                "volume",
                "create",
                "--label",
                self.label,
                self.name(suffix),
                action="create_volume",
            )
        for suffix in ("standby-data", "pitr-data"):
            self.run(
                "run",
                "--rm",
                "--mount",
                f"source={self.name(suffix)},target=/backup",
                POSTGRES_IMAGE,
                "sh",
                "-c",
                "mkdir -p /backup/18/docker && chown -R postgres:postgres /backup",
                action="prepare_backup_volume",
            )
        self.run(
            "run",
            "--rm",
            "--mount",
            f"source={self.name('wal-archive')},target=/wal-archive",
            POSTGRES_IMAGE,
            "sh",
            "-c",
            "chown postgres:postgres /wal-archive && chmod 0700 /wal-archive",
            action="prepare_archive_volume",
        )

    def host_port(self, container: str) -> int:
        result = self.run(
            "port",
            container,
            "5432/tcp",
            action="resolve_port",
            timeout=15,
        )
        for line in result.stdout.splitlines():
            candidate = line.rsplit(":", 1)[-1]
            if candidate.isdigit():
                return int(candidate)
        raise RuntimeError("postgresql_continuity.port_invalid")

    def start_primary(
        self, *, database_name: str, database_password: str
    ) -> str:
        name = self.name("primary")
        self.run(
            "run",
            "--detach",
            "--name",
            name,
            "--label",
            self.label,
            "--network",
            self.name("network"),
            "--publish",
            "127.0.0.1::5432",
            "--env",
            "POSTGRES_DB",
            "--env",
            "POSTGRES_PASSWORD",
            "--env",
            "POSTGRES_INITDB_ARGS",
            "--mount",
            f"source={self.name('primary-data')},target=/var/lib/postgresql",
            "--mount",
            f"source={self.name('wal-archive')},target=/wal-archive",
            POSTGRES_IMAGE,
            "postgres",
            "-c",
            "wal_level=replica",
            "-c",
            "max_wal_senders=5",
            "-c",
            "max_replication_slots=5",
            "-c",
            "hot_standby=on",
            "-c",
            "archive_mode=on",
            "-c",
            "archive_timeout=5s",
            "-c",
            "archive_command=test -f /wal-archive/%f || cp %p /wal-archive/%f",
            action="start_primary",
            env={
                "POSTGRES_DB": database_name,
                "POSTGRES_PASSWORD": database_password,
                "POSTGRES_INITDB_ARGS": "--auth-host=scram-sha-256",
            },
        )
        return name

    def take_basebackup(
        self,
        *,
        primary: str,
        destination_suffix: str,
        replication_password: str,
        standby: bool,
    ) -> None:
        arguments = [
            "run",
            "--rm",
            "--user",
            "postgres",
            "--network",
            self.name("network"),
            "--env",
            "PGPASSWORD",
            "--mount",
            f"source={self.name(destination_suffix)},target=/backup",
            POSTGRES_IMAGE,
            "pg_basebackup",
            f"--host={primary}",
            "--port=5432",
            f"--username={REPLICATION_ROLE}",
            f"--pgdata=/backup/18/docker",
            "--format=plain",
            "--wal-method=stream",
            "--checkpoint=fast",
            "--no-password",
        ]
        if standby:
            arguments.extend(
                (
                    "--write-recovery-conf",
                    f"--slot={REPLICATION_SLOT}",
                    "--create-slot",
                )
            )
        self.run(
            *arguments,
            action="physical_basebackup",
            env={"PGPASSWORD": replication_password},
        )

    def start_copy(self, suffix: str, *, recovery: bool = False) -> str:
        name = self.name(suffix)
        arguments = [
            "run",
            "--detach",
            "--name",
            name,
            "--label",
            self.label,
            "--network",
            self.name("network"),
            "--publish",
            "127.0.0.1::5432",
            "--mount",
            f"source={self.name(f'{suffix}-data')},target=/var/lib/postgresql",
            "--mount",
            (
                f"source={self.name('wal-archive')},target=/wal-archive,readonly"
                if recovery
                else f"source={self.name('wal-archive')},target=/wal-archive"
            ),
            POSTGRES_IMAGE,
            "postgres",
        ]
        if recovery:
            arguments.extend(
                (
                    "-c",
                    "archive_mode=off",
                    "-c",
                    "restore_command=cp /wal-archive/%f %p",
                    "-c",
                    f"recovery_target_name={RESTORE_POINT_NAME}",
                    "-c",
                    "recovery_target_action=promote",
                    "-c",
                    "recovery_target_timeline=current",
                )
            )
        self.run(*arguments, action=f"start_{suffix}")
        return name

    def volume_kibibytes(self, suffix: str) -> int:
        result = self.run(
            "run",
            "--rm",
            "--mount",
            f"source={self.name(suffix)},target=/data,readonly",
            POSTGRES_IMAGE,
            "sh",
            "-c",
            "du -sk /data | cut -f1",
            action="measure_volume",
        )
        value = result.stdout.strip()
        if not value.isdigit() or int(value) < 1:
            raise RuntimeError("postgresql_continuity.volume_size_invalid")
        return int(value)

    def archive_file_count(self) -> int:
        result = self.run(
            "run",
            "--rm",
            "--mount",
            f"source={self.name('wal-archive')},target=/wal-archive,readonly",
            POSTGRES_IMAGE,
            "sh",
            "-c",
            "find /wal-archive -maxdepth 1 -type f | wc -l",
            action="count_archive",
        )
        value = result.stdout.strip()
        if not value.isdigit():
            raise RuntimeError("postgresql_continuity.archive_count_invalid")
        return int(value)


def database_url(database: str, password: str, port: int) -> str:
    return f"postgresql://postgres:{password}@127.0.0.1:{port}/{database}"


def wait_database(
    url: str,
    *,
    expected_recovery: bool | None = None,
    timeout_seconds: float = 180.0,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(url, connect_timeout=2) as connection:
                recovering = bool(
                    connection.execute("SELECT pg_is_in_recovery()").fetchone()[0]
                )
            if expected_recovery is None or recovering is expected_recovery:
                return
        except psycopg.Error:
            pass
        time.sleep(0.25)
    raise RuntimeError("postgresql_continuity.database_not_ready")


def configure_replication(url: str, runner: DockerRunner, password: str) -> None:
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(
            sql.SQL("CREATE ROLE {} WITH REPLICATION LOGIN PASSWORD {}").format(
                sql.Identifier(REPLICATION_ROLE),
                sql.Literal(password),
            )
        )
    runner.run(
        "exec",
        runner.name("primary"),
        "sh",
        "-c",
        (
            "printf '%s\\n' "
            "'host replication iip_replication 0.0.0.0/0 scram-sha-256' "
            ">> \"$PGDATA/pg_hba.conf\""
        ),
        action="configure_replication_hba",
    )
    with psycopg.connect(url, autocommit=True) as connection:
        if connection.execute("SELECT pg_reload_conf()").fetchone()[0] is not True:
            raise RuntimeError("postgresql_continuity.replication_reload_failed")


def ingest_marker(url: str, *, stage: str, sequence: int) -> str:
    payload = json.loads(
        (ROOT / "contracts" / "examples" / "resource.json").read_text(
            encoding="utf-8"
        )
    )
    assert isinstance(payload, dict)
    payload = copy.deepcopy(payload)
    observed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    payload["metadata"]["observedAt"] = observed_at
    payload["metadata"]["observation"] = {
        "sourceId": "continuity-primary",
        "streamId": "obs_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "sequence": sequence,
        "mode": "incremental",
        "resourceVersion": str(sequence),
    }
    payload["metadata"]["labels"] = {"purpose": "continuity-qualification"}
    payload["spec"]["externalId"] = f"continuity/{stage}"
    payload["spec"]["displayName"] = f"continuity-{stage}"
    payload["spec"]["attributes"] = {"stage": stage}
    runtime = build_postgres_runtime(url, migrate=False)
    resource = runtime.ingestion.execute(
        IngestResourceCommand(
            ActorContext("postgresql-continuity-writer", "local"),
            payload,
            f"postgresql-continuity-{stage}",
        )
    )
    return resource.identity.uid


def marker_present(url: str, uid: str) -> bool:
    with psycopg.connect(url) as connection:
        row = connection.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM iip.resource_projections
                WHERE tenant_id = %s AND resource_uid = %s
            )
            """,
            ("local", uid),
        ).fetchone()
    return bool(row[0])


def physical_database_manifest(url: str) -> dict[str, object]:
    """Use direct sequence relations so recovery-mode catalog views do not hide state."""

    manifest = database_manifest(url)
    with psycopg.connect(url, row_factory=dict_row) as connection:
        sequence_rows = connection.execute(
            """
            SELECT sequencename AS name,
                   start_value AS "startValue",
                   min_value AS "minValue",
                   max_value AS "maxValue",
                   increment_by AS "incrementBy",
                   cycle,
                   cache_size AS "cacheSize"
            FROM pg_sequences
            WHERE schemaname = 'iip'
            ORDER BY sequencename
            """
        ).fetchall()
        sequences: list[dict[str, object]] = []
        for sequence_row in sequence_rows:
            item = dict(sequence_row)
            state = connection.execute(
                sql.SQL("SELECT last_value AS value FROM {}.{}").format(
                    sql.Identifier("iip"),
                    sql.Identifier(str(item["name"])),
                )
            ).fetchone()
            item["lastValue"] = int(state["value"])
            sequences.append(item)
    sequence_digest = "sha256:" + hashlib.sha256(
        canonical_json(sequences).encode("utf-8")
    ).hexdigest()
    tables = manifest["tables"]
    database_digest = "sha256:" + hashlib.sha256(
        canonical_json(
            {"tables": tables, "sequenceDigest": sequence_digest}
        ).encode("utf-8")
    ).hexdigest()
    manifest.update(
        {
            "sequences": sequences,
            "sequenceDigest": sequence_digest,
            "databaseDigest": database_digest,
        }
    )
    return manifest


def timeline(url: str, *, checkpoint: bool = False) -> int:
    with psycopg.connect(url, autocommit=True) as connection:
        if checkpoint:
            connection.execute("CHECKPOINT")
        row = connection.execute(
            "SELECT timeline_id FROM pg_control_checkpoint()"
        ).fetchone()
    return int(row[0])


def switch_and_archive(
    url: str, runner: DockerRunner, *, timeout_seconds: float = 90.0
) -> str:
    with psycopg.connect(url, autocommit=True) as connection:
        filename = str(
            connection.execute(
                "SELECT pg_walfile_name(pg_current_wal_lsn())"
            ).fetchone()[0]
        )
        connection.execute("SELECT pg_switch_wal()")
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        result = runner.run(
            "exec",
            runner.name("primary"),
            "test",
            "-f",
            f"/wal-archive/{filename}",
            action="archive_probe",
            check=False,
            timeout=15,
        )
        if result.returncode == 0:
            return filename
        time.sleep(0.25)
    raise RuntimeError("postgresql_continuity.wal_archive_timeout")


def primary_flush_lsn(url: str) -> str:
    with psycopg.connect(url) as connection:
        return str(
            connection.execute(
                "SELECT pg_current_wal_flush_lsn()::text"
            ).fetchone()[0]
        )


def wait_replay(
    standby_url: str, target_lsn: str, *, timeout_seconds: float
) -> tuple[str, int]:
    deadline = time.monotonic() + timeout_seconds
    last_lsn = "0/0"
    last_lag = 1
    while time.monotonic() < deadline:
        try:
            with psycopg.connect(standby_url) as connection:
                row = connection.execute(
                    """
                    SELECT COALESCE(pg_last_wal_replay_lsn()::text, '0/0'),
                           GREATEST(
                               CEIL(pg_wal_lsn_diff(%s::pg_lsn, pg_last_wal_replay_lsn())),
                               0
                           )::bigint
                    """,
                    (target_lsn,),
                ).fetchone()
            last_lsn = str(row[0])
            last_lag = int(row[1])
            if last_lag == 0:
                return last_lsn, last_lag
        except psycopg.Error:
            pass
        time.sleep(0.1)
    raise RuntimeError("postgresql_continuity.replica_catchup_timeout")


def row_digest(manifest: Mapping[str, object]) -> str:
    return "sha256:" + hashlib.sha256(
        canonical_json(manifest["tables"]).encode("utf-8")
    ).hexdigest()


def sequence_floor_satisfied(
    expected: Mapping[str, object], actual: Mapping[str, object]
) -> bool:
    expected_sequences = expected.get("sequences")
    actual_sequences = actual.get("sequences")
    if not isinstance(expected_sequences, Sequence) or not isinstance(
        actual_sequences, Sequence
    ):
        return False
    expected_by_name = {
        str(item["name"]): item
        for item in expected_sequences
        if isinstance(item, Mapping) and "name" in item
    }
    actual_by_name = {
        str(item["name"]): item
        for item in actual_sequences
        if isinstance(item, Mapping) and "name" in item
    }
    if set(expected_by_name) != set(actual_by_name):
        return False
    for name, expected_item in expected_by_name.items():
        actual_item = actual_by_name[name]
        expected_configuration = {
            key: value for key, value in expected_item.items() if key != "lastValue"
        }
        actual_configuration = {
            key: value for key, value in actual_item.items() if key != "lastValue"
        }
        if expected_configuration != actual_configuration:
            return False
        increment = int(expected_item.get("incrementBy", 1))
        expected_value = int(expected_item["lastValue"])
        actual_value = int(actual_item["lastValue"])
        if (increment > 0 and actual_value < expected_value) or (
            increment < 0 and actual_value > expected_value
        ):
            return False
    return True


def integrity(
    expected: Mapping[str, object],
    manifest: Mapping[str, object],
    projection: Mapping[str, object],
) -> dict[str, object]:
    tables = manifest["tables"]
    sequences = manifest["sequences"]
    assert isinstance(tables, Mapping)
    assert isinstance(sequences, Sequence)
    return {
        "rowMatched": expected["tables"] == tables,
        "rowDigest": row_digest(manifest),
        "tableCount": len(tables),
        "rowCount": sum(
            int(table["rowCount"])
            for table in tables.values()
            if isinstance(table, Mapping)
        ),
        "tables": dict(tables),
        "sequenceCount": len(sequences),
        "sequenceDigest": manifest["sequenceDigest"],
        "sequenceFloorSatisfied": sequence_floor_satisfied(expected, manifest),
        "projectionVerification": dict(projection),
    }


def assert_physical_state(
    expected: Mapping[str, object], actual: Mapping[str, object], *, stage: str
) -> None:
    if expected.get("tables") != actual.get("tables"):
        raise RuntimeError(f"postgresql_continuity.{stage}_row_integrity_mismatch")
    if not sequence_floor_satisfied(expected, actual):
        raise RuntimeError(f"postgresql_continuity.{stage}_sequence_floor_invalid")


def check(identifier: str, passed: bool) -> dict[str, str]:
    if passed:
        return {"id": identifier, "status": "passed"}
    return {
        "id": identifier,
        "status": "failed",
        "errorCode": f"postgresql.continuity.{identifier}.failed",
    }


def build_report(
    *,
    source_revision: str,
    source_dirty: bool,
    container_runtime_version: str,
    server_version: str,
    fixture: Mapping[str, object],
    backup_bytes: int,
    backup_duration_seconds: float,
    primary_timeline: int,
    primary_flush_lsn_value: str,
    standby_replay_lsn: str,
    replay_lag_bytes: int,
    catchup_seconds: float,
    source_manifest: Mapping[str, object],
    standby_manifest: Mapping[str, object],
    promoted_timeline: int,
    failover_ready_seconds: float,
    failover_manifest: Mapping[str, object],
    failover_projection: Mapping[str, object],
    archived_wal_file_count: int,
    pitr_ready_seconds: float,
    target_manifest: Mapping[str, object],
    pitr_manifest: Mapping[str, object],
    pitr_projection: Mapping[str, object],
    boundary: Mapping[str, bool],
    catchup_target_seconds: float,
    failover_target_seconds: float,
    pitr_target_seconds: float,
) -> dict[str, object]:
    maximum_catchup = elapsed_milliseconds(catchup_target_seconds)
    maximum_failover = elapsed_milliseconds(failover_target_seconds)
    maximum_pitr = elapsed_milliseconds(pitr_target_seconds)
    backup_duration = elapsed_milliseconds(backup_duration_seconds)
    catchup_duration = elapsed_milliseconds(catchup_seconds)
    failover_duration = elapsed_milliseconds(failover_ready_seconds)
    pitr_duration = elapsed_milliseconds(pitr_ready_seconds)
    source_row_digest = row_digest(source_manifest)
    standby_row_digest = row_digest(standby_manifest)
    target_row_digest = row_digest(target_manifest)
    recovered_row_digest = row_digest(pitr_manifest)
    replica_rows_match = source_manifest["tables"] == standby_manifest["tables"]
    replica_sequences_safe = sequence_floor_satisfied(source_manifest, standby_manifest)
    failover_rows_match = source_manifest["tables"] == failover_manifest["tables"]
    failover_sequences_safe = sequence_floor_satisfied(source_manifest, failover_manifest)
    pitr_rows_match = target_manifest["tables"] == pitr_manifest["tables"]
    pitr_sequences_safe = sequence_floor_satisfied(target_manifest, pitr_manifest)
    checks = [
        check("representative-state", int(fixture.get("resourceCount", 0)) >= 1 and int(fixture.get("relationshipCount", 0)) >= 1),
        check("physical-base-backups", backup_bytes > 0),
        check("streaming-standby", True),
        check("zero-lag-cutover", replay_lag_bytes == 0),
        check("replica-row-integrity", replica_rows_match),
        check("replica-sequence-safety", replica_sequences_safe),
        check("primary-stopped", True),
        check("standby-promoted", promoted_timeline > primary_timeline),
        check("failover-row-integrity", failover_rows_match),
        check("failover-sequence-safety", failover_sequences_safe),
        check("failover-projection-consistency", failover_projection.get("driftDetected") is False),
        check("wal-archive", archived_wal_file_count >= 2),
        check("named-restore-target", True),
        check("pitr-boundary", all(boundary.get(key) is expected for key, expected in (("baselinePresent", True), ("beforeTargetPresent", True), ("afterTargetAbsent", True)))),
        check("pitr-row-integrity", pitr_rows_match),
        check("pitr-sequence-safety", pitr_sequences_safe),
        check("pitr-projection-consistency", pitr_projection.get("driftDetected") is False),
        check("catchup-objective", catchup_duration <= maximum_catchup),
        check("failover-ready-objective", failover_duration <= maximum_failover),
        check("pitr-ready-objective", pitr_duration <= maximum_pitr),
    ]
    status = "qualified" if all(item["status"] == "passed" for item in checks) else "failed"
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    identity_material = canonical_json(
        {
            "generatedAt": generated_at,
            "profile": "physical-streaming-pitr-v1",
            "sourceRevision": source_revision,
        }
    )
    return {
        "apiVersion": "iip.platform/v1alpha1",
        "kind": "PostgreSQLContinuityQualificationReport",
        "metadata": {
            "id": "pgc_" + hashlib.sha256(identity_material.encode("utf-8")).hexdigest()[:32],
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
                "containerRuntime": {"name": "docker", "version": container_runtime_version},
                "database": {
                    "engine": "postgresql",
                    "version": server_version,
                    "migration": SCHEMA_MIGRATIONS[-1],
                    "image": POSTGRES_IMAGE,
                },
            },
            "profile": {
                "name": "physical-streaming-pitr-v1",
                "backup": "pg-basebackup-stream-wal",
                "replication": "asynchronous-streaming",
                "failover": "manual-pg-promote",
                "recovery": "archived-wal-named-restore-point",
                "workload": "bounded-committed-resource-writes",
                "scope": "complete-iip-schema",
                "objectives": {
                    "classification": "local-regression-guardrail",
                    "maximumCatchupMilliseconds": maximum_catchup,
                    "maximumFailoverReadyMilliseconds": maximum_failover,
                    "maximumPitrReadyMilliseconds": maximum_pitr,
                },
            },
            "measurements": {
                "fixture": dict(fixture),
                "physicalBackup": {
                    "copyCount": 2,
                    "totalBytes": backup_bytes,
                    "durationMilliseconds": backup_duration,
                },
                "replication": {
                    "standbyInRecovery": True,
                    "primaryTimeline": primary_timeline,
                    "primaryFlushLsn": primary_flush_lsn_value,
                    "standbyReplayLsn": standby_replay_lsn,
                    "replayLagBytes": replay_lag_bytes,
                    "catchupMilliseconds": catchup_duration,
                    "sourceRowDigest": source_row_digest,
                    "standbyRowDigest": standby_row_digest,
                    "sourceSequenceDigest": source_manifest["sequenceDigest"],
                    "standbySequenceDigest": standby_manifest["sequenceDigest"],
                    "sequenceFloorSatisfied": replica_sequences_safe,
                },
                "failover": {
                    "primaryStopped": True,
                    "standbyPromoted": True,
                    "promotedTimeline": promoted_timeline,
                    "readyMilliseconds": failover_duration,
                    "committedRecordLoss": 0,
                    "integrity": integrity(
                        source_manifest, failover_manifest, failover_projection
                    ),
                },
                "pitr": {
                    "restorePointName": RESTORE_POINT_NAME,
                    "archivedWalFileCount": archived_wal_file_count,
                    "targetReached": True,
                    "readyMilliseconds": pitr_duration,
                    "boundary": dict(boundary),
                    "expectedTargetRowDigest": target_row_digest,
                    "recoveredRowDigest": recovered_row_digest,
                    "expectedSequenceDigest": target_manifest["sequenceDigest"],
                    "recoveredSequenceDigest": pitr_manifest["sequenceDigest"],
                    "sequenceFloorSatisfied": pitr_sequences_safe,
                    "integrity": integrity(
                        target_manifest, pitr_manifest, pitr_projection
                    ),
                },
            },
            "checks": checks,
        },
    }


def _verify_integrity(value: Mapping[str, object], *, label: str) -> None:
    tables = value["tables"]
    assert isinstance(tables, Mapping)
    row_count = sum(
        int(table["rowCount"])
        for table in tables.values()
        if isinstance(table, Mapping)
    )
    if value.get("tableCount") != len(tables) or value.get("rowCount") != row_count:
        raise RuntimeError(f"postgresql_continuity.{label}_totals_invalid")
    expected_digest = "sha256:" + hashlib.sha256(
        canonical_json(tables).encode("utf-8")
    ).hexdigest()
    if value.get("rowDigest") != expected_digest:
        raise RuntimeError(f"postgresql_continuity.{label}_digest_invalid")


def validate_report(document: object, *, require_clean: bool = False) -> None:
    """Apply schema and derived semantic checks to continuity evidence."""

    schema = json.loads(REPORT_SCHEMA.read_text(encoding="utf-8"))
    errors = validate_schemas.instance_validation_errors(
        schema,
        document,
        label="PostgreSQL continuity qualification report",
    )
    if errors:
        raise RuntimeError("; ".join(errors))
    assert isinstance(document, Mapping)
    metadata = document["metadata"]
    spec = document["spec"]
    assert isinstance(metadata, Mapping)
    assert isinstance(spec, Mapping)
    environment = spec["environment"]
    profile = spec["profile"]
    measurements = spec["measurements"]
    checks = spec["checks"]
    assert isinstance(environment, Mapping)
    assert isinstance(profile, Mapping)
    assert isinstance(measurements, Mapping)
    assert isinstance(checks, Sequence)
    check_ids = tuple(item.get("id") for item in checks if isinstance(item, Mapping))
    if check_ids != CHECK_IDS:
        raise RuntimeError("postgresql_continuity.check_set_invalid")
    check_status = {
        str(item["id"]): item["status"] for item in checks if isinstance(item, Mapping)
    }
    derived_status = "qualified" if all(value == "passed" for value in check_status.values()) else "failed"
    if spec.get("status") != derived_status:
        raise RuntimeError("postgresql_continuity.status_invalid")
    fixture = measurements["fixture"]
    backup = measurements["physicalBackup"]
    replication = measurements["replication"]
    failover = measurements["failover"]
    pitr = measurements["pitr"]
    assert isinstance(fixture, Mapping)
    assert isinstance(backup, Mapping)
    assert isinstance(replication, Mapping)
    assert isinstance(failover, Mapping)
    assert isinstance(pitr, Mapping)
    failover_integrity = failover["integrity"]
    pitr_integrity = pitr["integrity"]
    assert isinstance(failover_integrity, Mapping)
    assert isinstance(pitr_integrity, Mapping)
    _verify_integrity(failover_integrity, label="failover_integrity")
    _verify_integrity(pitr_integrity, label="pitr_integrity")
    failover_projection = failover_integrity["projectionVerification"]
    pitr_projection = pitr_integrity["projectionVerification"]
    boundary = pitr["boundary"]
    objectives = profile["objectives"]
    assert isinstance(failover_projection, Mapping)
    assert isinstance(pitr_projection, Mapping)
    assert isinstance(boundary, Mapping)
    assert isinstance(objectives, Mapping)
    structural_checks = {
        "representative-state": int(fixture["resourceCount"]) >= 1 and int(fixture["relationshipCount"]) >= 1,
        "physical-base-backups": backup["copyCount"] == 2 and int(backup["totalBytes"]) > 0,
        "streaming-standby": replication["standbyInRecovery"] is True,
        "zero-lag-cutover": replication["replayLagBytes"] == 0,
        "replica-row-integrity": replication["sourceRowDigest"] == replication["standbyRowDigest"],
        "replica-sequence-safety": replication["sequenceFloorSatisfied"] is True,
        "primary-stopped": failover["primaryStopped"] is True,
        "standby-promoted": failover["standbyPromoted"] is True and int(failover["promotedTimeline"]) > int(replication["primaryTimeline"]),
        "failover-row-integrity": failover_integrity["rowMatched"] is True and failover_integrity["rowDigest"] == replication["sourceRowDigest"],
        "failover-sequence-safety": failover_integrity["sequenceFloorSatisfied"] is True,
        "failover-projection-consistency": failover_projection["driftDetected"] is False,
        "wal-archive": int(pitr["archivedWalFileCount"]) >= 2,
        "named-restore-target": pitr["restorePointName"] == RESTORE_POINT_NAME and pitr["targetReached"] is True,
        "pitr-boundary": boundary["baselinePresent"] is True and boundary["beforeTargetPresent"] is True and boundary["afterTargetAbsent"] is True,
        "pitr-row-integrity": pitr["expectedTargetRowDigest"] == pitr["recoveredRowDigest"] == pitr_integrity["rowDigest"],
        "pitr-sequence-safety": pitr["sequenceFloorSatisfied"] is True and pitr_integrity["sequenceFloorSatisfied"] is True,
        "pitr-projection-consistency": pitr_projection["driftDetected"] is False,
        "catchup-objective": replication["catchupMilliseconds"] <= objectives["maximumCatchupMilliseconds"],
        "failover-ready-objective": failover["readyMilliseconds"] <= objectives["maximumFailoverReadyMilliseconds"],
        "pitr-ready-objective": pitr["readyMilliseconds"] <= objectives["maximumPitrReadyMilliseconds"],
    }
    for identifier, passed in structural_checks.items():
        expected = "passed" if passed else "failed"
        if check_status.get(identifier) != expected:
            raise RuntimeError(f"postgresql_continuity.{identifier}.status_invalid")
    identity_material = canonical_json(
        {
            "generatedAt": metadata["generatedAt"],
            "profile": profile["name"],
            "sourceRevision": metadata["sourceRevision"],
        }
    )
    expected_id = "pgc_" + hashlib.sha256(identity_material.encode("utf-8")).hexdigest()[:32]
    if metadata.get("id") != expected_id:
        raise RuntimeError("postgresql_continuity.report_id_invalid")
    database = environment["database"]
    assert isinstance(database, Mapping)
    if database.get("image") != POSTGRES_IMAGE:
        raise RuntimeError("postgresql_continuity.database_image_invalid")
    if require_clean:
        revision, dirty = source_identity()
        if (
            metadata.get("sourceDirty") is not False
            or dirty
            or metadata.get("sourceRevision") != revision
            or environment.get("applicationVersion") != application_version
            or database.get("migration") != SCHEMA_MIGRATIONS[-1]
        ):
            raise RuntimeError("postgresql_continuity.clean_source_identity_invalid")


def run_experiment(args: argparse.Namespace) -> dict[str, object]:
    validate_names(args.project_name, args.database)
    if min(
        args.catchup_target_seconds,
        args.failover_target_seconds,
        args.pitr_target_seconds,
    ) <= 0:
        raise ValueError("postgresql_continuity.objective_invalid")
    if shutil.which(args.docker_bin) is None:
        raise RuntimeError("Docker CLI not found; install and start Docker Desktop")
    runner = DockerRunner(args.docker_bin, args.project_name)
    runner.assert_available_or_owned()
    runner.cleanup()
    revision, dirty = source_identity()
    runtime_version = docker_server_version(args.docker_bin)
    database_password = secrets.token_hex(24)
    replication_password = secrets.token_hex(24)
    try:
        runner.create_storage()
        primary = runner.start_primary(
            database_name=args.database,
            database_password=database_password,
        )
        primary_url = database_url(
            args.database, database_password, runner.host_port(primary)
        )
        wait_database(primary_url, expected_recovery=False)
        configure_replication(primary_url, runner, replication_password)
        fixture, tenant_id = seed_reference_workflow(primary_url)
        fixture = dict(fixture)
        fixture.update(
            {"beforeTargetResourceCount": 1, "afterTargetResourceCount": 1}
        )
        server_version = database_version(primary_url)
        primary_timeline = timeline(primary_url)

        backup_started = time.perf_counter()
        runner.take_basebackup(
            primary=primary,
            destination_suffix="pitr-data",
            replication_password=replication_password,
            standby=False,
        )
        runner.take_basebackup(
            primary=primary,
            destination_suffix="standby-data",
            replication_password=replication_password,
            standby=True,
        )
        backup_duration = time.perf_counter() - backup_started
        backup_bytes = 1024 * (
            runner.volume_kibibytes("pitr-data")
            + runner.volume_kibibytes("standby-data")
        )

        standby = runner.start_copy("standby")
        standby_url = database_url(
            args.database, database_password, runner.host_port(standby)
        )
        wait_database(standby_url, expected_recovery=True)

        before_uid = ingest_marker(primary_url, stage="before-target", sequence=100)
        target_manifest = physical_database_manifest(primary_url)
        with psycopg.connect(primary_url, autocommit=True) as connection:
            connection.execute(
                "SELECT pg_create_restore_point(%s)", (RESTORE_POINT_NAME,)
            )
        switch_and_archive(primary_url, runner)
        after_uid = ingest_marker(primary_url, stage="after-target", sequence=101)
        switch_and_archive(primary_url, runner)
        source_manifest = physical_database_manifest(primary_url)
        source_projection = projection_verification(primary_url, tenant_id)

        flush_lsn = primary_flush_lsn(primary_url)
        catchup_started = time.perf_counter()
        replay_lsn, replay_lag = wait_replay(
            standby_url,
            flush_lsn,
            timeout_seconds=args.catchup_target_seconds,
        )
        catchup_duration = time.perf_counter() - catchup_started
        standby_manifest = physical_database_manifest(standby_url)
        assert_physical_state(
            source_manifest, standby_manifest, stage="standby"
        )

        failover_started = time.perf_counter()
        runner.run(
            "stop",
            "--time",
            "30",
            primary,
            action="stop_primary",
            timeout=60,
        )
        with psycopg.connect(standby_url, autocommit=True) as connection:
            promoted = connection.execute("SELECT pg_promote(true, 30)").fetchone()[0]
        if promoted is not True:
            raise RuntimeError("postgresql_continuity.promotion_failed")
        wait_database(standby_url, expected_recovery=False)
        promoted_timeline = timeline(standby_url, checkpoint=True)
        failover_manifest = physical_database_manifest(standby_url)
        assert_physical_state(
            source_manifest, failover_manifest, stage="failover"
        )
        failover_projection = projection_verification(standby_url, tenant_id)
        if failover_projection != source_projection:
            raise RuntimeError("postgresql_continuity.failover_projection_mismatch")
        failover_ready = time.perf_counter() - failover_started

        runner.run(
            "run",
            "--rm",
            "--mount",
            f"source={runner.name('pitr-data')},target=/backup",
            POSTGRES_IMAGE,
            "sh",
            "-c",
            f"touch /backup/18/docker/recovery.signal && chown postgres:postgres /backup/18/docker/recovery.signal",
            action="prepare_recovery_signal",
        )
        pitr_started = time.perf_counter()
        pitr = runner.start_copy("pitr", recovery=True)
        pitr_url = database_url(
            args.database, database_password, runner.host_port(pitr)
        )
        wait_database(
            pitr_url,
            expected_recovery=False,
            timeout_seconds=args.pitr_target_seconds,
        )
        pitr_manifest = physical_database_manifest(pitr_url)
        assert_physical_state(target_manifest, pitr_manifest, stage="pitr")
        pitr_projection = projection_verification(pitr_url, tenant_id)
        boundary = {
            "baselinePresent": int(pitr_projection["resourceCount"])
            >= int(fixture["resourceCount"]),
            "beforeTargetPresent": marker_present(pitr_url, before_uid),
            "afterTargetAbsent": not marker_present(pitr_url, after_uid),
        }
        if not all(boundary.values()):
            raise RuntimeError("postgresql_continuity.pitr_boundary_mismatch")
        pitr_ready = time.perf_counter() - pitr_started
        archived_wal_count = runner.archive_file_count()

        report = build_report(
            source_revision=revision,
            source_dirty=dirty,
            container_runtime_version=runtime_version,
            server_version=server_version,
            fixture=fixture,
            backup_bytes=backup_bytes,
            backup_duration_seconds=backup_duration,
            primary_timeline=primary_timeline,
            primary_flush_lsn_value=flush_lsn,
            standby_replay_lsn=replay_lsn,
            replay_lag_bytes=replay_lag,
            catchup_seconds=catchup_duration,
            source_manifest=source_manifest,
            standby_manifest=standby_manifest,
            promoted_timeline=promoted_timeline,
            failover_ready_seconds=failover_ready,
            failover_manifest=failover_manifest,
            failover_projection=failover_projection,
            archived_wal_file_count=archived_wal_count,
            pitr_ready_seconds=pitr_ready,
            target_manifest=target_manifest,
            pitr_manifest=pitr_manifest,
            pitr_projection=pitr_projection,
            boundary=boundary,
            catchup_target_seconds=args.catchup_target_seconds,
            failover_target_seconds=args.failover_target_seconds,
            pitr_target_seconds=args.pitr_target_seconds,
        )
        validate_report(report)
        return report
    finally:
        runner.cleanup()


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Run the IIP PostgreSQL physical continuity qualification"
    )
    result.add_argument(
        "--docker-bin", default=os.environ.get("IIP_DOCKER_BIN", "docker")
    )
    result.add_argument("--project-name", default=DEFAULT_PROJECT_NAME)
    result.add_argument("--database", default=DEFAULT_DATABASE)
    result.add_argument(
        "--catchup-target-seconds",
        type=float,
        default=DEFAULT_CATCHUP_TARGET_SECONDS,
    )
    result.add_argument(
        "--failover-target-seconds",
        type=float,
        default=DEFAULT_FAILOVER_TARGET_SECONDS,
    )
    result.add_argument(
        "--pitr-target-seconds", type=float, default=DEFAULT_PITR_TARGET_SECONDS
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
            print(f"continuity report verification failed: {error}", file=sys.stderr)
            return 1
        if document["spec"]["status"] != "qualified":
            print("continuity report verification failed: profile failed", file=sys.stderr)
            return 1
        print(f"PostgreSQL continuity qualification verify passed: {args.verify_report}")
        return 0
    try:
        report = run_experiment(args)
    except (OSError, RuntimeError, ValueError, psycopg.Error, subprocess.SubprocessError) as error:
        print(f"PostgreSQL continuity experiment failed: {error}", file=sys.stderr)
        return 1
    if args.output is not None:
        write_report(args.output, report)
        measurements = report["spec"]["measurements"]
        print(
            "PostgreSQL continuity qualification "
            f"{report['spec']['status']}: "
            f"catch-up {measurements['replication']['catchupMilliseconds']} ms, "
            f"failover {measurements['failover']['readyMilliseconds']} ms, "
            f"PITR {measurements['pitr']['readyMilliseconds']} ms"
        )
        print(f"report: {args.output.resolve()}")
    else:
        print(canonical_json(report))
    return 0 if report["spec"]["status"] == "qualified" else 1


if __name__ == "__main__":
    raise SystemExit(main())
