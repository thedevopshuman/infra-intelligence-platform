# ADR 0138: Price aggregate output only under exact rate equivalence

**Status:** Accepted

**Date:** 2026-09-08

## Context

The qualified Amazon Bedrock `Converse` and `ConverseStream` profiles report
total output tokens but do not report the provider-neutral split between
reasoning and non-reasoning output. The normalized usage fact must therefore
remain `partial`; treating the missing reasoning quantity as zero would invent
telemetry.

The exact AWS public-price importer deliberately maps both canonical output
categories to the same retained AWS price dimension when the provider prices
aggregate output. In that case, the absent split cannot change the calculated
amount: every possible partition of the observed output total has the same
rate. Rejecting the record loses a reproducible estimate, while manufacturing
the split misstates the evidence.

## Decision

Cost engine `0.2.0` may resolve one narrow partial-usage shape:

1. `reasoningOutputTokens` is the only missing usage field;
2. input total, output total, cache-read input, and cache-write input are all
   present and valid;
3. exactly one catalog entry matches the invocation; and
4. that entry's reasoning-output and non-reasoning-output integer rates are
   exactly equal.

The result contains the usual three non-overlapping input lines plus one
`aggregate-output-tokens` line. It has complete cost coverage and carries
`aggregate-output-priced-at-equivalent-rates` in addition to
`calculated-cost-not-invoice`. It does not create a reasoning quantity or
rewrite the partial usage record.

If either output rate differs, or any other meter is missing, the result stays
`unpriced` with partial coverage and `missing-usage`. Complete usage continues
to produce the five detailed charge lines. The engine version changes from
`0.1.0` to `0.2.0` so recalculation creates a distinct immutable lineage and
cannot conflict with an earlier result for the same usage and catalog.

## Consequences

- Qualified Bedrock cache-aware usage can produce a mathematically exact
  calculated estimate under a catalog that proves output-rate equivalence.
- Instrumentation evidence remains honest: the usage fact is still partial,
  and the cost catalog—not an inferred token split—closes the arithmetic.
- A provider or model with different reasoning pricing remains unresolved
  until instrumentation reports the split.
- Existing deterministic savings rules still require their declared complete
  usage cohorts; this decision does not silently widen recommendation inputs.
- Invoice agreement, private rates, discounts, commitments, and billing
  reconciliation remain separate customer evidence.
