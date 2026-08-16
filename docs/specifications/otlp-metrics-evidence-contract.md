# OTLP metrics evidence contract

**Status:** v1alpha1

**Machine contract:** `contracts/schemas/otlp-metrics-evidence.schema.json`

`OtlpMetricsEvidence` is the normalized JSON artifact produced when an authenticated receiver channel accepts a bounded OTLP metrics export. The immutable public `Evidence` envelope carries its storage reference, digest, resource references, handling policy, and provenance. This contract is the stored result; it is not the OTLP wire request or response.

## Authority and tenancy

The protected receiver profile binds one channel credential to exactly one tenant, integration, resource set, metric catalog, attribute catalog, admission budget, sensitivity, and retention class. Those values are authoritative. OTLP resource, scope, metric, and data-point attributes are untrusted observations and cannot select or override a tenant, integration, channel, resource reference, credential, or retention policy.

Channel credentials use independent Bearer authentication and are not control-plane user credentials. Runtime configuration stores only a SHA-256 token verifier. Raw tokens, verifiers, endpoints, provider error text, and unallowlisted attributes never enter this artifact.

Every configured resource reference must resolve inside the bound tenant, and the existing `evidence:collect` policy must allow the exact scope before persistence. Missing and cross-tenant resources fail closed without storing any part of the batch.

## Normalization

The first receiver profile accepts OTLP/HTTP binary Protobuf metrics at `POST /v1/metrics`. It supports `identity` and `gzip` content encoding and implements gauges and numeric sums. Sums retain their delta/cumulative temporality and monotonic flag. Histograms, exponential histograms, summaries, exemplars, no-recorded-value flags, unspecified sum temporality, metric metadata, and complex mapped attribute values fail the entire request; the receiver does not silently reinterpret them.

Every incoming metric name and unit must match a protected catalog entry. The entry maps the OTLP name to the backend-neutral `metric` name and independently allowlists resource, instrumentation-scope, and point attributes. Credential-shaped source or logical attribute names are prohibited. Unallowlisted attributes are intentionally omitted. A logical attribute observed with conflicting values at multiple OTLP layers is ambiguous and fails the request.

Numeric timestamps are normalized to UTC RFC 3339 at microsecond precision. Data points must be strictly ascending within a series after normalization. Distinct nanosecond samples that collapse to the same microsecond therefore fail instead of overwriting one another.

## Bounds and atomicity

Protected per-channel limits bound encoded and post-decompression request bytes, normalized artifact bytes, series, total data points, attributes per layer, sample age, accepted future clock skew, and processing time. Platform maxima cap every configured value. The whole request is either accepted as one Evidence artifact or rejected; this version does not return OTLP partial success.

An empty valid `ExportMetricsServiceRequest` succeeds as an OTLP no-op and creates no Evidence. A non-empty artifact contains at least one series and point. `timeRange` is the minimum and maximum accepted sample timestamp. `summary.metricCount`, `seriesCount`, and `dataPointCount` must equal the actual contents.

## Redaction and retention

Secret-shaped mapped attribute values fail before persistence so redaction cannot collapse distinct metric series into the same stored identity. The remaining normalized JSON still passes through the same structured-text inspection, redaction, hashing, and immutable commit path as pulled evidence. The Evidence envelope uses type `telemetry.metrics.push`, provider `otlp-receiver`, an opaque channel locator, and the channel-configured sensitivity and retention class.

The receiver is evidence intake, not a general telemetry database. It does not provide historical query execution, aggregation, downsampling, or arbitrary-cardinality retention. The separate `TelemetryEvidenceRequest` and `TelemetryMetricsBackend` boundary remains the historical query path.

## HTTP binding

The wire behavior follows the [OTLP 1.11.0 specification](https://opentelemetry.io/docs/specs/otlp/) for the implemented binary Protobuf profile:

- request path `/v1/metrics` and media type `application/x-protobuf`;
- `HTTP 200` with an empty serialized `ExportMetricsServiceResponse` on full success;
- `HTTP 200` and no persistence for an empty export; and
- `application/x-protobuf` `google.rpc.Status` bodies with stable, non-sensitive messages for `4xx` and `5xx` failures.

The receiver returns `401` for channel authentication failure, `400` for invalid or unsupported payload semantics, `413` for byte-limit violations, `415` for unsupported media/content encoding, `403` for policy denial, and retryable `503` with `Retry-After` for an unavailable receiver/storage boundary. When disabled, the route returns `404` and stores nothing.
