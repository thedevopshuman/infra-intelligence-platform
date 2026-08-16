# OpenTelemetry portability boundary

**Status:** Accepted direction; outbound exporter, historical query, and inbound metrics receiver reference adapters implemented
**Date:** 2026-08-15
**Decision:** [ADR 0012](../decisions/0012-opentelemetry-portability-boundary.md)

[OpenTelemetry](https://opentelemetry.io/docs/) is the preferred vendor-neutral telemetry interchange for the platform, but it serves two different flows. Keeping them separate prevents an export endpoint from becoming evidence authority and prevents a backend SDK from entering the application core.

```mermaid
flowchart LR
    subgraph Customer["Customer environment"]
      Workloads["Workloads and infrastructure"]
      Collector["OpenTelemetry Collector"]
      Backend["Customer-selected telemetry backend"]
      Workloads --> Collector
      Collector --> Backend
    end

    subgraph IIP["Infrastructure Intelligence Platform"]
      Receiver["Optional OTLP receiver adapter"]
      Normalized["Normalized bounded telemetry evidence"]
      Evidence["Application evidence ports"]
      Signals["IIP operational and freshness signals"]
      Exporter["OTLP exporter adapter"]
      Receiver --> Normalized --> Evidence
      Signals --> Exporter
    end

    Collector -->|"selected OTLP stream"| Receiver
    Evidence -->|"backend query adapter when required"| Backend
    Exporter -->|"OTLP metrics, logs, traces"| Collector
```

## Outbound platform telemetry

The `IngestionFreshnessReport` remains the authoritative point-in-time product result. Its application service now offers the same bounded measurements to a provider-neutral sink after a successful evaluation. The OpenTelemetry adapter maps them to synchronous instruments, while the official SDK's periodic reader performs OTLP/HTTP network export. OpenTelemetry packages are confined to `adapters` and composition; neither `domain` nor the use case imports them.

The adapter uses the [standard exporter endpoint configuration](https://opentelemetry.io/docs/specs/otel/protocol/exporter/), including `OTEL_EXPORTER_OTLP_ENDPOINT` and signal-specific variables. The recommended target is a customer-controlled [OpenTelemetry Collector](https://opentelemetry.io/docs/collector/), which can route to a different open-source or commercial backend without an IIP code change. [ADR 0013](../decisions/0013-otlp-http-ingestion-metrics-export.md) records the concrete reference selection and the [operations guide](../operations/opentelemetry-export.md) lists its metrics and configuration.

Export is disabled by default, asynchronous, and observational. Endpoint unavailability does not roll back ingestion, block an investigation, or change report results. The reference adapter has bounded timeouts, SDK retry behavior, local recording-failure counting, and controlled dimensions. It does not yet provide automatic sampling, a durable queue, an export-loss SLI, or complete exporter health; those are required before production enablement.

## Customer telemetry evidence

OTLP is a push/export protocol, not a historical query protocol. The first executable customer-telemetry boundary therefore addresses data already retained in a backend. `TelemetryEvidenceRequest` expresses one bounded metric selector, `TelemetryMetricsBackend` hides the vendor query/API, and the application validates a `TelemetryEvidenceResult` before storing it through the immutable Evidence pipeline. [ADR 0014](../decisions/0014-backend-neutral-telemetry-evidence-query.md) records the contract decision. The default remains no-data; [ADR 0015](../decisions/0015-prometheus-telemetry-evidence-adapter.md) adds an explicitly selected Prometheus-compatible adapter with an allowlisted metric catalog and request-scoped credential resolution.

The optional receiver now accepts a customer-selected OTLP/HTTP Protobuf metrics stream and converts each non-empty batch into bounded `OtlpMetricsEvidence` through the immutable Evidence pipeline. [ADR 0016](../decisions/0016-tenant-bound-otlp-metrics-receiver.md) keeps it separate because channel authentication, tenant binding, admission control, and retention authority differ from a request/response backend query. The protected channel profile fixes tenant, integration, resources, allowed metrics/attributes, limits, sensitivity, and retention; payload attributes provide no authority.

The first runtime implementation accepts gauges and numeric sums, supports bounded identity/gzip requests, and atomically rejects unknown or unsupported semantics. It bounds time, request/artifact bytes, cardinality, and processing time. All resource/scope/point attributes and Collector output are untrusted input and pass through allowlisting, validation, redaction, hashing, and provenance controls before becoming evidence. Traces, logs, histograms, arbitrary retention, forwarding, and query storage remain out of scope.

## Security and tenancy

- Endpoint credentials and custom OTLP headers are secret references or protected runtime configuration, never public resource/report fields.
- Tenant and integration scope come from authenticated channel configuration, not OTLP resource attributes supplied by a workload.
- Tenant/source attributes are exported only under explicit deployment policy because they can be sensitive and high-cardinality.
- Raw logs, span payloads, resource contents, provider errors, and credentials never enter freshness metrics.
- Receivers and exporters have explicit network destinations, deadlines, payload bounds, and no ambient credentials.

## Pending implementation decisions

- whether later pushed signals use immutable Evidence only, customer storage forwarding, or a hybrid policy;
- the production external credential broker and additional `TelemetryMetricsBackend` adapters;
- production receiver topology, workload identity/mTLS, token rotation, and process/gateway isolation for self-hosted, customer-hosted, and managed deployments;
- retention, sampling, cardinality budgets, and regional/data-residency controls;
- queue durability and the division of retry, batching, and delivery-health ownership between the official SDK and a sidecar/customer Collector;
- the cadence and ownership of automatic freshness evaluation and production SLO windows.

External-broker, additional backend, receiver-isolation, and expanded-signal decisions belong to the Phase 2 telemetry-evidence slice; automatic sampling, delivery health, and SLO decisions belong to the Phase 3 operational-hardening gate. The current freshness API, outbound metric projection, normalized historical-query boundary, Prometheus adapter, and tenant-bound OTLP metrics receiver are deliberately useful before those production selections.
