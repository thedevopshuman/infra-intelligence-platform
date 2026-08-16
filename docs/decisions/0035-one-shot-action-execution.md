# ADR 0035: One-shot governed action execution

**Status:** Accepted  
**Date:** 2026-08-17

## Context

An idempotency key on a proposal does not by itself prevent duplicate impact. If an executor changes a provider and the process exits before persisting `ActionResult`, a retry cannot know whether the provider accepted the first operation. Automatically retrying in that ambiguity is unsafe.

## Decision

The application atomically claims the single execution attempt before crossing the executor port. The claim records the exact proposal and approval digests, current policy input digest, executor identity, and a bounded lease. A terminal result and execution status commit atomically.

An active claim rejects duplicate execution. An expired non-terminal claim becomes `manual-reconciliation-required` and is never reclaimed automatically. Proposal parameters are closed and must match the canonical target identity. Proposal, approval, and execution policy inputs are content-digested so the authorization chain can be reconstructed without recording credentials.

## Consequences

- Process loss cannot cause the platform to replay an operation with unknown impact.
- Operational recovery may require a human reconciliation step; this is intentional fail-closed behavior.
- The durable store, not an in-process lock, owns the one-shot invariant.
- A future workflow engine must preserve these semantics or publish an explicit compatibility migration.
- Live Kubernetes execution may now be added behind this boundary, but still requires request-scoped credentials, server-side dry-run, verification, and rollback.
