# ADR 0039: Dispatch investigations through tenant-scoped PostgreSQL leases

**Status:** Accepted  
**Date:** 2026-08-17

## Context

The synchronous API proves investigation behavior but ties work to an HTTP connection. The existing execution lease detects crashes only after an investigation has begun; it does not durably queue requests, coordinate multiple workers, or report retry delivery state. A generic cross-tenant poll would violate the platform's explicit tenancy boundary, and a renewable execution deadline could silently exceed customer budgets.

## Decision

Add an additive `InvestigationJobStatus` contract and asynchronous API. PostgreSQL stores the exact validated request, authenticated actor roles, canonical digest, bounded public status, next availability, and private lease ownership. Workers receive an explicit configured tenant list and every claim query contains one tenant predicate. `FOR UPDATE SKIP LOCKED` serializes competing claims; a random private token binds heartbeat, release, and terminal writes to the owner.

Dispatch leases are renewable delivery coordination. The investigation's `maxWallTimeSeconds` and existing execution lease remain independent and non-renewable. Reclaimed jobs call the idempotent investigation use case: a terminal report is reused, a live execution retries later, and an expired execution closes conservatively without tool replay. Cancellation is checked before execution and cannot be overwritten by a stale heartbeat or non-cancelled terminal result.

The synchronous endpoint remains supported. Queueing is therefore additive within `v1alpha1`, and SDKs expose separate submit/job methods.

## Consequences

- API pods no longer need to hold customer connections for background investigations.
- Horizontal workers can safely compete within an explicit tenant scope.
- Queue lag, attempts, heartbeat, completion, and stable failure state are inspectable without exposing worker or provider secrets.
- PostgreSQL is the initial workflow substrate; a future engine must preserve the contracts, exact tenant predicates, immutable input digest, separate deadlines, and no-replay recovery semantics.
- Tenant enrollment in worker configuration and production concurrency/SLO values remain deployment decisions.
