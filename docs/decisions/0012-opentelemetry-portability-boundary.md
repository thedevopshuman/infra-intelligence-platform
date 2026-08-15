# ADR 0012: Use OpenTelemetry as a replaceable telemetry interchange, not a storage authority

**Status:** Accepted

**Date:** 2026-08-15

## Context

Customers may operate Prometheus-compatible systems, commercial observability platforms, managed cloud services, or their own OpenTelemetry Collectors. The platform needs to export its operational signals and consume customer telemetry evidence without binding the domain/application layers to one vendor.

[OTLP](https://opentelemetry.io/docs/specs/otlp/) standardizes delivery of metrics, logs, and traces over gRPC or HTTP. It does not standardize querying historical data from whichever backend ultimately stores those signals. Treating an OTLP endpoint as both transport and query store would hide this distinction and create an incomplete portability promise.

## Decision

- Use OTLP as the preferred interchange for outbound IIP telemetry and optional inbound customer telemetry streams.
- Prefer a customer-controlled OpenTelemetry Collector as the configured endpoint so backend routing can change outside IIP.
- Define provider-neutral application ports and normalized evidence/measurement types; OpenTelemetry SDK, protocol, Collector, and backend behavior belongs in adapters and deployment composition.
- Keep the Phase 1 `IngestionFreshnessReport` authoritative. Exporters observe its semantics but do not become the source used to calculate the report.
- Treat inbound OTLP as untrusted push data. Historical queries against an existing backend require an evidence-provider adapter behind the same application boundary.
- Derive tenant scope from authenticated integration/channel configuration, never caller-supplied telemetry attributes.
- Keep export asynchronous and failure-isolated from ingestion and investigations. Production enablement requires bounded buffering, retry/backoff, timeouts, drop metrics, and payload/cardinality controls.
- Use standard `OTEL_EXPORTER_OTLP_ENDPOINT` and signal-specific endpoint configuration where the selected SDK supports them; credentials and headers stay in protected runtime configuration.

## Consequences

- Customers can change the downstream backend by changing Collector/export configuration rather than platform business logic.
- Direct backend adapters remain possible when query semantics, existing retention, or deployment constraints require them.
- IIP does not need to become a full telemetry store merely to support OpenTelemetry input.
- A production OTLP adapter is not included in the Phase 1 freshness unit; receiver/exporter conformance and failure behavior remain explicit follow-up work.

## Revisit triggers

Revisit when the first design partner selects its telemetry topology, when the first telemetry evidence contract is implemented, when high-cardinality cost is measurable, or when an official OpenTelemetry semantic convention covers the platform's custom infrastructure-intelligence signals.
