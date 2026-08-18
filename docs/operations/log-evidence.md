# Log evidence and OTLP logs intake

**Status:** Backend-neutral query, Loki and OpenSearch adapters, and executable OTLP receiver; disabled/no-data by default

The platform supports two deliberately separate log paths:

1. `POST /v1/evidence/logs/queries` performs a bounded historical query through `TelemetryLogsBackend`. The default returns honest no-data; the first live adapter queries Loki, and a second queries OpenSearch, proving the same backend-neutral boundary against two structurally different backends.
2. `POST /v1/logs` accepts selected OTLP/HTTP Protobuf logs through a separately authenticated push channel and commits normalized `telemetry.logs.push` Evidence.

OTLP does not define historical queries. Pointing a Collector at `/v1/logs` does not make the platform a general log store, and changing a customer's historical backend requires a backend adapter selected at composition.

## Historical query profile

`IIP_TELEMETRY_LOGS_BACKEND` defaults to `no-data`. Requests contain logical services, normalized severities, exact attribute filters, known resources, time range, and output bounds. Provider URLs, credentials, index names, and query languages are never public request fields.

The public operation uses the interactive control-plane Bearer identity and returns only an Evidence envelope. Log artifact bytes remain behind evidence authorization. A live adapter resolves protected integration configuration and short-lived credentials through the existing credential-broker boundary.

## Query Loki

Select the adapter and load its non-secret registry:

```bash
export IIP_TELEMETRY_LOGS_BACKEND=loki
export IIP_LOKI_INTEGRATIONS_JSON="$(tr -d '\n' < deploy/loki/integrations.example.json)"
```

Each registry entry belongs to one tenant and integration. It fixes the Loki endpoint, optional organization identifier, timeout and response limit, label bindings, logical service catalog, severity mapping, and an optional logical credential reference. The example maps `resourceUid`, service, severity, trace/span correlation, and two allowlisted attributes. Extend mappings deliberately; arbitrary Loki labels and LogQL are not public inputs.

The adapter calls Loki's [`GET /loki/api/v1/query_range`](https://grafana.com/docs/loki/latest/reference/loki-http-api/) with a generated label selector and nanosecond time bounds. It refuses redirects, limits URL/time/bytes/records, maps only configured values, and fails closed on malformed or cross-resource output. Log bodies are never searched or interpreted by this adapter.

For a locally protected Bearer token, supply a secret document only through `IIP_LOKI_CREDENTIALS_JSON`:

```json
{
  "credentials": [
    {
      "tenantId": "local",
      "integrationId": "loki-local",
      "credentialRef": "credential://local/loki/log-reader",
      "bearerToken": "<high-entropy-provider-token>",
      "expiresAt": "2026-08-17T12:00:00Z"
    }
  ]
}
```

Do not commit a populated document or put it in a ConfigMap. In production, use the shared [external credential broker](credential-broker.md); the adapter requests an exact `loki` / `logs:read` lease and accepts only a bounded Bearer result. Loki itself has no built-in authentication layer, so self-hosted production deployments require an authenticating gateway or reverse proxy in front of the endpoint.

Exercise the real adapter, normalization, and investigation path against an isolated pinned Loki container:

```bash
make test-loki
```

The gate pushes two current records, queries them through the public backend boundary, commits an immutable log Evidence artifact, and verifies that a deterministic investigation cites the supporting evidence. It removes its isolated container and storage on exit.

## Query OpenSearch

Select the adapter and load its non-secret registry:

```bash
export IIP_TELEMETRY_LOGS_BACKEND=opensearch
export IIP_OPENSEARCH_INTEGRATIONS_JSON="$(tr -d '\n' < deploy/opensearch/integrations.example.json)"
```

Each registry entry belongs to one tenant and integration. It fixes the OpenSearch endpoint, one lowercase index (or index pattern), timeout and response limit, document field bindings, logical service catalog, severity mapping, and an optional logical credential reference. The example maps `resourceUid`, service, severity, the ECS-style `@timestamp`/`message` fields, trace/span correlation, and two allowlisted attributes. Extend mappings deliberately; arbitrary OpenSearch fields, aggregations, and query DSL are not public inputs.

The adapter calls OpenSearch's [`POST /{index}/_search`](https://opensearch.org/docs/latest/api-reference/search/) with a generated `bool`/`filter` query and time-range bounds. It refuses redirects, limits URL/body/time/bytes/records, maps only configured values, and fails closed on malformed, timed-out, or cross-resource output. Log bodies are matched exactly, never scored or full-text searched.

For a locally protected Bearer token, supply a secret document only through `IIP_OPENSEARCH_CREDENTIALS_JSON`, matching the same shape as `IIP_LOKI_CREDENTIALS_JSON` above with `credentialRef: "credential://local/opensearch/log-reader"`. Do not commit a populated document or put it in a ConfigMap. In production, use the shared [external credential broker](credential-broker.md); the adapter requests an exact `opensearch` / `logs:read` lease and accepts only a bounded Bearer result. OpenSearch's own security plugin (Basic auth, JWT) is a deployment decision independent of this adapter; self-hosted production deployments that need it require an authenticating gateway or reverse proxy that terminates to Bearer, the same posture already accepted for Loki.

Exercise the real adapter, normalization, and investigation path against an isolated pinned OpenSearch container:

```bash
make test-opensearch
```

The gate indexes two current documents, queries them through the public backend boundary, commits an immutable log Evidence artifact, and verifies that a deterministic investigation cites the supporting evidence. It removes its isolated container and storage on exit.

## Enable OTLP logs locally

Create a random channel token of at least 32 characters. Store only its SHA-256 digest in a protected channel document; [`deploy/otlp/log-receiver-channels.example.json`](../../deploy/otlp/log-receiver-channels.example.json) shows the strict shape with an illustrative, unusable verifier.

```bash
export IIP_OTLP_LOGS_RECEIVER_ENABLED=true
export IIP_OTLP_LOGS_RECEIVER_CHANNELS_JSON="$(tr -d '\n' < deploy/otlp/log-receiver-channels.example.json)"
export OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=https://localhost:4318/v1/logs
export OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_LOGS_HEADERS="Authorization=Bearer%20<channel-token>"
export OTEL_EXPORTER_OTLP_CERTIFICATE=/protected/server-ca/ca.crt
export OTEL_EXPORTER_OTLP_CLIENT_CERTIFICATE=/protected/client/tls.crt
export OTEL_EXPORTER_OTLP_CLIENT_KEY=/protected/client/tls.key
```

The configured resource must already exist in the bound tenant. Each payload must provide an allowlisted string `service.name`; the matching service profile maps only approved attributes. Unknown services and attributes never expand tenant, integration, resource, sensitivity, or retention authority.

## Admission and privacy

Each protected channel fixes request/artifact bytes, record count, mapped attributes, body bytes, record age, future skew, and processing time. Configure smaller values than the platform maxima wherever possible.

String bodies may contain secrets or hostile instructions. They are treated as confidential untrusted text and redacted before hashing/persistence. Do not route unrestricted production logs into this receiver. Use a Collector to filter service pipelines and inject the channel authorization header outside application workloads.

Metrics and logs share a dedicated OTLP intake process, not the control-plane
listener. The production profile requires both a CA-verified SPIFFE client
certificate bound to the exact channel and the independent channel Bearer
credential before body reads. When Helm NetworkPolicy is enabled,
`networkPolicy.otlpReceiverIngress` applies to both signals; restrict it to the
trusted Collector/gateway namespace and pods, and configure database egress.
Use the validated persistent Collector queue in the
[receiver runbook](otlp-metrics-receiver.md) for outage buffering. Distributed
rate enforcement, deletion/data-residency controls, and receiver-specific SLOs
remain customer decisions.

## Helm and Docker verification

For historical Loki queries, set `logEvidence.backend: loki`, put the non-secret registry in `logEvidence.loki.integrationsJson`, and reference a Kubernetes Secret through `logEvidence.loki.credentialsExistingSecret` when static credentials are required. With NetworkPolicy enabled, configure `networkPolicy.lokiEgress` for the exact Loki/gateway namespace, pod labels, and port. Prefer the external credential broker for production leases.

For historical OpenSearch queries, set `logEvidence.backend: opensearch`, put the non-secret registry in `logEvidence.opensearch.integrationsJson`, and reference a Kubernetes Secret through `logEvidence.opensearch.credentialsExistingSecret` when static credentials are required. With NetworkPolicy enabled, configure `networkPolicy.opensearchEgress` for the exact OpenSearch/gateway namespace, pod labels, and port.

For Helm, set `otlpLogsReceiver.enabled: true`, `database.existingSecret`, and place the complete channel JSON in a Kubernetes Secret referenced by `otlpLogsReceiver.channelsExistingSecret`. The chart never writes channel configuration into a ConfigMap or mounts it into the API pod.

The Docker receiver gate builds one image, runs it as separate API and receiver processes against PostgreSQL, and sends both metrics and logs using official OpenTelemetry exporters:

```bash
make test-otlp-receiver
```
