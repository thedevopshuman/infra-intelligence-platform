# ADR 0150: Emit component heartbeats for missing-signal alerts

**Status:** Accepted

**Date:** 2026-09-06

## Context

The operational alert policy can evaluate availability, freshness, recording
failure, and AI coverage only while their metrics exist. Ordinary traffic and
AI activity can legitimately be absent, so treating those business signals as
process heartbeats would create false alerts. Conversely, leaving every
missing series to customer-specific monitoring creates a silent-failure gap in
the shipped production policy.

The platform already runs a failure-isolated exporter-health cycle for each
enabled API, workflow-worker, and OTLP-receiver process. That cycle is the
narrow place to emit an always-on signal without adding a proxy, another
scheduler, or customer-data capture.

## Decision

1. Add a provider-neutral `ComponentTelemetryHeartbeatSink` application port.
   Its only input is one closed component value: `api`, `workflow-worker`, or
   `otlp-receiver`.
2. Implement the port in the OpenTelemetry adapter as the synchronous gauge
   `iip.telemetry.heartbeat`, with value `1` and the single bounded attribute
   `iip.component`. Resource `service.name` continues to identify the configured
   component service. Tenant, source, resource, provider, model, endpoint,
   instance, prompt, response, and credential data are prohibited.
3. Invoke the sink from the existing exporter-health cycle before shared-store
   reporting. Instrument failure is counted through the existing bounded local
   recording-failure metric and never affects readiness, serving, workflow
   results, or health persistence.
4. Add separate Prometheus rules for enabled API, workflow-worker, and
   OTLP-receiver services using `absent_over_time` over a configurable bounded
   window. Require the window to cover at least two metric-export intervals and
   two exporter-health cycles.
5. Keep notification delivery, Prometheus rule selection, Collector/backend
   health, escalation, regional observation, and long-window SLO ownership with
   the customer. An absent heartbeat identifies a broken observed path; it does
   not identify which hop failed.

## Consequences

- Idle deployments now retain an active, metadata-only OTel signal suitable for
  end-to-end missing-series detection.
- A complete exporter, Collector, backend, or process outage can activate a
  shipped rule even when no query, ingestion, or AI workload is present.
- The default five-minute lookback plus five-minute pending duration avoids
  reacting to a single missed 60-second export. Customers may select longer
  bounded values for their topology.
- The signal is observational and fail-open; it grants no identity, telemetry
  backend, notification, or mutation authority.

## Alternatives considered

- Using query or AI request counters as heartbeats was rejected because idle
  traffic is valid and would create false incidents.
- Querying Kubernetes desired replicas from the application was rejected
  because it would add ambient cluster authority and would still not prove the
  exporter-to-backend path.
- Installing Prometheus, Alertmanager, or notification receivers was rejected
  because those systems remain replaceable customer-owned infrastructure.
