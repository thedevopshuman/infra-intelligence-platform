# AI usage OTLP trace receiver

**Status:** Executable metadata-only reference intake; disabled by default
**Provider qualification:** Bedrock-shaped deterministic fixture; live AWS qualification pending

The AI usage receiver accepts selected OpenTelemetry GenAI client spans at
`POST /v1/traces` and commits one immutable `AiUsageRecord` plus one
`io.iip.ai.usage-recorded.v1` CloudEvent. It shares the isolated receiver
process, mutual-TLS/SPIFFE identity boundary, independent channel Bearer
credential, rate limiter, availability telemetry, Collector queue pattern, and
PostgreSQL readiness gate used by metrics and logs.

IIP is not an inference proxy. Customer applications send telemetry
asynchronously to their Collector. A stopped or rejecting IIP receiver must
not make a Bedrock request fail; that property belongs to the application's
non-blocking telemetry setup and the Collector's bounded retry/queue policy.

## Privacy boundary

The receiver persists only normalized invocation metadata. It rejects the
entire export when a span contains:

- prompt, completion, input/output message, system instruction, tool argument
  or tool-call attributes;
- embedding or retrieved-document attributes;
- request/response body attributes;
- authorization, password, secret, API-key, access-key, private-key or similar
  credential attributes;
- any span event or span link; or
- any OTLP dropped-attribute, dropped-event, or dropped-link count, because the
  receiver cannot prove what disappeared before admission.

Unknown attributes with non-prohibited names are discarded and counted in
`privacy.droppedAttributeCount`. AWS request IDs are SHA-256 hashed before the
application boundary. Status descriptions, span names, raw protobuf bytes,
and unknown attribute values are never persisted.

## Protected channel configuration

[`ai-usage-receiver-channels.example.json`](../../deploy/otlp/ai-usage-receiver-channels.example.json)
shows the closed configuration shape. The committed catalog is deliberately
non-production and its token verifier is illustrative.

Each channel fixes:

- tenant, integration, provider, semantic-convention version, and allowed
  instrumentation scopes;
- OTLP `service.name` to reviewed service/namespace/environment attribution;
- allowed model, operation, and region values;
- the five token-meter attribute names and which missing breakdown fields may
  be explicitly interpreted as zero;
- service tier, routing mode, and purchase mode from protected configuration,
  never user-controlled span labels; and
- request, span, attribute, age, clock-skew, and processing bounds.

Generate an opaque token of at least 32 characters. Store only
`sha256:<lowercase-hex>` in the protected channel JSON and deliver the raw
token only to the Collector's secret store. Do not commit a populated
configuration or token.

The current reference profile expects these semantic attributes on a client
span:

| Fact | Attribute |
| --- | --- |
| Provider | `gen_ai.provider.name` |
| Operation | `gen_ai.operation.name` |
| Requested model | `gen_ai.request.model` |
| Response model | `gen_ai.response.model` (optional) |
| Input usage | channel-configured; example `gen_ai.usage.input_tokens` |
| Output usage | channel-configured; example `gen_ai.usage.output_tokens` |
| Region | `cloud.region` on the resource, scope, or span |
| Service | `service.name` on the resource |
| Error | `error.type` (required for error status, otherwise normalized to `unknown`) |
| Provider request | `aws.request_id` (optional and hashed) |
| Retries | `aws.retry_count` (optional non-negative integer) |

Only `CLIENT` spans are eligible. At least one of input/output tokens must be
present. A complete record has both totals and all configured breakdowns; the
example explicitly zero-fills absent cache/reasoning breakdowns because that
instrumentation profile defines absence as zero. A profile that cannot make
that guarantee must remove those fields from `zeroWhenAbsent`, producing
partial usage and preventing an unsupported exact cost.

## Run the isolated process

Apply all packaged migrations through `0019_ai_cost_ledger.sql`, then configure
the receiver. The usage ledger itself is introduced by
`0018_ai_usage_ledger.sql`; the later migration adds the separately operated
price and cost ledgers:

```bash
export IIP_DATABASE_URL=postgresql://...
export IIP_AI_USAGE_RECEIVER_ENABLED=true
export IIP_AI_USAGE_RECEIVER_CHANNELS_JSON="$(tr -d '\n' < /protected/ai-usage-channels.json)"
export IIP_OTLP_TLS_MODE=mutual-spiffe
export IIP_OTLP_TLS_CERTIFICATE_PATH=/protected/server/tls.crt
export IIP_OTLP_TLS_PRIVATE_KEY_PATH=/protected/server/tls.key
export IIP_OTLP_TLS_CLIENT_CA_PATH=/protected/client-ca/ca.crt
export IIP_OTLP_MTLS_IDENTITIES_JSON="$(tr -d '\n' < /protected/client-identities.json)"
PYTHONPATH=src python3 -m iip.surfaces.otlp_receiver
```

The SPIFFE registry entry must include the AI channel ID. Point the customer
Collector's traces exporter at `https://<receiver>:4318/v1/traces` and inject
the separate Bearer credential there. The validated
[`collector-to-iip.example.yaml`](../../deploy/otel/collector-to-iip.example.yaml)
includes a persistent queue and a traces pipeline. Retain 100% of eligible
metadata spans before IIP intake for accounting; IIP reports observed usage
and never extrapolates sampled spans into invoice cost.

## Helm

Set:

```yaml
database:
  existingSecret: iip-database
aiUsageReceiver:
  enabled: true
  channelsExistingSecret: iip-ai-usage-channels
otlpIngest:
  tls:
    mode: mutual-spiffe
    serverExistingSecret: iip-otlp-server-tls
    clientCaExistingSecret: iip-otlp-client-ca
    identitiesExistingSecret: iip-otlp-identities
networkPolicy:
  enabled: true
  databaseEgress:
    enabled: true
  otlpReceiverIngress:
    enabled: true
```

The chart reads the channel document from Secret key
`ai-usage-receiver-channels-json` by default. It does not expose the trace
route through the control-plane Service and does not mount interactive API
credentials or a Kubernetes service-account token into the receiver.

## Delivery and duplicate semantics

The canonical identity combines tenant, channel, trace ID, span ID, provider,
operation, and requested model. Retrying an unchanged span returns the first
stored record and creates no second event or outbox message. Reusing that
identity with changed usage returns `409` with `otlp.usage.conflict`.

HTTP `200` is sent only after the usage row, CloudEvent, and outbox row commit
in one PostgreSQL transaction. Storage failure returns `503`; malformed,
content-bearing, or unallowlisted input fails closed without persistence.

## Verification

`make verify PYTHON=.venv/bin/python` covers official protobuf decoding,
metadata allowlists, content rejection, tenant binding, hashing, completeness,
schema-valid output, duplicate/conflict behavior, HTTP errors, OpenAPI, and
Helm rendering. With Docker Desktop running:

```bash
make test-otlp-receiver PYTHON=.venv/bin/python
```

The Docker profile also starts the tenant-explicit workflow worker with the
test-only price catalog, then proves the committed usage is asynchronously
converted into one linked priced cost fact and one atomic cost event/outbox
record. The receiver still returns after its own PostgreSQL commit and never
waits on cost calculation.

The Docker gate sends metrics, logs, and a GenAI trace through official Python
OTLP exporters over the existing intermediate-CA and CRL-tested receiver, then
queries PostgreSQL for the committed metadata-only usage record. A real
Bedrock `Converse`/`ConverseStream` auto-instrumentation qualification remains
an explicit Phase A exit item.
