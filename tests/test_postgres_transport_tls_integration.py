from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from iip.adapters.postgres import (
    PostgresConnectionConfiguration,
    PostgresReadinessProbe,
    PostgresResourceStore,
)
from iip.application.ports import ReadinessError


TLS_DATABASE_URL = os.environ.get("IIP_TEST_POSTGRES_TLS_URL")
TLS_WRONG_HOST_DATABASE_URL = os.environ.get(
    "IIP_TEST_POSTGRES_TLS_WRONG_HOST_URL"
)
PLAINTEXT_DATABASE_URL = os.environ.get("IIP_TEST_POSTGRES_PLAINTEXT_URL")
TRUSTED_CA_FILE = os.environ.get("IIP_TEST_POSTGRES_TLS_CA_FILE")
WRONG_CA_FILE = os.environ.get("IIP_TEST_POSTGRES_TLS_WRONG_CA_FILE")
INTEGRATION_CONFIGURATION = (
    TLS_DATABASE_URL,
    TLS_WRONG_HOST_DATABASE_URL,
    PLAINTEXT_DATABASE_URL,
    TRUSTED_CA_FILE,
    WRONG_CA_FILE,
)


@unittest.skipUnless(
    all(INTEGRATION_CONFIGURATION),
    "the dedicated Docker harness enables PostgreSQL transport integration tests",
)
class PostgresTransportTlsIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        assert TLS_DATABASE_URL is not None
        assert PLAINTEXT_DATABASE_URL is not None
        assert TRUSTED_CA_FILE is not None

        cls.verified_configuration = PostgresConnectionConfiguration.from_environment(
            TLS_DATABASE_URL,
            {"IIP_DATABASE_CA_PATH": TRUSTED_CA_FILE},
        )
        cls.local_configuration = PostgresConnectionConfiguration.from_environment(
            PLAINTEXT_DATABASE_URL,
            {"IIP_DATABASE_TRANSPORT_MODE": "insecure-local"},
        )
        with patch.dict(os.environ, {}, clear=True):
            PostgresResourceStore(cls.verified_configuration).migrate()
            PostgresResourceStore(cls.local_configuration).migrate()

    @staticmethod
    def _transport_state(configuration: PostgresConnectionConfiguration) -> bool:
        store = PostgresResourceStore(configuration)
        with store._connect() as connection:
            row = connection.execute(
                "SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()"
            ).fetchone()
        if row is None or not isinstance(row.get("ssl"), bool):
            raise AssertionError("PostgreSQL did not return a transport state")
        return row["ssl"]

    def assert_readiness_denied(
        self,
        configuration: PostgresConnectionConfiguration,
    ) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            self.assertRaises(ReadinessError) as raised,
        ):
            PostgresReadinessProbe(configuration, timeout_seconds=2).check()
        self.assertEqual(str(raised.exception), "readiness.unavailable")
        self.assertIsNone(raised.exception.__cause__)

    def test_trusted_hostname_negotiates_verified_tls(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            PostgresReadinessProbe(self.verified_configuration).check()
            self.assertTrue(self._transport_state(self.verified_configuration))

    def test_ambient_transport_controls_fail_closed_without_mutation(self) -> None:
        assert WRONG_CA_FILE is not None
        hostile_environment = {
            "PGGSSENCMODE": "require",
            "PGSERVICE": "ambient-service-must-not-be-used",
            "PGSSLMODE": "disable",
            "PGSSLROOTCERT": WRONG_CA_FILE,
        }

        with patch.dict(os.environ, hostile_environment, clear=True):
            with self.assertRaises(ReadinessError) as raised:
                PostgresReadinessProbe(self.verified_configuration).check()
            for name, value in hostile_environment.items():
                self.assertEqual(os.environ[name], value)
        self.assertEqual(str(raised.exception), "readiness.unavailable")
        self.assertIsNone(raised.exception.__cause__)

    def test_unrelated_ca_fails_closed(self) -> None:
        assert TLS_DATABASE_URL is not None
        assert WRONG_CA_FILE is not None
        configuration = PostgresConnectionConfiguration.from_environment(
            TLS_DATABASE_URL,
            {"IIP_DATABASE_CA_PATH": WRONG_CA_FILE},
        )

        self.assert_readiness_denied(configuration)

    def test_wrong_hostname_fails_closed(self) -> None:
        assert TLS_WRONG_HOST_DATABASE_URL is not None
        assert TRUSTED_CA_FILE is not None
        configuration = PostgresConnectionConfiguration.from_environment(
            TLS_WRONG_HOST_DATABASE_URL,
            {"IIP_DATABASE_CA_PATH": TRUSTED_CA_FILE},
        )

        self.assert_readiness_denied(configuration)

    def test_verify_full_refuses_a_plaintext_server(self) -> None:
        assert PLAINTEXT_DATABASE_URL is not None
        assert TRUSTED_CA_FILE is not None
        configuration = PostgresConnectionConfiguration.from_environment(
            PLAINTEXT_DATABASE_URL,
            {"IIP_DATABASE_CA_PATH": TRUSTED_CA_FILE},
        )

        self.assert_readiness_denied(configuration)

    def test_tls_server_rejects_the_local_plaintext_mode(self) -> None:
        assert TLS_DATABASE_URL is not None
        configuration = PostgresConnectionConfiguration.from_environment(
            TLS_DATABASE_URL,
            {"IIP_DATABASE_TRANSPORT_MODE": "insecure-local"},
        )

        self.assert_readiness_denied(configuration)

    def test_explicit_local_mode_connects_only_without_tls(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            PostgresReadinessProbe(self.local_configuration).check()
            self.assertFalse(self._transport_state(self.local_configuration))

        self.assertEqual(self.local_configuration.transport_mode, "insecure-local")
        self.assertIsNone(self.local_configuration.ca_path)


if __name__ == "__main__":
    unittest.main()
