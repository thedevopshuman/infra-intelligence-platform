# ADR 0052: Process-local telemetry export health

**Status:** Accepted

## Context

The platform exports metrics and traces through customer-selected OTLP/HTTP endpoints. Existing record-failure counters cover instrumentation errors but cannot tell an operator whether the SDK exporter reached the Collector or backend. Treating an optional telemetry dependency as API readiness would also turn an observability outage into a product outage.

## Decision

Wrap the official OpenTelemetry metric and span exporters at the adapter boundary and record each completed export result in bounded, thread-safe process memory. Expose the state through a contract-backed control-plane operation requiring both the `platform-admin` role and policy approval.

The state is backend-neutral and includes only signal, latest-result status, saturating counters, timestamps, and stable failure codes. It contains no endpoint or provider error text. `/healthz` and `/readyz` remain unchanged. Multi-replica operators query each process or use deployment-level collection; this contract does not claim durable or cluster-wide aggregation.

## Consequences

- customers can replace the Collector or telemetry backend without changing the public contract;
- delivery failure and recovery are visible independently for metrics and traces;
- a telemetry outage cannot evict a serving API process;
- counters reset on restart and do not replace measured SLO windows;
- alert routing, durable exporter queues, and cluster-wide aggregation remain deployment concerns.
