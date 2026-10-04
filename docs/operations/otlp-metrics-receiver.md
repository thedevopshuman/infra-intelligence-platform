# OTLP metrics and logs receiver

**Status:** Executable mutual-TLS reference receiver; disabled by default

The optional receiver accepts deliberately selected customer metric and log
streams through standard OTLP/HTTP binary Protobuf and records each non-empty
export as immutable, normalized Evidence. It is independent of both outbound
platform telemetry and historical-query adapters.

The same isolated process can expose metadata-only GenAI trace intake. That
signal has a different protected channel catalog and persistence contract; see
the [AI usage receiver runbook](ai-usage-receiver.md).

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
export IIP_DATABASE_TRANSPORT_MODE=verify-full
export IIP_DATABASE_CA_PATH=/protected/database-ca/ca.crt
export IIP_OTLP_TLS_MODE=mutual-spiffe
export IIP_OTLP_TLS_CERTIFICATE_PATH=/protected/server/tls.crt
export IIP_OTLP_TLS_PRIVATE_KEY_PATH=/protected/server/tls.key
export IIP_OTLP_TLS_CLIENT_CA_PATH=/protected/client-ca/ca.crt
export IIP_OTLP_MTLS_IDENTITIES_JSON="$(tr -d '\n' < /protected/client-identities.json)"
# Optional: reject a client certificate whose serial number appears on this
# CA-signed CRL, even though its chain and validity window both still check
# out (ADR 0088). Absent, the receiver checks only chain and validity window.
export IIP_OTLP_TLS_CLIENT_CRL_PATH=/protected/client-ca/ca.crl
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

- a client certificate chains to the configured client CA bundle, contains
  exactly one URI SAN, and that SPIFFE ID is mapped to the selected channel;
  the client must present every required intermediate certificate; and
- the Bearer token matches the SHA-256 verifier for that tenant-bound channel.

The receiver validates both before reading the request body. Rotating a client
certificate with the same SPIFFE ID preserves its configured authority without
a receiver restart. A different SPIFFE ID or channel needs an explicit protected
registry change. `/healthz` and `/readyz` are server-TLS verified but do not
require a client certificate because they reveal only stable status.

When `IIP_OTLP_TLS_CLIENT_CRL_PATH` is configured, the TLS handshake itself
additionally fails closed for any presented certificate whose serial number
appears on that CRL, before either of the two checks above runs. This lets a
deployment revoke a workload identity immediately, without waiting for its
certificate's `notAfter` or updating the SPIFFE identity registry. The
receiver accepts exactly one PEM CRL up to 1 MiB. Its `lastUpdate` may be at
most equal to the receiver clock and its `nextUpdate` must be in the future. An
invalid issuer/signature or already expired CRL rejects startup; reaching
`nextUpdate` later removes the receiver from readiness and rejects all enabled
metrics, logs, and AI usage trace intake before body read. Project a current
CRL and roll the receiver before
expiry because OpenSSL and the application intentionally load the same bytes
once at startup. The client CA bundle must include the CRL issuer certificate
as well as the intended trust anchor so startup can validate the CRL signature
and CRL-signing authority. Distribution remains the customer's responsibility
([ADRs 0088](../decisions/0088-otlp-receiver-revoked-certificate-rejection-evidence.md)
and [0089](../decisions/0089-fail-closed-otlp-client-crl-freshness.md)). The
local gate now exercises a root/intermediate/leaf hierarchy and an
intermediate-signed CRL ([ADR 0090](../decisions/0090-intermediate-ca-and-otlp-crl-rollout-evidence.md)).

## CRL rotation runbook

Treat the CRL as a versioned deployment input, not as a file that the running
receiver hot-reloads:

1. Obtain the next CRL through the customer PKI distribution path. Validate
   that its issuer certificate is present in the configured client CA bundle,
   `lastUpdate` is not in the future, `nextUpdate` leaves enough time for a
   failed rollout and rollback, and the expected serials are present without
   logging them.
2. Publish it through the cluster secret-management workflow under a new,
   immutable Secret name such as `iip-otlp-client-crl-20260905`. Never put CRL
   or certificate material in Helm values or Git.
3. Run an atomic, waiting Helm upgrade that changes only
   `otlpIngest.tls.clientCrlExistingSecret` to the new name. The pod-template
   change rolls the receiver; an invalid or stale CRL keeps the replacement
   pod unready.
4. Wait for Deployment rollout completion, then verify `/readyz` through
   server-authenticated TLS, a permitted Collector export, and denial of a
   deliberately revoked non-production compatibility identity.
5. Retain the previous Secret through the declared rollback window, then
   remove it through the customer's secret-retention process.

If an operator updates the contents of the existing Secret name instead, an
explicit receiver Deployment restart and rollout wait are mandatory. A
projected-volume update alone does not replace the process-local SSL context.
Schedule rotation with overlap before `nextUpdate`; do not wait for readiness
to fail. The production profile supplies a zero-unavailable rollout, receiver
disruption budget, and hard topology spread. Qualify actual eviction, node
loss, Collector retry behavior, and rollback in the customer environment.

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

For Helm, set `otlpReceiver.enabled: true`, `database.existingSecret`,
`database.transportSecurity.mode: verify-full`, the exact
`database.transportSecurity.caExistingSecret`, and
`otlpReceiver.channelsExistingSecret`. Then select
`otlpIngest.tls.mode: mutual-spiffe` and reference existing Secrets through
`serverExistingSecret`, `clientCaExistingSecret`, and
`identitiesExistingSecret`. The identity Secret value follows
[`client-identities.example.json`](../../deploy/otlp/client-identities.example.json).
Optionally set `clientCrlExistingSecret`/`clientCrlSecretKey` to enable CRL
checking and freshness enforcement (ADRs 0088–0090); it may reference the same
Secret as `clientCaExistingSecret` or a separate one, and is otherwise left
empty. Prefer a separate version-named immutable CRL Secret so changing the
reference creates an observable receiver rollout. The chart never puts
channel or identity configuration in a ConfigMap or mounts it into the
control-plane pod.

The chart creates a dedicated receiver Deployment and ClusterIP Service on
OTLP/HTTP port `4318`. It exposes no console or control-plane operation, has no
interactive identity configuration or ambient service-account token, and
applies per-channel process admission. On `SIGTERM` it stops accepting new
requests and joins active handlers before closing runtime state; a bounded
pre-stop delay lets Service endpoint removal propagate. Size
`otlpIngest.terminationGracePeriodSeconds` above that delay and the longest
qualified request. If NetworkPolicy is enabled, both
`networkPolicy.databaseEgress` and an exact
`networkPolicy.otlpReceiverIngress` must be configured. The chart supports the
versioned-Secret receiver rollout fixed by ADR 0090; automating CRL publication,
CA/channel rotation, distributed gateway admission, and queue sizing remains a
customer production decision.

When `telemetry.metricsEnabled` is true, the receiver exports privacy-bounded
request availability and duration through the same customer-selected outbound
OTLP boundary as the API and worker. Configure `telemetry.otlpEndpoint`,
`telemetry.receiverServiceName`, optional protected `telemetry.existingSecret`,
`otlpIngest.availabilitySlo`, and `networkPolicy.otlpEgress`. The endpoint must
be a customer Collector or compatible backend and must not point back to this
IIP intake listener, which would create a telemetry loop. The deployment health
operation then reports `otlp-receiver` exporter heartbeats. TLS handshake
failures still require Collector/ingress or synthetic telemetry because no HTTP
handler exists for a rejected handshake.

For one-process development compatibility only, set `IIP_OTLP_RECEIVER_MODE=shared` on the control API. Helm deliberately never enables this mode.

## Verification

`make verify` covers Protobuf normalization, channel/control-plane credential
separation, TLS/SPIFFE policy, gzip and post-decompression limits,
tenant/resource binding, redaction, immutable persistence, separate OpenAPI
documents, SDK types, and Helm rendering. The Docker gate sends real metrics,
logs, and GenAI trace exports from the official Python exporters through an
intermediate-issued client chain,
tests certificate and credential denial, atomically rotates the CRL and
recreates the receiver, proves newly revoked and unaffected identities, queries
PostgreSQL, validates the persistent Collector queue configuration, and writes
`dist/otlp-receiver-compatibility-report.json`:

```bash
make test-otlp-receiver
```
