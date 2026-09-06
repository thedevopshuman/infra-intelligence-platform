# AI economics contracts

**Status:** v1alpha1  
**Machine contracts:**

- `contracts/schemas/ai-usage-record.schema.json`
- `contracts/schemas/ai-attribution-policy.schema.json`
- `contracts/schemas/ai-usage-attribution-record.schema.json`
- `contracts/schemas/ai-price-catalog.schema.json`
- `contracts/schemas/aws-bedrock-price-catalog-import-policy.schema.json`
- `contracts/schemas/ai-price-catalog-import-report.schema.json`
- `contracts/schemas/ai-price-catalog-qualification-policy.schema.json`
- `contracts/schemas/ai-price-catalog-qualification-report.schema.json`
- `contracts/schemas/ai-cost-record.schema.json`
- `contracts/schemas/ai-savings-finding.schema.json`
- `contracts/schemas/ai-savings-finding-page.schema.json`
- `contracts/schemas/ai-model-suitability-report.schema.json`

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
| `invocation` | Provider-neutral operation, model, region, commercial routing dimensions, timing, outcome, trace correlation, and optional protected-channel-mapped retry count. |
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

`retryCount` is the number of reported attempts beyond the initial logical
operation attempt. Its provider attribute and absence semantics are fixed by
protected channel configuration, never by span data. If the channel has not
qualified zero-on-absence behavior, a missing attribute remains unknown; the
retry rule will not treat it as zero.

## `AiPriceCatalog`

A catalog is an immutable tenant-scoped version. Its source identifies whether
the data came from a provider publication, operator-managed commercial terms,
or an explicitly non-production test fixture. A fixture price must not be
presented as a provider price.

Pre-promotion freshness, global overlap, and exact required-scope coverage use
the separate minimized [price-catalog qualification
contracts](ai-price-catalog-qualification-contract.md). Qualification neither
selects an authoritative source nor turns calculated estimates into invoices.
The production runtime gate requires one current exact qualified report before
registering or using each tenant catalog.

The first provider-source adapter uses the separate [AWS Bedrock price-catalog
import contracts](aws-bedrock-price-catalog-import-contract.md). It exact-maps
one retained official Price List snapshot under protected policy and produces
minimized reproducibility evidence before the ordinary qualification and
promotion stages. The checked import examples are synthetic contract data, not
deployable prices.

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

The executable runtime supports `context-growth`, `retry-amplification`, and
`expensive-model-anomaly` version `1.0.0`. Only a rule with complete source
facts and a declared minimum cohort may emit a finding. Potential savings can
be `calculated`, `unpriced`, or explicitly `unresolved`; missing price,
billing, or suitability evidence is never zero savings.

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

The retry-amplification profile uses the same fixed scope and adjacent-window
constraints, plus a minimum current retrying-operation rate and an absolute
rate-increase threshold. Every successful operation in both cohorts must have
a normalized `retryCount`. Its observation is the share of operations with
`retryCount > 0`, expressed in basis points:

```text
retryRateBasisPoints = roundHalfUp(retryingOperations * 10000 / operations)
increaseBasisPoints = currentRetryRateBasisPoints - baselineRetryRateBasisPoints
```

A qualifying retry finding cites every usage record but no cost record. Its
potential saving is `unresolved` with `retry-billing-unproven`, because the
final successful span does not establish whether hidden retry attempts used
billable tokens. It recommends reviewing throttling, timeouts, and retry
policy; it never manufactures a currency amount.

The expensive-model profile adds an exact candidate model and embeds one
protected `AiModelSuitabilityReport`. The report must be immutable,
content-addressed, valid at finding evaluation time, and bound to the exact
tenant, provider, reference/candidate model pair, region, service, and
deployment environment. It attests that workload-specific quality, latency,
safety, and compliance gates passed without persisting prompt, response, tool,
or evaluation-example content. Test-fixture reports require a separate
explicit non-production switch.

Its adjacent baseline window contains the candidate-model cohort and its
current window contains the reference-model cohort. Every record must be a
successful complete usage fact with a priced cost fact from the configured
catalog and engine generation. Both cohorts use one currency and scale. V1
uses integer half-up arithmetic:

```text
candidateMean = roundHalfUp(sum(candidateCostSubunits) / candidateCount)
referenceMean = roundHalfUp(sum(referenceCostSubunits) / referenceCount)
increaseBasisPoints = roundHalfUp((referenceMean - candidateMean) * 10000 / candidateMean)
amountSubunits = (referenceMean - candidateMean) * referenceCount
```

A qualifying finding cites every usage and cost record plus the registered
suitability report. Persistence reloads and revalidates all of them before the
atomic finding/event/outbox commit. The monetary result is a calculated
scenario, not an invoice, guarantee, or permission to change models.

Recommendations are advisory and always set `requiresValidation: true`.
Despite the prior suitability result, `evaluate-lower-cost-model` explicitly
requires validation against current traffic before a model change.

Committed findings are exposed through the separate bounded
[`AiSavingsFindingPage` read contract](ai-savings-finding-page-contract.md).
That tenant- and interval-scoped API pages immutable records without running a
rule, dereferencing evidence, or granting action authority.

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
All three supported rules use this event type and a rule-specific source URN.

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
