# OTLP metrics and logs receiver

**Status:** Executable mutual-TLS reference receiver; disabled by default

The optional receiver accepts deliberately selected customer metric and log
streams through standard OTLP/HTTP binary Protobuf and records each non-empty
export as immutable, normalized Evidence. It is independent of both outbound
platform telemetry and historical-query adapters.

## Enable locally

Generate a channel token with at least 32 random characters and put only its SHA-256 digest in a protected channel document. `deploy/otlp/receiver-channels.example.json` shows the full shape, but its illustrative verifier is not a usable deployment secret.

```bash
export IIP_OTLP_RECEIVER_ENABLED=true
export IIP_OTLP_RECEIVER_CHANNELS_JSON="$(tr -d '\n' < deploy/otlp/receiver-channels.example.json)"
```

The example resource reference is the deterministic UID of `contracts/examples/resource.json`. The resource must already exist in the configured tenant or the receiver rejects the export without persistence.

The production-shaped receiver requires the shared durable database, a server
keypair, a client CA, an exact SPIFFE-to-channel registry, and runs on its own
listener. Keep all of these values in protected secret-manager or mounted-file
state:

```bash
export IIP_DATABASE_URL=postgresql://...
export IIP_OTLP_TLS_MODE=mutual-spiffe
export IIP_OTLP_TLS_CERTIFICATE_PATH=/protected/server/tls.crt
export IIP_OTLP_TLS_PRIVATE_KEY_PATH=/protected/server/tls.key
export IIP_OTLP_TLS_CLIENT_CA_PATH=/protected/client-ca/ca.crt
export IIP_OTLP_MTLS_IDENTITIES_JSON="$(tr -d '\n' < /protected/client-identities.json)"
PYTHONPATH=src python3 -m iip.surfaces.otlp_receiver
```

Point an OTLP/HTTP exporter at the receiver:

```bash
export OTEL_EXPORTER_OTLP_METRICS_ENDPOINT=https://localhost:4318/v1/metrics
export OTEL_EXPORTER_OTLP_METRICS_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_METRICS_HEADERS="Authorization=Bearer%20<channel-token>"
export OTEL_EXPORTER_OTLP_CERTIFICATE=/protected/server-ca/ca.crt
export OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE=/protected/client/tls.crt
export OTEL_EXPORTER_OTLP_CLIENT_KEY=/protected/client/tls.key
```

Header and TLS environment-variable names vary by SDK. For production, use a
customer Collector that injects the receiver authorization header and sends
only the selected pipelines. [`collector-to-iip.example.yaml`](../../deploy/otel/collector-to-iip.example.yaml)
is validated against the digest-pinned Collector Contrib 0.158.0 image. Its
`file_storage` persistent sending queue retries receiver outages without making
the receiver stateful. Mount `/var/lib/otelcol/iip` on customer-managed durable
storage and alert on queue growth and disk exhaustion; without a persistent
volume the queue does not survive pod replacement.

## Production workload identity

Every OTLP POST in `mutual-spiffe` mode must satisfy two independent checks:

- a client certificate chains to the configured client CA, contains exactly one
  URI SAN, and that SPIFFE ID is mapped to the selected channel; and
- the Bearer token matches the SHA-256 verifier for that tenant-bound channel.

The receiver validates both before reading the request body. Rotating a client
certificate with the same SPIFFE ID preserves its configured authority without
a receiver restart. A different SPIFFE ID or channel needs an explicit protected
registry change. `/healthz` and `/readyz` are server-TLS verified but do not
require a client certificate because they reveal only stable status.

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

For Helm, set `otlpReceiver.enabled: true`, `database.existingSecret`, and
`otlpReceiver.channelsExistingSecret`. Then select
`otlpIngest.tls.mode: mutual-spiffe` and reference existing Secrets through
`serverExistingSecret`, `clientCaExistingSecret`, and
`identitiesExistingSecret`. The identity Secret value follows
[`client-identities.example.json`](../../deploy/otlp/client-identities.example.json).
The chart never puts channel or identity configuration in a ConfigMap or mounts
it into the control-plane pod.

The chart creates a dedicated receiver Deployment and ClusterIP Service on
OTLP/HTTP port `4318`. It exposes no console or control-plane operation, has no
interactive identity configuration or ambient service-account token, and
applies per-channel process admission. If NetworkPolicy is enabled, both
`networkPolicy.databaseEgress` and an exact
`networkPolicy.otlpReceiverIngress` must be configured. Automated CA/channel
rotation, distributed gateway admission, queue sizing, and receiver-specific
SLO objectives remain customer production decisions.

For one-process development compatibility only, set `IIP_OTLP_RECEIVER_MODE=shared` on the control API. Helm deliberately never enables this mode.

## Verification

`make verify` covers Protobuf normalization, channel/control-plane credential
separation, TLS/SPIFFE policy, gzip and post-decompression limits,
tenant/resource binding, redaction, immutable persistence, separate OpenAPI
documents, SDK types, and Helm rendering. The Docker gate sends real exports
from the official Python exporters, tests certificate and credential denial,
queries PostgreSQL, validates the persistent Collector queue configuration, and
writes `dist/otlp-receiver-compatibility-report.json`:

```bash
make test-otlp-receiver
```
