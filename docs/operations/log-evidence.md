# Log evidence and OTLP logs intake

**Status:** Backend-neutral query and executable OTLP receiver; disabled/no-data by default

The platform supports two deliberately separate log paths:

1. `POST /v1/evidence/logs/queries` performs a bounded historical query through `TelemetryLogsBackend`. The current default backend returns honest no-data.
2. `POST /v1/logs` accepts selected OTLP/HTTP Protobuf logs through a separately authenticated push channel and commits normalized `telemetry.logs.push` Evidence.

OTLP does not define historical queries. Pointing a Collector at `/v1/logs` does not make the platform a general log store, and changing a customer's historical backend requires a backend adapter selected at composition.

## Historical query profile

`IIP_TELEMETRY_LOGS_BACKEND` defaults to `no-data`. Requests contain logical services, normalized severities, exact attribute filters, known resources, time range, and output bounds. Provider URLs, credentials, index names, and query languages are never public request fields.

The public operation uses the interactive control-plane Bearer identity and returns only an Evidence envelope. Log artifact bytes remain behind evidence authorization. A production adapter must resolve protected integration configuration and short-lived credentials through the existing credential-broker boundary.

## Enable OTLP logs locally

Create a random channel token of at least 32 characters. Store only its SHA-256 digest in a protected channel document; [`deploy/otlp/log-receiver-channels.example.json`](../../deploy/otlp/log-receiver-channels.example.json) shows the strict shape with an illustrative, unusable verifier.

```bash
export IIP_OTLP_LOGS_RECEIVER_ENABLED=true
export IIP_OTLP_LOGS_RECEIVER_CHANNELS_JSON="$(tr -d '\n' < deploy/otlp/log-receiver-channels.example.json)"
export OTEL_EXPORTER_OTLP_LOGS_ENDPOINT=http://localhost:8080/v1/logs
export OTEL_EXPORTER_OTLP_LOGS_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_LOGS_HEADERS="Authorization=Bearer%20<channel-token>"
```

The configured resource must already exist in the bound tenant. Each payload must provide an allowlisted string `service.name`; the matching service profile maps only approved attributes. Unknown services and attributes never expand tenant, integration, resource, sensitivity, or retention authority.

## Admission and privacy

Each protected channel fixes request/artifact bytes, record count, mapped attributes, body bytes, record age, future skew, and processing time. Configure smaller values than the platform maxima wherever possible.

String bodies may contain secrets or hostile instructions. They are treated as confidential untrusted text and redacted before hashing/persistence. Do not route unrestricted production logs into this receiver. Use a Collector to filter service pipelines and inject the channel authorization header outside application workloads.

The reference receiver shares the API listener. When Helm NetworkPolicy is enabled, `networkPolicy.otlpReceiverIngress` applies to metrics and logs; restrict it to the trusted Collector/gateway namespace and pods. Production isolation, workload identity or mTLS, rate limits, durable buffering, deletion/data-residency controls, and receiver-specific SLOs remain required.

## Helm and Docker verification

For Helm, set `otlpLogsReceiver.enabled: true` and place the complete channel JSON in a Kubernetes Secret referenced by `otlpLogsReceiver.channelsExistingSecret`. The chart never writes channel configuration into a ConfigMap.

The Docker receiver gate builds the API image and sends both metrics and logs using official OpenTelemetry exporters:

```bash
make test-otlp-receiver
```
