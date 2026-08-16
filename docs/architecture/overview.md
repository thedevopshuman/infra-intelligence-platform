# Architecture overview

**Status:** Accepted foundation  
**Date:** 2026-08-14

## System context

The platform sits above existing infrastructure and operations systems. It does not become the source of credentials or replace provider control planes.

```mermaid
flowchart LR
    Humans["Operators and service owners"]
    Automation["CI/CD, alerts, workflows"]
    Platform["Infrastructure Intelligence Platform"]
    Providers["Cloud, Kubernetes, observability, GitOps, data platforms"]
    Models["Policy-approved model providers"]
    Destinations["Incident, chat, ticketing, and collaboration systems"]

    Humans --> Platform
    Automation --> Platform
    Platform <--> Providers
    Platform <--> Models
    Platform --> Destinations
```

## Logical architecture

```mermaid
flowchart TB
    subgraph Surfaces["Surfaces"]
      UI["Web console"]
      CLI["CLI and SDKs"]
      API["API, webhooks, MCP"]
    end

    subgraph Control["Control plane"]
      Gateway["Gateway: identity, tenant, rate, policy context"]
      Workflow["Workflow and approvals"]
      Registry["Agent and plugin registry"]
    end

    subgraph Intelligence["Intelligence plane"]
      Correlator["Correlation and timeline"]
      Runtime["Bounded agent runtime"]
      Evidence["Evidence service"]
      Policy["Policy decision point"]
    end

    subgraph Knowledge["Knowledge plane"]
      Graph["Resource graph and observations"]
      Events["Immutable event log"]
      Search["Indexes and retrieval"]
      Audit["Audit and provenance"]
    end

    subgraph Ecosystem["Extension plane"]
      Plugins["Sandboxed plugins"]
      Integrations["Provider adapters"]
      Tools["Agent tools and actions"]
    end

    Surfaces --> Gateway
    Gateway --> Workflow
    Gateway --> Registry
    Workflow --> Runtime
    Runtime --> Correlator
    Runtime --> Evidence
    Runtime --> Policy
    Correlator --> Graph
    Correlator --> Events
    Evidence --> Search
    Workflow --> Audit
    Integrations --> Graph
    Integrations --> Events
    Plugins --> Integrations
    Plugins --> Tools
    Tools --> Policy
```

## Core data flow

1. An integration observes an external object and submits a canonical resource observation.
2. Identity resolution maps `(tenant, provider, type, external ID)` to a stable platform UID.
3. The graph stores the latest view and observation provenance; a resource event enters the immutable log.
4. Correlation attaches events to resources, relationships, deployments, incidents, and investigations.
5. A surface or rule creates a scoped investigation request.
6. The runtime selects an agent manifest, resolves allowed tools, applies budgets, and gathers evidence.
7. The agent emits ranked hypotheses and recommendations with evidence references and uncertainty.
8. Proposed mutations enter a workflow. Policy and approval decide whether an action may execute.
9. Every decision, tool call, approval, execution, and result produces audit events.

Collector plugins cross the extension boundary through bounded resource collection request/result contracts. A complete batch returns an explicit scope digest, candidate checkpoint, and optional opaque provider cursor map; only the trusted ingestion workflow may commit that state after every resource observation and event is durable. The host stores the last complete source membership and generates deterministic tombstones for resources missing from the next complete snapshot. Membership, tombstones, checkpoint, and provider cursors commit together. Partial, failed, cancelled, stale-resume, or scope-drifted passes never imply deletion. [ADR 0007](../decisions/0007-reconciliation-membership-and-tombstones.md) records membership authority, and [ADR 0009](../decisions/0009-provider-cursor-sets-and-watch-recovery.md) records list/watch recovery.

The [ingestion freshness boundary](ingestion-freshness-telemetry.md) evaluates that committed checkpoint together with the latest accepted source observation and pending transactional-outbox delivery. This makes complete-source lag and downstream backlog measurable without exposing opaque provider state. [ADR 0011](../decisions/0011-ingestion-freshness-semantics.md) defines the point-in-time Phase 1 semantics. The optional exporter described by [ADR 0013](../decisions/0013-otlp-http-ingestion-metrics-export.md) maps successful evaluations to OTLP/HTTP metrics; automatic sampling, production export-health gates, and SLO windows remain operational-hardening work.

[OpenTelemetry portability](opentelemetry-portability.md) separates outbound platform observability from inbound customer telemetry evidence. OTLP is the preferred replaceable transport and a customer-controlled Collector is the preferred routing boundary, while historical backend queries use `TelemetryMetricsBackend` because OTLP does not define a query API. [ADR 0012](../decisions/0012-opentelemetry-portability-boundary.md) keeps both directions outside the provider-neutral kernel. The first outbound adapter uses the application-owned measurement sink, [ADR 0014](../decisions/0014-backend-neutral-telemetry-evidence-query.md) fixes the normalized metric request/result and Evidence boundary, and [ADR 0015](../decisions/0015-prometheus-telemetry-evidence-adapter.md) adds the first allowlisted Prometheus-compatible adapter. Vendor SDKs and query languages remain in adapters and composition.

Evidence providers cross a separate application-owned boundary. The [reference collection pipeline](evidence-collection-pipeline.md) authorizes an exact tenant, integration, evidence type, and resource scope before a provider runs, then validates and redacts provider output before hashing and atomic persistence. Providers do not receive ambient credentials through the application contract.

The current executable intelligence slice is deterministic and model-free: it collects canonical resource-state evidence, classifies a narrow Kubernetes failure set, and produces the same immutable report contract intended for future bounded agents. The evaluation harness scores machine-readable root-cause classes, evidence use, red-herring resistance, unsupported certainty, and budget compliance. [ADR 0006](../decisions/0006-deterministic-investigation-and-dry-run-actions.md) fixes this as the baseline that future runtimes must improve upon.

Actions cross a stricter workflow boundary. Proposal, approval, and execution are distinct policy checks and immutable records; self-approval is prohibited and duplicate execution returns the prior result. The reference executor is dry-run only. Plugin sessions similarly grant only a declared capability subset, persist only token references/digests, and carry mandatory expiry, cancellation, and resource limits.

## Storage responsibilities

The logical boundaries remain product-neutral. [ADR 0004](../decisions/0004-postgresql-observation-store-and-outbox.md) selects PostgreSQL for the initial resource/observation, event-log, checkpoint, and outbox substrate while leaving other stores and later transport specialization open:

| Store | Responsibility | Required semantics |
| --- | --- | --- |
| Resource graph | PostgreSQL latest view, immutable observations, and rebuildable relationship index | Tenant partitioning; idempotent upsert; time-aware provenance |
| Event log | PostgreSQL immutable log and transactional outbox initially | At-least-once ingestion; deduplication by source + ID; replay |
| Evidence store | PostgreSQL metadata and artifact bytes initially | Immutable versions; tenant partitioning; retention policy; redaction metadata |
| Workflow store | PostgreSQL investigations, approvals, idempotency, and results initially | Crash-safe immutable transitions; duplicate-impact prevention |
| Registry | Versioned agent/plugin manifests | Immutable releases; signatures and compatibility metadata |
| Search/index | Derived query acceleration | Rebuildable from authoritative stores |
| Audit store | Security and decision trail | Append-only; protected retention; exportability |

## Deployment shape

Start as a modular control-plane service plus asynchronous workers. Preserve logical boundaries in packages and contracts before splitting into network services. Split only when scaling, isolation, or independent failure domains justify the operational cost.

The initial Helm chart deploys the reference API and accepts an existing Secret reference for PostgreSQL configuration. Planned components are ingestion and outbox workers, correlator, workflow worker, plugin runner, and backing stores. Data-plane collectors may run in customer clusters and send normalized observations through authenticated, tenant-scoped channels.

## Cross-cutting invariants

- Tenant ID, actor ID, and roles are derived through the [authentication boundary](authentication-boundary.md), not trusted from payloads or identity assertion headers.
- Source events are immutable; corrections are new events.
- Resource identity is stable across observations and display-name changes.
- Side effects carry idempotency keys and emit before/after audit records.
- Tool output is untrusted and cannot directly modify policy or authority.
- Secrets are referenced by logical name and resolved at execution time; they never enter contracts or prompts.
- Derived indexes and summaries are rebuildable from authoritative data and provenance.
