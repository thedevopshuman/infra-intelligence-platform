# ADR 0015: Use an allowlisted Prometheus query adapter as the first real telemetry backend

**Status:** Accepted

**Date:** 2026-08-16

## Context

[ADR 0014](0014-backend-neutral-telemetry-evidence-query.md) introduced a provider-neutral metrics query and result boundary but deliberately shipped only a no-data adapter. A real implementation must prove that the normalized selector can map to a widely deployed historical metrics system without accepting arbitrary vendor query text, trusting provider labels for tenancy, or placing credentials in public contracts.

Prometheus exposes range queries through its [`POST /api/v1/query_range` HTTP API](https://prometheus.io/docs/prometheus/latest/querying/api/#range-queries). PromQL remains a vendor concern and must not become part of the application or SDK contract.

## Decision

- Add a Prometheus-compatible `TelemetryMetricsBackend` adapter selected explicitly by `IIP_TELEMETRY_METRICS_BACKEND=prometheus`. The default remains `no-data`.
- Load a protected tenant/integration registry at composition time. Each entry fixes the endpoint, enabled state, request timeout, raw-response byte limit, credential reference, and an allowlisted logical-to-Prometheus metric/label catalog.
- Generate PromQL only from validated catalog names and the closed public selector. Never accept PromQL, an endpoint, a backend metric name, or an authorization header from the HTTP request.
- Support `avg`, `min`, `max`, `sum`, `count`, `p50`, `p95`, and `p99` through native Prometheus aggregation. Fail `rate` explicitly until the public contract carries counter/window semantics sufficient for an unambiguous translation.
- Submit range parameters in an `application/x-www-form-urlencoded` POST body, pass a series limit one above the caller maximum to detect excess output, and bound both HTTP time and response bytes.
- Refuse redirects so a credential cannot be forwarded to a different endpoint. Accept only HTTP(S) endpoints without user information, query strings, or fragments.
- Resolve an optional bearer credential against the exact tenant, actor, integration, provider, `metrics:read` scope, and deadline. Keep secret-bearing leases inside the adapter and redact their representation.
- Treat every provider response as untrusted. Accept only successful matrix results, map only allowlisted labels, reject malformed or non-finite samples, discard provider error text, and convert provider warnings to the stable `backend-partial` warning.
- Pin a real Prometheus container for an explicit Docker Desktop interoperability gate. Keep it out of the fast default verification path.

## Consequences

- The existing public telemetry request/result, SDK, and OpenAPI contracts do not change.
- A customer can replace Prometheus by composing another `TelemetryMetricsBackend`; investigations and Evidence artifacts remain unchanged.
- The static JSON bearer broker is a local reference implementation. Production still requires an external secret-manager/identity broker that issues short-lived leases without placing raw credentials in ordinary process configuration.
- The metric catalog is intentional governance: a newly queryable metric or label requires an operator configuration change, not caller-supplied query language.
- Prometheus native histograms are not accepted by this first adapter. Float matrix samples are normalized; unsupported responses fail closed.

## Revisit triggers

Revisit when a design partner requires counter rates, native histograms, exemplars, a non-Prometheus query backend, workload identity instead of bearer access, or dynamic integration registration backed by the public `IntegrationConfig` lifecycle.
