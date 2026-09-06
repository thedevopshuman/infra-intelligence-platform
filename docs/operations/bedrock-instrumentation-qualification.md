# Bedrock instrumentation qualification

**Status:** Executable offline interoperability profile; live profile opt-in

This gate qualifies the official Python botocore instrumentation plus the
separately installed IIP Bedrock usage adapter against IIP's metadata-only
GenAI trace receiver. It adds neither an IIP application SDK nor an inference
proxy. The exact reviewed dependency set is isolated in
`requirements/bedrock-compatibility.txt`; the adapter lives under
`instrumentation/python/aws-bedrock`; neither enters the IIP product image.

The upstream botocore instrumentation is currently published as a beta
package. Keep it pinned, review semantic changes before upgrading, and rerun
this qualification profile for every version change.

## What the default gate proves

```bash
make test-bedrock-instrumentation
```

Docker builds the pinned compatibility image, then runs it with no network,
read-only root files, no Linux capabilities, bounded CPU, memory, PIDs, and
temporary storage. The harness calls the real boto3 Bedrock Runtime `Converse`
and `ConverseStream` methods through `botocore.stub.Stubber`; this exercises
botocore's call boundary and both official OpenTelemetry wrappers without
contacting AWS. The stream fixture is an actual botocore `EventStream` inserted
by a scoped `after-call` handler after Stubber validates the modeled response.

The gate verifies:

- boto3 `1.43.73`, botocore `1.43.73`,
  `opentelemetry-instrumentation-botocore` `0.65b0`, and OpenTelemetry SDK
  `1.44.0`;
- the actual service-specific scope
  `opentelemetry.instrumentation.botocore.bedrock-runtime`;
- normalization of current `gen_ai.system=aws.bedrock` into the provider-neutral
  IIP usage record, including conflict-safe support for the newer
  `gen_ai.provider.name`;
- provider-reported input/output totals, metadata-only span contents, and the
  real IIP protobuf decoder, channel allowlist, ingestion service, and ledger;
- non-zero provider cache-read/cache-write counters, their standard OTel
  attributes, and normalization of Bedrock uncached input into OTel total input;
- complete stream consumption, final metadata usage, and deferred span
  completion for `ConverseStream`; and
- a deterministic failed exporter behind a batch processor does not change the
  successful provider response.

The generated source-bound reports are
`dist/bedrock-instrumentation-offline-report.json` and
`dist/bedrock-converse-stream-instrumentation-offline-report.json`. Their
`qualificationLevel` is `offline-sdk-interoperability`; neither may be
presented as evidence of live AWS behavior.

## Important cost limitation

The pinned official instrumentation does not expose Bedrock cache counters and
copies Bedrock's uncached `inputTokens` into OTel's total-input attribute. The
IIP usage adapter corrects both behaviors for `Converse` and `ConverseStream`:
it publishes cache read/cache creation and calculates OTel total input from the
three provider input meters. It never converts an absent provider meter to
zero.

Bedrock does not expose the provider-neutral reasoning-output subset in this
profile. IIP therefore records the normalized usage as `partial` and does not
claim that the usage fact alone is exact-cost eligible. The cost engine must
leave it unresolved unless the exact selected catalog rates make the missing
split mathematically irrelevant. Engine `0.2.0` represents that case with an
explicit aggregate-output line and rate-equivalence warning; that separate
cost result is not part of this instrumentation qualification.

The synthetic AI FinOps demo uses an explicitly controlled fixture profile to
exercise full cost arithmetic. That proof and this provider-interoperability
proof answer different questions.

## Customer application installation

Install the adapter beside the pinned normal OpenTelemetry Python botocore
instrumentation in the customer application environment, then keep using the
standard launcher:

```bash
python -m pip install ./instrumentation/python/aws-bedrock
opentelemetry-instrument python customer_application.py
```

The package registers an `opentelemetry_pre_instrument` hook so its exact
Bedrock extension loads before ordinary botocore auto-instrumentation. Manual
instrumentation may call `iip_otel_aws_bedrock.install()` before
`BotocoreInstrumentor().instrument(...)`. Do not mix unqualified dependency
versions; this first adapter intentionally pins its private upstream boundary.

## Opt-in live qualification

The lower-level live gate makes one bounded selected operation. It is
deliberately excluded from `make verify` and requires an explicit enable flag,
exact model and region, latency bound, and a dedicated short-lived session
credential file. It mounts only that file, never the host `.aws` directory, and
does not pass credential values in the container environment. `converse` is the
default; select `converse-stream` explicitly for streaming evidence.

```bash
export IIP_BEDROCK_LIVE_TEST_ENABLED=true
export IIP_BEDROCK_MODEL_ID='<approved-model-id>'
export AWS_REGION='<approved-region>'
export IIP_BEDROCK_AWS_CREDENTIALS_FILE='/secure/iip/bedrock-credentials'
export IIP_BEDROCK_CREDENTIALS_PROFILE='iip-bedrock-qualification'
export IIP_BEDROCK_MAXIMUM_PROVIDER_CALL_MILLISECONDS=60000
make test-bedrock-live
# Separately qualify streaming for the same exact model and region:
IIP_BEDROCK_OPERATION=converse-stream make test-bedrock-live
```

Use a temporary identity restricted to `bedrock:InvokeModel` for `Converse` or
`bedrock:InvokeModelWithResponseStream` for `ConverseStream`, scoped to the
exact approved model, as documented by the
[AWS ConverseStream API](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_runtime_ConverseStream.html).
The fixed synthetic request asks for one short response; the
harness never prints or writes the prompt, response, provider request ID,
credentials, trace/span IDs, or token quantities. It records bounded
provider-call latency and writes
`dist/bedrock-instrumentation-live-report.json` or
`dist/bedrock-converse-stream-instrumentation-live-report.json`, which retains
operation, model, and region because compatibility evidence cannot be
generalized across untested profiles.

For a same-invocation customer-flow qualification, the same container can also
export its actual span to a selected OTLP/HTTP trace endpoint:

```bash
export IIP_BEDROCK_OTLP_TRACES_ENDPOINT='https://collector.example/v1/traces'
export IIP_BEDROCK_OTLP_HEADERS_FILE='/secure/iip/bedrock-otlp-headers.json'
export IIP_BEDROCK_OTLP_CA_FILE='/secure/iip/customer-collector-ca.pem'
export IIP_BEDROCK_CORRELATION_BASENAME='bedrock-invocation-correlation.json'
IIP_BEDROCK_OPERATION=converse-stream make test-bedrock-live
```

The headers JSON must be an owner-only mode-`0600` regular file. For mTLS, set
`IIP_BEDROCK_OTLP_CLIENT_CERT_FILE` and
`IIP_BEDROCK_OTLP_CLIENT_KEY_FILE` together. Plain HTTP requires the explicit
`IIP_BEDROCK_OTLP_ALLOW_INSECURE=true` local-test override. Endpoint userinfo,
query strings, fragments, and paths other than `/v1/traces` are rejected.

The correlation file is created once with mode `0600`. It contains trace/span
identity and is therefore protected ephemeral input, not transportable
evidence. Pass its values only in the body of the privileged
`POST /v1/operations/ai-economics/invocation-observations` request, retain the
returned digest, and delete the file after qualification. See the [AI
invocation observation contract](../specifications/ai-invocation-observation-contract.md).

Without the optional exporter, the live gate sends the captured span only
through the real in-process IIP adapter and ingestion service. Enabling export
proves an OTLP client attempted delivery, but only the exact deployed ledger
observation proves downstream acceptance and processing. Neither mode alone
qualifies customer PKI lifecycle, invoice agreement, sustained load, aggregate
export, or dashboard visibility. A streaming live report proves only its exact
selected operation; a non-streaming report cannot be reused.

For customer evidence, use the [customer Bedrock qualification
workflow](customer-bedrock-qualification.md). It adds a protected reviewed
target, exact release-image binding, minimized expiry-bound report, and offline
verifier around this lower-level harness.
