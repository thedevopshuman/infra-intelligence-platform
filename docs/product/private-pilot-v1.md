# First usable private-pilot release

**Status:** Accepted technical scope; external pilot approval required

**Date:** 2026-09-06

The owner now prioritizes [public open-source v1](../roadmap/public-v1-release-plan.md)
first, as recorded in ADR 0155. This document continues to define optional
private-pilot scope and admission; it is no longer the first distribution target.

The `v1` in this document names the first usable product slice. Its current
admission contract uses the stricter
`customer-ai-finops-design-partner-v2` evidence semantics from ADR 0152; those
versions describe different boundaries.

## Product outcome

The first usable release is a private, single-design-partner deployment that
answers two connected operational questions:

1. What is running, what changed, and what evidence explains a bounded
   Kubernetes incident?
2. For one qualified AWS Bedrock application path, how much AI usage and
   calculated cost occurred, where is it attributed, what changed, and what is
   one evidence-backed potential saving?

It is an evidence-first control plane and AI economics layer, not an inference
proxy, autonomous operator, telemetry database, or replacement for the
customer's observability stack. Collection and processing are asynchronous;
the application keeps calling its provider if IIP or its exporter is
unavailable. Prompt and response content is rejected by default.

## Included technical profile

- Helm deployment of the API, worker, isolated OTLP receiver, and migration job
  into one explicitly selected Kubernetes environment.
- Customer-owned PostgreSQL, OIDC issuer, policy engine, credential broker,
  Secret delivery, ingress/TLS, OpenTelemetry Collector, telemetry backend,
  and backup destination.
- Tenant-scoped resource/event/evidence/investigation APIs and the read-only
  Kubernetes observer/plugin path.
- Metadata-only Bedrock `Converse` or `ConverseStream` instrumentation through
  the optional separately packaged adapter when standard attributes need
  correction.
- Durable normalized usage, protected attribution, data-driven pricing,
  explainable cost, deterministic savings rules, bounded OTLP aggregates, and
  the Grafana reference dashboard.
- Signed immutable image publication, software-bill-of-materials and
  vulnerability gates, a verified Helm bundle, minimized diagnostics, and the
  v2 private-pilot readiness evidence join.
- Privacy-bounded Prometheus operational rules using `ai-finops-v0` for this
  pilot, with the customer retaining the monitoring CRD, evaluation, routing,
  contact, and escalation authority, plus a read-only source-bound
  qualification of one synthetic firing/recovery route. The rule handoff
  remains optional for deployments outside this admission profile.

The customer may operate only a subset of the investigation integrations, but
the first AI FinOps pilot admission requires the same-invocation Bedrock flow
and all evidence named by the customer pilot readiness contract.

## Admission boundary

A pilot can start only when all of the following are true:

- a clean exact release is published, signed by the accepted organizational
  identity, and verified against its manifest and checksums;
- the protected customer configuration and every external dependency have
  passed the documented preflight and live qualification gates;
- the selected `ai-finops-v0` operational rules are loaded and healthy,
  component heartbeats are visible, and one synthetic firing/recovery route is
  currently qualified after the selected deployment;
- the aggregate customer readiness report uses
  `customer-ai-finops-design-partner-v2`, includes that alert evidence as its
  tenth exact source, says `design-partner-candidate`, and is still current;
- customer and platform owners approve the workload proxy, planned
  disruptions, cost boundary, data handling, rollback, and evidence retention;
- named support and security channels, participants, service hours, response
  objectives, and escalation contacts exist outside this repository;
- the customer explicitly accepts the limitations below.

The readiness report is evidence for this decision. It does not make the
decision and carries no installation, provider-call, disruption, signing, or
production authority. Historical nine-input
`customer-ai-finops-design-partner-v1` reports cannot satisfy this admission
boundary.

## Pilot success measures

The pilot plan must choose numeric objectives before traffic begins. At
minimum, review:

- exact-release API and OTLP availability plus observed exporter delivery;
- resource freshness and investigation completion for the chosen Kubernetes
  scope;
- AI usage coverage, attribution coverage, pricing coverage, and dashboard
  freshness;
- at least one explainable change or potential-saving finding whose cited facts
  a customer reviewer can reproduce;
- support incidents, privacy/security findings, operator effort, and rollback
  readiness.

The platform emits privacy-bounded operational and AI economics metrics to the
customer's selected OpenTelemetry destination. It sends no product analytics
or feedback to the project automatically. The
[pilot feedback guide](../operations/private-pilot-feedback.md) defines the
minimum consent and minimization rules for anything the customer elects to
share.

## Explicit nonclaims

This scope does not claim:

- general production readiness, an SLA, unattended operation, or public
  support;
- representative customer traffic, long-window capacity, regional
  availability, or automatic/involuntary infrastructure or database failover;
- invoice equivalence, every Bedrock model/SDK/operation, universal pricing,
  or savings realization;
- collection of prompts/responses, correctness of arbitrary untrusted content,
  or autonomous remediation;
- complete customer identity, policy, credential, PKI, Collector, backup, or
  disaster-recovery lifecycle ownership;
- an accepted public license, company identity, trademark, product brand,
  repository namespace, package namespace, or release-support policy.

`Infrastructure Intelligence Platform`, `IIP`, and `iip` remain neutral
placeholders until the independent brand and governance track is accepted.
