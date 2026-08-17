# ADR 0076: Compare deterministic periodic telemetry baselines

**Status:** Accepted

**Date:** 2026-08-17

## Context

Explicit and rolling two-window comparisons cannot answer whether a metric is unusual for the same operational period on prior hours, days, or weeks. A portable seasonal comparison must remain reproducible from committed evidence, must not synthesize provider queries, and must distinguish missing historical periods from a real baseline.

## Decision

- Add `seasonalBaselineComparison` as an additive `v1alpha1` telemetry-selection rule. A selection declares at most one threshold, explicit-baseline, rolling-baseline, or seasonal-baseline rule and requires at least one matching root-cause class.
- Anchor the evaluation window at the accepted investigation `scope.end`. Shift that complete window backward by `periodSeconds` for each lookback, recording two to twelve prior windows in nearest-to-oldest order.
- Bound fixed periods from one hour through one week, the evaluation duration from one minute through one day and strictly below the period, and the total request range to 90 days. Every derived window must fit the accepted scope.
- Query the provider-neutral metric selector once over the accepted scope and commit one immutable Evidence artifact. Derived windows do not add network calls, credentials, tool calls, or Evidence items.
- Apply the declared metric statistic independently to every prior window and to the evaluation window. Require data in every window; otherwise emit `incomplete` without derived values.
- Aggregate the ordered prior-period values using declared `mean` or `median`, then calculate either evaluation-minus-baseline difference or evaluation-to-baseline ratio. A zero ratio denominator and every non-finite calculation are `incomplete`.
- Record the fixed-period parameters, derived absolute ranges, aggregation, ordered period values, aggregate baseline, evaluation value, comparison value, unit, rule, and disposition in a `seasonal-baseline-comparison` assessment. `no-data` and `incomplete` assessments omit all derived values.
- Keep seasonal rules request-scoped. Protected catalogs do not generate them because validity depends on the caller's accepted time range and sampling intent.
- Treat periods as exact elapsed seconds. Calendar alignment, time-zone interpretation, daylight-saving adjustment, interpolation, minimum sample density beyond one point per window, and learned baselines remain out of scope.

## Consequences

- Customers can compare current behavior with matching recent periods without binding the application or SDK contracts to PromQL, a telemetry vendor, or an internal historical store.
- The 90-day query ceiling supports the maximum twelve weekly lookbacks plus a bounded evaluation window while retaining existing result-cardinality, byte, and deadline limits.
- Sparse history fails visibly instead of biasing an aggregate toward the periods that happened to return data.
- The exact accepted request and the report's absolute ranges are sufficient to reproduce the comparison from its cited artifact.

## Revisit triggers

Revisit when measured scenarios require minimum point counts, calendar-aware schedules, daylight-saving behavior, per-series baselines, histogram semantics, robust outlier rejection, or explicitly budgeted multi-query plans.
