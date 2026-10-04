"""Apply PostgreSQL migrations for an explicitly configured database."""

from __future__ import annotations

import os

from .connection import (
    PostgresConnectionConfiguration,
    PostgresConnectionConfigurationError,
)
from .store import PostgresResourceStore


def main() -> None:
    database_url = os.environ.get("IIP_DATABASE_URL")
    if not database_url:
        raise SystemExit("IIP_DATABASE_URL is required")
    try:
        connection = PostgresConnectionConfiguration.from_environment(database_url)
    except PostgresConnectionConfigurationError as error:
        raise SystemExit(str(error)) from None
    PostgresResourceStore(connection).migrate()
    print("PostgreSQL migrations are current")


if __name__ == "__main__":
    main()
