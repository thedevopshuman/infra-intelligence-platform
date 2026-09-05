# PostgreSQL resource and event substrate

**Status:** Phase 1 reference implementation
**Date:** 2026-08-14
**Decision:** [ADR 0004](../decisions/0004-postgresql-observation-store-and-outbox.md)

This page describes the executable Phase 1 persistence slice. Public contracts remain storage-neutral; PostgreSQL implements application-owned ports under `src/iip/adapters/postgres/`.

## Authoritative records

| Table | Authority | Important key |
| --- | --- | --- |
| `iip.resource_projections` | Rebuildable latest-resource serving projection | `(tenant_id, resource_uid)` |
| `iip.resource_relationships` | Rebuildable current-edge query index | `(tenant_id, edge_id)` with source/target indexes |
| `iip.resource_observations` | Immutable accepted/rejected history and projection recovery source | Monotonic observation offset plus tenant/resource/hash uniqueness |
| `iip.event_log` | Immutable accepted platform events and replay offsets | `(tenant_id, event_source, event_id)` |
| `iip.event_outbox` | At-least-once delivery state for event-log rows | Unique event offset and tenant-scoped lease |
| `iip.source_checkpoints` | Last explicitly committed aggregate checkpoint and opaque provider cursor map | `(tenant_id, source_id)` |
| `iip.ai_usage_records` | Immutable metadata-only GenAI invocation usage | Tenant-scoped usage ID and deduplication key |
| `iip.ai_attribution_policies` | Immutable protected application/team mapping snapshots | Tenant-scoped policy ID and unique version |
| `iip.ai_usage_attributions` | Reproducible effective-time allocation facts | Tenant, usage, policy, and engine identity |
| `iip.ai_price_catalogs` | Immutable protected AI price snapshots | Tenant-scoped catalog ID and unique version |
| `iip.ai_cost_records` | Reproducible per-usage calculated-cost facts | Tenant, usage, catalog, and engine identity |
| `iip.ai_savings_findings` | Immutable deterministic evidence-backed savings facts | Tenant-scoped finding ID |

Canonical documents are stored as `jsonb`, but frequently enforced identity, tenancy, ordering, lifecycle, and delivery fields are relational columns. The relational columns are not a second public contract; migrations and adapter tests keep them aligned with the canonical document.

## Projection verification and rebuild

[ADR 0008](../decisions/0008-observation-history-projection-rebuild.md) makes immutable accepted observations the recovery authority for the current resource and relationship serving indexes. The maintenance surface defaults to a read-only dry run. It derives the latest accepted document per resource, rebuilds the expected relationship set in memory, and compares a canonical digest with the currently stored projection state.

An apply run requires an explicitly constructed `platform-admin` actor and policy approval. It holds an exclusive tenant maintenance advisory lock, replaces only that tenant's projection and relationship rows in one transaction, and verifies the resulting digest before commit. Normal accepted-observation transactions take the matching shared lock, so ingestion cannot interleave with projection replacement. Observation history, events, outbox state, checkpoints, and reconciliation membership are not changed.

```bash
export IIP_DATABASE_URL='postgresql://...'
PYTHONPATH=src python3 -m iip.surfaces.maintenance \
  rebuild-projections --tenant local --actor local-operator

# Apply only after reviewing driftDetected and the expected digest.
PYTHONPATH=src python3 -m iip.surfaces.maintenance \
  rebuild-projections --tenant local --actor local-operator --apply
```

## Accepted-observation transaction

1. Validate the public resource and verify event tenant/subject consistency.
2. Acquire a transaction-scoped advisory lock derived from tenant and resource UID.
3. Lock and classify the current projection using domain ordering rules.
4. On accepted input, upsert the projection and replace the relationship rows asserted by that projection.
5. Append the immutable observation.
6. Append the CloudEvents document and create its outbox row.
7. If the trusted command marks a safe checkpoint boundary, validate and advance the source checkpoint.
8. Commit all effects together.

A duplicate returns the projection without another history or event row. A stale or conflicting payload is retained with its disposition and commits no projection, event, outbox, or checkpoint change. Any accepted-path failure rolls back every effect.

AI usage intake uses the same atomic event boundary without entering the
resource projection. An accepted normalized span commits the immutable
`ai_usage_records` document, its CloudEvent, and the outbox row together. The
tenant, AI channel actor, stable span-derived identity, and document hash are
checked again at the storage port. An exact retry returns the first document;
reuse of the identity with different usage fails closed.

AI attribution is an independent worker transaction after intake. The worker
registers one protected immutable mapping snapshot per enrolled tenant, reads a
bounded invocation-time-ordered page, and resolves observed service/resource
identity to application/team ownership or an explicit unallocated result.
Storage reloads the exact policy and usage documents and re-resolves the
decision before atomically inserting `ai_usage_attributions`, its
value-minimized CloudEvent, and outbox row. The source usage row is never
updated, and exact retries emit no second event.

AI cost calculation remains a separate transaction after intake. The
tenant-explicit worker idempotently registers a protected catalog, selects a
bounded ordered page of usage not yet evaluated by that catalog and engine,
and commits each immutable `ai_cost_records` document with its value-minimized
CloudEvent and outbox row. Catalog ID and catalog version have independent
transaction advisory locks, while cost writes lock their complete semantic
identity. Storage rechecks the cited usage, catalog version/source hash,
fixture-warning state, event identity, and event routing fields before commit.
No partial record/event/outbox state survives a failure.

AI savings evaluation is another separate worker transaction. The adapter
queries only one exact tenant, commercial scope, catalog/engine lineage, and
fixed time window. Before insert it reloads the complete adjacent-window cohort
under a tenant/finding advisory lock and recalculates the rule. Context-growth
findings verify every cited immutable usage and cost record; retry-amplification
findings verify every cited usage record and prohibit cost references because
billable hidden attempts are unproven. The finding, value-minimized CloudEvent,
and outbox row commit atomically. Deterministic identity makes exact retries
idempotent; the same identity with different source-bound content is a
conflict. Missing, partial, cross-tenant, or out-of-window facts cannot be
committed, and context-growth additionally rejects cached or unpriced source
facts.

The HTTP surface never sets the safe-checkpoint signal. A collector workflow may set it only after all resource mutations represented by that opaque cursor are included. Resource-collection resume state must exactly match the current tenant/source checkpoint, and provider cursor maps update in the same transaction as complete reconciliation membership. Reconciliation completion markers are never inferred from the last resource received.

## Outbox delivery

Investigation jobs use the same explicit-tenant and `SKIP LOCKED` discipline in a separate table. Public job status never contains private claim ownership. Renewable dispatch leases coordinate delivery only; the immutable request's execution lease and wall-time budget remain separate. See [ADR 0039](../decisions/0039-tenant-scoped-investigation-dispatch.md).

Action timers query only exact-tenant `executing` rows whose stored lease has expired. The conditional state update and one audit insert share a PostgreSQL transaction, so competing workflow workers produce one fail-closed transition and one audit record. No new execution claim or provider call is possible from this repository operation. See [ADR 0040](../decisions/0040-action-timers-and-fail-closed-reconciliation.md).

Workers claim messages within one explicit tenant scope. Claims increment the attempt count and create a bounded lease. Parallel workers skip active leases. Successful delivery acknowledges the row using tenant, worker identity, and message ID. A failure releases it with a stable error code and bounded retry delay; provider exception text is never persisted.

Delivery is at least once. A crash after external publish but before acknowledgement can redeliver, so downstream transports and consumers use the CloudEvents identity `(tenantid, source, id)` for idempotency.

[ADR 0044](../decisions/0044-tenant-scoped-outbox-delivery.md) makes this dispatcher executable in the tenant-explicit workflow worker. The local structured-log sink is development-only; the Helm profile offers a TLS-only, authenticated, redirect-refusing webhook whose tenant authority exactly matches worker enrollment. Publisher credentials remain mounted only in the worker, and its dedicated NetworkPolicy has no ingress. See the [event-delivery operations guide](../operations/event-delivery.md).

## Evidence artifact retention

Evidence artifact retention uses the same exact-tenant discipline without deleting immutable Evidence metadata. Evidence commits and cleanup passes share a namespaced tenant-keyed transaction advisory lock, so the worker selects current committed, present, expired, non-`legal-hold` artifact bodies and bounds each batch without blocking another tenant or colliding with investigation admission locks. Setting the byte column to unavailable, recording its deletion time, and appending one aggregate audit row commit atomically. [ADR 0062](../decisions/0062-audited-evidence-artifact-retention.md) and the [retention runbook](../operations/evidence-retention.md) define the lifecycle and disabled-by-default enablement gate.

## Resource queries

The application query service authorizes `resource:read` before calling tenant-scoped repository ports. Neighborhood queries page the current `resource_relationships` index by deterministic edge ID, while timeline queries page immutable `resource_observations` by exclusive offset. The index records which latest resource projection asserted each canonical edge; unresolved provider references remain edge values and never create synthetic resource rows.

The relationship index is derived state. Every accepted projection replacement deletes and recreates only the rows asserted by that resource in the same transaction. Stale and conflicting observations remain visible in the timeline but cannot alter current edges. See the [resource query contract](../specifications/resource-query-contract.md) for pagination and external semantics.

## Ingestion freshness query

The PostgreSQL telemetry adapter reads one tenant/source checkpoint and derives the latest accepted source observation, accepted-observation count, and unpublished source-event count/oldest creation time from existing records. The application layer converts those facts into ages and stable objective violations. No provider cursor, checkpoint value, resource body, or raw error enters the report.

Checkpoint age remains measurable after a complete empty reconciliation. A source is not queryable until it has committed its first checkpoint; future integration-registration work may add an explicit pre-checkpoint state. See the [telemetry architecture](ingestion-freshness-telemetry.md) and [ADR 0011](../decisions/0011-ingestion-freshness-semantics.md).

## Tenancy and operations

- Every repository, graph, history, event, outbox, and checkpoint operation requires tenant scope.
- Composite keys and SQL predicates include the tenant even when another identifier appears globally unique.
- Cross-tenant administration is not implemented by omitting a predicate; it requires a separate future use case and policy.
- Projection maintenance requires one explicit tenant, a bounded resource count, the `platform-admin` role, and policy authorization.
- Psycopg and SQL exceptions are translated at the adapter boundary; public HTTP responses expose only the stable `storage.unavailable` code.
- Migrations are packaged with the adapter and serialized by a database advisory lock.
- `IIP_DATABASE_URL` selects the PostgreSQL profile. `IIP_DATABASE_AUTO_MIGRATE` exists for local Compose only and defaults to false in Helm. The opt-in isolated migration hook documented in [ADR 0045](../decisions/0045-controlled-helm-schema-migrations.md) owns chart-driven schema mutation.
- Production credentials come from an existing Kubernetes Secret or an external secret provider, never chart values committed to this repository.

## Backup and restore verification

[ADR 0010](../decisions/0010-postgresql-backup-restore-verification.md) adds a complete-schema logical backup experiment without declaring a production backup provider. The Docker Desktop target seeds authoritative and derived records through the platform boundaries, takes a quiesced custom-format backup, restores into a fresh database, and compares every `iip` table plus identity-sequence state by canonical digest. Recovery is considered ready only after the tenant projection verifier also proves that restored serving state agrees with immutable accepted observations.

The [operator procedure and recorded measurement](../operations/postgresql-backup-restore.md) distinguish the local experiment's recovery-point age and verified recovery-readiness time from production RPO/RTO commitments. Scheduled encrypted backups, off-host retention, WAL-based point-in-time recovery, and restore authorization remain tied to the future production hosting decision.

## Verification profiles

`make verify` runs all dependency, contract, unit, SDK, and Helm gates; PostgreSQL tests skip when no database URL is present. `make test-postgres` uses Docker Desktop to start an ephemeral PostgreSQL 18.4 container on localhost, runs the integration suite, and removes the container and volume. `make test-backup-restore` uses a separately named disposable Compose project and emits measured integrity, recovery-point-age, and recovery-readiness evidence. CI supplies the same database major version through a service container and therefore runs the integration tests as part of `make verify`.

Every PostgreSQL-backed HTTP process uses a fresh bounded `SELECT` against `iip.schema_migrations` for `/readyz` and requires the latest migration packaged with its image. `/healthz` remains process-only, and readiness never owns schema mutation. `IIP_READINESS_DATABASE_TIMEOUT_SECONDS` is restricted to one through ten seconds; the Helm probe timeout is configured separately and must exceed it.
