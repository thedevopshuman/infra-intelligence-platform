# ADR 0013: Export bounded ingestion freshness measurements over optional OTLP/HTTP

**Status:** Accepted

**Date:** 2026-08-15

## Context

[ADR 0012](0012-opentelemetry-portability-boundary.md) selects OpenTelemetry as a replaceable interchange and keeps outbound platform observability separate from inbound customer telemetry evidence. The first executable slice needs to prove that the authoritative ingestion-freshness calculation can reach a real Collector without importing telemetry SDKs into application logic or making product behavior depend on an observability endpoint.

The freshness service currently runs on demand. It has no scheduler, so an export triggered by that service represents an evaluation event rather than a continuous scrape or availability window.

## Decision

- Define a provider-neutral `IngestionTelemetrySink` and immutable measurement in the application ports. The freshness service offers the sink the same rounded values only after a successful tenant-authorized evaluation.
- Use the official OpenTelemetry Python SDK and OTLP/HTTP protobuf metrics exporter in the adapter layer. Pin both at `1.44.0` for the reference runtime.
- Keep export disabled by default and require `IIP_OTEL_METRICS_ENABLED=true` plus an explicit standard OTLP endpoint.
- Honor the standard base and metrics-specific endpoint variables. A metrics-specific endpoint is exact; the base endpoint receives `/v1/metrics`.
- Record local synchronous gauges and let the SDK's periodic reader own asynchronous network export, timeout, and retry behavior.
- Treat the sink as observational. Adapter recording failures are contained and counted locally; exporter or Collector failure cannot alter the freshness report, ingestion commit, or investigation behavior.
- Restrict custom dimensions to a fixed status, a fixed violation set, and a deployment-selected identity mode: `none`, `source`, or `tenant-source`. Raw resource content, cursor values, errors, and credentials are never metric data.
- Keep OTLP headers in process/Secret configuration. Reject endpoint user information, query strings, and fragments so credentials are not embedded in the endpoint value.
- Prefer a customer-controlled Collector as the routing boundary. Changing its downstream exporter changes the telemetry backend without changing platform application code.

## Consequences

- The application and domain remain independent of OpenTelemetry packages and vendor backends.
- A real Collector integration test can verify protocol interoperability through Docker Desktop.
- Metrics exist only after freshness evaluations. A production sampler and SLO window processor remain separate work.
- The reference counter covers local instrument-recording failures, not background network-delivery loss. Collector/SDK health monitoring, durable queue policy, and a production export-loss objective remain required before production enablement.
- Inbound OTLP ingestion and historical backend queries remain unimplemented evidence-adapter concerns; this decision does not turn the metrics exporter into a customer telemetry store.

## Revisit triggers

Revisit when a background sampler is introduced, when export-loss measurements define a durable queue requirement, when customer cardinality budgets are known, or when the platform adds traces, logs, inbound OTLP, or a second protocol.
