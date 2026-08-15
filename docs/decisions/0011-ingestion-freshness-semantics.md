# ADR 0011: Measure ingestion freshness from committed checkpoints and durable delivery state

**Status:** Accepted

**Date:** 2026-08-15

## Context

The Phase 1 exit gate requires graph correctness and source lag to be measurable. Provider activity alone is not sufficient: a collector can run while a partial result fails to commit, a stale cursor can be rejected, or downstream event delivery can remain backed up. The platform already persists the required authority in source checkpoints, accepted observation history, and its transactional outbox.

This first measurement must preserve tenant isolation, avoid disclosing opaque cursor state, and remain useful for an empty but successful reconciliation. No representative production workload or deployment topology exists yet, so it cannot honestly define a production availability window or error budget.

## Decision

Add a tenant-scoped `IngestionFreshnessReport` and authenticated `GET /v1/telemetry/ingestion?sourceId=...` operation with these semantics:

- checkpoint age, measured from the last complete durable collection boundary, is the primary source-freshness signal;
- the latest accepted observation contributes observation age and provider-to-platform ingestion delay when one exists;
- unpublished source events contribute pending count and oldest-pending age;
- an empty complete reconciliation is measurable from its checkpoint without inventing an observation;
- clock skew beyond a trusted allowance creates a stable violation, while serialized durations remain nonnegative;
- trusted runtime configuration supplies objectives, and the response echoes the applied values;
- the query authorizes `ingestion-telemetry:read` and always filters raw state by the credential-derived tenant and requested source;
- the public response omits provider checkpoints/cursors, resource contents, credentials, and raw adapter errors.

The initial local defaults are 300 seconds for checkpoint and observation age, 60 seconds for ingestion delay and oldest pending event age, and 5 seconds for accepted clock skew. They are reference objectives, not production SLO commitments.

PostgreSQL derives the report inputs from existing records rather than introducing a telemetry table. The application service owns calculations and stable violation codes so adapters and future metric exporters cannot diverge in meaning.

## Consequences

- A collector that only produces incomplete or rejected work cannot appear fresh.
- Source lag and event backlog are directly queryable through the OpenAPI and both SDK boundaries.
- Tenant-scoped tests cover empty sources, breached objectives, skew, pending delivery, and cross-tenant non-disclosure.
- No migration or telemetry vendor is required for the point-in-time report.
- Historical aggregation must sample or export these signals later; this API alone does not calculate availability windows or percentiles.

## Revisit triggers

Revisit the default objectives and add production SLO/error-budget decisions when design-partner traffic and deployment topology are known. Add a pre-checkpoint "never completed" source state when integration registration becomes authoritative. Consider pre-aggregated storage only when measured query volume or retention requirements justify it.
