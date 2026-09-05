# ADR 0111: Require workload suitability evidence for model-cost recommendations

**Status:** Accepted

## Context

The AI FinOps vision reserves an `expensive-model-anomaly` rule, but price
difference alone cannot establish that a cheaper model is suitable. A model
change may alter answer quality, latency, safety, compliance, or workload
behavior. Reporting a monetary saving without those facts would violate the
product constitution's evidence-before-confidence principle.

## Decision

Implement the rule as a comparison between a lower-cost candidate model in the
baseline window and the currently observed reference model in the adjacent
window, within one exact protected provider, region, service, and deployment
scope.

The protected profile must contain one immutable, time-bounded
`AiModelSuitabilityReport`. It must attest passed quality, latency, safety, and
compliance gates for that exact candidate/reference pair and workload scope.
The worker registers the report before evaluation. The finding cites it and all
usage and calculated-cost records used by integer half-up arithmetic. The
ledger revalidates the stored report and complete cohort before committing the
finding and minimized CloudEvent.

No provider SDK, prompt content, response content, autonomous action, or model
switching enters the kernel. The recommendation is `evaluate-lower-cost-model`
with `requiresValidation: true` even when the prior suitability report remains
valid.

## Consequences

- Price-only comparisons cannot create findings or savings amounts.
- Suitability reports are protected tenant inputs, not self-asserted telemetry.
- Test fixtures must be explicitly enabled and cannot support a production
  profile accidentally.
- A quantified amount is a calculated scenario from observed cohorts, not an
  invoice or guarantee.
- Requalification is required after report expiry or scope/model change.

## Revisit when

Customer evidence justifies non-adjacent cohorts, cross-provider candidates,
confidence calibration beyond sample floors, signed external attestations, or
automatic proposal generation through the separately governed action boundary.
