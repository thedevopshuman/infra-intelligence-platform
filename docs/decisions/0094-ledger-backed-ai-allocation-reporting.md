# ADR 0094: Ledger-backed bounded AI allocation reporting

**Status:** Accepted

**Date:** 2026-09-05

## Context

The AI economics foundation can normalize usage, resolve protected ownership,
and calculate cost. Customers need application and team views, but querying or
aggregating arbitrary telemetry attributes would reintroduce untrusted
ownership, uncontrolled cardinality, and backend-specific accounting.

## Decision

1. Build allocation reports from the immutable tenant-scoped usage,
   attribution, and cost ledgers; never from Prometheus or Grafana.
2. Bind every report to operator-configured policy, catalog, and engine
   generations. API callers may select only a bounded UTC interval and one of
   the closed `application` or `team` dimensions.
3. Reject intervals over 31 days and source sets over 10,000 records instead of
   returning partial totals.
4. Keep allocation and pricing coverage explicit. Sum money only from exact
   priced records and label it calculated estimate, not invoice cost.
5. Group historical display names from immutable attribution records while
   using only stable application/team IDs in OTLP metric attributes.
6. Authorize the HTTP read with `ai-economics:read`; tenant identity always
   comes from authenticated context.
7. Replace exported last-value snapshots independently per tenant so one
   tenant's empty or changed policy cannot zero another tenant's series.

## Consequences

- Reports remain vendor-neutral, reproducible, and independent of the selected
  telemetry backend.
- Pending, unallocated, and unpriced data is visible instead of silently
  disappearing or becoming zero.
- Renames do not rewrite history, and metric cardinality is bounded by reviewed
  policy dimensions rather than workload input.
- Large exports require a future asynchronous export contract rather than
  widening this synchronous query.
