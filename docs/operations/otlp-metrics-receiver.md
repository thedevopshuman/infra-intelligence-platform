# OTLP metrics receiver

**Status:** Executable reference receiver; disabled by default

The optional receiver accepts a deliberately selected customer metric stream through standard OTLP/HTTP binary Protobuf and records each non-empty export as immutable, normalized Evidence. It is independent of both outbound platform telemetry and the Prometheus historical-query adapter.

## Enable locally

Generate a channel token with at least 32 random characters and put only its SHA-256 digest in a protected channel document. `deploy/otlp/receiver-channels.example.json` shows the full shape, but its illustrative verifier is not a usable deployment secret.

```bash
export IIP_OTLP_RECEIVER_ENABLED=true
export IIP_OTLP_RECEIVER_CHANNELS_JSON="$(tr -d '\n' < deploy/otlp/receiver-channels.example.json)"
```

The example resource reference is the deterministic UID of `contracts/examples/resource.json`. The resource must already exist in the configured tenant or the receiver rejects the export without persistence.

Point an OTLP/HTTP exporter at the API:

```bash
export OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://localhost:8080/v1/metrics
export OTEL_EXPORTER_OTLP_METRICS_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_METRICS_HEADERS="Authorization=Bearer%20<channel-token>"
```

Header environment-variable escaping varies by SDK. For production, prefer a customer Collector that injects the receiver authorization header and sends only the selected metric pipeline.

## Protected channel configuration

Each channel entry requires:

- `tokenSha256`, never a raw token;
- fixed `tenantId`, `integrationId`, and existing tenant-scoped `resourceRefs`;
- an exact metric catalog mapping OTLP names to logical metric names and units;
- a per-metric attribute mapping that is also the dimension allowlist;
- request/artifact byte, series, point, attribute, age, skew, and processing budgets; and
- sensitivity and retention classes applied to the Evidence envelope.

The current platform maxima are 16 MiB for request and normalized artifact, 100 series, 10,000 total points, 16 attributes per OTLP layer, seven days of sample age, five minutes of future clock skew, and 60 seconds of processing. Configure materially smaller channel budgets for real workloads. Secret-shaped values in mapped attributes are rejected before persistence; metric attributes are not a credential transport.

The payload cannot change its configured scope. Attributes such as `tenant.id`, `service.namespace`, or a custom resource UID are ordinary untrusted dimensions and are discarded unless mapped; even when mapped they never become authority.

## Supported profile

The endpoint is `POST /v1/metrics`, with `Content-Type: application/x-protobuf`. `identity` and `gzip` are supported. Gauges and numeric sums are accepted. Sums must declare delta or cumulative temporality. Histograms, exponential histograms, summaries, exemplars, flagged no-value points, complex mapped attribute values, unknown metrics/units, and ambiguous attributes fail the entire export.

Successful non-empty exports produce an `OtlpMetricsEvidence` JSON artifact behind an `Evidence` envelope. Empty valid requests return success but create no artifact. The receiver does not offer arbitrary telemetry storage or queries.

## Docker Compose and Helm

Compose passes `IIP_OTLP_RECEIVER_ENABLED` and `IIP_OTLP_RECEIVER_CHANNELS_JSON` to the API. Keep the latter in shell/secret-manager state; do not commit a populated document.

For Helm, set `otlpReceiver.enabled: true` and reference a Kubernetes Secret through `otlpReceiver.channelsExistingSecret`. The secret value must contain the complete JSON document under the configured key. The chart never puts channel configuration in a ConfigMap.

The reference receiver shares the API's HTTP port. If NetworkPolicy is enabled, restrict inbound access to the namespace hosting the trusted Collector/gateway. A production managed deployment should normally give this route a dedicated gateway policy and rate limits; a dedicated listener/process is a future isolation option.

## Verification

`make verify` covers Protobuf normalization, channel/control-plane credential separation, gzip and post-decompression limits, tenant/resource binding, redaction, immutable persistence, schemas, OpenAPI, SDK types, and Helm rendering. The Docker gate sends a real OTLP export from the official Python exporter into the built API image:

```bash
make test-otlp-receiver
```
