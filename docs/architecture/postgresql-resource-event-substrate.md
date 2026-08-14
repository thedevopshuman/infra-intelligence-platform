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
| `iip.source_checkpoints` | Last explicitly committed collector cursor | `(tenant_id, source_id)` |

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

The HTTP surface never sets the safe-checkpoint signal. A collector workflow may set it only after all resource mutations represented by that opaque cursor are included. Reconciliation completion markers remain a later workflow unit and are never inferred from the last resource received.

## Outbox delivery

Workers claim messages within one explicit tenant scope. Claims increment the attempt count and create a bounded lease. Parallel workers skip active leases. Successful delivery acknowledges the row using tenant, worker identity, and message ID. A failure releases it with a stable error code and bounded retry delay; provider exception text is never persisted.

Delivery is at least once. A crash after external publish but before acknowledgement can redeliver, so downstream transports and consumers use the CloudEvents identity `(tenantid, source, id)` for idempotency.

## Resource queries

The application query service authorizes `resource:read` before calling tenant-scoped repository ports. Neighborhood queries page the current `resource_relationships` index by deterministic edge ID, while timeline queries page immutable `resource_observations` by exclusive offset. The index records which latest resource projection asserted each canonical edge; unresolved provider references remain edge values and never create synthetic resource rows.

The relationship index is derived state. Every accepted projection replacement deletes and recreates only the rows asserted by that resource in the same transaction. Stale and conflicting observations remain visible in the timeline but cannot alter current edges. See the [resource query contract](../specifications/resource-query-contract.md) for pagination and external semantics.

## Tenancy and operations

- Every repository, graph, history, event, outbox, and checkpoint operation requires tenant scope.
- Composite keys and SQL predicates include the tenant even when another identifier appears globally unique.
- Cross-tenant administration is not implemented by omitting a predicate; it requires a separate future use case and policy.
- Projection maintenance requires one explicit tenant, a bounded resource count, the `platform-admin` role, and policy authorization.
- Psycopg and SQL exceptions are translated at the adapter boundary; public HTTP responses expose only the stable `storage.unavailable` code.
- Migrations are packaged with the adapter and serialized by a database advisory lock.
- `IIP_DATABASE_URL` selects the PostgreSQL profile. `IIP_DATABASE_AUTO_MIGRATE` exists for local Compose only and defaults to false in Helm.
- Production credentials come from an existing Kubernetes Secret or an external secret provider, never chart values committed to this repository.

## Verification profiles

`make verify` runs all dependency, contract, unit, SDK, and Helm gates; PostgreSQL tests skip when no database URL is present. `make test-postgres` uses Docker Desktop to start an ephemeral PostgreSQL 18.4 container on localhost, runs the integration suite, and removes the container and volume. CI supplies the same database major version through a service container and therefore runs the integration tests as part of `make verify`.
