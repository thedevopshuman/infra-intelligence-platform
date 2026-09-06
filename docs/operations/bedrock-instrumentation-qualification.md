# Bedrock instrumentation qualification

**Status:** Executable offline interoperability profile; live profile opt-in

This gate qualifies the official Python botocore instrumentation against IIP's
metadata-only GenAI trace receiver without adding an IIP SDK or proxying the
model request. The exact reviewed dependency set is isolated in
`requirements/bedrock-compatibility.txt` and never enters the IIP product
image.

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

The pinned official instrumentation does not expose cache-read, cache-write,
or reasoning-token subsets. The compatibility profile leaves those fields
missing, so IIP records the usage as `partial` and does not claim exact cost
eligibility. Do not add these fields to `zeroWhenAbsent` unless the chosen
model, operation, and billing behavior prove that absence means zero.

The synthetic AI FinOps demo uses an explicitly controlled fixture profile to
exercise full cost arithmetic. That proof and this upstream interoperability
proof answer different questions.

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

The live gate sends the captured span through the real in-process IIP adapter
and ingestion service. It does not qualify a customer Collector, PKI, receiver
network path, price catalog, invoice agreement, or sustained load. A streaming
live report proves only its exact selected operation; a non-streaming report
cannot be reused. Run `make test-otlp-receiver` separately for isolated
Collector-to-IIP transport evidence.

For customer evidence, use the [customer Bedrock qualification
workflow](customer-bedrock-qualification.md). It adds a protected reviewed
target, exact release-image binding, minimized expiry-bound report, and offline
verifier around this lower-level harness.
