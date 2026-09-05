# AI economics contracts

**Status:** v1alpha1  
**Machine contracts:**

- `contracts/schemas/ai-usage-record.schema.json`
- `contracts/schemas/ai-attribution-policy.schema.json`
- `contracts/schemas/ai-usage-attribution-record.schema.json`
- `contracts/schemas/ai-price-catalog.schema.json`
- `contracts/schemas/ai-cost-record.schema.json`
- `contracts/schemas/ai-savings-finding.schema.json`

These contracts separate observed model usage, price configuration, calculated
cost, and potential savings. An implementation must not mutate one record to
stand in for another stage.

Protected application/team allocation is specified separately in
[`ai-attribution-contracts.md`](ai-attribution-contracts.md). Its policy and
result schemas are listed here because attribution is a peer stage in the same
AI economics flow, not a mutation of usage or cost.

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
`effectiveUntil`, when present, is exclusive. Catalog review should prevent
applicable entries from overlapping; the runtime deliberately retains a
defensive multiple-match path so a bad snapshot becomes `ambiguous` rather
than selecting a rate silently. Zero matches are unpriced.

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

Every result carries `calculated-cost-not-invoice`. Unresolved results also
carry `cost-unresolved`; safe-integer overflow additionally carries
`cost-overflow`. These warnings are part of the semantic validation boundary,
not display-only text.

The engine selects `responseModel` when instrumentation reports it and falls
back to `requestModel`; this prices the model that actually served the
invocation. Catalogs sourced from `test-fixture` require explicit non-production
enablement, and their cost results carry `test-fixture-pricing` in `warnings`.

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

The contract reserves `context-growth`, `retry-amplification`, and
`expensive-model-anomaly`. The executable V0 runtime supports only
`context-growth` version `1.0.0`; accepting another category in the public
schema does not claim that its evaluator exists. Only a rule with complete
source facts and a declared minimum cohort may emit a finding. Potential
savings can be `calculated` or `unpriced`; a missing price is never zero
savings. The V0 runtime emits only a calculated finding and emits no finding
when exact pricing is unavailable.

The V0 profile fixes adjacent, equal-duration baseline and current windows and
the exact tenant, provider, model, region, service, deployment environment,
catalog, cost-engine version, request minimum, record ceiling, growth
threshold, and grace period. Only successful, complete records with zero
cache-read and cache-write input qualify. Both cohorts must use one currency
and scale; the current cohort must use one uncached-input rate.

V0 uses integer half-up arithmetic:

```text
mean = roundHalfUp(sum(inputTokens) / sampleCount)
changeBasisPoints = roundHalfUp((currentMean - baselineMean) * 10000 / baselineMean)
excessQuantity = (currentMean - baselineMean) * currentSampleCount
amountSubunits = roundHalfUp(excessQuantity * currentRate / 1000000)
```

Every qualifying finding cites all usage and cost records used by the formula.
Its identifier is the first 32 hexadecimal characters of the canonical
specification SHA-256 digest with the `aif_` prefix. Evaluation time is not an
identity input. Persistence must revalidate the exact source cohort and
calculation before commit.

Recommendations are advisory and always set `requiresValidation: true`. A
lower-cost model suggestion requires separate workload-specific quality,
latency, safety, and compliance evaluation.

## CloudEvent

`io.iip.ai.usage-recorded.v1` is emitted after the usage record commits. Its
subject and `data.usageRecordId` are the immutable usage identity. The event
contains only bounded routing metadata and the deduplication digest, not token
content or pricing.

`io.iip.ai.cost-calculated.v1` commits atomically with a new cost record. It
contains usage, catalog, status, provider, model, and service routing identity,
but never contains quantities, rates, monetary totals, or catalog contents.
An exact retry creates no second cost event.

`io.iip.ai.savings-finding-recorded.v1` commits atomically with a new savings
finding. Its data contains the finding and rule identities plus bounded
category, severity, provider, model, and service routing dimensions. It omits
windows, samples, quantities, rates, currency, monetary values, and evidence
record IDs. An exact deterministic retry creates no second finding or event.

## Compatibility

The contracts are additive within `v1alpha1`. Breaking meter definitions,
pricing lookup, monetary scale, privacy, or authority semantics require a new
contract version and migration notes. SDK types represent the public records
only; they do not instrument provider calls or import server internals.

## OTLP trace binding

The executable intake binding is `POST /v1/traces` on the isolated OTLP
listener described by `api/openapi/otlp-receiver.openapi.json`. It accepts
binary OTLP Protobuf with identity or gzip encoding. The authenticated channel,
not span attributes, supplies tenant and protected commercial scope. Only
allowlisted GenAI client metadata becomes a usage record; content-bearing
attributes, span events, span links, raw payload persistence, and ambiguous
or dropped input fail the entire export before commit.

The usage row, its CloudEvent, and its outbox row commit atomically. Exact
retries return the original record without a second event. Reusing a canonical
span identity with changed normalized facts is a conflict. See the
[AI usage receiver runbook](../operations/ai-usage-receiver.md).
