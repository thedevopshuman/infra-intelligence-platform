# AI usage OTLP trace receiver

**Status:** Executable metadata-only reference intake; disabled by default
**Provider qualification:** Bedrock/OpenAI deterministic fixture and pinned offline SDK profiles; live-provider qualification pending

The AI usage receiver accepts selected OpenTelemetry GenAI client spans at
`POST /v1/traces` and commits one immutable `AiUsageRecord` plus one
`io.iip.ai.usage-recorded.v1` CloudEvent. It shares the isolated receiver
process, mutual-TLS/SPIFFE identity boundary, independent channel Bearer
credential, rate limiter, availability telemetry, Collector queue pattern, and
PostgreSQL readiness gate used by metrics and logs.

IIP is not an inference proxy. Customer applications send telemetry
asynchronously to their Collector. A stopped or rejecting IIP receiver must
not make a provider request fail; that property belongs to the application's
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
`privacy.droppedAttributeCount`. Channel-mapped provider request IDs are
SHA-256 hashed before the application boundary. Status descriptions, span
names, raw protobuf bytes, and unknown attribute values are never persisted.

## Protected channel configuration

[`ai-usage-receiver-channels.example.json`](../../deploy/otlp/ai-usage-receiver-channels.example.json)
shows the closed configuration shape. The committed catalog is deliberately
non-production and its token verifier is illustrative.

Each channel fixes:

- tenant, integration, provider, semantic-convention version, and allowed
  instrumentation scopes;
- OTLP `service.name` to reviewed service/namespace/environment attribution;
- allowed model, operation, and region values;
- provider request-ID and retry-count attribute names plus the reviewed choice
  of whether absent retry count means zero;
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
| Provider | `gen_ai.provider.name`, or shipped botocore alias `gen_ai.system`; equal dual publication is accepted and conflicts are rejected |
| Operation | `gen_ai.operation.name` |
| Requested model | `gen_ai.request.model` |
| Response model | `gen_ai.response.model` (optional) |
| Input usage | channel-configured; example `gen_ai.usage.input_tokens` |
| Output usage | channel-configured; example `gen_ai.usage.output_tokens` |
| Cache-read input | channel-configured; example `gen_ai.usage.cache_read.input_tokens` |
| Cache-write input | channel-configured; example `gen_ai.usage.cache_creation.input_tokens` |
| Reasoning output | channel-configured; example `gen_ai.usage.reasoning.output_tokens` |
| Region | `cloud.region` on the resource, scope, or span |
| Service | `service.name` on the resource |
| Error | `error.type` (required for error status, otherwise normalized to `unknown`) |
| Provider request | channel-configured; example `aws.request_id` (optional and hashed) |
| Retries | channel-configured; example `aws.retry_count` (optional integer from 0 through 100) |

`invocationAttributes.attributes` accepts only the canonical keys `requestId`
and `retryCount`, mapped to exact provider attributes. An empty mapping keeps
both facts absent. `zeroWhenAbsent` may contain only mapped `retryCount`; use it
only after qualifying that the instrumentation's absence means no retry. This
keeps provider-specific names out of the application and prevents unknown
retry evidence from becoming a false zero.

Official Python botocore `0.65b0` uses the service-specific instrumentation
scope `opentelemetry.instrumentation.botocore.bedrock-runtime`; protected
channel configuration must allowlist that exact scope. The generic scope in
older fixtures remains supported only when explicitly enrolled.

Only `CLIENT` spans are eligible. At least one of input/output tokens must be
present. A complete record has both totals and all configured breakdowns; the
example explicitly zero-fills absent cache/reasoning breakdowns because that
instrumentation profile defines absence as zero. A profile that cannot make
that guarantee must remove those fields from `zeroWhenAbsent`, producing
partial usage and preventing an unsupported exact cost.

The pinned official botocore profile alone does not report cache-read,
cache-write, or reasoning subsets and maps Bedrock's uncached input counter to
OTel total input. The separately installed Bedrock usage adapter publishes
reported cache meters and corrects total input for both `Converse` operations.
It leaves absent cache values and reasoning missing, so its qualification uses
an empty `zeroWhenAbsent` list and remains partial. See the [Bedrock
instrumentation qualification](bedrock-instrumentation-qualification.md).

The pinned official OpenAI chat-completions profile also leaves unreported
cache-write and reasoning meters missing and remains partial. Its deployment
supplies `cloud.region=global` rather than inferring geography from an endpoint.
See the [OpenAI instrumentation qualification](openai-instrumentation-qualification.md).

## Run the isolated process

Apply all packaged migrations through `0024_ai_invocation_correlation.sql`, then
configure the receiver. The usage ledger itself is introduced by
`0018_ai_usage_ledger.sql`; migrations `0019` through `0021` add the separately
operated price/cost, savings, and attribution ledgers, while `0022` enables the
source-bound retry finding:

```bash
export IIP_DATABASE_URL=postgresql://...
export IIP_DATABASE_TRANSPORT_MODE=verify-full
export IIP_DATABASE_CA_PATH=/protected/database-ca/ca.crt
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
  transportSecurity:
    mode: verify-full
    caExistingSecret: iip-database-ca
    caKey: ca.crt
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
metadata allowlists, provider-neutral invocation mapping, content rejection,
tenant binding, hashing, completeness,
schema-valid output, duplicate/conflict behavior, HTTP errors, OpenAPI, and
Helm rendering. With Docker Desktop running:

```bash
make test-otlp-receiver PYTHON=.venv/bin/python
make test-bedrock-instrumentation
make test-openai-instrumentation
```

The Docker profile also starts the tenant-explicit workflow worker with the
test-only price catalog, then proves the committed usage is asynchronously
converted into one linked priced cost fact and one atomic cost event/outbox
record. The receiver still returns after its own PostgreSQL commit and never
waits on cost calculation.

The Docker gate sends metrics, logs, and a GenAI trace through official Python
OTLP exporters over the existing intermediate-CA and CRL-tested receiver, then
queries PostgreSQL for the committed metadata-only usage record. Offline
exact-profile qualification covers botocore `Converse`, botocore
`ConverseStream` through complete event consumption, and OpenAI Python
`chat.completions.create`. Live Bedrock/OpenAI model, region, operation, and
streaming behavior remain explicit provider qualification work.
