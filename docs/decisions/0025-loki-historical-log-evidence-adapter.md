# ADR 0025: Use an allowlisted Loki adapter for historical log evidence

**Status:** Accepted

**Date:** 2026-08-17

## Context

[ADR 0023](0023-backend-neutral-log-evidence-and-otlp-intake.md) introduced a provider-neutral historical log query and a separate OTLP logs intake boundary. It deliberately shipped an honest no-data query backend. A real implementation must prove that logical selectors can map to a widely deployed log system without exposing LogQL, trusting provider labels for tenant scope, or placing credentials in public contracts.

Loki exposes historical stream queries through its [`GET /loki/api/v1/query_range` HTTP API](https://grafana.com/docs/loki/latest/reference/loki-http-api/). LogQL remains an adapter concern and is not part of the application, OpenAPI, or SDK contract.

## Decision

- Add a Loki `TelemetryLogsBackend` selected explicitly by `IIP_TELEMETRY_LOGS_BACKEND=loki`. The default remains `no-data`.
- Load a protected tenant/integration registry at composition time. Each entry fixes the endpoint, organization identifier, enabled state, request/response limits, optional credential reference, label bindings, logical services, and normalized severities.
- Generate only selector-only LogQL from validated resource UIDs and the closed logical selector. Never accept LogQL, an endpoint, backend labels, tenant headers, or credentials from a public request.
- Translate `eq` exactly. Translate `neq` as both label existence and inequality so a missing label cannot broaden the match.
- Query forward with nanosecond bounds and request one record above the caller maximum to detect truncation. Bound URL size, HTTP time, provider-response bytes, record count, log-body bytes, and mapped labels.
- Refuse redirects. Accept only HTTP(S) endpoints without user information, query strings, or fragments.
- Resolve an optional Bearer lease against the exact tenant, actor, integration, `loki` provider, `logs:read` scope, and deadline. `X-Scope-OrgID` comes only from protected integration configuration.
- Treat provider responses as untrusted. Accept only successful stream results, require exact requested resource/service/severity mappings, allowlist attributes, require paired trace/span identifiers, discard provider warning text, and expose only stable warning/error codes.
- Pin a real Loki container in an explicit Docker Desktop interoperability gate. Keep the external-system gate outside the fast default verification path.

## Consequences

- Public log contracts, OpenAPI, SDKs, Evidence artifacts, and investigation reports do not change.
- A customer can replace Loki by composing another `TelemetryLogsBackend`; the evidence and investigation layers remain unchanged.
- Loki does not provide an authentication layer by itself. Production deployments must place an authenticating gateway in front of it or use a managed endpoint, then resolve short-lived credentials through the platform credential broker.
- The closed catalog is intentional governance. New services, severities, or mapped labels require operator configuration rather than caller-supplied query language.
- The first adapter performs indexed label selection only. It does not search or interpret log bodies.

## Revisit triggers

Revisit when a design partner requires body search, structured metadata, non-Bearer authentication, multiple Loki organization routing schemes, automatic catalog discovery, or a non-Loki historical log backend.
