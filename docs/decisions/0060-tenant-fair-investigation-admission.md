# ADR 0060: Bound investigation concurrency per process and tenant

**Status:** Accepted  
**Date:** 2026-08-17

## Context

ADR 0039 gives every claim an exact tenant predicate and durable renewable lease, but the first worker loop executes one investigation synchronously. A slow provider call for one tenant can therefore delay polling every other enrolled tenant. Multiple worker replicas can also claim different jobs for the same busy tenant and collectively consume all execution capacity. Process-local scheduling alone cannot enforce a deployment-wide tenant limit.

## Decision

Run investigation claim attempts in a bounded process-local executor. Keep at most one task in flight per enrolled tenant in each process, rotate the first tenant considered between polls, and default to four process slots. Continue action reconciliation, event delivery, and ingestion sampling on the owning worker loop while investigations run.

Add a deployment-wide live-lease cap to the tenant-scoped repository claim. The default is one active investigation per tenant. PostgreSQL serializes only same-tenant admission with a transaction advisory lock, counts unexpired `running` and `cancellation-requested` leases, and refuses a new claim at the configured cap. Different tenants retain independent database claim concurrency. Expired leases do not consume the cap and remain eligible for the existing conservative recovery path. The in-memory reference implements the same semantics under its existing lock.

Expose separate bounded settings for process concurrency and maximum live investigations per tenant. Neither setting discovers tenants, changes request budgets, bypasses policy, extends execution leases, or grants a worker new authority. Worker tenant enrollment remains explicit and closed.

## Consequences

- one long investigation no longer blocks action timers, outbox delivery, freshness sampling, or another tenant when a process slot is available;
- a busy tenant cannot consume more than its configured live-lease share across worker replicas;
- same-tenant claim admission has a short serialization point, while evidence execution remains concurrent and unlocked;
- raising the tenant cap is an explicit capacity/isolation tradeoff and does not change per-investigation tool, time, or cost budgets;
- running work is not preempted, so large tenant sets still require measured capacity, worker sharding, and completion-SLO alerting.
