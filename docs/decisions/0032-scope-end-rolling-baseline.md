# ADR 0032: Derive rolling baselines from the investigation scope end

**Status:** Accepted

**Date:** 2026-08-17

## Context

Explicit baseline timestamps are auditable but cumbersome for recurring alerts and automated callers. A rolling comparison must remain reproducible, must not silently widen an investigation, and must not introduce extra backend queries or a hidden historical store.

## Decision

- Add `rollingBaselineComparison` as an additive `v1alpha1` telemetry-selection rule. A selection may declare exactly one threshold, explicit-baseline, or rolling-baseline rule.
- Accept bounded baseline and evaluation durations plus a positive gap. Both durations are between 60 seconds and seven days; the gap is between one second and one day.
- Anchor the evaluation end at the accepted investigation `scope.end`. Place the gap immediately before the evaluation window and the baseline immediately before the gap.
- Reject the request when any derived window falls outside the accepted scope or violates strict baseline-before-evaluation ordering.
- Persist and digest the exact accepted duration-based request. Normalize the rule to explicit timestamps only inside execution, and include those absolute windows in the immutable report.
- Query the inherited investigation range once, commit one Evidence artifact, and derive both window values only from that artifact. Do not interpolate, carry values forward, or issue hidden follow-up queries.
- Keep seasonal comparison, automatic sampling, and learned baseline selection out of this rule; they require separate sample-quality and scheduling policy.

## Consequences

- Recurring callers can submit stable duration rules without calculating wall-clock ranges.
- Reviewers can reconstruct every decision from the accepted request, scope, and report.
- Telemetry backend replacement does not change comparison semantics or tool/evidence budgets.
- A scope too short for the requested durations and gap fails before provider execution.

## Revisit triggers

Revisit when seasonal comparison is designed, when minimum sample-count rules are introduced, or when calendar-aware windows and daylight-saving behavior become a supported requirement.
