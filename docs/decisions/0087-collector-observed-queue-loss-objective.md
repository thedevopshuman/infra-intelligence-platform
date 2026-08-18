# ADR 0087: Collector-observed sending-queue depth and send-loss objective

**Status:** Accepted

**Date:** 2026-08-18

## Context

ADR 0072, ADR 0073, ADR 0081, and `docs/operations/opentelemetry-export.md`
each repeatedly flag the same gap: the export-attempt SLO and burn-rate
reports measure IIP's own local OTLP exporter attempts (IIP-to-Collector
reliability) from durable counter samples this process writes itself. None
of them measure the customer's own OpenTelemetry Collector's internal
sending queue for its pipeline *to* IIP's receiver (Collector-to-backend
reliability). ADR 0080 makes that queue an explicit customer deployment
choice - IIP does not operate, store, or otherwise see it - so "Collector
queue/loss measurement" cannot be built the way the burn-rate report was:
there is no local counter to sample.

The Collector itself already emits this as ordinary self-telemetry.
Empirically, against the same `otel/opentelemetry-collector:0.158.0` image
this repository already pins for its OTLP-intake compatibility fixture, an
`otlphttp` exporter with `sending_queue` enabled exposes, on its own
Prometheus-format internal metrics endpoint: `otelcol_exporter_queue_size`
and `otelcol_exporter_queue_capacity` (gauges, labeled `data_type` and
`exporter`), and `otelcol_exporter_sent_metric_points` /
`otelcol_exporter_send_failed_metric_points` (and the `_log_records`
equivalents for the logs pipeline), all labeled by `exporter`. A customer
who scrapes their Collector's self-metrics into their own observability
backend already has this signal; IIP's existing backend-neutral
`TelemetryMetricsBackend` port (proven against a real Prometheus instance)
can query it exactly like any other customer metric.

## Decision

Add `CollectorQueueLossService` (`query_collector_queue_loss.py`): a
privileged, deployment-scoped report, structurally a sibling of
`TelemetryExportBurnRateService` but sourced differently. It queries the
existing `TelemetryMetricsBackend` port directly (not through the Evidence
pipeline, which is for resource-scoped investigation evidence) for six
fixed, closed logical metric names - `platform.collector.exporter.{queue-
size,queue-capacity,sent-metric-points,send-failed-metric-points,sent-log-
records,send-failed-log-records}` - that a deployment binds, through the
existing Prometheus integration registry, to whatever the customer's own
Collector self-metrics happen to be named.

A new deployment-owned binding (`CollectorQueueLossBinding`: which already-
configured metrics integration, and the `exporter` component name the
customer gave their IIP-bound pipeline) selects the metrics backend and
disambiguates the specific exporter among others the Collector may run.
Absent this binding, the report is `disabled`, matching every other
optional-feature report in this codebase. A single-window objective
(`CollectorQueueLossObjectives`: window duration, maximum loss basis
points, maximum queue-utilization basis points, minimum eligible attempts)
governs a `disabled | no-data | insufficient-data | meeting | breached`
status per signal (`metrics`, `logs` - the two signals this receiver
actually accepts, not `traces` like the export-attempt reports), combined
deployment-wide with the same conservative precedence ADR 0073 established.
Send-loss is `failedDelta / (sentDelta + failedDelta)` between the first and
last counter reading in the window; a counter that appears to have gone
backward (a Collector process restart) is treated as `no-data` for that
signal rather than understating loss.

## Consequences

- IIP-to-Collector reliability (burn-rate/export-SLO) and Collector-to-
  backend queue/loss reliability now both have an executable report, and
  the distinction between them stays visible in the API/console rather than
  being asserted only in prose;
- customers must still scrape their own Collector's self-metrics into their
  telemetry backend and configure the binding; IIP does not enable this by
  discovering the Collector automatically, matching ADR 0080's position
  that queue capacity, retention, and loss policy are customer choices;
- the six logical metric names are a closed reference to the real,
  version-pinned Collector's own metric surface, not a general Collector
  self-telemetry query capability; a customer running a materially
  different Collector distribution or exporter type may expose differently
  named or shaped metrics that this binding cannot express.

## Revisit triggers

Revisit when regional aggregation of this report is needed, when a
customer's Collector exposes queue metrics under different names or an
additional signal type, or when Collector-side alert routing is designed.
