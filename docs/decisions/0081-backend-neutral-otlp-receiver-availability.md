# ADR 0081: Backend-neutral OTLP receiver availability

**Status:** Accepted
**Date:** 2026-08-17

## Context

The isolated OTLP receiver can prove workload identity and commit accepted
telemetry as durable Evidence, but process health alone does not tell an operator
whether authenticated exports are succeeding, being rejected by capacity, or
failing on a dependency. Recording request counters in the same PostgreSQL
database would hide the database outages the signal must measure. Adding a
vendor-specific metrics backend would also break the platform portability
boundary.

## Decision

Measure completed metrics and logs requests at the HTTP response boundary and
offer one bounded observation to an application-owned sink. The OpenTelemetry
adapter exports `iip.otlp.receiver.requests` and
`iip.otlp.receiver.duration` through the existing OTLP/HTTP metrics exporter.
The customer-selected Collector or backend owns aggregation, burn-rate policy,
regional views, and alerts.

Observations contain only signal, closed outcome, availability class, duration,
and deployment-owned objective values. Tenant, channel, SPIFFE identity,
certificate, credential, payload, endpoint, raw path, status code, and error text
are prohibited. Successful HTTP responses are available; authenticated
rate-limit and dependency/server failures are unavailable; malformed,
unauthenticated, denied, or disabled requests are excluded. Metric recording and
export are failure-isolated from intake and readiness.

The receiver composes only the platform metrics exporter, never the investigation
trace exporter. Its exporter status is reported under the `otlp-receiver`
component in the existing deployment export-health and sampled export-SLO
contracts. Helm uses the same protected endpoint/header settings and an explicit
Collector egress rule. The outbound destination must not point back at the IIP
intake listener.

TLS handshakes rejected before an HTTP handler exists cannot produce this
application metric. Collector sender metrics, ingress metrics, or a synthetic
probe remain required for certificate/transport availability and total-replica
loss.

## Consequences

- customers can change the Collector exporter or backend without an IIP build;
- availability remains observable during primary-database failures when the
  outbound Collector path still works;
- client mistakes and unauthorized traffic do not consume the receiver error
  budget, while admitted capacity rejection does;
- exporter delivery health distinguishes a quiet receiver from an exporter that
  cannot deliver its observations;
- production claims still require Collector queue/loss, ingress, regional, and
  synthetic signals outside this process-local measurement.
