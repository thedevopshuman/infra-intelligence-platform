# ADR 0008: Rebuild serving projections from accepted observation history

**Status:** Accepted
**Date:** 2026-08-15

## Context

The PostgreSQL substrate keeps immutable resource observations alongside current resource and relationship projections. The first migration linked observations back to projections with a foreign key. That protected referential integrity during ingestion, but it also made observation retention depend on the derived state it must be able to recover. A full projection rebuild therefore could not delete and recreate a damaged or missing serving projection without deleting its recovery source.

The rebuild path must remain tenant-scoped, preserve the event log, outbox, checkpoints, and immutable observations, exclude rejected observations from current state, and prevent concurrent ingestion from interleaving with replacement of derived rows.

## Decision

Treat the latest accepted immutable observation for each `(tenant_id, resource_uid)` as the recovery authority for the current resource projection. Treat `resource_projections` and `resource_relationships` as atomically replaceable serving indexes.

Remove the observation-to-projection foreign key. Ingestion still writes an accepted projection before its immutable observation in the same transaction, but recovery no longer requires the projection row to exist.

Provide a privileged, tenant-scoped maintenance use case with these semantics:

- dry-run is the default and compares canonical digests of stored and expected serving state;
- apply requires the `platform-admin` role plus policy authorization;
- the operator supplies an explicit tenant and a bounded maximum resource count;
- an exclusive tenant maintenance advisory lock serializes rebuilds, while ingestion takes the corresponding shared lock;
- apply deletes and recreates only that tenant's resource and relationship projections in one transaction;
- the operation verifies the rebuilt digest before commit;
- immutable observations, events, outbox records, reconciliations, and checkpoints are never rewritten.

Rejected observations remain historical evidence only. A stale or conflicting observation can never become a current projection during recovery.

## Consequences

- Current graph state can be reconstructed after projection/index corruption without recollecting the cluster.
- Projection drift is measurable before mutation and the post-rebuild state is verifiable by digest.
- Observation history must be protected, backed up, and retained as recovery-critical data.
- The command is local operator tooling, not a cross-tenant HTTP administration shortcut.
- A future online rebuild may use shadow tables or streaming checkpoints if tenant size makes the bounded in-transaction rebuild too disruptive.

## Revisit triggers

Revisit the in-transaction strategy when measured rebuild duration exceeds maintenance objectives, observation volume exceeds the configured bound, or serving projections move to an external graph/search system.
