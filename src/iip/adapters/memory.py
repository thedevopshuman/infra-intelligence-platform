"""In-memory adapters for local development and contract tests."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import RLock
from typing import Dict, Iterable, Mapping, Optional

from iip.application.ports import (
    ActorContext,
    AiAllocationLedgerQuery,
    AiInvocationEconomicsQuery,
    AiSavingsCohortQuery,
    AiSavingsFindingLedgerQuery,
    EventDeliverySloState,
    EventDeliveryState,
    OutboxMessage,
    PersistenceError,
    PolicyDecision,
    QuarantinedOutboxMessage,
    ReconciliationSnapshot,
    ResourceObservationRecord,
    ResourceWriteResult,
    SourceCheckpoint,
    SourceIngestionState,
    StoredEvent,
)
from iip.application.query_ai_allocations import validate_ai_allocation_ledger_query
from iip.application.query_ai_invocation import validate_ai_invocation_query
from iip.application.query_ai_savings import (
    validate_ai_savings_finding_ledger_query,
)
from iip.adapters.ai_attribution_store import (
    prepare_ai_attribution_policy,
    prepare_ai_attribution_writes,
    validate_ai_attribution_actor,
)
from iip.adapters.ai_cost_store import (
    prepare_ai_cost_writes,
    prepare_ai_price_catalog,
    validate_ai_cost_actor,
    validate_ai_cost_usage_binding,
)
from iip.adapters.ai_model_suitability_store import (
    prepare_ai_model_suitability_report,
)
from iip.adapters.ai_savings_store import (
    PreparedAiSavingsWrite,
    prepare_ai_savings_writes,
    validate_ai_savings_query,
)
from iip.adapters.ai_usage_store import prepare_ai_usage_writes
from iip.application.evaluate_ai_savings import (
    InvalidAiSavingsInputError,
    validate_ai_savings_source_binding,
)
from iip.application.attribute_ai_usage import (
    InvalidAiAttributionInputError,
    validate_ai_attribution_source_binding,
)
from iip.domain.models import (
    ObservationDisposition,
    PlatformEvent,
    Resource,
    ResourceRelationshipEdge,
    classify_resource_observation,
    index_resource_relationships,
)


@dataclass
class _MemoryOutboxEntry:
    message_id: int
    event: PlatformEvent
    created_at: str
    attempts: int = 0
    claimed_by: Optional[str] = None
    claim_expires_at: Optional[datetime] = None
    available_at: datetime = datetime.min.replace(tzinfo=timezone.utc)
    published_at: Optional[datetime] = None
    last_error_code: Optional[str] = None
    quarantined_at: Optional[datetime] = None


class InMemoryResourceStore:
    """Thread-safe transactional store for the local runtime profile."""

    def __init__(self) -> None:
        self._items: Dict[tuple[str, str], Resource] = {}
        self._history: list[ResourceObservationRecord] = []
        self._event_log: list[StoredEvent] = []
        self._outbox: Dict[int, _MemoryOutboxEntry] = {}
        self._checkpoints: Dict[tuple[str, str], SourceCheckpoint] = {}
        self._reconciliations: Dict[tuple[str, str], ReconciliationSnapshot] = {}
        self._ai_usage: Dict[
            tuple[str, str], tuple[str, Mapping[str, object]]
        ] = {}
        self._ai_usage_ids: Dict[tuple[str, str], str] = {}
        self._ai_attribution_policies: Dict[
            tuple[str, str], tuple[str, Mapping[str, object]]
        ] = {}
        self._ai_attribution_policy_versions: Dict[tuple[str, str], str] = {}
        self._ai_attributions: Dict[
            tuple[str, str], tuple[str, Mapping[str, object]]
        ] = {}
        self._ai_attribution_identities: Dict[
            tuple[str, str, str, str], str
        ] = {}
        self._ai_price_catalogs: Dict[
            tuple[str, str], tuple[str, Mapping[str, object]]
        ] = {}
        self._ai_price_catalog_versions: Dict[tuple[str, str], str] = {}
        self._ai_costs: Dict[
            tuple[str, str], tuple[str, Mapping[str, object]]
        ] = {}
        self._ai_cost_identities: Dict[tuple[str, str, str, str], str] = {}
        self._ai_model_suitability_reports: Dict[
            tuple[str, str], tuple[str, Mapping[str, object]]
        ] = {}
        self._ai_savings: Dict[
            tuple[str, str], tuple[str, Mapping[str, object]]
        ] = {}
        self._lock = RLock()

    @property
    def events(self) -> tuple[PlatformEvent, ...]:
        """Expose accepted events for local diagnostics and focused tests."""

        with self._lock:
            return tuple(item.event for item in self._event_log)

    @property
    def ai_savings_findings(self) -> tuple[Mapping[str, object], ...]:
        """Expose immutable savings findings for local diagnostics and tests."""

        with self._lock:
            return tuple(
                self._json_copy(document)
                for (_digest, document) in self._ai_savings.values()
            )

    @property
    def ai_usage_attributions(self) -> tuple[Mapping[str, object], ...]:
        """Expose immutable attribution facts for local diagnostics and tests."""

        with self._lock:
            return tuple(
                self._json_copy(document)
                for (_digest, document) in self._ai_attributions.values()
            )

    def apply(
        self,
        resource: Resource,
        event: PlatformEvent,
        *,
        checkpoint_ready: bool = False,
    ) -> ResourceWriteResult:
        self._validate_event(resource, event)
        self._validate_checkpoint_request(resource, checkpoint_ready)
        key = (resource.identity.tenant_id, resource.identity.uid)
        observation_hash = PlatformEvent.canonical_hash(resource.to_dict())

        with self._lock:
            current = self._items.get(key)
            disposition = (
                ObservationDisposition.ACCEPTED
                if current is None
                else classify_resource_observation(current, resource)
            )
            if disposition == ObservationDisposition.DUPLICATE:
                return ResourceWriteResult(current, disposition)

            checkpoint = None
            if disposition == ObservationDisposition.ACCEPTED and checkpoint_ready:
                checkpoint = self._next_checkpoint(resource)

            history_offset = len(self._history) + 1
            recorded_at = PlatformEvent.now()
            self._history.append(
                ResourceObservationRecord(
                    offset=history_offset,
                    resource=resource,
                    disposition=disposition,
                    observation_hash=observation_hash,
                    recorded_at=recorded_at,
                )
            )
            if disposition != ObservationDisposition.ACCEPTED:
                return ResourceWriteResult(current, disposition)

            self._items[key] = resource
            event_offset = len(self._event_log) + 1
            self._event_log.append(StoredEvent(event_offset, event))
            self._outbox[event_offset] = _MemoryOutboxEntry(
                event_offset, event, recorded_at
            )
            if checkpoint is not None:
                self._checkpoints[(checkpoint.tenant_id, checkpoint.source_id)] = checkpoint
            return ResourceWriteResult(resource, disposition)

    def get(self, tenant_id: str, uid: str) -> Optional[Resource]:
        with self._lock:
            return self._items.get((tenant_id, uid))

    def list(self, tenant_id: str) -> Iterable[Resource]:
        with self._lock:
            return tuple(
                item
                for (item_tenant, _), item in sorted(self._items.items())
                if item_tenant == tenant_id
            )

    def get_many(self, tenant_id: str, uids: Iterable[str]) -> Iterable[Resource]:
        requested = set(uids)
        with self._lock:
            return tuple(
                resource
                for (item_tenant, uid), resource in sorted(self._items.items())
                if item_tenant == tenant_id and uid in requested
            )

    def history(
        self,
        tenant_id: str,
        uid: str,
        *,
        after_offset: int = 0,
        limit: int = 1000,
    ) -> Iterable[ResourceObservationRecord]:
        self._validate_page(after_offset, limit)
        with self._lock:
            return tuple(
                item
                for item in self._history
                if item.resource.identity.tenant_id == tenant_id
                and item.resource.identity.uid == uid
                and item.offset > after_offset
            )[:limit]

    def relationships(
        self,
        tenant_id: str,
        uid: str,
        *,
        direction: str = "both",
        relationship_types: tuple[str, ...] = (),
        after_edge_id: Optional[str] = None,
        limit: int = 100,
    ) -> Iterable[ResourceRelationshipEdge]:
        self._validate_page(0, limit)
        if direction not in ("incoming", "outgoing", "both"):
            raise ValueError("direction is invalid")
        edges = []
        allowed_types = set(relationship_types)
        with self._lock:
            resources = tuple(
                resource
                for (item_tenant, _), resource in self._items.items()
                if item_tenant == tenant_id
            )
        for resource in resources:
            for edge in index_resource_relationships(resource):
                if direction == "incoming" and edge.target_ref != uid:
                    continue
                if direction == "outgoing" and edge.source_ref != uid:
                    continue
                if direction == "both" and uid not in (edge.source_ref, edge.target_ref):
                    continue
                if allowed_types and edge.relationship_type not in allowed_types:
                    continue
                if after_edge_id is not None and edge.edge_id <= after_edge_id:
                    continue
                edges.append(edge)
        return tuple(sorted(edges, key=lambda item: item.edge_id))[:limit]

    def list_events(
        self,
        tenant_id: str,
        *,
        after_offset: int = 0,
        limit: int = 100,
    ) -> Iterable[StoredEvent]:
        self._validate_page(after_offset, limit)
        with self._lock:
            return tuple(
                item
                for item in self._event_log
                if item.offset > after_offset and item.event.tenant_id == tenant_id
            )[:limit]

    def commit_usage_batch(
        self,
        actor: ActorContext,
        records: tuple[Mapping[str, object], ...],
        events: tuple[PlatformEvent, ...],
    ) -> tuple[Mapping[str, object], ...]:
        """Atomically retain exact AI usage and enqueue each new usage event."""

        prepared = prepare_ai_usage_writes(actor, records, events)
        with self._lock:
            event_identities = {
                (item.event.tenant_id, item.event.source, item.event.event_id)
                for item in self._event_log
            }
            for item in prepared:
                existing = self._ai_usage.get(
                    (item.tenant_id, item.deduplication_key)
                )
                existing_deduplication_key = self._ai_usage_ids.get(
                    (item.tenant_id, item.usage_record_id)
                )
                if existing is not None and existing[0] != item.document_hash:
                    raise PersistenceError("storage.conflict")
                if (
                    existing_deduplication_key is not None
                    and existing_deduplication_key != item.deduplication_key
                ):
                    raise PersistenceError("storage.conflict")
                if existing is None and (
                    item.tenant_id,
                    item.event.source,
                    item.event.event_id,
                ) in event_identities:
                    raise PersistenceError("storage.conflict")

            results: list[Mapping[str, object]] = []
            for item in prepared:
                key = (item.tenant_id, item.deduplication_key)
                existing = self._ai_usage.get(key)
                if existing is not None:
                    results.append(self._json_copy(existing[1]))
                    continue
                document = self._json_copy(item.document)
                self._ai_usage[key] = (item.document_hash, document)
                self._ai_usage_ids[
                    (item.tenant_id, item.usage_record_id)
                ] = item.deduplication_key
                event_offset = len(self._event_log) + 1
                self._event_log.append(StoredEvent(event_offset, item.event))
                self._outbox[event_offset] = _MemoryOutboxEntry(
                    event_offset,
                    item.event,
                    item.recorded_at,
                )
                results.append(self._json_copy(document))
            return tuple(results)

    def register_attribution_policy(
        self,
        actor: ActorContext,
        policy: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Register one immutable exact-tenant attribution snapshot."""

        prepared = prepare_ai_attribution_policy(actor, policy)
        key = (prepared.tenant_id, prepared.policy_id)
        version_key = (prepared.tenant_id, prepared.policy_version)
        with self._lock:
            existing = self._ai_attribution_policies.get(key)
            version_policy = self._ai_attribution_policy_versions.get(version_key)
            if existing is not None:
                if existing[0] != prepared.document_hash:
                    raise PersistenceError("storage.conflict")
                return self._json_copy(existing[1])
            if version_policy is not None and version_policy != prepared.policy_id:
                raise PersistenceError("storage.conflict")
            copied = self._json_copy(prepared.document)
            self._ai_attribution_policies[key] = (prepared.document_hash, copied)
            self._ai_attribution_policy_versions[version_key] = prepared.policy_id
            return self._json_copy(copied)

    def list_usage_without_attribution(
        self,
        actor: ActorContext,
        policy_id: str,
        engine_version: str,
        *,
        limit: int = 100,
    ) -> tuple[Mapping[str, object], ...]:
        """List a bounded exact-tenant page not resolved by this generation."""

        validate_ai_attribution_actor(
            actor,
            policy_id=policy_id,
            engine_version=engine_version,
            limit=limit,
        )
        with self._lock:
            if (actor.tenant_id, policy_id) not in self._ai_attribution_policies:
                raise PersistenceError("storage.request.invalid")
            candidates: list[tuple[str, str, Mapping[str, object]]] = []
            for (tenant_id, _deduplication), (_digest, usage) in self._ai_usage.items():
                if tenant_id != actor.tenant_id:
                    continue
                metadata = usage.get("metadata")
                spec = usage.get("spec")
                invocation = spec.get("invocation") if isinstance(spec, Mapping) else None
                if not isinstance(metadata, Mapping) or not isinstance(invocation, Mapping):
                    raise PersistenceError("storage.state.invalid")
                usage_id = metadata.get("id")
                started_at = invocation.get("startedAt")
                if not isinstance(usage_id, str) or not isinstance(started_at, str):
                    raise PersistenceError("storage.state.invalid")
                identity = (tenant_id, usage_id, policy_id, engine_version)
                if identity in self._ai_attribution_identities:
                    continue
                candidates.append((started_at, usage_id, usage))
            candidates.sort(key=lambda item: (item[0], item[1]))
            return tuple(
                self._json_copy(usage)
                for _started_at, _usage_id, usage in candidates[:limit]
            )

    def commit_usage_attribution_batch(
        self,
        actor: ActorContext,
        records: tuple[Mapping[str, object], ...],
        events: tuple[PlatformEvent, ...],
    ) -> tuple[Mapping[str, object], ...]:
        """Atomically retain source-bound attribution and enqueue events."""

        prepared = prepare_ai_attribution_writes(actor, records, events)
        with self._lock:
            event_identities = {
                (item.event.tenant_id, item.event.source, item.event.event_id)
                for item in self._event_log
            }
            for item in prepared:
                policy = self._ai_attribution_policies.get(
                    (item.tenant_id, item.policy_id)
                )
                usage_key = self._ai_usage_ids.get(
                    (item.tenant_id, item.usage_record_id)
                )
                usage = (
                    self._ai_usage.get((item.tenant_id, usage_key))
                    if usage_key is not None
                    else None
                )
                if policy is None or usage is None:
                    raise PersistenceError("storage.request.invalid")
                policy_document = policy[1]
                policy_metadata = policy_document.get("metadata")
                policy_spec = policy_document.get("spec")
                policy_source = (
                    policy_spec.get("source")
                    if isinstance(policy_spec, Mapping)
                    else None
                )
                if (
                    not isinstance(policy_metadata, Mapping)
                    or not isinstance(policy_source, Mapping)
                    or policy_metadata.get("version") != item.policy_version
                    or policy_source.get("contentHash") != item.policy_source_hash
                ):
                    raise PersistenceError("storage.request.invalid")
                try:
                    validate_ai_attribution_source_binding(
                        item.document,
                        policy_document,
                        usage[1],
                    )
                except InvalidAiAttributionInputError:
                    raise PersistenceError("storage.request.invalid") from None
                key = (item.tenant_id, item.attribution_record_id)
                existing = self._ai_attributions.get(key)
                identity = (
                    item.tenant_id,
                    item.usage_record_id,
                    item.policy_id,
                    item.engine_version,
                )
                existing_id = self._ai_attribution_identities.get(identity)
                if (
                    (existing is not None and existing[0] != item.document_hash)
                    or (
                        existing_id is not None
                        and existing_id != item.attribution_record_id
                    )
                    or (
                        existing is None
                        and (item.tenant_id, item.event.source, item.event.event_id)
                        in event_identities
                    )
                ):
                    raise PersistenceError("storage.conflict")

            results: list[Mapping[str, object]] = []
            for item in prepared:
                key = (item.tenant_id, item.attribution_record_id)
                existing = self._ai_attributions.get(key)
                if existing is not None:
                    results.append(self._json_copy(existing[1]))
                    continue
                copied = self._json_copy(item.document)
                self._ai_attributions[key] = (item.document_hash, copied)
                self._ai_attribution_identities[
                    (
                        item.tenant_id,
                        item.usage_record_id,
                        item.policy_id,
                        item.engine_version,
                    )
                ] = item.attribution_record_id
                event_offset = len(self._event_log) + 1
                self._event_log.append(StoredEvent(event_offset, item.event))
                self._outbox[event_offset] = _MemoryOutboxEntry(
                    event_offset,
                    item.event,
                    item.resolved_at,
                )
                results.append(self._json_copy(copied))
            return tuple(results)

    def register_price_catalog(
        self,
        actor: ActorContext,
        catalog: Mapping[str, object],
    ) -> Mapping[str, object]:
        """Register one immutable exact-tenant price snapshot."""

        prepared = prepare_ai_price_catalog(actor, catalog)
        key = (prepared.tenant_id, prepared.catalog_id)
        version_key = (prepared.tenant_id, prepared.catalog_version)
        with self._lock:
            existing = self._ai_price_catalogs.get(key)
            version_catalog = self._ai_price_catalog_versions.get(version_key)
            if existing is not None:
                if existing[0] != prepared.document_hash:
                    raise PersistenceError("storage.conflict")
                return self._json_copy(existing[1])
            if version_catalog is not None and version_catalog != prepared.catalog_id:
                raise PersistenceError("storage.conflict")
            copied = self._json_copy(prepared.document)
            self._ai_price_catalogs[key] = (prepared.document_hash, copied)
            self._ai_price_catalog_versions[version_key] = prepared.catalog_id
            return self._json_copy(copied)

    def list_usage_without_cost(
        self,
        actor: ActorContext,
        catalog_id: str,
        engine_version: str,
        *,
        limit: int = 100,
    ) -> tuple[Mapping[str, object], ...]:
        validate_ai_cost_actor(
            actor,
            catalog_id=catalog_id,
            engine_version=engine_version,
            limit=limit,
        )
        with self._lock:
            if (actor.tenant_id, catalog_id) not in self._ai_price_catalogs:
                raise PersistenceError("storage.request.invalid")
            candidates: list[Mapping[str, object]] = []
            for (tenant_id, _deduplication_key), (_digest, document) in self._ai_usage.items():
                if tenant_id != actor.tenant_id:
                    continue
                metadata = document["metadata"]
                spec = document["spec"]
                assert isinstance(metadata, Mapping)
                assert isinstance(spec, Mapping)
                usage_record_id = metadata["id"]
                if not isinstance(usage_record_id, str):
                    raise PersistenceError("storage.state.invalid")
                identity = (
                    tenant_id,
                    usage_record_id,
                    catalog_id,
                    engine_version,
                )
                if identity not in self._ai_cost_identities:
                    candidates.append(document)
            candidates.sort(key=self._ai_usage_order)
            return tuple(self._json_copy(item) for item in candidates[:limit])

    def commit_cost_batch(
        self,
        actor: ActorContext,
        records: tuple[Mapping[str, object], ...],
        events: tuple[PlatformEvent, ...],
    ) -> tuple[Mapping[str, object], ...]:
        """Atomically retain calculated cost and enqueue each new event."""

        prepared = prepare_ai_cost_writes(actor, records, events)
        with self._lock:
            event_identities = {
                (item.event.tenant_id, item.event.source, item.event.event_id)
                for item in self._event_log
            }
            for item in prepared:
                catalog = self._ai_price_catalogs.get(
                    (item.tenant_id, item.catalog_id)
                )
                usage_key = self._ai_usage_ids.get(
                    (item.tenant_id, item.usage_record_id)
                )
                usage = (
                    self._ai_usage.get((item.tenant_id, usage_key))
                    if usage_key is not None
                    else None
                )
                existing = self._ai_costs.get(
                    (item.tenant_id, item.cost_record_id)
                )
                identity = (
                    item.tenant_id,
                    item.usage_record_id,
                    item.catalog_id,
                    item.engine_version,
                )
                existing_id = self._ai_cost_identities.get(identity)
                if catalog is None or usage is None:
                    raise PersistenceError("storage.request.invalid")
                validate_ai_cost_usage_binding(item, usage[1])
                catalog_document = catalog[1]
                catalog_metadata = catalog_document["metadata"]
                catalog_spec = catalog_document["spec"]
                assert isinstance(catalog_metadata, Mapping)
                assert isinstance(catalog_spec, Mapping)
                catalog_source = catalog_spec["source"]
                assert isinstance(catalog_source, Mapping)
                if (
                    catalog_metadata["version"] != item.catalog_version
                    or catalog_source["contentHash"]
                    != item.catalog_source_hash
                    or (
                        "test-fixture-pricing" in item.warnings
                    ) != (catalog_source["kind"] == "test-fixture")
                    or (
                        item.cost_status == "priced"
                        and (
                            item.currency != catalog_spec["currency"]
                            or item.currency_scale != catalog_spec["currencyScale"]
                        )
                    )
                ):
                    raise PersistenceError("storage.request.invalid")
                if (
                    (existing is not None and existing[0] != item.document_hash)
                    or (
                        existing_id is not None
                        and existing_id != item.cost_record_id
                    )
                    or (
                        existing is None
                        and (item.tenant_id, item.event.source, item.event.event_id)
                        in event_identities
                    )
                ):
                    raise PersistenceError("storage.conflict")

            results: list[Mapping[str, object]] = []
            for item in prepared:
                key = (item.tenant_id, item.cost_record_id)
                existing = self._ai_costs.get(key)
                if existing is not None:
                    results.append(self._json_copy(existing[1]))
                    continue
                copied = self._json_copy(item.document)
                self._ai_costs[key] = (item.document_hash, copied)
                self._ai_cost_identities[
                    (
                        item.tenant_id,
                        item.usage_record_id,
                        item.catalog_id,
                        item.engine_version,
                    )
                ] = item.cost_record_id
                event_offset = len(self._event_log) + 1
                self._event_log.append(StoredEvent(event_offset, item.event))
                self._outbox[event_offset] = _MemoryOutboxEntry(
                    event_offset,
                    item.event,
                    item.calculated_at,
                )
                results.append(self._json_copy(copied))
            return tuple(results)

    def list_ai_savings_cohort(
        self,
        actor: ActorContext,
        query: AiSavingsCohortQuery,
    ) -> tuple[tuple[Mapping[str, object], Mapping[str, object] | None], ...]:
        """Read a bounded exact-scope usage/cost cohort."""

        validate_ai_savings_query(actor, query)
        with self._lock:
            candidates: list[
                tuple[str, str, Mapping[str, object], Mapping[str, object] | None]
            ] = []
            for (tenant_id, _deduplication_key), (_digest, usage) in self._ai_usage.items():
                if tenant_id != actor.tenant_id or not self._ai_savings_usage_matches(
                    usage, query
                ):
                    continue
                metadata = usage["metadata"]
                invocation = usage["spec"]["invocation"]  # type: ignore[index]
                assert isinstance(metadata, Mapping) and isinstance(invocation, Mapping)
                usage_id = metadata["id"]
                started_at = invocation["startedAt"]
                if not isinstance(usage_id, str) or not isinstance(started_at, str):
                    raise PersistenceError("storage.state.invalid")
                cost_id = self._ai_cost_identities.get(
                    (
                        tenant_id,
                        usage_id,
                        query.catalog_id,
                        query.engine_version,
                    )
                )
                cost = (
                    self._ai_costs.get((tenant_id, cost_id))[1]
                    if cost_id is not None
                    and (tenant_id, cost_id) in self._ai_costs
                    else None
                )
                candidates.append((started_at, usage_id, usage, cost))
            candidates.sort(key=lambda item: (item[0], item[1]))
            return tuple(
                (
                    self._json_copy(usage),
                    self._json_copy(cost) if cost is not None else None,
                )
                for _started_at, _usage_id, usage, cost in candidates[: query.limit]
            )

    def register_ai_model_suitability_report(
        self,
        actor: ActorContext,
        report: Mapping[str, object],
        *,
        allow_test_fixtures: bool = False,
    ) -> Mapping[str, object]:
        """Register one immutable exact-tenant model suitability report."""

        prepared = prepare_ai_model_suitability_report(
            actor,
            report,
            allow_test_fixtures=allow_test_fixtures,
        )
        key = (prepared.tenant_id, prepared.report_id)
        with self._lock:
            existing = self._ai_model_suitability_reports.get(key)
            if existing is not None:
                if existing[0] != prepared.document_hash:
                    raise PersistenceError("storage.conflict")
                return self._json_copy(existing[1])
            copied = self._json_copy(prepared.document)
            self._ai_model_suitability_reports[key] = (
                prepared.document_hash,
                copied,
            )
            return self._json_copy(copied)

    def list_ai_allocation_rows(
        self,
        actor: ActorContext,
        query: AiAllocationLedgerQuery,
    ) -> tuple[
        tuple[
            Mapping[str, object],
            Mapping[str, object] | None,
            Mapping[str, object] | None,
        ],
        ...,
    ]:
        """Read a bounded interval with exact attribution and cost generations."""

        start, end = validate_ai_allocation_ledger_query(actor, query)
        with self._lock:
            candidates: list[
                tuple[
                    str,
                    str,
                    Mapping[str, object],
                    Mapping[str, object] | None,
                    Mapping[str, object] | None,
                ]
            ] = []
            for (tenant_id, _deduplication_key), (_digest, usage) in self._ai_usage.items():
                if tenant_id != actor.tenant_id:
                    continue
                try:
                    metadata = usage["metadata"]
                    spec = usage["spec"]
                    assert isinstance(metadata, Mapping) and isinstance(spec, Mapping)
                    invocation = spec["invocation"]
                    assert isinstance(invocation, Mapping)
                    usage_id = metadata["id"]
                    started_text = invocation["startedAt"]
                    if not isinstance(usage_id, str) or not isinstance(started_text, str):
                        raise ValueError
                    started_at = datetime.fromisoformat(
                        started_text.replace("Z", "+00:00")
                    )
                except (AssertionError, KeyError, TypeError, ValueError):
                    raise PersistenceError("storage.state.invalid") from None
                if not start <= started_at < end:
                    continue
                attribution_id = self._ai_attribution_identities.get(
                    (
                        tenant_id,
                        usage_id,
                        query.policy_id,
                        query.attribution_engine_version,
                    )
                )
                attribution = (
                    self._ai_attributions[(tenant_id, attribution_id)][1]
                    if attribution_id is not None
                    and (tenant_id, attribution_id) in self._ai_attributions
                    else None
                )
                cost_id = self._ai_cost_identities.get(
                    (
                        tenant_id,
                        usage_id,
                        query.catalog_id,
                        query.cost_engine_version,
                    )
                )
                cost = (
                    self._ai_costs[(tenant_id, cost_id)][1]
                    if cost_id is not None and (tenant_id, cost_id) in self._ai_costs
                    else None
                )
                candidates.append(
                    (started_text, usage_id, usage, attribution, cost)
                )
            candidates.sort(key=lambda item: (item[0], item[1]))
            return tuple(
                (
                    self._json_copy(usage),
                    self._json_copy(attribution) if attribution is not None else None,
                    self._json_copy(cost) if cost is not None else None,
                )
                for _started, _usage_id, usage, attribution, cost in candidates[
                    : query.limit
                ]
            )

    def find_ai_invocation_economics(
        self,
        actor: ActorContext,
        query: AiInvocationEconomicsQuery,
    ) -> tuple[
        Mapping[str, object],
        Mapping[str, object] | None,
        Mapping[str, object] | None,
    ] | None:
        """Find one exact trace/span under the selected economics generations."""

        validate_ai_invocation_query(actor, query)
        with self._lock:
            matches: list[Mapping[str, object]] = []
            for (tenant_id, _deduplication), (_digest, usage) in self._ai_usage.items():
                if tenant_id != actor.tenant_id:
                    continue
                spec = usage.get("spec")
                invocation = spec.get("invocation") if isinstance(spec, Mapping) else None
                if (
                    isinstance(invocation, Mapping)
                    and invocation.get("traceId") == query.trace_id
                    and invocation.get("spanId") == query.span_id
                ):
                    matches.append(usage)
            if len(matches) > 1:
                raise PersistenceError("storage.state.invalid")
            if not matches:
                return None
            usage = matches[0]
            metadata = usage.get("metadata")
            if not isinstance(metadata, Mapping) or not isinstance(
                metadata.get("id"), str
            ):
                raise PersistenceError("storage.state.invalid")
            usage_id = str(metadata["id"])
            attribution_id = self._ai_attribution_identities.get(
                (
                    actor.tenant_id,
                    usage_id,
                    query.policy_id,
                    query.attribution_engine_version,
                )
            )
            cost_id = self._ai_cost_identities.get(
                (
                    actor.tenant_id,
                    usage_id,
                    query.catalog_id,
                    query.cost_engine_version,
                )
            )
            attribution = (
                self._ai_attributions.get((actor.tenant_id, attribution_id))
                if attribution_id is not None
                else None
            )
            cost = (
                self._ai_costs.get((actor.tenant_id, cost_id))
                if cost_id is not None
                else None
            )
            return (
                self._json_copy(usage),
                self._json_copy(attribution[1]) if attribution is not None else None,
                self._json_copy(cost[1]) if cost is not None else None,
            )

    def list_ai_savings_findings(
        self,
        actor: ActorContext,
        query: AiSavingsFindingLedgerQuery,
    ) -> tuple[Mapping[str, object], ...]:
        """Read a bounded newest-first page of exact-tenant findings."""

        start, end, before = validate_ai_savings_finding_ledger_query(actor, query)
        with self._lock:
            candidates: list[
                tuple[datetime, str, Mapping[str, object]]
            ] = []
            try:
                for (tenant_id, finding_id), (_digest, document) in self._ai_savings.items():
                    if tenant_id != actor.tenant_id:
                        continue
                    metadata = document["metadata"]
                    if not isinstance(metadata, Mapping):
                        raise ValueError
                    evaluated_text = metadata["evaluatedAt"]
                    if (
                        metadata.get("id") != finding_id
                        or not isinstance(evaluated_text, str)
                    ):
                        raise ValueError
                    evaluated_at = datetime.fromisoformat(
                        evaluated_text.replace("Z", "+00:00")
                    )
                    key = (evaluated_at, finding_id)
                    if (
                        start <= evaluated_at < end
                        and (before is None or key < before)
                    ):
                        candidates.append((evaluated_at, finding_id, document))
            except (KeyError, TypeError, ValueError, OverflowError):
                raise PersistenceError("storage.state.invalid") from None
            candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
            return tuple(
                self._json_copy(document)
                for _evaluated_at, _finding_id, document in candidates[: query.limit]
            )

    def commit_ai_savings_batch(
        self,
        actor: ActorContext,
        findings: tuple[Mapping[str, object], ...],
        events: tuple[PlatformEvent, ...],
    ) -> tuple[Mapping[str, object], ...]:
        """Atomically retain source-bound savings findings and enqueue events."""

        prepared = prepare_ai_savings_writes(actor, findings, events)
        with self._lock:
            event_identities = {
                (item.event.tenant_id, item.event.source, item.event.event_id)
                for item in self._event_log
            }
            for item in prepared:
                usage_documents = tuple(
                    document
                    for (
                        tenant_id,
                        _deduplication_key,
                    ), (_digest, document) in self._ai_usage.items()
                    if tenant_id == item.tenant_id
                    and self._ai_savings_finding_scope_matches(document, item)
                )
                if {
                    document["metadata"]["id"]  # type: ignore[index]
                    for document in usage_documents
                } != set(item.usage_record_ids):
                    raise PersistenceError("storage.request.invalid")
                cost_documents: list[Mapping[str, object]] = []
                for cost_id in item.cost_record_ids:
                    stored_cost = self._ai_costs.get((item.tenant_id, cost_id))
                    if stored_cost is None:
                        raise PersistenceError("storage.request.invalid")
                    cost_documents.append(stored_cost[1])
                suitability_documents: tuple[Mapping[str, object], ...] = ()
                if item.suitability_report_ids:
                    reports: list[Mapping[str, object]] = []
                    for report_id in item.suitability_report_ids:
                        stored_report = self._ai_model_suitability_reports.get(
                            (item.tenant_id, report_id)
                        )
                        if stored_report is None:
                            raise PersistenceError("storage.request.invalid")
                        reports.append(stored_report[1])
                    suitability_documents = tuple(reports)
                try:
                    validate_ai_savings_source_binding(
                        item.document,
                        usage_documents,
                        tuple(cost_documents),
                        suitability_documents,
                    )
                except InvalidAiSavingsInputError:
                    raise PersistenceError("storage.request.invalid") from None
                existing = self._ai_savings.get(
                    (item.tenant_id, item.finding_id)
                )
                if (
                    (existing is not None and existing[0] != item.document_hash)
                    or (
                        existing is None
                        and (item.tenant_id, item.event.source, item.event.event_id)
                        in event_identities
                    )
                ):
                    raise PersistenceError("storage.conflict")

            results: list[Mapping[str, object]] = []
            for item in prepared:
                key = (item.tenant_id, item.finding_id)
                existing = self._ai_savings.get(key)
                if existing is not None:
                    results.append(self._json_copy(existing[1]))
                    continue
                copied = self._json_copy(item.document)
                self._ai_savings[key] = (item.document_hash, copied)
                event_offset = len(self._event_log) + 1
                self._event_log.append(StoredEvent(event_offset, item.event))
                self._outbox[event_offset] = _MemoryOutboxEntry(
                    event_offset,
                    item.event,
                    item.evaluated_at,
                )
                results.append(self._json_copy(copied))
            return tuple(results)

    @staticmethod
    def _ai_savings_usage_matches(
        usage: Mapping[str, object],
        query: AiSavingsCohortQuery,
    ) -> bool:
        try:
            spec = usage["spec"]
            assert isinstance(spec, Mapping)
            invocation = spec["invocation"]
            attribution = spec["attribution"]
            assert isinstance(invocation, Mapping) and isinstance(attribution, Mapping)
            model_id = invocation.get("responseModel", invocation["requestModel"])
            started_at = datetime.fromisoformat(
                str(invocation["startedAt"]).replace("Z", "+00:00")
            )
            start = datetime.fromisoformat(query.start.replace("Z", "+00:00"))
            end = datetime.fromisoformat(query.end.replace("Z", "+00:00"))
            return (
                invocation["provider"] == query.provider
                and model_id == query.model_id
                and invocation["region"] == query.region
                and invocation["outcome"] == "success"
                and attribution["serviceName"] == query.service_name
                and attribution.get("deploymentEnvironment")
                == query.deployment_environment
                and start <= started_at < end
            )
        except (AssertionError, KeyError, TypeError, ValueError):
            raise PersistenceError("storage.state.invalid") from None

    @classmethod
    def _ai_savings_finding_scope_matches(
        cls,
        usage: Mapping[str, object],
        finding: PreparedAiSavingsWrite,
    ) -> bool:
        model_id = finding.model_id
        start = finding.baseline_start
        end = finding.current_end
        if finding.candidate_model_id is not None:
            try:
                spec = usage["spec"]
                assert isinstance(spec, Mapping)
                invocation = spec["invocation"]
                assert isinstance(invocation, Mapping)
                usage_model = invocation.get(
                    "responseModel",
                    invocation["requestModel"],
                )
                started_at = datetime.fromisoformat(
                    str(invocation["startedAt"]).replace("Z", "+00:00")
                )
                current_start = datetime.fromisoformat(
                    finding.current_start.replace("Z", "+00:00")
                )
            except (AssertionError, KeyError, TypeError, ValueError):
                raise PersistenceError("storage.state.invalid") from None
            if started_at < current_start:
                model_id = finding.candidate_model_id
                end = finding.baseline_end
            else:
                start = finding.current_start
        baseline_query = AiSavingsCohortQuery(
            provider=finding.provider,
            model_id=model_id,
            region=finding.region,
            service_name=finding.service_name,
            deployment_environment=finding.deployment_environment,
            start=start,
            end=end,
            catalog_id="apc_00000000000000000000000000000000",
            engine_version="0.1.0",
            limit=1,
        )
        return cls._ai_savings_usage_matches(usage, baseline_query)

    def claim_outbox(
        self,
        tenant_id: str,
        worker_id: str,
        *,
        limit: int = 100,
        lease_seconds: int = 30,
    ) -> Iterable[OutboxMessage]:
        self._validate_worker(worker_id)
        self._validate_page(0, limit)
        if lease_seconds < 1 or lease_seconds > 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")

        now = datetime.now(timezone.utc)
        claimed: list[OutboxMessage] = []
        with self._lock:
            for entry in self._outbox.values():
                if len(claimed) == limit:
                    break
                if (
                    entry.event.tenant_id != tenant_id
                    or entry.published_at is not None
                    or entry.quarantined_at is not None
                ):
                    continue
                if entry.available_at > now:
                    continue
                if entry.claim_expires_at is not None and entry.claim_expires_at > now:
                    continue
                entry.attempts += 1
                entry.claimed_by = worker_id
                entry.claim_expires_at = now + timedelta(seconds=lease_seconds)
                claimed.append(OutboxMessage(entry.message_id, entry.event, entry.attempts))
        return tuple(claimed)

    def acknowledge_outbox(
        self,
        tenant_id: str,
        worker_id: str,
        message_id: int,
    ) -> bool:
        with self._lock:
            entry = self._outbox.get(message_id)
            if (
                entry is None
                or entry.event.tenant_id != tenant_id
                or entry.claimed_by != worker_id
                or entry.claim_expires_at is None
                or entry.claim_expires_at <= datetime.now(timezone.utc)
                or entry.published_at is not None
                or entry.quarantined_at is not None
            ):
                return False
            entry.published_at = datetime.now(timezone.utc)
            entry.claimed_by = None
            entry.claim_expires_at = None
            return True

    def release_outbox(
        self,
        tenant_id: str,
        worker_id: str,
        message_id: int,
        error_code: str,
        *,
        retry_after_seconds: int = 0,
    ) -> bool:
        self._validate_error_code(error_code)
        if retry_after_seconds < 0 or retry_after_seconds > 86_400:
            raise ValueError("retry_after_seconds must be between 0 and 86400")
        with self._lock:
            entry = self._outbox.get(message_id)
            if (
                entry is None
                or entry.event.tenant_id != tenant_id
                or entry.claimed_by != worker_id
                or entry.claim_expires_at is None
                or entry.claim_expires_at <= datetime.now(timezone.utc)
                or entry.published_at is not None
                or entry.quarantined_at is not None
            ):
                return False
            entry.claimed_by = None
            entry.claim_expires_at = None
            entry.available_at = datetime.now(timezone.utc) + timedelta(
                seconds=retry_after_seconds
            )
            entry.last_error_code = error_code
            return True

    def quarantine_outbox(
        self,
        tenant_id: str,
        worker_id: str,
        message_id: int,
        error_code: str,
    ) -> bool:
        self._validate_error_code(error_code)
        now = datetime.now(timezone.utc)
        with self._lock:
            entry = self._outbox.get(message_id)
            if (
                entry is None
                or entry.event.tenant_id != tenant_id
                or entry.claimed_by != worker_id
                or entry.claim_expires_at is None
                or entry.claim_expires_at <= now
                or entry.published_at is not None
                or entry.quarantined_at is not None
            ):
                return False
            entry.claimed_by = None
            entry.claim_expires_at = None
            entry.last_error_code = error_code
            entry.quarantined_at = now
            return True

    def get_event_delivery_state(
        self,
        tenant_id: str,
        *,
        quarantine_limit: int = 50,
    ) -> EventDeliveryState:
        self._validate_page(0, quarantine_limit)
        if quarantine_limit > 50:
            raise ValueError("quarantine_limit must be between 1 and 50")
        now = datetime.now(timezone.utc)
        with self._lock:
            tenant_entries = tuple(
                entry
                for entry in self._outbox.values()
                if entry.event.tenant_id == tenant_id
            )
            pending = tuple(
                entry
                for entry in tenant_entries
                if entry.published_at is None and entry.quarantined_at is None
            )
            quarantined = sorted(
                (
                    entry
                    for entry in tenant_entries
                    if entry.quarantined_at is not None
                ),
                key=lambda entry: (entry.quarantined_at, entry.message_id),
                reverse=True,
            )
            oldest = min((entry.created_at for entry in pending), default=None)
            return EventDeliveryState(
                tenant_id=tenant_id,
                pending_events=len(pending),
                in_flight_events=sum(
                    entry.claim_expires_at is not None
                    and entry.claim_expires_at > now
                    for entry in pending
                ),
                retrying_events=sum(
                    entry.last_error_code is not None for entry in pending
                ),
                quarantined_events=len(quarantined),
                oldest_pending_event_recorded_at=oldest,
                quarantined=tuple(
                    self._quarantined_message(entry)
                    for entry in quarantined[:quarantine_limit]
                    if entry.quarantined_at is not None
                ),
            )

    def get_event_delivery_slo_state(
        self,
        tenant_id: str,
        *,
        window_start: str,
        window_end: str,
        maturity_cutoff: str,
        latency_objective_seconds: int,
    ) -> EventDeliverySloState:
        if (
            isinstance(latency_objective_seconds, bool)
            or not isinstance(latency_objective_seconds, int)
            or not 1 <= latency_objective_seconds <= 86_400
        ):
            raise ValueError("latency_objective_seconds is invalid")
        try:
            start = datetime.fromisoformat(window_start.replace("Z", "+00:00"))
            end = datetime.fromisoformat(window_end.replace("Z", "+00:00"))
            cutoff = datetime.fromisoformat(
                maturity_cutoff.replace("Z", "+00:00")
            )
        except (AttributeError, TypeError, ValueError):
            raise ValueError("event delivery SLO window is invalid") from None
        if (
            start.tzinfo is None
            or end.tzinfo is None
            or cutoff.tzinfo is None
            or not start < cutoff < end
            or int((end - cutoff).total_seconds()) != latency_objective_seconds
        ):
            raise ValueError("event delivery SLO window is invalid")

        with self._lock:
            cohort = []
            for entry in self._outbox.values():
                if entry.event.tenant_id != tenant_id:
                    continue
                created_at = datetime.fromisoformat(
                    entry.created_at.replace("Z", "+00:00")
                )
                if start <= created_at <= end:
                    cohort.append((entry, created_at))
            eligible = [item for item in cohort if item[1] <= cutoff]
            within = []
            late = []
            undelivered = []
            for entry, created_at in eligible:
                if entry.published_at is None:
                    undelivered.append(entry)
                elif entry.published_at <= created_at + timedelta(
                    seconds=latency_objective_seconds
                ):
                    within.append(entry)
                else:
                    late.append(entry)

        return EventDeliverySloState(
            tenant_id=tenant_id,
            window_start=window_start,
            window_end=window_end,
            maturity_cutoff=maturity_cutoff,
            created_events=len(cohort),
            immature_events=len(cohort) - len(eligible),
            eligible_events=len(eligible),
            within_objective_events=len(within),
            late_delivered_events=len(late),
            undelivered_events=len(undelivered),
            quarantined_events=sum(
                entry.quarantined_at is not None for entry in undelivered
            ),
        )

    def get_quarantined_outbox(
        self,
        tenant_id: str,
        message_id: int,
    ) -> Optional[QuarantinedOutboxMessage]:
        if (
            isinstance(message_id, bool)
            or not isinstance(message_id, int)
            or message_id < 1
        ):
            raise ValueError("message_id must be a positive integer")
        with self._lock:
            entry = self._outbox.get(message_id)
            if (
                entry is None
                or entry.event.tenant_id != tenant_id
                or entry.quarantined_at is None
            ):
                return None
            return self._quarantined_message(entry)

    def requeue_quarantined_outbox(
        self,
        tenant_id: str,
        message_id: int,
        *,
        expected_event_id: str,
        expected_quarantined_at: str,
        expected_attempts: int,
    ) -> bool:
        if (
            isinstance(message_id, bool)
            or not isinstance(message_id, int)
            or message_id < 1
            or not isinstance(expected_event_id, str)
            or not isinstance(expected_quarantined_at, str)
            or isinstance(expected_attempts, bool)
            or not isinstance(expected_attempts, int)
        ):
            raise ValueError("event delivery replay preconditions are invalid")
        now = datetime.now(timezone.utc)
        with self._lock:
            entry = self._outbox.get(message_id)
            quarantined_at = (
                entry.quarantined_at.isoformat().replace("+00:00", "Z")
                if entry is not None and entry.quarantined_at is not None
                else None
            )
            if (
                entry is None
                or entry.event.tenant_id != tenant_id
                or entry.event.event_id != expected_event_id
                or quarantined_at != expected_quarantined_at
                or entry.attempts != expected_attempts
                or entry.published_at is not None
            ):
                return False
            entry.attempts = 0
            entry.claimed_by = None
            entry.claim_expires_at = None
            entry.available_at = now
            entry.last_error_code = None
            entry.quarantined_at = None
            return True

    @staticmethod
    def _quarantined_message(
        entry: _MemoryOutboxEntry,
    ) -> QuarantinedOutboxMessage:
        assert entry.quarantined_at is not None
        return QuarantinedOutboxMessage(
            message_id=entry.message_id,
            tenant_id=entry.event.tenant_id,
            event_id=entry.event.event_id,
            event_source=entry.event.source,
            event_type=entry.event.event_type,
            subject=entry.event.subject,
            attempts=entry.attempts,
            quarantined_at=entry.quarantined_at.isoformat().replace(
                "+00:00", "Z"
            ),
            last_error_code=str(entry.last_error_code),
        )

    def get_checkpoint(self, tenant_id: str, source_id: str) -> Optional[SourceCheckpoint]:
        with self._lock:
            return self._checkpoints.get((tenant_id, source_id))

    def get_source_ingestion_state(
        self, tenant_id: str, source_id: str
    ) -> Optional[SourceIngestionState]:
        with self._lock:
            checkpoint = self._checkpoints.get((tenant_id, source_id))
            if checkpoint is None:
                return None
            observations = [
                item
                for item in self._history
                if item.disposition == ObservationDisposition.ACCEPTED
                and item.resource.identity.tenant_id == tenant_id
                and item.resource.observation is not None
                and item.resource.observation.source_id == source_id
            ]
            latest = max(observations, key=lambda item: item.offset, default=None)
            pending = []
            for entry in self._outbox.values():
                observation = entry.event.data.get("observation")
                if (
                    entry.event.tenant_id == tenant_id
                    and entry.published_at is None
                    and entry.quarantined_at is None
                    and isinstance(observation, Mapping)
                    and observation.get("sourceId") == source_id
                ):
                    pending.append(entry)
            return SourceIngestionState(
                tenant_id=tenant_id,
                source_id=source_id,
                stream_id=checkpoint.stream_id,
                checkpoint_sequence=checkpoint.sequence,
                checkpoint_committed_at=checkpoint.committed_at,
                latest_observed_at=(
                    latest.resource.observed_at if latest is not None else None
                ),
                latest_recorded_at=latest.recorded_at if latest is not None else None,
                accepted_observation_count=len(observations),
                pending_event_count=len(pending),
                oldest_pending_event_recorded_at=(
                    min(item.created_at for item in pending) if pending else None
                ),
            )

    def commit_checkpoint(self, checkpoint: SourceCheckpoint, *, mode: str) -> None:
        if mode not in ("incremental", "reconciliation"):
            raise ValueError("checkpoint mode is invalid")
        key = (checkpoint.tenant_id, checkpoint.source_id)
        with self._lock:
            current = self._checkpoints.get(key)
            self._validate_checkpoint_advance(current, checkpoint, mode)
            if current is not None and self._same_checkpoint(current, checkpoint):
                return
            self._checkpoints[key] = checkpoint

    def get_reconciliation(
        self, tenant_id: str, source_id: str
    ) -> Optional[ReconciliationSnapshot]:
        with self._lock:
            return self._reconciliations.get((tenant_id, source_id))

    def commit_reconciliation(
        self,
        snapshot: ReconciliationSnapshot,
        checkpoint: SourceCheckpoint,
    ) -> None:
        self._validate_reconciliation(snapshot, checkpoint)
        key = (snapshot.tenant_id, snapshot.source_id)
        with self._lock:
            current_snapshot = self._reconciliations.get(key)
            if current_snapshot is not None:
                if current_snapshot.snapshot_id == snapshot.snapshot_id:
                    if not self._same_reconciliation(current_snapshot, snapshot):
                        raise ValueError("reconciliation snapshot content conflicts")
                elif current_snapshot.scope_digest != snapshot.scope_digest:
                    raise ValueError("reconciliation source scope cannot change")
            self._validate_checkpoint_advance(
                self._checkpoints.get(key), checkpoint, "reconciliation"
            )
            self._reconciliations[key] = snapshot
            self._checkpoints[key] = checkpoint

    def _next_checkpoint(self, resource: Resource) -> SourceCheckpoint:
        cursor = resource.observation
        assert cursor is not None and cursor.checkpoint is not None
        key = (resource.identity.tenant_id, cursor.source_id)
        current = self._checkpoints.get(key)
        if current is not None:
            if current.stream_id == cursor.stream_id and cursor.sequence < current.sequence:
                raise ValueError("checkpoint sequence cannot move backwards")
            if (
                current.stream_id == cursor.stream_id
                and cursor.sequence == current.sequence
                and cursor.checkpoint != current.checkpoint
            ):
                raise ValueError("checkpoint content conflicts at the same sequence")
            if current.stream_id != cursor.stream_id and cursor.mode != "reconciliation":
                raise ValueError("checkpoint stream reset requires reconciliation")
        return SourceCheckpoint(
            tenant_id=resource.identity.tenant_id,
            source_id=cursor.source_id,
            stream_id=cursor.stream_id,
            sequence=cursor.sequence,
            checkpoint=cursor.checkpoint,
            committed_at=PlatformEvent.now(),
        )

    @staticmethod
    def _same_checkpoint(
        current: SourceCheckpoint, candidate: SourceCheckpoint
    ) -> bool:
        return (
            current.stream_id == candidate.stream_id
            and current.sequence == candidate.sequence
            and current.checkpoint == candidate.checkpoint
            and current.provider_cursors == candidate.provider_cursors
        )

    @staticmethod
    def _validate_checkpoint_advance(
        current: Optional[SourceCheckpoint],
        checkpoint: SourceCheckpoint,
        mode: str,
    ) -> None:
        if current is not None and current.stream_id == checkpoint.stream_id:
            if checkpoint.sequence < current.sequence:
                raise ValueError("checkpoint sequence cannot move backwards")
            if (
                checkpoint.sequence == current.sequence
                and (
                    checkpoint.checkpoint != current.checkpoint
                    or checkpoint.provider_cursors != current.provider_cursors
                )
            ):
                raise ValueError("checkpoint content conflicts at the same sequence")
        elif current is not None and mode != "reconciliation":
            raise ValueError("checkpoint stream reset requires reconciliation")

    @staticmethod
    def _validate_reconciliation(
        snapshot: ReconciliationSnapshot,
        checkpoint: SourceCheckpoint,
    ) -> None:
        if (
            snapshot.tenant_id != checkpoint.tenant_id
            or snapshot.source_id != checkpoint.source_id
            or snapshot.stream_id != checkpoint.stream_id
            or snapshot.sequence != checkpoint.sequence
            or snapshot.checkpoint != checkpoint.checkpoint
            or snapshot.committed_at != checkpoint.committed_at
            or snapshot.resource_uids != tuple(sorted(set(snapshot.resource_uids)))
            or snapshot.tombstoned_uids
            != tuple(sorted(set(snapshot.tombstoned_uids)))
            or set(snapshot.resource_uids).intersection(snapshot.tombstoned_uids)
            or len(snapshot.resource_uids) > 10_000
            or len(snapshot.tombstoned_uids) > 10_000
            or snapshot.sequence < 0
            or snapshot.sequence > 9_007_199_254_740_991
            or not re.fullmatch(r"snap_[a-f0-9]{32}", snapshot.snapshot_id)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", snapshot.scope_digest)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", snapshot.result_digest)
            or any(
                not re.fullmatch(r"res_[a-f0-9]{32}", uid)
                for uid in snapshot.resource_uids + snapshot.tombstoned_uids
            )
        ):
            raise ValueError("reconciliation state is invalid")

    @staticmethod
    def _same_reconciliation(
        current: ReconciliationSnapshot,
        incoming: ReconciliationSnapshot,
    ) -> bool:
        return (
            current.tenant_id,
            current.source_id,
            current.stream_id,
            current.snapshot_id,
            current.scope_digest,
            current.sequence,
            current.checkpoint,
            current.result_digest,
            current.resource_uids,
            current.tombstoned_uids,
        ) == (
            incoming.tenant_id,
            incoming.source_id,
            incoming.stream_id,
            incoming.snapshot_id,
            incoming.scope_digest,
            incoming.sequence,
            incoming.checkpoint,
            incoming.result_digest,
            incoming.resource_uids,
            incoming.tombstoned_uids,
        )

    @staticmethod
    def _validate_event(resource: Resource, event: PlatformEvent) -> None:
        if event.tenant_id != resource.identity.tenant_id:
            raise ValueError("event tenant does not match resource tenant")
        if event.subject != resource.identity.uid:
            raise ValueError("event subject does not match resource identity")

    @staticmethod
    def _validate_checkpoint_request(resource: Resource, checkpoint_ready: bool) -> None:
        if checkpoint_ready and (
            resource.observation is None or resource.observation.checkpoint is None
        ):
            raise ValueError("checkpoint-ready write requires an observation checkpoint")

    @staticmethod
    def _validate_page(after_offset: int, limit: int) -> None:
        if after_offset < 0:
            raise ValueError("after_offset must not be negative")
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")

    @staticmethod
    def _validate_worker(worker_id: str) -> None:
        if not isinstance(worker_id, str) or not re.fullmatch(r"[a-zA-Z0-9._:-]{1,128}", worker_id):
            raise ValueError("worker_id is invalid")

    @staticmethod
    def _validate_error_code(error_code: str) -> None:
        if not isinstance(error_code, str) or not re.fullmatch(
            r"[a-z][a-z0-9_.-]{2,127}", error_code
        ):
            raise ValueError("error_code must be stable and non-sensitive")

    @staticmethod
    def _json_copy(value: Mapping[str, object]) -> Mapping[str, object]:
        return json.loads(
            json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )

    @staticmethod
    def _ai_usage_order(value: Mapping[str, object]) -> tuple[str, str]:
        metadata = value.get("metadata")
        spec = value.get("spec")
        if not isinstance(metadata, Mapping) or not isinstance(spec, Mapping):
            raise PersistenceError("storage.state.invalid")
        invocation = spec.get("invocation")
        if not isinstance(invocation, Mapping):
            raise PersistenceError("storage.state.invalid")
        started_at = invocation.get("startedAt")
        usage_record_id = metadata.get("id")
        if not isinstance(started_at, str) or not isinstance(usage_record_id, str):
            raise PersistenceError("storage.state.invalid")
        return started_at, usage_record_id


class InMemoryEventPublisher:
    """Append-only downstream publisher useful for dispatcher tests."""

    def __init__(self) -> None:
        self.events: list[PlatformEvent] = []
        self._lock = RLock()

    def publish(self, event: PlatformEvent) -> None:
        with self._lock:
            self.events.append(event)


class AllowTenantPolicy:
    """Development policy that allows non-anonymous actors in their own tenant."""

    def decide(
        self,
        actor: ActorContext,
        action: str,
        resource: Mapping[str, object],
    ) -> PolicyDecision:
        if not actor.actor_id or actor.actor_id == "anonymous":
            return PolicyDecision(False, "actor.anonymous")
        if resource.get("tenantId") != actor.tenant_id:
            return PolicyDecision(False, "tenant.scope_mismatch")
        if action not in (
            "action:approve",
            "action:execute",
            "action:propose",
            "action:read",
            "ai-economics:qualify",
            "ai-economics:read",
            "evidence:collect",
            "evidence-retention:expire",
            "evidence-retention:read",
            "event-delivery-health:read",
            "event-delivery-slo:read",
            "ingestion-telemetry:read",
            "investigation-completion-slo:read",
            "plugin:open-session",
            "plugin:get-invocation",
            "plugin:mediate-read",
            "plugin:propose-action",
            "plugin:cancel-invocation",
            "plugin:reconcile-invocation",
            "resource-projection:rebuild",
            "resource:ingest",
            "resource:read",
            "telemetry-export-health:read",
            "telemetry-export-slo:read",
            "telemetry-export-burn-rate:read",
            "collector-queue-loss:read",
        ):
            return PolicyDecision(False, "action.unsupported")
        return PolicyDecision(True, "development.allow")


# Transitional alias for callers that used the foundation repository name.
InMemoryResourceRepository = InMemoryResourceStore
