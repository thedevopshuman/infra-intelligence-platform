# ADR 0093: Protected effective-time AI attribution

**Status:** Accepted

**Date:** 2026-09-05

## Context

Normalized GenAI telemetry already carries standard service identity. That is
useful for correlation but is not sufficient authority for charging spend to a
business application or team. A workload can be misconfigured or malicious,
and organization ownership changes over time. Mutating historical usage after
an ownership change would also make reports irreproducible.

## Decision

1. Treat service, namespace, environment, and resource references on
   `AiUsageRecord` as observed identity, never trusted organizational ownership.
2. Load one protected, versioned attribution-policy snapshot per enrolled
   tenant into the workflow worker. Test fixtures require explicit opt-in.
3. Resolve rules in a deterministic priority order using the invocation start
   time. Store unmatched usage as explicitly unallocated.
4. Persist a separate immutable `AiUsageAttributionRecord` bound to the exact
   usage record, policy ID/version/source digest, and engine version.
5. Recompute the result from its immutable sources inside the persistence
   boundary before atomically writing the record, CloudEvent, and outbox row.
6. Give only the non-interactive attribution worker the exact
   `ai-attribution:resolve` role. The API and telemetry receivers receive no
   policy material or write authority.

## Consequences

- A workload cannot self-assign a billable team through OpenTelemetry labels.
- Historical results remain reproducible across reorganizations; a revised
  policy is a new generation rather than an in-place rewrite.
- Unallocated usage is measurable instead of being silently assigned.
- Policy deployment remains an operator workflow in V0. A future organization
  catalog adapter can replace the configuration source without changing the
  immutable result contract.
- Allocation queries, budgets, exports, and the Grafana team/application views
  remain subsequent units built on this fact.
