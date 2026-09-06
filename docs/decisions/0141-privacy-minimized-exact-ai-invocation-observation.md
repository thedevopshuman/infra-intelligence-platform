# ADR 0141: Privacy-minimized exact AI invocation observation

**Status:** Accepted  
**Date:** 2026-09-07

## Context

The customer AI FinOps prerequisite report proves that the release, deployment,
customer Collector, live Bedrock profile, and pricing inputs are independently
qualified and consistently bound. It cannot prove that the span from one live
provider call became one usage record and then reached the selected attribution
and cost generations.

Trace and span identifiers are required to establish that fact, but they are
high-cardinality operational identifiers. Putting them in URL paths, query
strings, logs, metrics, dashboards, or transportable evidence would create an
unnecessary disclosure and retention surface. Adding them as telemetry labels
would also make the backend unsafe and vendor-specific.

## Decision

Add a privileged tenant-scoped observation use case and POST endpoint for one
exact trace/span pair. The identifiers exist only in the closed request body and
the tenant-scoped indexed ledger query. The response replaces them with a
SHA-256 digest over tenant, trace, and span and returns only source-bound stage
status, immutable record identities/digests, protected application/team IDs,
and calculated-estimate cost when present.

The endpoint requires the `platform-admin` role and the separate
`ai-economics:qualify` policy action. It always selects the active protected
attribution policy and price catalog configured by the deployment; a caller
cannot choose a source generation. `not-observed`, `processing`, and `complete`
describe authoritative ledger progress. Terminal `unallocated`, `unpriced`, or
`ambiguous` results are complete observations, not false success claims.

The pinned Bedrock qualifier may optionally export the exact generated span to
a selected OTLP/HTTP trace endpoint. When enabled, it writes the trace/span pair
to a new owner-only mode-`0600` correlation artifact. This file is ephemeral
protected input to a qualification workflow and is not a transportable report
or public SDK model. Endpoint credentials are read from an owner-only header
file; HTTPS is required except for explicitly enabled local testing.

Prometheus and Grafana continue to receive bounded protected-dimension
aggregates without trace, span, invocation, record, model, or catalog labels.
An end-to-end qualification may prove an aggregate delta after the exact ledger
observation, but it must not claim that the dashboard itself is an exact-trace
store.

## Consequences

- One live call can be correlated through collection, usage, attribution, and
  cost without an inference proxy or a new instrumentation SDK.
- The PostgreSQL usage ledger gains an index on tenant plus JSON trace/span
  fields. The immutable document remains the source of truth.
- Exact observation is intentionally privileged and unsuitable for broad
  interactive querying or general trace search.
- Raw correlation artifacts require protected storage and prompt deletion after
  the qualification report is finalized.
- Backend dashboard portability remains intact because no exact-invocation
  identity is added to metrics.

This decision does not establish invoice agreement, provider billing
reconciliation, production availability, or a complete customer end-to-end
qualification by itself. [ADR 0142](0142-customer-ai-finops-same-invocation-qualification.md)
composes this narrow serving operation into the external same-invocation gate.
