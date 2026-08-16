# ADR 0018: Assess stored metric evidence with declared threshold rules

**Status:** Accepted

**Date:** 2026-08-16

## Context

ADR 0017 allows an investigation to collect a bounded metric result but deliberately prevents the presence of data from becoming automatic hypothesis support. The next useful step needs numeric interpretation without allowing an agent, backend, or adapter to invent a rule after seeing the values. Units, incomplete output, tenant scope, and contradictory evidence must remain explicit.

## Decision

- Add an optional `interpretation` to a telemetry selection. It is legal only when the selection names one or more `rootCauseClasses`.
- The caller declares a closed rule before execution: `minimum`, `maximum`, or `mean`; an exact result unit; `lt`, `lte`, `gt`, or `gte`; a finite threshold; and distinct matched/unmatched dispositions of `supports`, `contradicts`, or `neutral`.
- Evaluate only the normalized `TelemetryEvidenceResult` bytes read back through the actor- and tenant-scoped `EvidenceStore` after immutable Evidence commit. Never assess a raw backend response.
- Use numeric points only for `complete` results, requiring the selected logical metric, declared unit, and finite values. Flatten all returned series for the selected metric before computing the declared statistic.
- Record `no-data` and `incomplete` assessments without an observed value. Corrupt, unavailable, mismatched, non-finite, or unreadable artifacts fail closed to a stable telemetry evidence gap.
- Add structured `telemetryAssessments` to the report. Each assessment pins the selection, Evidence ID, root-cause class, metric, unit, statistic, operator, threshold, observed value when complete, and resulting disposition.
- Cite supporting and contradicting metric Evidence IDs on the matched hypothesis. Neutral, no-data, and incomplete results remain visible but are not hypothesis citations.
- Do not let a threshold result change the resource-derived root-cause class, confidence, rank, or terminal outcome in this deterministic slice. Those changes require separately evaluated multi-signal reasoning semantics.

## Consequences

- Metric values can now participate in an investigation without vendor query text, post-hoc rule synthesis, or hidden unit assumptions.
- The exact request and rule remain pinned by `requestDigest`; the report exposes the applied calculation and stored Evidence citation for review and replay.
- A contradiction is first-class and cannot be silently discarded, but the current narrow classifier still owns the hypothesis class and confidence.
- The rule evaluates a flat bounded point set. Window baselines, seasonality, missing-series policy, unit conversion, histogram semantics, and cross-signal inference remain out of scope.

## Revisit triggers

Revisit when evaluation scenarios justify confidence updates, competing hypothesis ranks, baseline comparisons, per-series policies, histogram/exemplar interpretation, or model-assisted selection from a policy-owned rule catalog.
