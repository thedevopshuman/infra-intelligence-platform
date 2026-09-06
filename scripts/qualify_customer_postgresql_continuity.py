#!/usr/bin/env python3
"""Observe a customer PostgreSQL primary promotion under live platform traffic."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import stat
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import psycopg
from jsonschema import Draft202012Validator, FormatChecker
from psycopg.rows import dict_row

import qualify_customer_processing_continuity as processing
import qualify_ingress_availability as ingress


ROOT = Path(__file__).resolve().parents[1]
PROFILE_SCHEMA = ROOT / "contracts/schemas/customer-postgresql-continuity-profile.schema.json"
REPORT_SCHEMA = ROOT / "contracts/schemas/customer-postgresql-continuity-qualification-report.schema.json"
API_VERSION = "iip.platform/v1alpha1"
KIND = "CustomerPostgreSQLContinuityQualificationReport"
QUALIFICATION_LEVEL = "customer-postgresql-primary-promotion-v1"
REPORT_ID = re.compile(r"^cpgq_[a-f0-9]{32}$")
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
PHASES = ("baseline", "promotion-observation", "recovery")
WORKFLOW_BOUNDARIES = (
    "completed-before-promotion",
    "submitted-before-observed-after-promotion",
    "completed-after-promotion",
)
CHECK_IDS = (
    "source-binding",
    "minimized-output",
    "explicit-target-bindings",
    "api-verified-https",
    "receiver-mutual-tls",
    "database-verified-tls",
    "database-read-only-probe",
    "initial-writable-primary",
    "promotion-observed",
    "timeline-advanced",
    "promoted-writable-primary",
    "api-availability",
    "receiver-availability",
    "bounded-api-outage",
    "bounded-receiver-outage",
    "receiver-durable-intake",
    "workflow-baseline-completion",
    "workflow-survived-promotion",
    "workflow-recovery-completion",
)
LIMITATIONS = (
    "operator-triggered-planned-promotion",
    "single-stable-database-endpoint",
    "topology-and-failure-domain-not-proven",
    "fencing-and-split-brain-not-proven",
    "zero-data-loss-and-rpo-not-proven",
    "synthetic-qualification-traffic",
    "regional-disaster-recovery-not-proven",
)
FORBIDDEN_RETAINED_KEYS = frozenset(
    {
        "actorId",
        "address",
        "baseUrl",
        "certificate",
        "clientKey",
        "context",
        "credential",
        "databaseName",
        "databaseUser",
        "host",
        "namespace",
        "password",
        "resourceUid",
        "secret",
        "tenantId",
        "token",
        "url",
    }
)
MAX_PROFILE_BYTES = 65_536
MAX_SECRET_BYTES = 8_192
MAX_TLS_BYTES = 1024 * 1024
DEFAULT_ATTEMPTS_PER_PHASE = 20
DEFAULT_INTERVAL_MILLISECONDS = 250
DEFAULT_PROMOTION_MILLISECONDS = 300_000
DEFAULT_WORKFLOW_MILLISECONDS = 60_000
DEFAULT_REQUEST_MILLISECONDS = 2_000
DEFAULT_AVAILABILITY_BASIS_POINTS = 9_500
DEFAULT_MAXIMUM_CONSECUTIVE_FAILURES = 20


class CustomerPostgreSQLContinuityError(RuntimeError):
    """Stable customer PostgreSQL continuity failure."""


def _fail(code: str) -> None:
    raise CustomerPostgreSQLContinuityError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _digest_value(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        _fail("customer-postgresql-continuity.time.invalid")
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _parse_timestamp(value: object, code: str) -> datetime:
    if not isinstance(value, str):
        _fail(code)
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _fail(code)
    if result.tzinfo is None:
        _fail(code)
    return result.astimezone(timezone.utc)


def _mapping(value: object, code: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(code)
    return value


def _integer(value: object, minimum: int, maximum: int, code: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not minimum <= value <= maximum
    ):
        _fail(code)
    return value


def _regular_file(
    path: Path, *, code: str, maximum_bytes: int, protected: bool = False
) -> Path:
    candidate = path.expanduser()
    try:
        if candidate.is_symlink():
            _fail(code)
        details = candidate.stat()
    except OSError:
        _fail(code)
    if (
        not stat.S_ISREG(details.st_mode)
        or not 1 <= details.st_size <= maximum_bytes
        or (protected and details.st_mode & 0o077)
    ):
        _fail(code)
    return candidate.absolute()


def _load_schema(path: Path, code: str) -> Mapping[str, Any]:
    try:
        return _mapping(json.loads(path.read_text(encoding="utf-8")), code)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail(code)


def _validate_schema(document: Mapping[str, Any], path: Path, code: str) -> None:
    errors = list(
        Draft202012Validator(
            _load_schema(path, code), format_checker=FormatChecker()
        ).iter_errors(document)
    )
    if errors:
        _fail(code)


def _load_profile(path: Path) -> tuple[Mapping[str, Any], str]:
    source = _regular_file(
        path,
        code="customer-postgresql-continuity.profile.invalid",
        maximum_bytes=MAX_PROFILE_BYTES,
        protected=True,
    )
    try:
        profile = _mapping(
            json.loads(source.read_text(encoding="utf-8")),
            "customer-postgresql-continuity.profile.invalid",
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("customer-postgresql-continuity.profile.invalid")
    _validate_schema(
        profile,
        PROFILE_SCHEMA,
        "customer-postgresql-continuity.profile.invalid",
    )
    return profile, _digest_value(profile)


def _password(path: Path) -> str:
    source = _regular_file(
        path,
        code="customer-postgresql-continuity.database-credential.invalid",
        maximum_bytes=MAX_SECRET_BYTES,
        protected=True,
    )
    try:
        raw = source.read_bytes()
        if raw.endswith(b"\n"):
            raw = raw[:-1]
        value = raw.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        _fail("customer-postgresql-continuity.database-credential.invalid")
    if not value or "\x00" in value or "\n" in value or "\r" in value:
        _fail("customer-postgresql-continuity.database-credential.invalid")
    return value


def _tls_file(path: Path, *, protected: bool = False) -> Path:
    return _regular_file(
        path,
        code="customer-postgresql-continuity.database-tls.invalid",
        maximum_bytes=MAX_TLS_BYTES,
        protected=protected,
    )


def _profile_sections(
    profile: Mapping[str, Any],
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    spec = _mapping(
        profile.get("spec"), "customer-postgresql-continuity.profile.invalid"
    )
    database = _mapping(
        spec.get("database"), "customer-postgresql-continuity.profile.invalid"
    )
    return spec, database


def _database_target_digest(
    *, host: str, port: int, database: str, user: str
) -> str:
    normalized_host = host.strip().lower()
    if (
        not normalized_host
        or len(normalized_host) > 255
        or any(character.isspace() for character in normalized_host)
        or any(character in normalized_host for character in "/@?#")
        or isinstance(port, bool)
        or not 1 <= port <= 65_535
    ):
        _fail("customer-postgresql-continuity.database-target.invalid")
    return _digest_value(
        {
            "host": normalized_host,
            "port": port,
            "database": database,
            "user": user,
        }
    )


@dataclass(frozen=True)
class DatabaseObservation:
    timeline: int
    major_version: int
    primary: bool
    tls: bool
    transaction_read_only: bool
    server_binding_digest: str


class PostgreSQLObserver:
    """Explicit verified-TLS, read-only connection to one writable endpoint."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        database: str,
        user: str,
        password_file: Path,
        ca_file: Path,
        request_timeout_milliseconds: int,
        client_cert_file: Path | None = None,
        client_key_file: Path | None = None,
    ) -> None:
        self.target_digest = _database_target_digest(
            host=host, port=port, database=database, user=user
        )
        certificate: Path | None = None
        key: Path | None = None
        if client_cert_file is not None or client_key_file is not None:
            if client_cert_file is None or client_key_file is None:
                _fail("customer-postgresql-continuity.database-tls.invalid")
            certificate = _tls_file(client_cert_file)
            key = _tls_file(client_key_file, protected=True)
        self.client_identity = (
            "mutual-tls-password" if certificate is not None else "password"
        )
        self._parameters: dict[str, object] = {
            "host": host.strip().lower(),
            "port": port,
            "dbname": database,
            "user": user,
            "password": _password(password_file),
            "sslmode": "verify-full",
            "sslrootcert": str(_tls_file(ca_file)),
            "target_session_attrs": "read-write",
            "connect_timeout": max(1, math.ceil(request_timeout_milliseconds / 1000)),
            "application_name": "iip-customer-postgresql-continuity",
            "options": (
                "-c default_transaction_read_only=on "
                f"-c statement_timeout={request_timeout_milliseconds}"
            ),
            "row_factory": dict_row,
        }
        if certificate is not None and key is not None:
            self._parameters["sslcert"] = str(certificate)
            self._parameters["sslkey"] = str(key)

    def observe(self) -> DatabaseObservation | None:
        try:
            with psycopg.connect(**self._parameters) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT
                          pg_is_in_recovery() AS in_recovery,
                          current_setting('server_version_num')::integer AS server_version_num,
                          pg_postmaster_start_time()::text AS postmaster_started_at,
                          inet_server_addr()::text AS server_address,
                          inet_server_port() AS server_port,
                          current_setting('transaction_read_only') = 'on' AS transaction_read_only,
                          (SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()) AS ssl,
                          (SELECT version FROM pg_stat_ssl WHERE pid = pg_backend_pid()) AS tls_version,
                          (SELECT timeline_id FROM pg_split_walfile_name(
                            pg_walfile_name(pg_current_wal_lsn())
                          ))::bigint AS timeline_id
                        """
                    )
                    row = cursor.fetchone()
        except (psycopg.Error, OSError, ValueError):
            return None
        if not isinstance(row, Mapping):
            return None
        try:
            version_number = int(row["server_version_num"])
            timeline = int(row["timeline_id"])
            server_port = int(row["server_port"])
        except (KeyError, TypeError, ValueError):
            return None
        server_address = row.get("server_address")
        started = row.get("postmaster_started_at")
        if (
            timeline < 1
            or version_number < 100_000
            or not isinstance(server_address, str)
            or not server_address
            or not isinstance(started, str)
            or not started
            or not isinstance(row.get("tls_version"), str)
            or not row.get("tls_version")
        ):
            return None
        return DatabaseObservation(
            timeline=timeline,
            major_version=version_number // 10_000,
            primary=row.get("in_recovery") is False,
            tls=row.get("ssl") is True,
            transaction_read_only=row.get("transaction_read_only") is True,
            server_binding_digest=_digest_value(
                {
                    "address": server_address,
                    "port": server_port,
                    "postmasterStartedAt": started,
                }
            ),
        )


@dataclass
class ProbeCounts:
    attempts: int = 0
    api_successes: int = 0
    receiver_successes: int = 0
    api_streak: int = 0
    receiver_streak: int = 0
    api_maximum_streak: int = 0
    receiver_maximum_streak: int = 0

    def record(self, api_ok: bool, receiver_ok: bool) -> None:
        self.attempts += 1
        if api_ok:
            self.api_successes += 1
            self.api_streak = 0
        else:
            self.api_streak += 1
            self.api_maximum_streak = max(self.api_maximum_streak, self.api_streak)
        if receiver_ok:
            self.receiver_successes += 1
            self.receiver_streak = 0
        else:
            self.receiver_streak += 1
            self.receiver_maximum_streak = max(
                self.receiver_maximum_streak, self.receiver_streak
            )

    def phase(self, identifier: str, workflow: Mapping[str, int]) -> dict[str, Any]:
        index = PHASES.index(identifier)
        return {
            "id": identifier,
            "apiAttempts": self.attempts,
            "apiSuccesses": self.api_successes,
            "apiFailures": self.attempts - self.api_successes,
            "apiMaximumConsecutiveFailures": self.api_maximum_streak,
            "receiverAttempts": self.attempts,
            "receiverSuccesses": self.receiver_successes,
            "receiverFailures": self.attempts - self.receiver_successes,
            "receiverMaximumConsecutiveFailures": self.receiver_maximum_streak,
            **workflow,
            "workflowBoundary": WORKFLOW_BOUNDARIES[index],
        }


def _probe_cycles(
    client: processing.ProcessingClient,
    *,
    attempts: int,
    interval_milliseconds: int,
    sleeper: Callable[[float], None],
) -> ProbeCounts:
    result = ProbeCounts()
    for index in range(attempts):
        result.record(*client.probe_cycle())
        if index + 1 < attempts:
            sleeper(interval_milliseconds / 1000)
    return result


def _availability(successes: int, attempts: int) -> int:
    if attempts < 1:
        _fail("customer-postgresql-continuity.measurements.invalid")
    return successes * 10_000 // attempts


def _check(identifier: str, passed: bool, code: str) -> dict[str, str]:
    result = {"id": identifier, "status": "passed" if passed else "failed"}
    if not passed:
        result["errorCode"] = code
    return result


def _derived_checks(
    *,
    subject: Mapping[str, Any],
    bindings: Mapping[str, Any],
    objective: Mapping[str, Any],
    environment: Mapping[str, Any],
    database_probe: Mapping[str, Any],
    receiver_intake: Mapping[str, Any],
    database: Mapping[str, Any],
    phases: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    total_api = sum(int(phase["apiAttempts"]) for phase in phases)
    api_successes = sum(int(phase["apiSuccesses"]) for phase in phases)
    total_receiver = sum(int(phase["receiverAttempts"]) for phase in phases)
    receiver_successes = sum(int(phase["receiverSuccesses"]) for phase in phases)
    api_maximum = max(int(phase["apiMaximumConsecutiveFailures"]) for phase in phases)
    receiver_maximum = max(
        int(phase["receiverMaximumConsecutiveFailures"]) for phase in phases
    )
    timeline_advanced = (
        int(database["promotedTimeline"]) > int(database["initialTimeline"])
        and int(database["timelineDelta"])
        == int(database["promotedTimeline"]) - int(database["initialTimeline"])
    )
    baseline, promotion, recovery = phases
    baseline_and_recovery_api_clean = (
        baseline["apiFailures"] == 0 and recovery["apiFailures"] == 0
    )
    baseline_and_recovery_receiver_clean = (
        baseline["receiverFailures"] == 0 and recovery["receiverFailures"] == 0
    )
    workflow_ok = [
        phase["workflowSubmitted"] == 1
        and phase["workflowCompleted"] == 1
        and phase["workflowFailures"] == 0
        and phase["workflowCompletionMilliseconds"]
        <= objective["maximumWorkflowCompletionMilliseconds"]
        for phase in phases
    ]
    target_keys = (
        "apiTargetBindingDigest",
        "otlpTargetBindingDigest",
        "databaseTargetBindingDigest",
        "kubernetesContextBindingDigest",
        "namespaceBindingDigest",
        "profileDigest",
    )
    return [
        _check(
            "source-binding",
            subject.get("sourceRevision") is not None,
            "customer-postgresql-continuity.source.invalid",
        ),
        _check("minimized-output", True, "customer-postgresql-continuity.output.not-minimized"),
        _check(
            "explicit-target-bindings",
            all(DIGEST.fullmatch(str(bindings.get(key, ""))) for key in target_keys),
            "customer-postgresql-continuity.target.invalid",
        ),
        _check(
            "api-verified-https",
            environment.get("apiTransport") == "verified-https",
            "customer-postgresql-continuity.api.transport-invalid",
        ),
        _check(
            "receiver-mutual-tls",
            environment.get("otlpTransport") == "mutual-tls-https",
            "customer-postgresql-continuity.receiver.transport-invalid",
        ),
        _check(
            "database-verified-tls",
            environment.get("databaseTransport") == "verified-tls"
            and environment.get("databaseCaSource") == "custom"
            and database.get("tlsBefore") is True
            and database.get("tlsAfter") is True,
            "customer-postgresql-continuity.database.transport-invalid",
        ),
        _check(
            "database-read-only-probe",
            database_probe.get("mode") == "read-only-native-functions"
            and database_probe.get("session") == "default-transaction-read-only"
            and database.get("transactionReadOnlyBefore") is True
            and database.get("transactionReadOnlyAfter") is True,
            "customer-postgresql-continuity.database.probe-invalid",
        ),
        _check(
            "initial-writable-primary",
            database.get("primaryBefore") is True,
            "customer-postgresql-continuity.database.initial-primary-invalid",
        ),
        _check(
            "promotion-observed",
            timeline_advanced
            and int(database["promotionMilliseconds"])
            <= objective["maximumPromotionMilliseconds"],
            "customer-postgresql-continuity.database.promotion-not-observed",
        ),
        _check(
            "timeline-advanced",
            timeline_advanced,
            "customer-postgresql-continuity.database.timeline-not-advanced",
        ),
        _check(
            "promoted-writable-primary",
            database.get("primaryAfter") is True,
            "customer-postgresql-continuity.database.promoted-primary-invalid",
        ),
        _check(
            "api-availability",
            baseline_and_recovery_api_clean
            and _availability(api_successes, total_api)
            >= objective["minimumApiAvailabilityBasisPoints"],
            "customer-postgresql-continuity.api.availability-objective-missed",
        ),
        _check(
            "receiver-availability",
            baseline_and_recovery_receiver_clean
            and _availability(receiver_successes, total_receiver)
            >= objective["minimumReceiverAvailabilityBasisPoints"],
            "customer-postgresql-continuity.receiver.availability-objective-missed",
        ),
        _check(
            "bounded-api-outage",
            api_maximum <= objective["maximumConsecutiveFailures"],
            "customer-postgresql-continuity.api.outage-objective-missed",
        ),
        _check(
            "bounded-receiver-outage",
            receiver_maximum <= objective["maximumConsecutiveFailures"],
            "customer-postgresql-continuity.receiver.outage-objective-missed",
        ),
        _check(
            "receiver-durable-intake",
            receiver_intake.get("successBoundary")
            == "postgresql-commit-before-http-200"
            and receiver_successes > 0,
            "customer-postgresql-continuity.receiver.persistence-invalid",
        ),
        _check(
            "workflow-baseline-completion",
            workflow_ok[0],
            "customer-postgresql-continuity.workflow.baseline-failed",
        ),
        _check(
            "workflow-survived-promotion",
            workflow_ok[1]
            and promotion.get("workflowBoundary")
            == "submitted-before-observed-after-promotion",
            "customer-postgresql-continuity.workflow.promotion-failed",
        ),
        _check(
            "workflow-recovery-completion",
            workflow_ok[2],
            "customer-postgresql-continuity.workflow.recovery-failed",
        ),
    ]


def _report_identifier(metadata: Mapping[str, Any], spec: Mapping[str, Any]) -> str:
    return "cpgq_" + hashlib.sha256(
        _canonical({"metadata": metadata, "spec": spec})
    ).hexdigest()[:32]


def _walk_keys(value: object) -> list[str]:
    result: list[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if isinstance(key, str):
                result.append(key)
            result.extend(_walk_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            result.extend(_walk_keys(nested))
    return result


def _summary(
    phases: Sequence[Mapping[str, Any]],
    checks: Sequence[Mapping[str, str]],
    promotion_milliseconds: int,
) -> dict[str, Any]:
    total_api = sum(int(item["apiAttempts"]) for item in phases)
    failed_api = sum(int(item["apiFailures"]) for item in phases)
    total_receiver = sum(int(item["receiverAttempts"]) for item in phases)
    failed_receiver = sum(int(item["receiverFailures"]) for item in phases)
    failed_checks = sum(item.get("status") == "failed" for item in checks)
    status = "qualified" if failed_checks == 0 else "not-qualified"
    return {
        "totalChecks": len(CHECK_IDS),
        "passedChecks": len(CHECK_IDS) - failed_checks,
        "failedChecks": failed_checks,
        "totalApiAttempts": total_api,
        "failedApiAttempts": failed_api,
        "apiAvailabilityBasisPoints": _availability(total_api - failed_api, total_api),
        "totalReceiverAttempts": total_receiver,
        "failedReceiverAttempts": failed_receiver,
        "receiverAvailabilityBasisPoints": _availability(
            total_receiver - failed_receiver, total_receiver
        ),
        "totalWorkflowSubmissions": len(PHASES),
        "completedWorkflows": sum(int(item["workflowCompleted"]) for item in phases),
        "failedWorkflows": sum(int(item["workflowFailures"]) for item in phases),
        "maximumApiConsecutiveFailures": max(
            int(item["apiMaximumConsecutiveFailures"]) for item in phases
        ),
        "maximumReceiverConsecutiveFailures": max(
            int(item["receiverMaximumConsecutiveFailures"]) for item in phases
        ),
        "promotionMilliseconds": promotion_milliseconds,
        "overallStatus": status,
    }


def build_report(
    *,
    revision: str,
    repository: Mapping[str, str],
    image_digest: str,
    api_target_digest: str,
    otlp_target_digest: str,
    database_target_digest: str,
    context: str,
    namespace: str,
    profile_digest: str,
    api_ca_source: str,
    otlp_ca_source: str,
    database_client_identity: str,
    objective: Mapping[str, int],
    initial: DatabaseObservation,
    promoted: DatabaseObservation,
    connection_attempts: int,
    connection_failures: int,
    promotion_milliseconds: int,
    phases: Sequence[Mapping[str, Any]],
    started_at: datetime,
    promotion_wait_started_at: datetime,
    promotion_observed_at: datetime,
    completed_at: datetime,
) -> dict[str, Any]:
    if [item.get("id") for item in phases] != list(PHASES):
        _fail("customer-postgresql-continuity.phases.invalid")
    subject = {
        "applicationVersion": repository["applicationVersion"],
        "chartVersion": repository["chartVersion"],
        "contractsApiVersion": API_VERSION,
        "requiredMigration": repository["requiredMigration"],
        "sourceRevision": revision,
        "imageDigest": image_digest,
    }
    bindings = {
        "apiTargetBindingDigest": api_target_digest,
        "otlpTargetBindingDigest": otlp_target_digest,
        "databaseTargetBindingDigest": database_target_digest,
        "kubernetesContextBindingDigest": _digest_value(context),
        "namespaceBindingDigest": _digest_value(namespace),
        "profileDigest": profile_digest,
    }
    environment = {
        "platform": f"{platform.system().lower()}/{platform.machine().lower()}",
        "pythonVersion": platform.python_version(),
        "databaseEngine": "postgresql",
        "databaseMajorVersion": initial.major_version,
        "databaseTransport": "verified-tls",
        "databaseCaSource": "custom",
        "databaseClientIdentity": database_client_identity,
        "apiTransport": "verified-https",
        "otlpTransport": "mutual-tls-https",
        "apiCaSource": api_ca_source,
        "otlpCaSource": otlp_ca_source,
        "proxyMode": "disabled",
        "redirectMode": "deny",
    }
    database_probe = {
        "mode": "read-only-native-functions",
        "session": "default-transaction-read-only",
        "primarySelection": "libpq-target-session-attrs-read-write",
        "promotionTrigger": "external-operator",
        "promotionProof": "writable-primary-wal-timeline-advance",
    }
    receiver_intake = {
        "signal": "metric",
        "payload": "non-empty-otlp-protobuf",
        "successBoundary": "postgresql-commit-before-http-200",
    }
    database = {
        "initialTimeline": initial.timeline,
        "promotedTimeline": promoted.timeline,
        "timelineDelta": promoted.timeline - initial.timeline,
        "primaryBefore": initial.primary,
        "primaryAfter": promoted.primary,
        "tlsBefore": initial.tls,
        "tlsAfter": promoted.tls,
        "transactionReadOnlyBefore": initial.transaction_read_only,
        "transactionReadOnlyAfter": promoted.transaction_read_only,
        "initialServerBindingDigest": initial.server_binding_digest,
        "promotedServerBindingDigest": promoted.server_binding_digest,
        "serverIdentityChanged": (
            initial.server_binding_digest != promoted.server_binding_digest
        ),
        "connectionAttempts": connection_attempts,
        "connectionFailures": connection_failures,
        "promotionMilliseconds": promotion_milliseconds,
    }
    checks = _derived_checks(
        subject=subject,
        bindings=bindings,
        objective=objective,
        environment=environment,
        database_probe=database_probe,
        receiver_intake=receiver_intake,
        database=database,
        phases=phases,
    )
    summary = _summary(phases, checks, promotion_milliseconds)
    spec: dict[str, Any] = {
        "status": summary["overallStatus"],
        "qualificationLevel": QUALIFICATION_LEVEL,
        "subject": subject,
        "bindings": bindings,
        "objective": dict(objective),
        "environment": environment,
        "databaseProbe": database_probe,
        "receiverIntake": receiver_intake,
        "measurements": {
            "startedAt": _timestamp(started_at),
            "promotionWaitStartedAt": _timestamp(promotion_wait_started_at),
            "promotionObservedAt": _timestamp(promotion_observed_at),
            "completedAt": _timestamp(completed_at),
            "database": database,
            "phases": [dict(item) for item in phases],
        },
        "checks": checks,
        "limitations": list(LIMITATIONS),
        "summary": summary,
    }
    metadata_without_id = {
        "generatedAt": _timestamp(completed_at),
        "sourceRevision": revision,
        "sourceDirty": False,
    }
    report = {
        "apiVersion": API_VERSION,
        "kind": KIND,
        "metadata": {
            "id": _report_identifier(metadata_without_id, spec),
            **metadata_without_id,
        },
        "spec": spec,
    }
    validate_report_document(report)
    return report


def validate_report_document(report: Mapping[str, Any]) -> None:
    _validate_schema(
        report,
        REPORT_SCHEMA,
        "customer-postgresql-continuity.report.schema-invalid",
    )
    if any(key in FORBIDDEN_RETAINED_KEYS for key in _walk_keys(report)):
        _fail("customer-postgresql-continuity.report.sensitive-field")
    metadata = _mapping(
        report.get("metadata"), "customer-postgresql-continuity.report.invalid"
    )
    spec = _mapping(report.get("spec"), "customer-postgresql-continuity.report.invalid")
    metadata_without_id = dict(metadata)
    report_id = metadata_without_id.pop("id", None)
    if (
        report.get("apiVersion") != API_VERSION
        or report.get("kind") != KIND
        or not isinstance(report_id, str)
        or REPORT_ID.fullmatch(report_id) is None
        or report_id != _report_identifier(metadata_without_id, spec)
        or spec.get("qualificationLevel") != QUALIFICATION_LEVEL
    ):
        _fail("customer-postgresql-continuity.report.invalid")
    subject = _mapping(
        spec.get("subject"), "customer-postgresql-continuity.report.invalid"
    )
    if metadata.get("sourceRevision") != subject.get("sourceRevision"):
        _fail("customer-postgresql-continuity.report.source-mismatch")
    objective = _mapping(
        spec.get("objective"), "customer-postgresql-continuity.report.invalid"
    )
    minimum_attempts = _integer(
        objective.get("minimumProbeAttemptsPerPhase"), 5, 1000,
        "customer-postgresql-continuity.report.objective-invalid",
    )
    _integer(objective.get("probeIntervalMilliseconds"), 50, 5000, "customer-postgresql-continuity.report.objective-invalid")
    maximum_promotion = _integer(objective.get("maximumPromotionMilliseconds"), 10000, 3600000, "customer-postgresql-continuity.report.objective-invalid")
    maximum_workflow = _integer(objective.get("maximumWorkflowCompletionMilliseconds"), 1000, 120000, "customer-postgresql-continuity.report.objective-invalid")
    _integer(objective.get("requestTimeoutMilliseconds"), 100, 30000, "customer-postgresql-continuity.report.objective-invalid")
    _integer(objective.get("minimumApiAvailabilityBasisPoints"), 1, 10000, "customer-postgresql-continuity.report.objective-invalid")
    _integer(objective.get("minimumReceiverAvailabilityBasisPoints"), 1, 10000, "customer-postgresql-continuity.report.objective-invalid")
    _integer(objective.get("maximumConsecutiveFailures"), 0, 1000, "customer-postgresql-continuity.report.objective-invalid")
    measurements = _mapping(
        spec.get("measurements"), "customer-postgresql-continuity.report.invalid"
    )
    times = [
        _parse_timestamp(measurements.get(key), "customer-postgresql-continuity.report.time-invalid")
        for key in ("startedAt", "promotionWaitStartedAt", "promotionObservedAt", "completedAt")
    ]
    if times != sorted(times) or _parse_timestamp(metadata.get("generatedAt"), "customer-postgresql-continuity.report.time-invalid") != times[-1]:
        _fail("customer-postgresql-continuity.report.time-invalid")
    phases_value = measurements.get("phases")
    if not isinstance(phases_value, list) or [item.get("id") for item in phases_value if isinstance(item, Mapping)] != list(PHASES):
        _fail("customer-postgresql-continuity.report.phases-invalid")
    phases: list[Mapping[str, Any]] = []
    for index, value in enumerate(phases_value):
        phase = _mapping(value, "customer-postgresql-continuity.report.phases-invalid")
        api_attempts = _integer(phase.get("apiAttempts"), minimum_attempts, 100000, "customer-postgresql-continuity.report.phases-invalid")
        api_successes = _integer(phase.get("apiSuccesses"), 0, api_attempts, "customer-postgresql-continuity.report.phases-invalid")
        api_failures = _integer(phase.get("apiFailures"), 0, api_attempts, "customer-postgresql-continuity.report.phases-invalid")
        receiver_attempts = _integer(phase.get("receiverAttempts"), minimum_attempts, 100000, "customer-postgresql-continuity.report.phases-invalid")
        receiver_successes = _integer(phase.get("receiverSuccesses"), 0, receiver_attempts, "customer-postgresql-continuity.report.phases-invalid")
        receiver_failures = _integer(phase.get("receiverFailures"), 0, receiver_attempts, "customer-postgresql-continuity.report.phases-invalid")
        _integer(phase.get("apiMaximumConsecutiveFailures"), 0, api_failures, "customer-postgresql-continuity.report.phases-invalid")
        _integer(phase.get("receiverMaximumConsecutiveFailures"), 0, receiver_failures, "customer-postgresql-continuity.report.phases-invalid")
        completed = _integer(phase.get("workflowCompleted"), 0, 1, "customer-postgresql-continuity.report.phases-invalid")
        failed = _integer(phase.get("workflowFailures"), 0, 1, "customer-postgresql-continuity.report.phases-invalid")
        _integer(phase.get("workflowSubmitted"), 1, 1, "customer-postgresql-continuity.report.phases-invalid")
        _integer(phase.get("workflowPollAttempts"), 1, 10000, "customer-postgresql-continuity.report.phases-invalid")
        workflow_ms = _integer(phase.get("workflowCompletionMilliseconds"), 0, 3600000, "customer-postgresql-continuity.report.phases-invalid")
        if (
            api_successes + api_failures != api_attempts
            or receiver_successes + receiver_failures != receiver_attempts
            or completed + failed != 1
            or workflow_ms > maximum_workflow
            or phase.get("workflowBoundary") != WORKFLOW_BOUNDARIES[index]
        ):
            _fail("customer-postgresql-continuity.report.phases-invalid")
        phases.append(phase)
    database = _mapping(measurements.get("database"), "customer-postgresql-continuity.report.database-invalid")
    initial_timeline = _integer(database.get("initialTimeline"), 1, 2**63 - 1, "customer-postgresql-continuity.report.database-invalid")
    promoted_timeline = _integer(database.get("promotedTimeline"), 1, 2**63 - 1, "customer-postgresql-continuity.report.database-invalid")
    timeline_delta = _integer(database.get("timelineDelta"), 0, 2**63 - 1, "customer-postgresql-continuity.report.database-invalid")
    connection_attempts = _integer(database.get("connectionAttempts"), 2, 100002, "customer-postgresql-continuity.report.database-invalid")
    connection_failures = _integer(database.get("connectionFailures"), 0, 100000, "customer-postgresql-continuity.report.database-invalid")
    promotion_ms = _integer(database.get("promotionMilliseconds"), 0, 3600000, "customer-postgresql-continuity.report.database-invalid")
    environment = _mapping(spec.get("environment"), "customer-postgresql-continuity.report.environment-invalid")
    if (
        promoted_timeline - initial_timeline != timeline_delta
        or connection_failures > connection_attempts - 2
        or promotion_ms > maximum_promotion
        or database.get("serverIdentityChanged")
        is not (database.get("initialServerBindingDigest") != database.get("promotedServerBindingDigest"))
        or environment.get("databaseMajorVersion") is None
    ):
        _fail("customer-postgresql-continuity.report.database-invalid")
    checks = spec.get("checks")
    if not isinstance(checks, list) or [item.get("id") for item in checks if isinstance(item, Mapping)] != list(CHECK_IDS):
        _fail("customer-postgresql-continuity.report.checks-invalid")
    expected_checks = _derived_checks(
        subject=subject,
        bindings=_mapping(spec.get("bindings"), "customer-postgresql-continuity.report.bindings-invalid"),
        objective=objective,
        environment=environment,
        database_probe=_mapping(spec.get("databaseProbe"), "customer-postgresql-continuity.report.database-invalid"),
        receiver_intake=_mapping(spec.get("receiverIntake"), "customer-postgresql-continuity.report.receiver-invalid"),
        database=database,
        phases=phases,
    )
    summary = _mapping(spec.get("summary"), "customer-postgresql-continuity.report.summary-invalid")
    expected_summary = _summary(phases, expected_checks, promotion_ms)
    if (
        checks != expected_checks
        or dict(summary) != expected_summary
        or spec.get("status") != expected_summary["overallStatus"]
        or spec.get("limitations") != list(LIMITATIONS)
    ):
        _fail("customer-postgresql-continuity.report.summary-invalid")


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    destination = path.expanduser()
    if destination.is_symlink():
        _fail("customer-postgresql-continuity.output.invalid")
    destination = destination.absolute()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            delete=False,
        ) as handle:
            json.dump(report, handle, indent=2)
            handle.write("\n")
            temporary = Path(handle.name)
        os.replace(temporary, destination)
    except OSError:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        _fail("customer-postgresql-continuity.output.invalid")


def qualify(
    *,
    api_base_url: str,
    api_token_file: Path,
    otlp_base_url: str,
    otlp_token_file: Path,
    otlp_client_cert_file: Path,
    otlp_client_key_file: Path,
    profile_path: Path,
    image_digest: str,
    context: str,
    namespace: str,
    database_host: str,
    database_port: int,
    database_password_file: Path,
    database_ca_file: Path,
    output: Path,
    allow_failover_observation: bool,
    api_ca_file: Path | None = None,
    otlp_ca_file: Path | None = None,
    database_client_cert_file: Path | None = None,
    database_client_key_file: Path | None = None,
    attempts_per_phase: int = DEFAULT_ATTEMPTS_PER_PHASE,
    probe_interval_milliseconds: int = DEFAULT_INTERVAL_MILLISECONDS,
    maximum_promotion_milliseconds: int = DEFAULT_PROMOTION_MILLISECONDS,
    maximum_workflow_milliseconds: int = DEFAULT_WORKFLOW_MILLISECONDS,
    request_timeout_milliseconds: int = DEFAULT_REQUEST_MILLISECONDS,
    minimum_api_availability_basis_points: int = DEFAULT_AVAILABILITY_BASIS_POINTS,
    minimum_receiver_availability_basis_points: int = DEFAULT_AVAILABILITY_BASIS_POINTS,
    maximum_consecutive_failures: int = DEFAULT_MAXIMUM_CONSECUTIVE_FAILURES,
    platform_client_factory: Callable[..., processing.ProcessingClient] = processing.ProcessingClient,
    database_client_factory: Callable[..., PostgreSQLObserver] = PostgreSQLObserver,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    if not allow_failover_observation:
        _fail("customer-postgresql-continuity.failover.explicit-enable-required")
    for value, minimum, maximum in (
        (attempts_per_phase, 5, 1000),
        (probe_interval_milliseconds, 50, 5000),
        (maximum_promotion_milliseconds, 10000, 3600000),
        (maximum_workflow_milliseconds, 1000, 120000),
        (request_timeout_milliseconds, 100, 30000),
        (minimum_api_availability_basis_points, 1, 10000),
        (minimum_receiver_availability_basis_points, 1, 10000),
        (maximum_consecutive_failures, 0, 1000),
    ):
        _integer(value, minimum, maximum, "customer-postgresql-continuity.objective.invalid")
    if DIGEST.fullmatch(image_digest) is None or not context or not namespace:
        _fail("customer-postgresql-continuity.target.invalid")
    try:
        revision, dirty = ingress._git_state()
        repository = ingress._repository_identity()
    except ingress.IngressQualificationError:
        _fail("customer-postgresql-continuity.source.invalid")
    if dirty:
        _fail("customer-postgresql-continuity.source.dirty")
    profile, profile_digest = _load_profile(profile_path)
    _, database_profile = _profile_sections(profile)
    subject = {**repository, "sourceRevision": revision, "imageDigest": image_digest}
    try:
        platform_client = platform_client_factory(
            api_base_url=api_base_url,
            api_token_file=api_token_file,
            otlp_base_url=otlp_base_url,
            otlp_token_file=otlp_token_file,
            otlp_client_cert_file=otlp_client_cert_file,
            otlp_client_key_file=otlp_client_key_file,
            profile=profile,
            subject=subject,
            api_ca_file=api_ca_file,
            otlp_ca_file=otlp_ca_file,
            request_timeout_milliseconds=request_timeout_milliseconds,
            qualification_id="customer-postgresql-continuity",
        )
    except processing.CustomerProcessingContinuityError:
        _fail("customer-postgresql-continuity.platform-client.invalid")
    database_client = database_client_factory(
        host=database_host,
        port=database_port,
        database=str(database_profile["name"]),
        user=str(database_profile["user"]),
        password_file=database_password_file,
        ca_file=database_ca_file,
        client_cert_file=database_client_cert_file,
        client_key_file=database_client_key_file,
        request_timeout_milliseconds=request_timeout_milliseconds,
    )
    initial = database_client.observe()
    if initial is None:
        _fail("customer-postgresql-continuity.database.initial-unavailable")
    if (
        not initial.primary
        or not initial.tls
        or not initial.transaction_read_only
        or initial.major_version < int(database_profile["minimumMajorVersion"])
    ):
        _fail("customer-postgresql-continuity.database.initial-invalid")
    try:
        platform_client.validate_resource()
    except processing.CustomerProcessingContinuityError:
        _fail("customer-postgresql-continuity.platform.resource-invalid")
    clock = now or (lambda: datetime.now(timezone.utc))
    started_at = clock()
    try:
        baseline_job, baseline_started = platform_client.start_workflow("baseline")
        baseline_counts = _probe_cycles(
            platform_client,
            attempts=attempts_per_phase,
            interval_milliseconds=probe_interval_milliseconds,
            sleeper=sleeper,
        )
        baseline_workflow = platform_client.finish_workflow(
            baseline_job, baseline_started, maximum_workflow_milliseconds, sleeper
        )
        promotion_job, promotion_job_started = platform_client.start_workflow(
            "promotion-observation"
        )
    except processing.CustomerProcessingContinuityError:
        _fail("customer-postgresql-continuity.platform.baseline-failed")
    promotion_wait_started_at = clock()
    promotion_start_tick = monotonic()
    print(
        "customer postgresql continuity: baseline complete; initiate the planned primary promotion",
        file=os.sys.stderr,
        flush=True,
    )
    transition_counts = ProbeCounts()
    database_attempts = 1
    database_failures = 0
    promoted: DatabaseObservation | None = None
    deadline = promotion_start_tick + maximum_promotion_milliseconds / 1000
    while True:
        observed = database_client.observe()
        database_attempts += 1
        if observed is None:
            database_failures += 1
        elif (
            observed.primary
            and observed.tls
            and observed.transaction_read_only
            and observed.major_version == initial.major_version
            and observed.timeline > initial.timeline
        ):
            promoted = observed
        transition_counts.record(*platform_client.probe_cycle())
        if promoted is not None and transition_counts.attempts >= attempts_per_phase:
            break
        if monotonic() >= deadline:
            _fail("customer-postgresql-continuity.database.promotion-timeout")
        sleeper(probe_interval_milliseconds / 1000)
    promotion_elapsed = max(
        0, math.ceil((monotonic() - promotion_start_tick) * 1000)
    )
    promotion_observed_at = clock()
    try:
        promotion_workflow = platform_client.finish_workflow(
            promotion_job,
            promotion_job_started,
            maximum_workflow_milliseconds,
            sleeper,
        )
        recovery_job, recovery_started = platform_client.start_workflow("recovery")
        recovery_counts = _probe_cycles(
            platform_client,
            attempts=attempts_per_phase,
            interval_milliseconds=probe_interval_milliseconds,
            sleeper=sleeper,
        )
        recovery_workflow = platform_client.finish_workflow(
            recovery_job, recovery_started, maximum_workflow_milliseconds, sleeper
        )
    except processing.CustomerProcessingContinuityError:
        _fail("customer-postgresql-continuity.platform.recovery-failed")
    final_observation = database_client.observe()
    database_attempts += 1
    if final_observation is None:
        _fail("customer-postgresql-continuity.database.recovery-unavailable")
    if (
        final_observation.timeline != promoted.timeline
        or final_observation.major_version != promoted.major_version
        or not final_observation.primary
        or not final_observation.tls
        or not final_observation.transaction_read_only
    ):
        _fail("customer-postgresql-continuity.database.recovery-invalid")
    completed_at = clock()
    objective = {
        "minimumProbeAttemptsPerPhase": attempts_per_phase,
        "probeIntervalMilliseconds": probe_interval_milliseconds,
        "maximumPromotionMilliseconds": maximum_promotion_milliseconds,
        "maximumWorkflowCompletionMilliseconds": maximum_workflow_milliseconds,
        "requestTimeoutMilliseconds": request_timeout_milliseconds,
        "minimumApiAvailabilityBasisPoints": minimum_api_availability_basis_points,
        "minimumReceiverAvailabilityBasisPoints": minimum_receiver_availability_basis_points,
        "maximumConsecutiveFailures": maximum_consecutive_failures,
    }
    phases = [
        baseline_counts.phase("baseline", baseline_workflow),
        transition_counts.phase("promotion-observation", promotion_workflow),
        recovery_counts.phase("recovery", recovery_workflow),
    ]
    report = build_report(
        revision=revision,
        repository=repository,
        image_digest=image_digest,
        api_target_digest=platform_client.api_target_digest,
        otlp_target_digest=platform_client.otlp_target_digest,
        database_target_digest=database_client.target_digest,
        context=context,
        namespace=namespace,
        profile_digest=profile_digest,
        api_ca_source=platform_client.api_ca_source,
        otlp_ca_source=platform_client.otlp_ca_source,
        database_client_identity=database_client.client_identity,
        objective=objective,
        initial=initial,
        promoted=promoted,
        connection_attempts=database_attempts,
        connection_failures=database_failures,
        promotion_milliseconds=promotion_elapsed,
        phases=phases,
        started_at=started_at,
        promotion_wait_started_at=promotion_wait_started_at,
        promotion_observed_at=promotion_observed_at,
        completed_at=completed_at,
    )
    _write_report(output, report)
    if report["spec"]["status"] != "qualified":
        _fail("customer-postgresql-continuity.report.not-qualified")
    return report


def verify_report(
    *,
    report_path: Path,
    profile_path: Path,
    api_base_url: str,
    otlp_base_url: str,
    database_host: str,
    database_port: int,
    image_digest: str,
    context: str,
    namespace: str,
    require_clean: bool,
    require_qualified: bool,
) -> Mapping[str, Any]:
    try:
        report = _mapping(
            json.loads(report_path.read_text(encoding="utf-8")),
            "customer-postgresql-continuity.report.unreadable",
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        _fail("customer-postgresql-continuity.report.unreadable")
    validate_report_document(report)
    profile, profile_digest = _load_profile(profile_path)
    _, database_profile = _profile_sections(profile)
    try:
        _, api_target_digest = processing._https_target(api_base_url)
        _, otlp_target_digest = processing._https_target(otlp_base_url)
        revision, dirty = ingress._git_state()
        repository = ingress._repository_identity()
    except (processing.CustomerProcessingContinuityError, ingress.IngressQualificationError):
        _fail("customer-postgresql-continuity.source.invalid")
    expected_bindings = {
        "apiTargetBindingDigest": api_target_digest,
        "otlpTargetBindingDigest": otlp_target_digest,
        "databaseTargetBindingDigest": _database_target_digest(
            host=database_host,
            port=database_port,
            database=str(database_profile["name"]),
            user=str(database_profile["user"]),
        ),
        "kubernetesContextBindingDigest": _digest_value(context),
        "namespaceBindingDigest": _digest_value(namespace),
        "profileDigest": profile_digest,
    }
    metadata = _mapping(report.get("metadata"), "customer-postgresql-continuity.report.invalid")
    spec = _mapping(report.get("spec"), "customer-postgresql-continuity.report.invalid")
    bindings = _mapping(spec.get("bindings"), "customer-postgresql-continuity.report.invalid")
    subject = _mapping(spec.get("subject"), "customer-postgresql-continuity.report.invalid")
    if dict(bindings) != expected_bindings:
        _fail("customer-postgresql-continuity.report.bindings-mismatch")
    if (
        metadata.get("sourceRevision") != revision
        or subject.get("sourceRevision") != revision
        or subject.get("applicationVersion") != repository["applicationVersion"]
        or subject.get("chartVersion") != repository["chartVersion"]
        or subject.get("requiredMigration") != repository["requiredMigration"]
        or subject.get("imageDigest") != image_digest
    ):
        _fail("customer-postgresql-continuity.source.identity-mismatch")
    if require_clean and (dirty or metadata.get("sourceDirty") is not False):
        _fail("customer-postgresql-continuity.source.dirty")
    if require_qualified and spec.get("status") != "qualified":
        _fail("customer-postgresql-continuity.report.not-qualified")
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--api-token-file", type=Path, required=True)
    run.add_argument("--otlp-token-file", type=Path, required=True)
    run.add_argument("--otlp-client-cert-file", type=Path, required=True)
    run.add_argument("--otlp-client-key-file", type=Path, required=True)
    run.add_argument("--api-ca-file", type=Path)
    run.add_argument("--otlp-ca-file", type=Path)
    run.add_argument("--database-password-file", type=Path, required=True)
    run.add_argument("--database-ca-file", type=Path, required=True)
    run.add_argument("--database-client-cert-file", type=Path)
    run.add_argument("--database-client-key-file", type=Path)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--allow-failover-observation", action="store_true")
    run.add_argument("--attempts-per-phase", type=int, default=DEFAULT_ATTEMPTS_PER_PHASE)
    run.add_argument("--probe-interval-milliseconds", type=int, default=DEFAULT_INTERVAL_MILLISECONDS)
    run.add_argument("--maximum-promotion-milliseconds", type=int, default=DEFAULT_PROMOTION_MILLISECONDS)
    run.add_argument("--maximum-workflow-milliseconds", type=int, default=DEFAULT_WORKFLOW_MILLISECONDS)
    run.add_argument("--request-timeout-milliseconds", type=int, default=DEFAULT_REQUEST_MILLISECONDS)
    run.add_argument("--minimum-api-availability-basis-points", type=int, default=DEFAULT_AVAILABILITY_BASIS_POINTS)
    run.add_argument("--minimum-receiver-availability-basis-points", type=int, default=DEFAULT_AVAILABILITY_BASIS_POINTS)
    run.add_argument("--maximum-consecutive-failures", type=int, default=DEFAULT_MAXIMUM_CONSECUTIVE_FAILURES)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--report", type=Path, required=True)
    verify.add_argument("--require-clean", action="store_true")
    verify.add_argument("--require-qualified", action="store_true")
    for command in (run, verify):
        command.add_argument("--api-base-url", required=True)
        command.add_argument("--otlp-base-url", required=True)
        command.add_argument("--profile", type=Path, required=True)
        command.add_argument("--image-digest", required=True)
        command.add_argument("--context", required=True)
        command.add_argument("--namespace", required=True)
        command.add_argument("--database-host", required=True)
        command.add_argument("--database-port", type=int, default=5432)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "run":
            report = qualify(
                api_base_url=args.api_base_url,
                api_token_file=args.api_token_file,
                otlp_base_url=args.otlp_base_url,
                otlp_token_file=args.otlp_token_file,
                otlp_client_cert_file=args.otlp_client_cert_file,
                otlp_client_key_file=args.otlp_client_key_file,
                api_ca_file=args.api_ca_file,
                otlp_ca_file=args.otlp_ca_file,
                profile_path=args.profile,
                image_digest=args.image_digest,
                context=args.context,
                namespace=args.namespace,
                database_host=args.database_host,
                database_port=args.database_port,
                database_password_file=args.database_password_file,
                database_ca_file=args.database_ca_file,
                database_client_cert_file=args.database_client_cert_file,
                database_client_key_file=args.database_client_key_file,
                output=args.output,
                allow_failover_observation=args.allow_failover_observation,
                attempts_per_phase=args.attempts_per_phase,
                probe_interval_milliseconds=args.probe_interval_milliseconds,
                maximum_promotion_milliseconds=args.maximum_promotion_milliseconds,
                maximum_workflow_milliseconds=args.maximum_workflow_milliseconds,
                request_timeout_milliseconds=args.request_timeout_milliseconds,
                minimum_api_availability_basis_points=args.minimum_api_availability_basis_points,
                minimum_receiver_availability_basis_points=args.minimum_receiver_availability_basis_points,
                maximum_consecutive_failures=args.maximum_consecutive_failures,
            )
        else:
            report = verify_report(
                report_path=args.report,
                profile_path=args.profile,
                api_base_url=args.api_base_url,
                otlp_base_url=args.otlp_base_url,
                database_host=args.database_host,
                database_port=args.database_port,
                image_digest=args.image_digest,
                context=args.context,
                namespace=args.namespace,
                require_clean=args.require_clean,
                require_qualified=args.require_qualified,
            )
    except CustomerPostgreSQLContinuityError as exc:
        print(str(exc), file=os.sys.stderr)
        return 1
    print("customer postgresql continuity qualification: " + str(report["spec"]["status"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
