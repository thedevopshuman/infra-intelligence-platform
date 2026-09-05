# AI FinOps implementation roadmap

**Status:** Working plan  
**Date:** 2026-09-05

This roadmap extends, rather than replaces, the initial infrastructure
intelligence roadmap. Each phase must preserve the product constitution,
tenant isolation, metadata-only default, asynchronous failure behavior, and
provider-neutral package boundaries.

## Phase A — Bedrock vertical slice

**Outcome:** A local, executable metadata-only path shows Bedrock usage,
calculated cost, attribution, change, and one evidence-backed saving in
Grafana.

1. Accept the AI usage, price catalog, cost, and saving contracts plus ADR 0091.
2. Add an isolated tenant-bound OTLP trace route using the existing receiver
   identity, admission, durability, and availability patterns.
3. Normalize one qualified Bedrock `Converse`/`ConverseStream` instrumentation
   profile into `AiUsageRecord` and reject content attributes.
4. Persist usage and its CloudEvent atomically with deterministic duplicate
   handling.
5. Load a protected, versioned price catalog and calculate explainable cost
   facts with cache/reasoning subset handling.
6. Evaluate the context-growth rule over explicit baseline/current windows.
   Add retry amplification only after its normalized source fact and honest
   non-monetary semantics are executable.
7. Export privacy-bounded aggregates/findings over OTLP and ship a
   Collector/Prometheus/Loki/Grafana reference topology.
8. Prove the full flow against a deterministic Bedrock-shaped fixture and an
   opt-in real Bedrock compatibility profile.

Exit gate: the reference dashboard answers the five V0 product questions;
duplicate delivery does not duplicate cost; unknown pricing is visible; no
content crosses the boundary; and stopping the observability path does not fail
the model request.

Implementation status: items 1, 2, 4, 5, 6, and 7 are delivered. Item 3 has an
executable Bedrock-shaped OTLP fixture, strict metadata/content boundary, and
separate no-network gates against the exact pinned official Python botocore
`Converse` and `ConverseStream` instrumentation. The streaming gate consumes
the actual wrapped event stream through final usage metadata and proves span
completion is deferred until then. Both gates normalize the shipped legacy
provider attribute and service-specific scope without guessing absent
cache/reasoning usage. Live model/region/operation qualification remains.
Item 7 includes the privacy-bounded OTLP aggregate projection, finding view,
and a disposable Collector/Prometheus/Loki/Grafana topology. Item 8 now has a
deterministic full-flow gate covering deduplication, visible unpriced usage,
content rejection, exact cost, and one saving. A source-bound offline report
now also proves official SDK interoperability and asynchronous exporter-failure
isolation. The explicitly enabled real-provider call remains.

Two additional deterministic rules are delivered. Protected channel
configuration maps provider retry attributes into `retryCount`, and the
source-bound retry-amplification evaluator compares complete fixed-window
cohorts. Its finding and dashboard signal carry an unresolved monetary status
until billable-attempt evidence exists. The expensive-model evaluator requires
a protected, immutable, time-bounded suitability report with passed
workload-specific quality, latency, safety, and compliance gates before it can
compare candidate/reference cost per request. Its calculated scenario cites
the report and complete usage/cost cohorts and remains advisory.

A closed `production-ai-finops-v0` Helm overlay and minimized deployment
preflight are also executable. Static mode proves the complete non-secret
configuration shape; explicit-context cluster mode additionally proves that
every referenced Secret key, ConfigMap, and backup claim exists. This remains
a pre-install gate: live provider/workload behavior, authoritative prices, and
customer Collector/PKI interoperability require separate qualification.

A provider-neutral static catalog qualification unit is also executable. A
protected content-addressed policy declares exact required commercial scopes,
source age, and report validity. The minimized report binds the exact catalog
and policy digests, checks source profile/freshness/publication order, detects
overlapping effective prices, and proves every required scope resolves once.
It exposes no rates, model names, locators, credentials, or scope details. The
production worker and Helm preflight now require one current exact
`production-catalog` report per catalog tenant and reject a stale, altered,
offline-only, or cross-tenant binding before catalog registration. For AWS
Bedrock public token pricing, exact provider-source selection is now
executable: a protected content-addressed mapping converts one retained
official `AmazonBedrock` Bulk API snapshot without description parsing,
floating point, or runtime network access, and emits minimized reproducibility
evidence. Organizational approval, private/negotiated rates, and invoice
agreement remain open.

## Phase B — application and team attribution

**Outcome:** Operators can allocate observed usage and calculated cost to
reviewed organizational ownership without arbitrary user-controlled labels.

- map standard service/resource identity to application and team using
  protected tenant configuration;
- record mapping generation and unmatched coverage;
- add bounded allocation queries, dashboards, and export dimensions;
- evaluate whether optional business attributes require a small helper, using
  standard OTel attributes before creating any IIP SDK;
- prove cardinality, tenant isolation, renames, and historical ownership.

Implementation status: the protected policy and immutable attribution-result
contracts, deterministic effective-time resolver, explicit unallocated state,
tenant-bound in-memory/PostgreSQL persistence, worker authority, CloudEvent,
bounded generation-bound allocation query, calculated-cost join, public API
and SDK types, privacy-bounded export dimensions, Docker/Helm configuration,
application/team dashboard views, and executable isolation/rename/replay/
cardinality tests are delivered. Optional business attributes remain deferred
pending evidence that standard service/resource identity is insufficient.

Exit gate: every allocation is traceable to an effective-time mapping, and
unallocated usage remains visible rather than guessed.

## Phase C — additional providers

**Outcome:** The same public ledger and cost contracts work across qualified
providers.

- add one provider adapter and executable compatibility profile at a time;
- keep provider request/response types outside domain/application packages;
- add effective-time provider pricing fixtures and overlap tests;
- add provider-specific coverage reporting without provider-specific dashboard
  queries;
- introduce invoice reconciliation as separate variance facts only after an
  authoritative billing source is selected.

Exit gate: a second provider reaches the same normalized dashboard and the
kernel contains no provider SDK import or vendor pricing branch.

Implementation status: the local Phase C exit gate is executable. A second
protected Collector trace route accepts complete synthetic OpenAI-shaped GenAI
usage through an independent channel credential, persists it in the common
ledger, selects an effective-time OpenAI fixture rate from the common catalog,
maps it to protected application/team ownership, and displays it beside
Bedrock through the same provider-neutral metrics and Grafana queries. Exact
replay remains idempotent and both provider channels reject content-bearing
spans. Neither the OpenAI SDK nor its instrumentation enters the kernel, and
pricing contains no provider branch.

The separate pinned official OpenAI Python compatibility gate calls the real
SDK against a loopback fixture in a no-network container and normalizes the
actual instrumented span. Its missing cache-write and reasoning meters remain
partial and ineligible for exact cost. Live OpenAI service, streaming,
Responses API, private endpoints, authoritative prices, and customer Collector
qualification remain open; the synthetic complete profile does not claim
those outcomes.

## Later — unified infrastructure and AI economics

- correlate AI economic changes with deployments, resource ownership, and
  incidents;
- evaluate rule precision against design-partner outcomes;
- introduce a bounded FinOps agent only after deterministic rules, evaluation,
  budgets, and authority are production-proven;
- propose governed optimizations without granting approval or execution;
- add invoice variance, commitments, budgets, and forecasting as explicit
  contracts rather than overloading calculated cost.

## Decisions intentionally deferred

- stable public product/company name and package migration;
- open-source license and commercial boundary;
- organizational approval workflow, private-price ingestion, and invoice
  reconciliation;
- first real Bedrock model/region and OpenAI API/model qualification targets;
- long-term analytics backend and retention objectives;
- optional business-attribution helper;
- provider billing reconciliation and private-rate encryption.
