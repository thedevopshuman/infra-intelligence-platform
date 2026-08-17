# ADR 0040: action timers and fail-closed reconciliation

**Status:** Accepted  
**Date:** 2026-08-17

## Context

Governed action execution is deliberately one-shot. If an executor disappears after the durable claim, the platform cannot infer whether provider impact occurred. Previously, the ambiguous attempt moved to `manual-reconciliation-required` only when another caller tried to execute the same proposal. That left abandoned attempts looking active until operator interaction.

Proposal expiry also needs to be visible without rewriting the immutable proposal or invalidating the digest already bound into an approval.

## Decision

The tenant-explicit workflow worker scans a bounded ordered set of expired `executing` leases. It atomically changes each still-expired execution to `manual-reconciliation-required` and appends exactly one tenant-scoped audit record. The timer never invokes an action executor, renews an execution lease, or creates a new attempt. A racing terminal commit wins only if it commits before the fail-closed transition.

The action workflow read model derives `expired` from the immutable proposal's `expiresAt` and the current clock when no execution exists. It may describe either a pending or approved proposal; the stored proposal and approval remain unchanged.

Workers have an explicit tenant list with no wildcard mode. The PostgreSQL query and transition both repeat the exact tenant predicate, and the in-memory adapter enforces the same boundary.

## Consequences

- Ambiguous provider impact becomes visible without waiting for a duplicate execution request.
- Status and audit cannot diverge during this timer transition.
- Expiry is accurate at read time without changing a proposal digest.
- `manual-reconciliation-required` still requires a human/provider comparison and a new proposal for any follow-up; the platform does not automate that decision.
