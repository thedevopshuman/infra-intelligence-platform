# ADR 0019: Compare ordered telemetry windows inside one committed artifact

**Status:** Accepted

**Date:** 2026-08-16

## Context

ADR 0018 assesses a metric against an absolute threshold. Many incident questions instead ask whether a metric changed relative to an earlier period. A portable implementation cannot hide a second backend query, rely on provider-specific offset syntax, or compare values that were not preserved as evidence. Window authority, missing data, units, and division by zero must be explicit.

## Decision

- Add an optional `baselineComparison` to a root-cause-scoped telemetry selection. A selection may declare either `interpretation` or `baselineComparison`, never both.
- Keep one provider-neutral query over the investigation time range and one immutable `TelemetryEvidenceResult`. The comparison does not consume another tool call or Evidence item.
- Require ordered, non-overlapping subranges satisfying `scope.start <= baseline.start < baseline.end < evaluation.start < evaluation.end <= scope.end`.
- Apply the same declared `minimum`, `maximum`, or `mean` statistic independently to finite points in each window. Both windows use the exact declared result unit; no unit conversion occurs.
- Calculate either `evaluation - baseline` or `evaluation / baseline`. A difference retains the metric unit; a ratio has comparison unit `1`.
- Treat a missing window, partial result, or zero ratio denominator as `incomplete`. Treat an explicitly empty result as `no-data`. Corrupt, mismatched, non-finite, or unreadable artifacts fail closed to a stable evidence gap.
- Record both windows, both observed values, the calculation, comparison value and unit, declared operator/threshold, and disposition in a `baseline-comparison` telemetry assessment.
- Cite supporting and contradicting results on the matching hypothesis, while leaving class, confidence, rank, and terminal outcome unchanged in the deterministic runtime.

## Consequences

- The comparison remains replayable from the exact stored artifact and portable across telemetry backends.
- Baseline assessment adds no hidden network authority or budget consumption beyond the original bounded query.
- A gap between the windows is permitted, but overlap and reversed periods fail request validation before collection.
- Sparse data is conservative: the runtime does not interpolate, carry values across windows, substitute a denominator, or silently fetch more history.
- Seasonal, rolling, calendar-aligned, multi-query, cross-metric, histogram, and confidence-update semantics remain out of scope.

## Revisit triggers

Revisit when evaluation scenarios justify policy-owned rolling/seasonal baselines, minimum sample counts, per-series comparisons, cross-metric reasoning, histogram semantics, or an explicitly budgeted multi-query plan.
