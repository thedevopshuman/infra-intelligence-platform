"""Composition root for local and test runtime profiles."""

from __future__ import annotations

import os
from dataclasses import dataclass

from iip.adapters.memory import AllowTenantPolicy, InMemoryResourceStore
from iip.application.ports import (
    EventLog,
    EventOutbox,
    ResourceRepository,
    SourceCheckpointRepository,
)
from iip.application.ingest_resource import ResourceIngestionService
from iip.application.query_resources import ResourceQueryService


@dataclass(frozen=True)
class Runtime:
    """Concrete services and adapters owned by one process."""

    resources: ResourceRepository
    event_log: EventLog
    outbox: EventOutbox
    checkpoints: SourceCheckpointRepository
    ingestion: ResourceIngestionService
    queries: ResourceQueryService


def build_local_runtime() -> Runtime:
    """Build the dependency graph for local execution."""

    store = InMemoryResourceStore()
    policy = AllowTenantPolicy()
    ingestion = ResourceIngestionService(store, policy)
    queries = ResourceQueryService(store, policy)
    return Runtime(
        resources=store,
        event_log=store,
        outbox=store,
        checkpoints=store,
        ingestion=ingestion,
        queries=queries,
    )


def build_postgres_runtime(database_url: str, *, migrate: bool = False) -> Runtime:
    """Build a PostgreSQL-backed runtime without leaking the adapter into use cases."""

    from iip.adapters.postgres import PostgresResourceStore

    store = PostgresResourceStore(database_url)
    if migrate:
        store.migrate()
    policy = AllowTenantPolicy()
    ingestion = ResourceIngestionService(store, policy)
    queries = ResourceQueryService(store, policy)
    return Runtime(
        resources=store,
        event_log=store,
        outbox=store,
        checkpoints=store,
        ingestion=ingestion,
        queries=queries,
    )


def build_runtime_from_env() -> Runtime:
    """Select a runtime profile from process configuration at the composition root."""

    database_url = os.environ.get("IIP_DATABASE_URL")
    if not database_url:
        return build_local_runtime()
    auto_migrate = os.environ.get("IIP_DATABASE_AUTO_MIGRATE", "false").lower() == "true"
    return build_postgres_runtime(database_url, migrate=auto_migrate)
