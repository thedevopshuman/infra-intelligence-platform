# ADR 0084: Use an allowlisted OpenSearch adapter as a second historical log backend

**Status:** Accepted

**Date:** 2026-08-18

## Context

[ADR 0025](0025-loki-historical-log-evidence-adapter.md) proved the backend-neutral historical log query boundary against Loki and explicitly listed "a non-Loki historical log backend" as a revisit trigger. Many production log deployments index into Elasticsearch or OpenSearch rather than Loki, and the platform's own constitution treats external systems as adapters: a second, structurally different backend is the strongest available proof that `TelemetryLogsBackend` generalizes rather than accidentally encoding Loki's label/stream model.

OpenSearch exposes historical document search through its [`POST /{index}/_search` HTTP API](https://opensearch.org/docs/latest/api-reference/search/). Its query DSL, document/field model, and `_id`-per-record shape are meaningfully different from Loki's label-selected log streams, so this adapter cannot reuse Loki's translation logic; it proves the port boundary on a second, independent implementation.

## Decision

- Add an OpenSearch `TelemetryLogsBackend` selected explicitly by `IIP_TELEMETRY_LOGS_BACKEND=opensearch`. The default remains `no-data`; `loki` remains available and unaffected.
- Load a protected tenant/integration registry at composition time. Each entry fixes the endpoint, a single lowercase index (or index pattern) name, enabled state, request/response limits, an optional credential reference, closed field bindings (resource UID, service, severity, timestamp, body, optional paired trace/span, and an allowlisted attribute map), and closed service/severity value maps — the same governance shape ADR 0025 established for Loki labels, translated to OpenSearch document fields.
- Generate only a closed `bool`/`filter` query from validated field bindings and the closed public selector: a `range` filter on the configured timestamp field, `terms` filters on resource UID/service/severity, and `term`/`exists`+`must_not` pairs for `eq`/`neq` attribute filters. Never accept a caller-supplied query, index name, endpoint, or credential.
- Request one record above the caller maximum, sort by the configured timestamp field then document ID for determinism, and bound URL/body size, HTTP time, and provider-response bytes exactly as the Loki adapter does.
- Refuse redirects. Accept only HTTP(S) endpoints without user information, query strings, or fragments.
- Resolve an optional Bearer lease against the exact tenant, actor, integration, `opensearch` provider, `logs:read` scope, and deadline, reusing the same `CredentialBroker` port and lease shape as every other adapter.
- Treat every provider response as untrusted: require the documented `hits.hits[]._source` shape, require exact requested resource/service/severity mapping and in-range timestamps per record, allowlist attributes, require paired trace/span identifiers, reject a shard failure silently, and expose only the stable `backend-partial`/`record-limit` warning codes.
- Pin a real OpenSearch container (security plugin disabled, single-node) for an explicit Docker Desktop interoperability gate, kept outside the fast default verification path exactly like the Prometheus and Loki gates.

## Consequences

- The existing public log evidence request/result, OpenAPI, and SDK contracts do not change; a customer chooses Loki or OpenSearch by composition, not by a different public contract.
- The port boundary is now proven against two structurally different backends, reducing the risk that the log evidence contract accidentally encodes Loki-specific semantics.
- OpenSearch's own security plugin (Basic auth, JWT, or fronting proxy) is a deployment decision; this adapter only speaks Bearer, matching the credential broker's existing lease shape, so production deployments needing Basic auth still require a fronting proxy or JWT-issuing gateway, the same posture ADR 0025 already accepted for Loki.
- The closed field/value catalog remains intentional governance: a new field or index requires operator configuration, not caller-supplied query language.
- This adapter performs field-filtered document search only; it does not use OpenSearch's relevance scoring, aggregations, or full-text query syntax.

## Revisit triggers

Revisit when a design partner requires relevance-scored body search, aggregation-backed summaries, Basic/JWT credential schemes in the broker abstraction itself, multi-index routing per tenant, or automatic index-template discovery.
