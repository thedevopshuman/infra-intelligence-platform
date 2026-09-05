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
7. Export privacy-bounded aggregates/findings over OTLP and ship a
   Collector/Prometheus/Loki/Grafana reference topology.
8. Prove the full flow against a deterministic Bedrock-shaped fixture and an
   opt-in real Bedrock compatibility profile.

Exit gate: the reference dashboard answers the five V0 product questions;
duplicate delivery does not duplicate cost; unknown pricing is visible; no
content crosses the boundary; and stopping the observability path does not fail
the model request.

Implementation status: items 1, 2, 4, 5, 6, and 7 are delivered. Item 3 has an
executable Bedrock-shaped OTLP fixture, strict metadata/content boundary, and a
no-network gate against the exact pinned official Python botocore `Converse`
instrumentation. The gate normalizes the shipped legacy provider attribute and
service-specific scope without guessing absent cache/reasoning usage. Live
model/region and `ConverseStream` auto-instrumentation qualification remain.
Item 7 includes the privacy-bounded OTLP aggregate projection, finding view,
and a disposable Collector/Prometheus/Loki/Grafana topology. Item 8 now has a
deterministic full-flow gate covering deduplication, visible unpriced usage,
content rejection, exact cost, and one saving. A source-bound offline report
now also proves official SDK interoperability and asynchronous exporter-failure
isolation. The explicitly enabled real-provider call remains.

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
- authoritative provider price ingestion/approval workflow;
- first real Bedrock model/region qualification target;
- long-term analytics backend and retention objectives;
- optional business-attribution helper;
- provider billing reconciliation and private-rate encryption.
