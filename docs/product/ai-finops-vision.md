# AI FinOps and generative-AI observability vision

**Status:** Accepted V0 direction  
**Date:** 2026-09-05

## Product outcome

Extend the Infrastructure Intelligence Platform with an open-source,
OpenTelemetry-native, vendor-neutral AI usage and cost observability layer. An
organization should be able to answer where generative-AI spend is happening,
why it changed, and which evidence-backed adjustment may reduce it without
placing IIP in the inference request path.

This capability reuses the platform's tenancy, evidence, event, PostgreSQL,
OpenTelemetry, SDK, deployment, and audit boundaries. It is not a separate
product kernel.

## Operating principles

- **No proxy:** applications call their model provider directly. IIP is never a
  required hop for inference.
- **Asynchronous and fail open:** telemetry delivery failure cannot fail or
  delay the customer's model request. The customer Collector owns buffering
  and retry policy.
- **Metadata only by default:** prompt, response, tool arguments, retrieved
  documents, embeddings, and provider request bodies are neither required nor
  persisted by the V0 contracts.
- **OpenTelemetry first:** prefer standard generative-AI spans and upstream
  instrumentation. Provider adapters translate gaps at the boundary rather
  than introducing provider concepts into the domain.
- **Collection and calculation are separate:** immutable usage facts do not
  change when a pricing catalog changes. Recalculation produces a new cost
  record bound to an exact catalog version.
- **No false precision:** calculated cost is an estimate, not an invoice.
  Missing or conflicting coverage is visible as `unpriced` or `ambiguous`,
  never silently converted to zero.
- **Evidence before recommendation:** a potential saving cites the usage and
  cost facts, comparison windows, rule version, observed change, and formula
  that produced it.
- **Ownership is protected:** workload telemetry supplies observed service
  identity, while reviewed tenant policy supplies effective-time application
  and team ownership in a separate immutable fact.

Fail open applies only to customer application telemetry emission. Receiver
authentication, tenant binding, schema validation, pricing consistency, and
stored evidence continue to fail closed.

## V0 product slice

The first vertical flow is:

> AWS Bedrock → OpenTelemetry generative-AI span → customer Collector →
> tenant-bound IIP trace intake → normalized AI usage record → calculated cost
> record → deterministic saving rule → Grafana.

One dashboard must answer:

1. How many invocations and tokens were observed?
2. What calculated cost do those observations represent?
3. Which service, environment, region, and model produced the usage?
4. What changed against the preceding comparison window?
5. What is one potential saving, and which facts support it?

The provisioned Grafana dashboard remains the V0 cross-window and saving view.
The same API image now provides a read-only AI Economics console for bounded
current-window usage, calculated cost, protected application/team allocation,
generation provenance, incomplete coverage, and one newest committed
evidence-backed opportunity. Calculated, unpriced, and unresolved savings stay
distinct; recommendations remain advisory and require validation. If no
finding exists in the selected interval, the console says so instead of
inventing one.

The first supported profile is metadata-only Bedrock `Converse` and
`ConverseStream` traffic using on-demand pricing for one explicitly configured
region and model. Broader API, model, region, purchase-mode, and language
support must be claimed only after executable compatibility evidence exists.
The pinned official Python instrumentation now has separate no-network evidence
for both operations; live support remains bound to an explicitly qualified
model, region, and operation.

## Attribution

V0 uses standard OpenTelemetry resource identity: `service.name`, optional
`service.namespace`, deployment environment, provider region, and model. The
first Phase B unit maps that observed identity to reviewed application and team
ownership under protected effective-time policy and leaves unmatched usage
explicitly unallocated. Allocation queries, cost joins, export dimensions, and
dashboard views remain. A future optional helper may add business dimensions,
but a new IIP instrumentation SDK is not part of V0.

## Initial intelligence

Rules are deterministic and bounded:

- context growth compares input tokens per request across declared windows;
- retry amplification compares attempts per logical operation when the source
  instrumentation exposes an honest retry count;
- expensive-model anomaly compares calculated cost per request within the same
  attribution scope.

`evaluate-lower-cost-model` means evaluate a candidate under workload-specific
quality, latency, safety, and compliance tests. It never claims that a cheaper
model is a safe replacement from price evidence alone.

## V0 non-goals

- invoice reconciliation or replacement of AWS Cost and Usage Reports;
- prompt or response analytics;
- an inference gateway, proxy, cache, or model router;
- automatic model switching or spend-control mutations;
- autonomous FinOps agents;
- universal Bedrock instrumentation support across every SDK and operation;
- customer-defined arbitrary telemetry queries in the ingestion path.

## Success criteria

- A reference Bedrock call reaches the dashboard without an IIP proxy or IIP
  instrumentation SDK.
- Duplicate span delivery produces one usage fact and one deterministic cost
  fact.
- Cache and reasoning subsets are never double charged.
- Unknown and overlapping price matches are visibly unresolved.
- No prompt or response content is present in contracts, storage, events,
  exported labels, or dashboard queries.
- Stopping IIP or its backend does not change the outcome of a model request.
- Every displayed saving links to a rule version and stored evidence.
