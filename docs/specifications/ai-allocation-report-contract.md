# AI allocation report contract

**Status:** v1alpha1

`AiAllocationReport` is a bounded, tenant-scoped read model over immutable AI
usage, attribution, and calculated-cost records. It answers which protected
application or team incurred observed usage and estimated cost without turning
the telemetry backend into accounting authority.

## Query and authority

`GET /v1/ai/economics/allocation` requires Bearer authentication and policy
approval for `ai-economics:read`. Tenant identity comes only from the
authenticated actor. The route requires exactly one `start`, `end`, and
`groupBy` query value. Timestamps are canonical UTC instants, the interval is
half-open, and its maximum duration is 31 days. `groupBy` is `application` or
`team`.

The active attribution policy, price catalog, and both engine versions come
only from protected deployment configuration. Callers cannot select or submit
them. The report freezes those generations under `sources` so the result is
interpretable and repeatable.

The application reads at most 10,001 rows to enforce a 10,000-source-record
ceiling without returning a partial total. Exceeding the time or record bound
returns a stable input error. Storage failure returns `storage.unavailable`;
policy denial returns `policy.denied`; neither response exposes provider or SQL
details.

## Coverage before totals

Every usage row belongs to exactly one group:

- `allocated`, grouped by the immutable application or team ID and display
  name stored on the attribution record;
- `unallocated`, with `no-matching-rule`; or
- `pending`, with `not-yet-attributed` when the selected generation has not yet
  produced a result.

Cost coverage independently counts `priced`, `unpriced`, `ambiguous`, and
`pending` records. A numeric `pricedCost` sums only `priced` records and is
always labelled `calculated-estimate`; it is not a provider invoice. Missing
token meters contribute no quantity and are counted by `inputTokenRecords` and
`outputTokenRecords` rather than being interpreted as zero usage.

Coverage identities must hold for the report and each group:

```text
usageRecords = allocatedRecords + unallocatedRecords + pendingAttributionRecords
usageRecords = pricedRecords + unpricedRecords + ambiguousRecords + pendingCostRecords
```

## Historical names and bounded dimensions

Allocated rows group by the pair of stable ID and immutable display name.
Consequently, a rename represented by effective-time rules remains visible as
separate historical labels instead of rewriting old ownership. The protected
policy has at most 1,000 rules, so one report has at most 1,002 groups including
unallocated and pending.

OTLP metric projection uses stable IDs only; display names never become metric
attributes. Prompts, responses, request IDs, trace/span IDs, arbitrary
application attributes, raw payloads, and credentials are absent from this
contract.
