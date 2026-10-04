from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from iip.adapters.postgres import (
    PostgresConnectionConfiguration,
    PostgresConnectionConfigurationError,
    PostgresOperationalStore,
    PostgresReadinessProbe,
    PostgresResourceStore,
)
from iip.adapters.postgres.__main__ import main as migration_main
from iip.application.ports import ReadinessError
from iip.bootstrap import (
    build_otlp_receiver_runtime_from_env,
    build_postgres_runtime,
    build_projection_maintenance_from_env,
    build_runtime_from_env,
    build_workflow_worker_runtime_from_env,
)
from tests.test_otlp_receiver import receiver_config


ERROR_CODE = "database.transport.configuration.invalid"


def _write_ca(directory: str) -> Path:
    private_key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "IIP test CA")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(private_key, hashes.SHA256())
    )
    path = Path(directory) / "database-ca.pem"
    path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return path


class PostgresConnectionConfigurationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.ca_path = _write_ca(self.directory.name)

    def test_verify_full_is_default_and_builds_explicit_libpq_policy(self) -> None:
        password = "not-for-logs"
        configuration = PostgresConnectionConfiguration.from_environment(
            f"postgresql://operator:{password}@db-a.example:5432,db-b.example:5433/iip",
            {"IIP_DATABASE_CA_PATH": str(self.ca_path)},
        )

        parameters = conninfo_to_dict(configuration.connection_string)
        self.assertEqual(configuration.transport_mode, "verify-full")
        self.assertEqual(parameters["sslmode"], "verify-full")
        self.assertEqual(parameters["sslrootcert"], str(self.ca_path.absolute()))
        self.assertEqual(parameters["gssencmode"], "disable")
        self.assertEqual(parameters["host"], "db-a.example,db-b.example")
        self.assertNotIn(password, repr(configuration))
        with self.assertRaises(FrozenInstanceError):
            setattr(configuration, "transport_mode", "insecure-local")

    def test_verify_full_rejects_missing_or_invalid_ca_without_echoing_input(self) -> None:
        secret = "database-password-must-not-escape"
        invalid_file = Path(self.directory.name) / "invalid-ca.pem"
        invalid_file.write_text(secret, encoding="utf-8")
        missing_file = Path(self.directory.name) / "missing-ca.pem"

        for environment in (
            {},
            {"IIP_DATABASE_CA_PATH": ""},
            {"IIP_DATABASE_CA_PATH": str(missing_file)},
            {"IIP_DATABASE_CA_PATH": str(invalid_file)},
            {"IIP_DATABASE_CA_PATH": self.directory.name},
        ):
            with self.subTest(environment=environment):
                with self.assertRaises(PostgresConnectionConfigurationError) as raised:
                    PostgresConnectionConfiguration.from_environment(
                        f"postgresql://operator:{secret}@database.example/iip",
                        environment,
                    )
                self.assertEqual(str(raised.exception), ERROR_CODE)
                self.assertNotIn(secret, str(raised.exception))

    def test_verify_full_rejects_dsn_owned_transport_parameters(self) -> None:
        queries = (
            "sslmode=verify-full",
            "gssencmode=disable",
            "sslmode=require",
            "sslrootcert=%2Ftmp%2Fca.pem",
            "sslcert=%2Ftmp%2Fclient.pem",
            "sslkey=%2Ftmp%2Fclient.key",
            "sslcrl=%2Ftmp%2Fdatabase.crl",
            "ssl_min_protocol_version=TLSv1.2",
            "gssencmode=require",
            "channel_binding=require",
            "sslkeylogfile=%2Ftmp%2Fkeylog",
            "hostaddr=127.0.0.1",
            "servicefile=untrusted-service",
            "service=untrusted-service",
        )

        for query in queries:
            with self.subTest(query=query):
                with self.assertRaisesRegex(
                    PostgresConnectionConfigurationError,
                    ERROR_CODE,
                ):
                    PostgresConnectionConfiguration.from_environment(
                        f"postgresql://database.example/iip?{query}",
                        {"IIP_DATABASE_CA_PATH": str(self.ca_path)},
                    )

    def test_direct_configuration_cannot_omit_verified_transport_options(self) -> None:
        required = {
            "host": "database.example",
            "dbname": "iip",
            "sslmode": "verify-full",
            "gssencmode": "disable",
            "sslrootcert": str(self.ca_path),
        }
        for missing in ("sslmode", "gssencmode", "sslrootcert", "host"):
            with self.subTest(missing=missing):
                parameters = {k: v for k, v in required.items() if k != missing}
                with self.assertRaisesRegex(PostgresConnectionConfigurationError, ERROR_CODE):
                    PostgresConnectionConfiguration(
                        "verify-full", str(self.ca_path), make_conninfo(**parameters)
                    )

    def test_ambient_transport_configuration_fails_without_mutating_environment(self) -> None:
        for name in ("PGSERVICE", "PGSERVICEFILE", "PGHOSTADDR", "PGSSLMODE",
                     "PGSSLROOTCERT", "PGSSLKEYLOGFILE", "PGGSSENCMODE"):
            with self.subTest(variable=name):
                environment = {"IIP_DATABASE_CA_PATH": str(self.ca_path), name: "private"}
                before = dict(environment)
                with self.assertRaisesRegex(PostgresConnectionConfigurationError, ERROR_CODE):
                    PostgresConnectionConfiguration.from_environment(
                        "postgresql://database.example/iip", environment
                    )
                self.assertEqual(environment, before)

    def test_verify_full_requires_only_non_unix_hostname_targets(self) -> None:
        urls = (
            "postgresql:///iip",
            "postgresql:///iip?host=%2Fvar%2Frun%2Fpostgresql",
            "postgresql://iip@hostaddr.example/database?hostaddr=127.0.0.1",
            "host=@postgresql dbname=iip",
            "host=db.example,/var/run/postgresql dbname=iip",
            "host=db.example, dbname=iip",
        )

        for database_url in urls:
            with self.subTest(database_url=database_url):
                with self.assertRaisesRegex(
                    PostgresConnectionConfigurationError,
                    ERROR_CODE,
                ):
                    PostgresConnectionConfiguration.from_environment(
                        database_url,
                        {"IIP_DATABASE_CA_PATH": str(self.ca_path)},
                    )

    def test_insecure_local_is_explicit_and_forces_plain_local_transport(self) -> None:
        configuration = PostgresConnectionConfiguration.from_environment(
            "postgresql://postgres/iip?sslmode=verify-full&sslrootcert=%2Ftmp%2Fignored.pem",
            {
                "IIP_DATABASE_TRANSPORT_MODE": "insecure-local",
                "IIP_DATABASE_CA_PATH": "/tmp/also-ignored.pem",
            },
        )

        parameters = conninfo_to_dict(configuration.connection_string)
        self.assertEqual(configuration.transport_mode, "insecure-local")
        self.assertIsNone(configuration.ca_path)
        self.assertEqual(parameters["sslmode"], "disable")
        self.assertEqual(parameters["gssencmode"], "disable")
        self.assertNotIn("sslrootcert", parameters)

        for mode in ("", "require", "VERIFY-FULL", "prefer"):
            with self.subTest(mode=mode):
                with self.assertRaisesRegex(
                    PostgresConnectionConfigurationError,
                    ERROR_CODE,
                ):
                    PostgresConnectionConfiguration.from_environment(
                        "postgresql://postgres/iip",
                        {"IIP_DATABASE_TRANSPORT_MODE": mode},
                    )


class PostgresTransportCompositionTests(unittest.TestCase):
    def test_later_ambient_override_is_rejected_before_any_connection(self) -> None:
        configuration = PostgresConnectionConfiguration.from_environment(
            "postgresql://postgres/iip",
            {"IIP_DATABASE_TRANSPORT_MODE": "insecure-local"},
        )
        for store_type in (PostgresResourceStore, PostgresOperationalStore):
            store = store_type(configuration)
            with (
                self.subTest(adapter=store_type.__name__),
                patch.dict(os.environ, {"PGSERVICE": "private"}),
                patch("psycopg.connect") as connect,
            ):
                before = dict(os.environ)
                with self.assertRaisesRegex(PostgresConnectionConfigurationError, ERROR_CODE):
                    store._connect()
                connect.assert_not_called()
                self.assertEqual(dict(os.environ), before)

        with patch.dict(os.environ, {"PGHOSTADDR": "192.0.2.1"}), patch("psycopg.connect") as connect:
            with self.assertRaisesRegex(ReadinessError, "readiness.unavailable"):
                PostgresReadinessProbe(configuration).check()
            connect.assert_not_called()

    def test_each_postgres_adapter_connects_with_effective_policy(self) -> None:
        configuration = PostgresConnectionConfiguration.from_environment(
            "postgresql://postgres/iip",
            {"IIP_DATABASE_TRANSPORT_MODE": "insecure-local"},
        )

        resources = PostgresResourceStore(configuration)
        with patch("iip.adapters.postgres.store.psycopg.connect") as connect:
            resources._connect()
        self.assertEqual(connect.call_args.args[0], configuration.connection_string)

        operations = PostgresOperationalStore(configuration)
        with patch("iip.adapters.postgres.operations.psycopg.connect") as connect:
            operations._connect()
        self.assertEqual(connect.call_args.args[0], configuration.connection_string)

        readiness = PostgresReadinessProbe(configuration)
        with patch("iip.adapters.postgres.health.psycopg.connect") as connect:
            connection = connect.return_value.__enter__.return_value
            connection.execute.return_value.fetchone.return_value = {"ready": True}
            readiness.check()
        self.assertEqual(connect.call_args.args[0], configuration.connection_string)

    def test_runtime_uses_one_configuration_for_stores_and_readiness(self) -> None:
        configuration = PostgresConnectionConfiguration.from_environment(
            "postgresql://postgres/iip",
            {"IIP_DATABASE_TRANSPORT_MODE": "insecure-local"},
        )
        with (
            patch("iip.adapters.postgres.PostgresResourceStore") as resources,
            patch("iip.adapters.postgres.PostgresOperationalStore") as operations,
            patch("iip.adapters.postgres.PostgresReadinessProbe") as readiness,
        ):
            runtime = build_postgres_runtime(configuration)

        self.addCleanup(runtime.close)
        resources.assert_called_once_with(configuration)
        operations.assert_called_once_with(configuration)
        readiness.assert_called_once_with(configuration, 2)

    def test_api_and_worker_environment_composition_cannot_bypass_policy(self) -> None:
        identity = json.dumps(
            {
                "identities": [
                    {
                        "actorId": "operator",
                        "tenantId": "local",
                        "roles": ["operator"],
                        "tokenSha256": "sha256:" + "0" * 64,
                    }
                ]
            }
        )
        environment = {
            "IIP_AUTH_IDENTITIES_JSON": identity,
            "IIP_DATABASE_URL": "postgresql://postgres/iip",
            "IIP_DATABASE_TRANSPORT_MODE": "insecure-local",
        }

        for builder in (build_runtime_from_env, build_workflow_worker_runtime_from_env):
            sentinel = object()
            with (
                self.subTest(builder=builder.__name__),
                patch.dict(os.environ, environment, clear=True),
                patch(
                    "iip.bootstrap.build_postgres_runtime",
                    return_value=sentinel,
                ) as postgres_runtime,
            ):
                runtime = builder()

            self.assertIs(runtime, sentinel)
            configuration = postgres_runtime.call_args.args[0]
            self.assertIsInstance(configuration, PostgresConnectionConfiguration)
            self.assertEqual(configuration.transport_mode, "insecure-local")

    def test_isolated_receiver_and_migration_use_central_configuration(self) -> None:
        environment = {
            "IIP_DATABASE_URL": "postgresql://postgres/iip",
            "IIP_DATABASE_TRANSPORT_MODE": "insecure-local",
            "IIP_OTLP_RECEIVER_ENABLED": "true",
            "IIP_OTLP_RECEIVER_CHANNELS_JSON": receiver_config(),
        }
        sentinel = object()
        with (
            patch.dict(os.environ, environment, clear=True),
            patch(
                "iip.bootstrap.build_postgres_runtime",
                return_value=sentinel,
            ) as postgres_runtime,
        ):
            runtime = build_otlp_receiver_runtime_from_env()

        self.assertIs(runtime, sentinel)
        configuration = postgres_runtime.call_args.args[0]
        self.assertIsInstance(configuration, PostgresConnectionConfiguration)
        self.assertEqual(configuration.transport_mode, "insecure-local")

        output = io.StringIO()
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("iip.adapters.postgres.__main__.PostgresResourceStore") as store,
            redirect_stdout(output),
        ):
            migration_main()
        migration_configuration = store.call_args.args[0]
        self.assertIsInstance(
            migration_configuration,
            PostgresConnectionConfiguration,
        )
        self.assertEqual(migration_configuration.transport_mode, "insecure-local")
        store.return_value.migrate.assert_called_once_with()
        self.assertIn("migrations are current", output.getvalue())

    def test_maintenance_uses_central_configuration(self) -> None:
        environment = {
            "IIP_DATABASE_URL": "postgresql://postgres/iip",
            "IIP_DATABASE_TRANSPORT_MODE": "insecure-local",
        }
        with (
            patch.dict(os.environ, environment, clear=True),
            patch(
                "iip.bootstrap.build_projection_maintenance",
                side_effect=RuntimeError("composition-observed"),
            ) as maintenance,
            self.assertRaisesRegex(RuntimeError, "composition-observed"),
        ):
            build_projection_maintenance_from_env()

        configuration = maintenance.call_args.args[0]
        self.assertIsInstance(configuration, PostgresConnectionConfiguration)
        self.assertEqual(configuration.transport_mode, "insecure-local")

        source = Path("src/iip/surfaces/maintenance.py").read_text(encoding="utf-8")
        self.assertNotIn("iip.adapters.postgres", source)

    def test_packaged_composition_defaults_to_verify_full_and_fails_closed(self) -> None:
        password = "must-not-escape"
        environment = {
            "IIP_AUTH_IDENTITIES_JSON": json.dumps(
                {
                    "identities": [
                        {
                            "actorId": "operator",
                            "tenantId": "local",
                            "roles": ["operator"],
                            "tokenSha256": "sha256:" + "0" * 64,
                        }
                    ]
                }
            ),
            "IIP_DATABASE_URL": f"postgresql://operator:{password}@database.example/iip",
        }

        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaises(PostgresConnectionConfigurationError) as raised:
                build_runtime_from_env()

        self.assertEqual(str(raised.exception), ERROR_CODE)
        self.assertNotIn(password, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
