# AI economics contracts

**Status:** v1alpha1  
**Machine contracts:**

- `contracts/schemas/ai-usage-record.schema.json`
- `contracts/schemas/ai-price-catalog.schema.json`
- `contracts/schemas/ai-cost-record.schema.json`
- `contracts/schemas/ai-savings-finding.schema.json`

These contracts separate observed model usage, price configuration, calculated
cost, and potential savings. An implementation must not mutate one record to
stand in for another stage.

## `AiUsageRecord`

One record represents one accepted generative-AI invocation from a
tenant-bound telemetry channel.

| Area | Semantics |
| --- | --- |
| `source` | Integration/channel provenance, OTLP trace signal, semantic-convention version, and instrumentation scope. |
| `invocation` | Provider-neutral operation, model, region, commercial routing dimensions, timing, outcome, and trace correlation. |
| `attribution` | Standard service, namespace, environment, and optional IIP resource references. |
| `usage` | Provider, instrumentation, or derived token totals with explicit completeness. |
| `privacy` | Fixed metadata-only policy; content and raw payload persistence are false. |
| `deduplicationKey` | SHA-256 digest of the canonical tenant/channel/span invocation identity. |

`cacheReadInputTokens + cacheWriteInputTokens` must not exceed `inputTokens`.
`reasoningOutputTokens` must not exceed `outputTokens`. The subset fields do not
add to their parent totals.

A complete record has input and output totals and no missing fields. A partial
record identifies missing meters. Partial usage remains useful for volume and
reliability analysis, but cost calculation must return an unresolved result
when the missing data prevents exact catalog evaluation.

Prompt, response, message, embedding, tool, retrieved-document, and raw payload
fields are not extensions to this contract. They require a separate future
privacy decision and contract version.

## `AiPriceCatalog`

A catalog is an immutable tenant-scoped version. Its source identifies whether
the data came from a provider publication, operator-managed commercial terms,
or an explicitly non-production test fixture. A fixture price must not be
presented as a provider price.

An entry matches provider, model, region, service tier, routing mode, purchase
mode, and invocation start time. `effectiveFrom` is inclusive;
`effectiveUntil`, when present, is exclusive. Applicable entries must not
overlap. Zero matches are unpriced and multiple matches are ambiguous.

Every entry supplies five canonical rates:

1. uncached input tokens;
2. cache-read input tokens;
3. cache-write input tokens;
4. non-reasoning output tokens;
5. reasoning output tokens.

This forces cache and reasoning behavior to be explicit even when a provider
uses the same price for multiple categories. A zero rate is an actual catalog
fact; a missing or unsupported rate is not silently interpreted as free.

`currencyScale` defines integer subunits: `amount / 10^currencyScale` is the
currency value. Rates are subunits per one million tokens. V0 calculation uses
integer half-up rounding.

## `AiCostRecord`

One record references one usage record and one exact catalog version/source
hash. `costBasis` is always `calculated-estimate`.

`priced` means one catalog entry matched and every required meter was resolved.
Each line retains observed quantity, non-overlapping billable quantity, rate,
entry ID, and amount; `totalSubunits` equals the sum of line amounts.

`unpriced` means no trustworthy numeric calculation is possible. `ambiguous`
means multiple applicable catalog entries or another unresolved choice exists.
Neither shape contains a numeric total.

For a complete usage record:

```text
uncachedInput = inputTokens - cacheReadInputTokens - cacheWriteInputTokens
nonReasoningOutput = outputTokens - reasoningOutputTokens
lineAmount = roundHalfUp(billableQuantity * rate / 1_000_000)
```

## `AiSavingsFinding`

A finding is a deterministic rule result. It freezes rule ID/version,
attribution, baseline and current windows, observations, confidence, potential
saving calculation, recommendation, and evidence references.

The initial categories are `context-growth`, `retry-amplification`, and
`expensive-model-anomaly`. Only a rule with complete source facts and a
declared minimum cohort may emit a finding. Potential savings can be
`calculated` or `unpriced`; a missing price is never zero savings.

Recommendations are advisory and always set `requiresValidation: true`. A
lower-cost model suggestion requires separate workload-specific quality,
latency, safety, and compliance evaluation.

## CloudEvent

`io.iip.ai.usage-recorded.v1` is emitted after the usage record commits. Its
subject and `data.usageRecordId` are the immutable usage identity. The event
contains only bounded routing metadata and the deduplication digest, not token
content or pricing. Cost and finding events will be added with their runtime
use cases rather than speculatively.

## Compatibility

The contracts are additive within `v1alpha1`. Breaking meter definitions,
pricing lookup, monetary scale, privacy, or authority semantics require a new
contract version and migration notes. SDK types represent the public records
only; they do not instrument provider calls or import server internals.

