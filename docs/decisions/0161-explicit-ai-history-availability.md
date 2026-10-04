# ADR 0161: Explicit AI history availability before payload retirement

**Status:** Accepted  
**Date:** 2026-10-04

## Context

Allocation joins immutable usage with selected attribution and cost generations.
Exact-invocation qualification distinguishes absent usage from pending and
complete downstream processing. Removing source payloads without a separate
historical-availability fact would turn retired usage into smaller totals,
false `not-observed` results, or apparently pending work. Deterministic savings
rules would likewise evaluate incomplete historical cohorts.

The successful v1alpha1 report shapes are closed. Adding a retired status or
optional partial totals to those shapes would change their meaning and break
existing consumers. Delivered-outbox retention in ADR 0160 does not authorize
AI-ledger retirement or solve these read semantics.

## Decision

Add the separate read-only `AiHistoryAvailabilityReport` and authenticated
`GET /v1/ai/economics/history-availability` operation. Reuse
`ai-economics:read`, derive the exact tenant from identity, and accept only one
canonical UTC `start` and `end` for a positive half-open interval of at most
31 days. Expose counts of retained and retired locally committed usage identities,
not money, dimensions, record/correlation identifiers, or retention policy.
`available` means exactly zero recorded retirements, not complete traffic
capture or cost processing.

Introduce a compact, tenant-bound whole-invocation marker relation as a
storage prerequisite. Each marker represents one normalized usage identity,
not necessarily a distinct physical provider call; separate telemetry channels
can legitimately retain records with the same trace/span correlation. Use
invocation start time for interval membership and a
tenant-bound canonical correlation digest for exact lookup. A marker wins
over a still-present payload. There is no single earliest-retained timestamp:
future reference pins may retain older invocations while newer eligible
history is retired. Read counts exclude marked usage to avoid double counting.
This is not a complete tombstone/idempotency or data-erasure design, and no
production writer or physical deletion is added in this unit.

Preserve existing successful v1alpha1 allocation and exact-invocation shapes.
Return `410 ai.history.retired` whenever their required history has a recorded
retirement, rather than returning partial data or interpreting retirement as
absence/pending. Allocation guards its entire requested tenant interval before
source limits; savings-cohort reads guard the whole interval conservatively
before narrower rule filters. Exact observation checks its tenant-bound
correlation first. Each guard and data read share one consistent storage
snapshot. A separately retrieved availability report is informational, never
an authorization token or freshness guarantee for another read.

The new GET uses only the closed `ai-history-availability` operation label in
the existing failure-isolated query telemetry. Time bounds, report counts,
tenant identity, correlation, and provider details never become metric labels.

## Consequences

- Clients can distinguish missing traffic from recorded retired history
  without accepting new variants in existing successful envelopes.
- Empty availability results remain honest: they establish no retirement
  among locally recorded history, not observation of every provider request.
- Markers prevent partial allocation and savings reads even before payload
  deletion exists; they do not reduce disk usage by themselves.
- Existing finding pages remain immutable reference-bearing results, not proof
  that every referenced payload is available indefinitely.
- Future repricing or reattribution requires retained source usage; neither
  availability counts nor correlation markers can recreate it.
- A future lifecycle must define explicit operator-owned policy, protected
  reference/pin semantics, and an atomic boundary joining eligibility,
  concurrent finding creation, marker insertion, payload deletion, and audit.
  It must preserve exact duplicate/conflict identity, address foreign keys and
  all derived generations, and prove event replay plus compatible backup and
  restore. Those requirements must be met before enabling cleanup.
- Event and audit history, telemetry backends, external copies, and backup
  expiration remain separately owned; this decision makes no erasure claim.

## Revisit when

Revisit when implementing AI payload retirement, source repricing after
retirement, finding-reference pin expiry, historical aggregate snapshots,
versioned partial-result contracts, or customer erasure requirements. Any
future successful shape with partial history requires an explicit versioned
compatibility decision rather than silently weakening v1alpha1 totals.
