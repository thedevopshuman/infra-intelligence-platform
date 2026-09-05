# ADR 0091: OpenTelemetry-native AI economics ledger

**Status:** Accepted

**Date:** 2026-09-05

## Context

The platform needs to show where generative-AI usage and calculated spend occur,
why they change, and which bounded optimization may be worth validating. Making
IIP an inference proxy would increase latency, availability coupling, data
exposure, and provider lock-in. Deriving cost only from aggregated token metrics
would lose per-invocation attribution, deduplication, effective-time pricing,
and evidence lineage. Reusing general Evidence blobs as an accounting ledger
would make high-volume usage expensive to query and blur immutable evidence
metadata with purpose-built economic facts.

OpenTelemetry generative-AI semantic conventions and instrumentation are still
evolving. Provider pricing also varies by model, region, tier, routing,
purchase mode, caching, effective time, and private commercial terms. A numeric
result therefore cannot be treated as an invoice merely because its arithmetic
is deterministic.

## Decision

Build the AI economics flow as an asynchronous, metadata-only extension of the
existing OTLP and PostgreSQL boundaries:

1. customer applications continue calling providers directly;
2. upstream OpenTelemetry instrumentation emits GenAI spans to a
   customer-controlled Collector;
3. an isolated, authenticated IIP trace receiver allowlists metadata, binds the
   tenant from the channel, and normalizes supported spans;
4. a purpose-built append-only usage ledger stores one `AiUsageRecord` per
   accepted invocation with deterministic deduplication;
5. a separate cost service selects an exact versioned `AiPriceCatalog` entry
   and emits an immutable `AiCostRecord`;
6. deterministic rules emit `AiSavingsFinding` records that cite their usage
   and cost evidence;
7. bounded aggregates and findings export through OTLP to a
   customer-selectable backend, with Grafana as the reference visualization.

V0 accepts AWS Bedrock metadata-only traces for one explicitly qualified
profile. It uses standard OpenTelemetry resource attributes for attribution and
does not add an IIP instrumentation SDK. Prompt, response, message, tool,
retrieval-document, embedding, and raw request/response payload capture are
prohibited by the V0 contract.

Token totals and their cache/reasoning subsets are priced without double
counting. Prices and amounts use integer currency subunits. Price lookup with no
match is `unpriced`; overlapping matches are `ambiguous`. Numeric results are
always labeled `calculated-estimate`, retain their catalog source/version, and
make no invoice-reconciliation claim.

Application telemetry delivery fails open: an IIP or Collector failure does not
alter the provider request. Authenticated intake, tenant binding, content
policy, schema validation, and economic invariants fail closed. Durable
buffering remains a customer Collector responsibility under ADR 0080.

No FinOps agent or automatic mutation is part of this slice. Initial
recommendations are versioned deterministic rules, and any model substitution
recommendation requires separate quality, latency, safety, and compliance
validation.

## Consequences

- IIP remains outside the inference data path and does not become a model
  gateway or telemetry backend monopoly.
- Per-invocation facts support attribution and recalculation while Grafana and
  customer observability backends remain replaceable.
- The trace receiver is a new signal surface; the existing metrics/logs intake
  implementation cannot be represented as if it already supports traces.
- Accounting completeness depends on retaining eligible metadata spans. Sampled
  inputs are reported as observed usage and are never extrapolated into an
  invoice claim.
- Price catalog maintenance, overlap checks, effective-time tests, and source
  provenance become release responsibilities.
- Provider-specific instrumentation gaps stay in adapters and compatibility
  profiles rather than leaking into domain contracts.

## Revisit triggers

Revisit when OpenTelemetry GenAI conventions become stable and incompatible
with the accepted normalization, when a provider exposes authoritative billing
line items, when private pricing needs a separate encrypted catalog service,
when invoice reconciliation is introduced, or when an optional business-
attribution helper cannot be expressed through standard OpenTelemetry resource
attributes.

