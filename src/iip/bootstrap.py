"""Composition root for local and test runtime profiles."""

from __future__ import annotations

from dataclasses import dataclass

from iip.adapters.memory import (
    AllowTenantPolicy,
    InMemoryEventPublisher,
    InMemoryResourceRepository,
)
from iip.application.ingest_resource import ResourceIngestionService


@dataclass(frozen=True)
class Runtime:
    """Concrete services and adapters owned by one process."""

    resources: InMemoryResourceRepository
    events: InMemoryEventPublisher
    ingestion: ResourceIngestionService


def build_local_runtime() -> Runtime:
    """Build the dependency graph for local execution."""

    resources = InMemoryResourceRepository()
    events = InMemoryEventPublisher()
    policy = AllowTenantPolicy()
    ingestion = ResourceIngestionService(resources, events, policy)
    return Runtime(resources=resources, events=events, ingestion=ingestion)

