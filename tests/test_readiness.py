from __future__ import annotations

import unittest
from http import HTTPStatus
from unittest.mock import patch

import psycopg

from iip.adapters.postgres.health import PostgresReadinessProbe
from iip.application.ports import ReadinessError
from iip.bootstrap import build_local_runtime
from iip.surfaces.http import ApiHandler
from iip.surfaces.otlp_receiver import OtlpReceiverHandler


class _UnavailableProbe:
    def check(self) -> None:
        raise ReadinessError("provider details must not escape")


class ReadinessHttpTests(unittest.TestCase):
    def test_liveness_stays_up_while_readiness_fails_with_stable_code(self) -> None:
        runtime = build_local_runtime(readiness=_UnavailableProbe())
        self.addCleanup(runtime.close)

        for handler_type in (ApiHandler, OtlpReceiverHandler):
            with self.subTest(handler=handler_type.__name__):
                handler = object.__new__(handler_type)
                handler.runtime = runtime
                handler.headers = {}
                responses: list[tuple[HTTPStatus, dict]] = []
                handler._json = lambda status, payload: responses.append(
                    (status, payload)
                )

                handler.path = "/healthz"
                handler.do_GET()
                handler.path = "/readyz"
                handler.do_GET()

                self.assertEqual(responses[0], (HTTPStatus.OK, {"status": "ok"}))
                self.assertEqual(
                    responses[1],
                    (
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {
                            "status": "unavailable",
                            "error": {"code": "readiness.unavailable"},
                        },
                    ),
                )


class PostgresReadinessProbeTests(unittest.TestCase):
    def test_probe_requires_connectivity_and_latest_migration(self) -> None:
        probe = PostgresReadinessProbe("postgresql://database.example/iip", 3)
        with patch("iip.adapters.postgres.health.psycopg.connect") as connect:
            connection = connect.return_value.__enter__.return_value
            connection.execute.return_value.fetchone.return_value = {"ready": True}

            probe.check()

        self.assertEqual(connect.call_args.kwargs["connect_timeout"], 3)
        query, parameters = connection.execute.call_args.args
        self.assertIn("iip.schema_migrations", query)
        self.assertEqual(parameters, ("0015_plugin_invocation_lifecycle.sql",))

    def test_database_error_or_old_schema_fails_closed(self) -> None:
        probe = PostgresReadinessProbe("postgresql://database.example/iip")
        with patch(
            "iip.adapters.postgres.health.psycopg.connect",
            side_effect=psycopg.OperationalError("secret provider detail"),
        ):
            with self.assertRaisesRegex(ReadinessError, "readiness.unavailable"):
                probe.check()

        with patch("iip.adapters.postgres.health.psycopg.connect") as connect:
            connection = connect.return_value.__enter__.return_value
            connection.execute.return_value.fetchone.return_value = {"ready": False}
            with self.assertRaisesRegex(ReadinessError, "readiness.unavailable"):
                probe.check()

    def test_configuration_is_bounded(self) -> None:
        for database_url, timeout in (("", 2), ("postgresql://db/iip", 0), ("postgresql://db/iip", 11)):
            with self.subTest(database_url=database_url, timeout=timeout):
                with self.assertRaisesRegex(
                    ValueError, "readiness.database.configuration.invalid"
                ):
                    PostgresReadinessProbe(database_url, timeout)


if __name__ == "__main__":
    unittest.main()
