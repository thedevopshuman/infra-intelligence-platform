"""Bounded PostgreSQL readiness verification."""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from iip.adapters.postgres.connection import (
    PostgresConnectionConfiguration,
    PostgresConnectionConfigurationError,
    connection_string_for,
    validate_connection_environment,
)
from iip.adapters.postgres.store import SCHEMA_MIGRATIONS
from iip.application.ports import ReadinessError


class PostgresReadinessProbe:
    """Require connectivity and the latest committed schema migration."""

    def __init__(
        self,
        database_url: str | PostgresConnectionConfiguration,
        timeout_seconds: int = 2,
    ) -> None:
        try:
            connection_string = connection_string_for(database_url)
        except ValueError:
            raise ValueError("readiness.database.configuration.invalid") from None
        if not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool):
            raise ValueError("readiness.database.configuration.invalid")
        if timeout_seconds < 1 or timeout_seconds > 10:
            raise ValueError("readiness.database.configuration.invalid")
        self._database_url = connection_string
        self._database_configuration = database_url
        self._timeout_seconds = timeout_seconds

    def check(self) -> None:
        try:
            validate_connection_environment(self._database_configuration)
            with psycopg.connect(
                self._database_url,
                connect_timeout=self._timeout_seconds,
                row_factory=dict_row,
            ) as connection:
                row = connection.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM iip.schema_migrations
                        WHERE version = %s
                    ) AS ready
                    """,
                    (SCHEMA_MIGRATIONS[-1],),
                ).fetchone()
        except (psycopg.Error, PostgresConnectionConfigurationError):
            raise ReadinessError("readiness.unavailable") from None
        if row is None or row.get("ready") is not True:
            raise ReadinessError("readiness.unavailable")
