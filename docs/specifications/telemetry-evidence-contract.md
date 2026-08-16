# Telemetry evidence request and result contracts

**Status:** v1alpha1

**Machine contracts:**

- `contracts/schemas/telemetry-evidence-request.schema.json`
- `contracts/schemas/telemetry-evidence-result.schema.json`

These contracts define the first backend-neutral customer telemetry evidence boundary. A caller requests a bounded metric query for known tenant-scoped resources. The selected backend adapter returns normalized time series, which the platform validates, redacts, hashes, and stores as an immutable Evidence artifact.

They do not define OTLP ingestion. OTLP is a push/export protocol, while this contract represents a bounded historical query. An OTLP receiver can later produce the same normalized result shape after separate channel authentication, admission control, and retention decisions.

## Identity and authority

`metadata.tenantId` and `metadata.actorId` are consistency assertions. The authenticated transport identity is authoritative and must match both values before policy or provider execution. `requestId` is caller-generated, contains no source data, and correlates the normalized result with the request.

`requestId` is a correlation identifier, not an idempotency key. Repeating a historical query creates a new immutable Evidence record because the backend may have received late or corrected samples between executions.

The application authorizes `evidence:collect` for the exact tenant, integration, evidence type, and resource UID set. Every resource reference must resolve inside the authenticated tenant before the backend is called. A missing and a cross-tenant resource have the same external failure behavior.

The request carries an integration identifier, never a credential or provider endpoint. A backend adapter resolves endpoint and short-lived credentials from protected integration configuration outside the public contract.

## Query semantics

The first version supports only `signal: metrics`. It intentionally avoids PromQL, vendor query languages, and raw OTLP payloads. The normalized query contains:

- one metric identity;
- at most eight equality or inequality attribute filters, combined with logical AND;
- one aggregation function and fixed step;
- at most four grouping attributes; and
- a closed time range.

Backend adapters must either implement these semantics or fail with a stable provider-unavailable code. They must not silently reinterpret an unsupported function. A future signal type or richer operator requires an additive contract change and conformance tests.

`eq` requires the attribute to equal the supplied value. `neq` requires the attribute to exist and differ from the supplied value; a missing attribute is not a match. This rule avoids backend-specific missing-label behavior.

`timeRange.start` is inclusive and `timeRange.end` is inclusive. Application validation requires `start < end <= requestedAt`, a maximum seven-day range, and a deadline no more than five minutes after `requestedAt`.

## Bounds and output

The caller supplies upper bounds for series, total data points, decoded artifact bytes, and wall-clock execution. Platform maxima still apply even when a caller requests more. Provider output above any requested bound fails closed; the application does not accept an unannounced truncation.

`TelemetryEvidenceResult` is the normalized JSON artifact stored behind an Evidence envelope. It contains:

- the exact request, tenant, and integration correlation fields;
- a SHA-256 digest of the canonical complete request;
- ordered finite numeric points within the requested time range;
- at most 16 bounded string attributes per series;
- explicit `complete`, `partial`, or `no-data` status; and
- stable warnings instead of provider error text.

`complete` requires at least one series and no warnings. `partial` requires at least one stable warning. `no-data` requires zero series, zero points, and no warning. `summary` counts must equal the actual series and point totals.

## Security and privacy

- Query fields, attribute names/values, units, and normalized result attributes are untrusted input and pass structural, control-character, secret-pattern, and size validation.
- Tenant scope never comes from backend labels or OTLP resource attributes.
- Provider endpoints, credentials, raw response bodies, query-language strings, and exception text are absent from both contracts.
- High-cardinality output is bounded by series, point, attribute, byte, and time limits.
- The immutable Evidence envelope records the integration, resource references, content hash, retrieval times, redaction methods, sensitivity, and retention class.

## HTTP binding

`POST /v1/evidence/telemetry/queries` accepts `TelemetryEvidenceRequest` and returns the resulting Evidence envelope with `201 Created`. The normalized result remains in the evidence artifact; it is not copied into the response.

| HTTP status | Stable code | Meaning |
| --- | --- | --- |
| `400` | `telemetry.request.invalid` | Contract, identity assertion, time, query, or bound validation failed. |
| `401` | `authentication.required` / `authentication.invalid` | Bearer authentication failed. |
| `403` | `policy.denied` | The actor cannot collect the scoped evidence. |
| `408` | `evidence.deadline.exceeded` | The bounded collection deadline elapsed. |
| `503` | `evidence.provider.unavailable` / `storage.unavailable` | The backend, validation/redaction boundary, or evidence store failed closed. |

The default local backend deliberately returns normalized `no-data`. It proves the complete authorization, adapter, artifact, storage, HTTP, and SDK boundary without pretending to be a configured customer system. Selecting the Prometheus-compatible reference adapter at composition time enables real range queries through an allowlisted logical metric catalog; see the [operations guide](../operations/prometheus-evidence.md). Other customer backends replace the same application port without changing this contract.
