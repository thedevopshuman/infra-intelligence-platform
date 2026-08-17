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

The production-shaped receiver requires the shared durable database and runs on its own listener:

```bash
export IIP_DATABASE_URL=postgresql://...
PYTHONPATH=src python3 -m iip.surfaces.otlp_receiver
```

Point an OTLP/HTTP exporter at the receiver:

```bash
export OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=http://localhost:4318/v1/metrics
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

The Docker integration starts PostgreSQL, the control API, and the receiver as separate containers. It passes `IIP_OTLP_RECEIVER_ENABLED` and `IIP_OTLP_RECEIVER_CHANNELS_JSON` only to the receiver. Keep the latter in shell/secret-manager state; do not commit a populated document.

For Helm, set `otlpReceiver.enabled: true`, `database.existingSecret`, and `otlpReceiver.channelsExistingSecret`. The secret value must contain the complete JSON document under the configured key. The chart never puts channel configuration in a ConfigMap or mounts it into the control-plane pod.

The chart creates a dedicated receiver Deployment and Service on OTLP/HTTP port `4318`. It exposes no console or control-plane operation, has no interactive identity configuration or ambient service-account token, and applies per-channel process admission from `otlpIngest.maxRequestsPerSecond` and `otlpIngest.requestBurst`. If NetworkPolicy is enabled, both `networkPolicy.databaseEgress` and an exact `networkPolicy.otlpReceiverIngress` must be configured. Federated workload identity or mTLS, automated channel rotation, distributed gateway admission, and durable buffering remain production deployment decisions.

For one-process development compatibility only, set `IIP_OTLP_RECEIVER_MODE=shared` on the control API. Helm deliberately never enables this mode.

## Verification

`make verify` covers Protobuf normalization, channel/control-plane credential separation, gzip and post-decompression limits, tenant/resource binding, redaction, immutable persistence, separate OpenAPI documents, SDK types, and Helm rendering. The Docker gate sends real exports from the official Python exporters into the isolated receiver and verifies route separation:

```bash
make test-otlp-receiver
```
