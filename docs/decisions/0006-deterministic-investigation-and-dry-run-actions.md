# ADR 0006: Deterministic investigation baseline and dry-run action authority

**Status:** Accepted
**Date:** 2026-08-14

## Context

The roadmap requires evidence-backed investigations, measurable evaluation, and governed actions. Selecting a production model provider, workflow engine, policy engine, or credential broker before real workload data would couple the platform to unmeasured assumptions. At the same time, contract-only documents do not prove that authority, budgets, citations, separation of duties, or idempotency compose correctly.

## Decision

The executable reference runtime uses a deterministic, no-model investigator. It collects canonical resource-state evidence through the normal evidence boundary, applies caller budgets, classifies a deliberately small Kubernetes failure taxonomy, emits structured hypotheses with `rootCauseClass`, and records zero model tokens and cost. This is the stable evaluation baseline, not the final agent strategy.

Governed actions are separate immutable proposal, approval, and result documents. Proposal, approval, and execution each receive a current policy decision. Approval requires an authenticated `approver` role and a different actor from the proposer. Execution requires an `executor` role and returns an existing result for duplicate delivery. The only reference executor validates `kubernetes.restart-workload` in dry-run mode and performs no mutation.

PostgreSQL is extended as the initial durable evidence, investigation, action, plugin-session, and audit store. This does not accept PostgreSQL as the final workflow engine or policy system. The plugin handshake persists token references and digests only; raw capability material is never stored in a public contract.

## Consequences

- Investigation and evaluation behavior is runnable without sending infrastructure data to a model provider.
- A future agent/model implementation must beat the deterministic scorecard and preserve the same evidence, budget, and terminal-report semantics.
- No API call in the reference runtime can mutate Kubernetes. Live mutation, verification, rollback, short-lived credential brokering, and production policy remain explicit Phase 4 exit work.
- The plugin session contract proves least-authority negotiation, but signing, digest enforcement, process isolation, and resource governance remain Phase 5 runner work.
