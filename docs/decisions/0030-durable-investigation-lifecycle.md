# 0030 — Persist investigation lifecycle before tool execution

**Status:** Accepted

## Context

The initial deterministic investigator wrote its request and report together after all evidence work completed. A process crash could therefore leave committed Evidence without a durable investigation attempt, and a duplicate request could not distinguish active work from a lost worker. The report contract already allowed cancelled and failed outcomes but there was no executable cancellation boundary.

## Decision

Persist the exact validated immutable request, request digest, running status, and a lease bounded by `maxWallTimeSeconds` before the first tool call. Keep the terminal report immutable and separate from the mutable lifecycle status. PostgreSQL stores the transition atomically in one tenant-keyed row; the local adapter mirrors the same first-writer and idempotency semantics.

Cancellation is a separate authenticated versioned request. The first valid request atomically changes `running` to `cancellation-requested`; later requests cannot rewrite it. Workers inspect durable cancellation before each new evidence call and before terminal commit. They retain already committed Evidence but publish no hypotheses or recommendations from a cancelled partial run.

If the same immutable request is submitted while its lease is live, return `investigation.in_progress`. If the lease expired without a report, close the attempt as `failed/runtime-error` (or `cancelled` when cancellation was already requested) without reissuing any tool call. A fresh attempt requires a new investigation ID.

## Consequences

- Accepted work and operator cancellation survive process restarts in the PostgreSQL profile.
- Duplicate delivery cannot create concurrent tool execution under one investigation ID.
- Crash recovery is conservative and auditable; it does not guess which calls completed or silently resume them.
- The synchronous HTTP endpoint remains compatible while status and cancellation can be used concurrently through the threaded server.
- A future queue/worker engine may adopt heartbeats and background stale-lease sweeping behind the same contracts; this reference unit intentionally does not select that engine.
