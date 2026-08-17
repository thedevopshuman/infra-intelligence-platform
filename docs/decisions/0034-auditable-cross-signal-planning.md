# ADR 0034: Plan evidence candidates across signals and budgets

**Status:** Accepted

**Date:** 2026-08-17

## Context

Investigation requests can declare candidates for several evidence types, but executing separate type-specific loops until a budget is exhausted makes skipped work implicit. Customers and evaluators need to see why a signal ran or did not run, while request fields must remain upper bounds rather than capability grants.

## Decision

- Build one deterministic `risk-aware-v1` plan after resource classification and before external evidence calls.
- Consider candidates in this least-risk sequence while preserving request order within each signal: Kubernetes Events, repository/runbook context, value-minimized resource changes, metrics, then logs.
- Apply root-cause applicability, request evidence/tool upper bounds, and remaining tool/evidence capacity. Record every candidate exactly once as scheduled or deferred.
- Use stable reasons: `eligible`, `root-cause-mismatch`, `request-upper-bound`, and `budget-exhausted`.
- Include the plan, classification, initial remaining capacity, counts, and contiguous steps in the terminal report. Actual usage and the tool ledger remain execution authority.
- Do not generate provider queries or integration identifiers absent from the accepted request. Protected catalog-driven candidate generation requires its own provenance and policy boundary.

## Consequences

- Budget pruning and cross-signal ordering are reproducible and customer-visible.
- A scheduled step can still fail, be cancelled, or miss a deadline; the plan is not an execution receipt.
- Lower-risk signals deterministically consume scarce capacity before logs.
- The additive report field remains compatible with `v1alpha1` clients that ignore unknown optional fields.

## Revisit triggers

Revisit when protected signal catalogs generate candidates automatically, when observed evidence changes a plan mid-run, or when measured design-partner outcomes justify a different risk/cost ordering.

**Revisited:** [ADR 0054](0054-protected-investigation-signal-catalog.md) adds protected catalog candidates by freezing them into the accepted request before this planner runs. The risk order and budget decisions here remain unchanged; plan steps now record candidate origin and catalog digest provenance.

**Revisited:** [ADR 0075](0075-bounded-adaptive-signal-replanning.md) permits one candidate initially deferred only for budget exhaustion to be promoted after a scheduled provider gap releases real capacity. It preserves the accepted candidates, risk order, scope, and authority while recording the bounded transition as `risk-aware-v2` provenance.
