from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import qualify_customer_postgresql_continuity as continuity  # noqa: E402


REVISION = "0123456789abcdef0123456789abcdef01234567"
IMAGE_DIGEST = "sha256:" + "a" * 64
STARTED = datetime(2026, 9, 7, 10, 0, tzinfo=timezone.utc)
WAITING = STARTED + timedelta(minutes=1)
PROMOTED_AT = WAITING + timedelta(seconds=5)
COMPLETED = PROMOTED_AT + timedelta(minutes=1)
REPOSITORY = {
    "applicationVersion": "0.84.0",
    "chartVersion": "0.87.0",
    "requiredMigration": "0023_ai_model_suitability.sql",
}


def observation(
    timeline: int,
    *,
    server: str,
    primary: bool = True,
    tls: bool = True,
    read_only: bool = True,
) -> continuity.DatabaseObservation:
    return continuity.DatabaseObservation(
        timeline=timeline,
        major_version=18,
        primary=primary,
        tls=tls,
        transaction_read_only=read_only,
        server_binding_digest="sha256:" + server * 64,
    )


def workflow() -> dict[str, int]:
    return {
        "workflowSubmitted": 1,
        "workflowCompleted": 1,
        "workflowFailures": 0,
        "workflowPollAttempts": 2,
        "workflowCompletionMilliseconds": 500,
    }


def phase(identifier: str, failures: int = 0) -> dict[str, object]:
    index = continuity.PHASES.index(identifier)
    return {
        "id": identifier,
        "apiAttempts": 20,
        "apiSuccesses": 20 - failures,
        "apiFailures": failures,
        "apiMaximumConsecutiveFailures": failures,
        "receiverAttempts": 20,
        "receiverSuccesses": 20 - failures,
        "receiverFailures": failures,
        "receiverMaximumConsecutiveFailures": failures,
        **workflow(),
        "workflowBoundary": continuity.WORKFLOW_BOUNDARIES[index],
    }


def objective() -> dict[str, int]:
    return {
        "minimumProbeAttemptsPerPhase": 20,
        "probeIntervalMilliseconds": 250,
        "maximumPromotionMilliseconds": 300_000,
        "maximumWorkflowCompletionMilliseconds": 60_000,
        "requestTimeoutMilliseconds": 2_000,
        "minimumApiAvailabilityBasisPoints": 9_500,
        "minimumReceiverAvailabilityBasisPoints": 9_500,
        "maximumConsecutiveFailures": 20,
    }


def report() -> dict[str, object]:
    return continuity.build_report(
        revision=REVISION,
        repository=REPOSITORY,
        image_digest=IMAGE_DIGEST,
        api_target_digest="sha256:" + "1" * 64,
        otlp_target_digest="sha256:" + "2" * 64,
        database_target_digest="sha256:" + "3" * 64,
        context="customer-context",
        namespace="iip-system",
        profile_digest="sha256:" + "4" * 64,
        api_ca_source="custom",
        otlp_ca_source="custom",
        database_client_identity="password",
        objective=objective(),
        initial=observation(7, server="5"),
        promoted=observation(8, server="6"),
        connection_attempts=8,
        connection_failures=2,
        promotion_milliseconds=5_000,
        phases=[phase(identifier) for identifier in continuity.PHASES],
        started_at=STARTED,
        promotion_wait_started_at=WAITING,
        promotion_observed_at=PROMOTED_AT,
        completed_at=COMPLETED,
    )


class CustomerPostgreSQLContinuityReportTests(unittest.TestCase):
    def test_profile_and_report_examples_validate(self) -> None:
        for stem in (
            "customer-postgresql-continuity-profile",
            "customer-postgresql-continuity-qualification-report",
        ):
            schema = json.loads(
                (ROOT / "contracts" / "schemas" / f"{stem}.schema.json").read_text()
            )
            example = json.loads(
                (ROOT / "contracts" / "examples" / f"{stem}.json").read_text()
            )
            errors = list(
                Draft202012Validator(
                    schema, format_checker=FormatChecker()
                ).iter_errors(example)
            )
            self.assertEqual(errors, [])

    def test_builds_closed_qualified_report(self) -> None:
        value = report()
        self.assertEqual(value["spec"]["status"], "qualified")
        self.assertEqual(value["spec"]["summary"]["passedChecks"], 19)
        self.assertEqual(
            value["spec"]["measurements"]["database"]["timelineDelta"], 1
        )
        continuity.validate_report_document(value)

    def test_recomputes_timeline_and_checks(self) -> None:
        value = report()
        value["spec"]["measurements"]["database"]["timelineDelta"] = 2
        metadata = dict(value["metadata"])
        metadata.pop("id")
        value["metadata"]["id"] = continuity._report_identifier(
            metadata, value["spec"]
        )
        with self.assertRaisesRegex(
            continuity.CustomerPostgreSQLContinuityError,
            "customer-postgresql-continuity.report.database-invalid",
        ):
            continuity.validate_report_document(value)

        value = report()
        value["spec"]["checks"][9]["status"] = "failed"
        value["spec"]["checks"][9]["errorCode"] = (
            "customer-postgresql-continuity.database.timeline-not-advanced"
        )
        metadata = dict(value["metadata"])
        metadata.pop("id")
        value["metadata"]["id"] = continuity._report_identifier(
            metadata, value["spec"]
        )
        with self.assertRaisesRegex(
            continuity.CustomerPostgreSQLContinuityError,
            "customer-postgresql-continuity.report.checks-invalid|customer-postgresql-continuity.report.summary-invalid",
        ):
            continuity.validate_report_document(value)

    def test_rejects_sensitive_retained_field(self) -> None:
        value = report()
        value["spec"]["databaseName"] = "customer"
        with self.assertRaisesRegex(
            continuity.CustomerPostgreSQLContinuityError,
            "customer-postgresql-continuity.report.schema-invalid",
        ):
            continuity.validate_report_document(value)

    def test_transition_failure_can_be_bounded(self) -> None:
        phases = [phase("baseline"), phase("promotion-observation", 2), phase("recovery")]
        value = continuity.build_report(
            revision=REVISION,
            repository=REPOSITORY,
            image_digest=IMAGE_DIGEST,
            api_target_digest="sha256:" + "1" * 64,
            otlp_target_digest="sha256:" + "2" * 64,
            database_target_digest="sha256:" + "3" * 64,
            context="customer-context",
            namespace="iip-system",
            profile_digest="sha256:" + "4" * 64,
            api_ca_source="custom",
            otlp_ca_source="custom",
            database_client_identity="password",
            objective=objective(),
            initial=observation(7, server="5"),
            promoted=observation(8, server="6"),
            connection_attempts=8,
            connection_failures=2,
            promotion_milliseconds=5_000,
            phases=phases,
            started_at=STARTED,
            promotion_wait_started_at=WAITING,
            promotion_observed_at=PROMOTED_AT,
            completed_at=COMPLETED,
        )
        self.assertEqual(value["spec"]["status"], "qualified")
        self.assertEqual(value["spec"]["summary"]["apiAvailabilityBasisPoints"], 9666)


class CustomerPostgreSQLContinuityBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.profile = self.root / "profile.json"
        self.profile.write_text(
            (ROOT / "contracts/examples/customer-postgresql-continuity-profile.json").read_text(),
            encoding="utf-8",
        )
        self.profile.chmod(0o600)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_profile_and_password_require_private_regular_files(self) -> None:
        self.profile.chmod(0o644)
        with self.assertRaisesRegex(
            continuity.CustomerPostgreSQLContinuityError,
            "customer-postgresql-continuity.profile.invalid",
        ):
            continuity._load_profile(self.profile)
        password = self.root / "password"
        password.write_text("sensitive\n", encoding="utf-8")
        password.chmod(0o600)
        self.assertEqual(continuity._password(password), "sensitive")
        password.chmod(0o644)
        with self.assertRaisesRegex(
            continuity.CustomerPostgreSQLContinuityError,
            "customer-postgresql-continuity.database-credential.invalid",
        ):
            continuity._password(password)

    def test_database_target_rejects_connection_strings(self) -> None:
        with self.assertRaisesRegex(
            continuity.CustomerPostgreSQLContinuityError,
            "customer-postgresql-continuity.database-target.invalid",
        ):
            continuity._database_target_digest(
                host="postgresql://customer.example", port=5432, database="iip", user="iip"
            )

    def test_observer_uses_verified_read_only_primary_connection(self) -> None:
        password = self.root / "password"
        ca = self.root / "ca.pem"
        password.write_text("secret", encoding="utf-8")
        ca.write_text("certificate", encoding="utf-8")
        password.chmod(0o600)
        captured: dict[str, object] = {}

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def execute(self, query):
                captured["query"] = query

            def fetchone(self):
                return {
                    "in_recovery": False,
                    "server_version_num": 180004,
                    "postmaster_started_at": "2026-09-07 10:00:00+00",
                    "server_address": "192.0.2.10",
                    "server_port": 5432,
                    "transaction_read_only": True,
                    "ssl": True,
                    "tls_version": "TLSv1.3",
                    "timeline_id": 8,
                }

        class Connection:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def cursor(self):
                return Cursor()

        def connect(**kwargs):
            captured.update(kwargs)
            return Connection()

        with patch.object(continuity.psycopg, "connect", side_effect=connect):
            observer = continuity.PostgreSQLObserver(
                host="DB.EXAMPLE.TEST",
                port=5432,
                database="iip",
                user="observer",
                password_file=password,
                ca_file=ca,
                request_timeout_milliseconds=2000,
            )
            observed = observer.observe()
        self.assertIsNotNone(observed)
        self.assertEqual(observed.timeline, 8)
        self.assertEqual(captured["sslmode"], "verify-full")
        self.assertEqual(captured["target_session_attrs"], "read-write")
        self.assertIn("default_transaction_read_only=on", captured["options"])
        self.assertIn("pg_split_walfile_name", captured["query"])
        self.assertNotIn("INSERT", captured["query"])

    def test_qualification_requires_explicit_observation_enable(self) -> None:
        with self.assertRaisesRegex(
            continuity.CustomerPostgreSQLContinuityError,
            "customer-postgresql-continuity.failover.explicit-enable-required",
        ):
            continuity.qualify(
                api_base_url="https://api.example.test",
                api_token_file=self.root / "api-token",
                otlp_base_url="https://otlp.example.test",
                otlp_token_file=self.root / "otlp-token",
                otlp_client_cert_file=self.root / "client.crt",
                otlp_client_key_file=self.root / "client.key",
                profile_path=self.profile,
                image_digest=IMAGE_DIGEST,
                context="customer-context",
                namespace="iip-system",
                database_host="db.example.test",
                database_port=5432,
                database_password_file=self.root / "db-password",
                database_ca_file=self.root / "db-ca.pem",
                output=self.root / "report.json",
                allow_failover_observation=False,
            )

    def test_qualification_observes_timeline_and_workflow_across_promotion(self) -> None:
        created: dict[str, object] = {}

        class PlatformClient:
            api_target_digest = "sha256:" + "1" * 64
            otlp_target_digest = "sha256:" + "2" * 64
            api_ca_source = "custom"
            otlp_ca_source = "custom"

            def __init__(self, **kwargs):
                created["qualification_id"] = kwargs["qualification_id"]
                self.jobs: list[str] = []

            def validate_resource(self):
                return None

            def start_workflow(self, selected_phase):
                self.jobs.append(selected_phase)
                return "inv_" + "b" * 32, 0.0

            def finish_workflow(self, *_args):
                return workflow()

            def probe_cycle(self):
                return True, True

        class DatabaseClient:
            target_digest = "sha256:" + "3" * 64
            client_identity = "password"

            def __init__(self, **_kwargs):
                self.observations = iter(
                    [
                        observation(7, server="5"),
                        None,
                        observation(7, server="5"),
                        observation(8, server="6"),
                        observation(8, server="6"),
                        observation(8, server="6"),
                        observation(8, server="6"),
                    ]
                )

            def observe(self):
                return next(self.observations)

        wall_clock = iter((STARTED, WAITING, PROMOTED_AT, COMPLETED))
        ticks = iter(index / 10 for index in range(100))
        with (
            patch.object(continuity.ingress, "_git_state", return_value=(REVISION, False)),
            patch.object(continuity.ingress, "_repository_identity", return_value=REPOSITORY),
        ):
            value = continuity.qualify(
                api_base_url="https://api.example.test",
                api_token_file=self.root / "api-token",
                otlp_base_url="https://otlp.example.test",
                otlp_token_file=self.root / "otlp-token",
                otlp_client_cert_file=self.root / "client.crt",
                otlp_client_key_file=self.root / "client.key",
                profile_path=self.profile,
                image_digest=IMAGE_DIGEST,
                context="customer-context",
                namespace="iip-system",
                database_host="db.example.test",
                database_port=5432,
                database_password_file=self.root / "db-password",
                database_ca_file=self.root / "db-ca.pem",
                output=self.root / "report.json",
                allow_failover_observation=True,
                attempts_per_phase=5,
                probe_interval_milliseconds=50,
                maximum_promotion_milliseconds=10_000,
                platform_client_factory=PlatformClient,
                database_client_factory=DatabaseClient,
                sleeper=lambda _seconds: None,
                monotonic=lambda: next(ticks),
                now=lambda: next(wall_clock),
            )
        self.assertEqual(value["spec"]["status"], "qualified")
        self.assertEqual(created["qualification_id"], "customer-postgresql-continuity")
        self.assertEqual(
            value["spec"]["measurements"]["database"]["connectionFailures"], 1
        )
        self.assertEqual(
            value["spec"]["measurements"]["phases"][1]["workflowBoundary"],
            "submitted-before-observed-after-promotion",
        )
        self.assertTrue((self.root / "report.json").is_file())

    def test_source_has_no_provider_failover_or_secret_output(self) -> None:
        source = (ROOT / "scripts/qualify_customer_postgresql_continuity.py").read_text()
        self.assertNotIn("aws rds", source.lower())
        self.assertNotIn("failover-db-cluster", source)
        self.assertNotIn("print(database_host", source)


if __name__ == "__main__":
    unittest.main()
