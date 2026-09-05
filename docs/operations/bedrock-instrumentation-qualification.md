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
temporary storage. The harness calls the real boto3 Bedrock Runtime
`Converse` method through `botocore.stub.Stubber`; this exercises botocore's
normal call boundary and the official OpenTelemetry wrapper without contacting
AWS.

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
  and
- a deterministic failed exporter behind a batch processor does not change the
  successful provider response.

The generated source-bound report is
`dist/bedrock-instrumentation-offline-report.json`. Its
`qualificationLevel` is `offline-sdk-interoperability`; it must never be
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

The live gate makes one bounded `Converse` call. It is deliberately excluded
from `make verify` and requires an explicit enable flag, exact model and region,
and short-lived session credentials. The wrapper does not mount an AWS profile
or search the host for credentials.

```bash
export IIP_BEDROCK_LIVE_TEST_ENABLED=true
export IIP_BEDROCK_MODEL_ID='<approved-model-id>'
export AWS_REGION='<approved-region>'
export AWS_ACCESS_KEY_ID='<short-lived-access-key>'
export AWS_SECRET_ACCESS_KEY='<short-lived-secret-key>'
export AWS_SESSION_TOKEN='<short-lived-session-token>'
make test-bedrock-live
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY AWS_SESSION_TOKEN
```

Use a temporary identity restricted to `bedrock:InvokeModel` for the exact
approved model. The fixed synthetic request asks for one short response; the
harness never prints or writes the prompt, response, provider request ID,
credentials, trace/span IDs, or token quantities. It writes
`dist/bedrock-instrumentation-live-report.json`, which retains model and region
because compatibility evidence cannot be generalized across untested
profiles.

The live gate sends the captured span through the real in-process IIP adapter
and ingestion service. It does not qualify a customer Collector, PKI, receiver
network path, streaming API, price catalog, invoice agreement, or sustained
load. Run `make test-otlp-receiver` separately for the isolated Collector-to-IIP
transport evidence. `ConverseStream` requires its own future live profile.
