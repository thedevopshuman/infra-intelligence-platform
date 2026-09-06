# Bedrock instrumentation compatibility report

**Status:** v1alpha1 operational evidence

`BedrockInstrumentationCompatibilityReport` binds a compatibility claim to one
IIP source revision, one exact Python dependency set, one Bedrock operation,
one model and region, and either an offline botocore stub or a live AWS call.
`compatible` means all ten `Converse` checks or all eleven `ConverseStream`
checks passed for that exact qualification level; it does not silently promote
offline evidence into live-provider proof or non-streaming evidence into
streaming proof.

The report contains no credential, prompt, response, trace/span ID, provider
request ID, tenant ID, token quantity, cost, or endpoint. Model and region are
retained because interoperability cannot be generalized across untested
Bedrock profiles.

The result also retains provider-call latency in milliseconds. The live
customer wrapper compares it with the reviewed target objective; timeout and
retry configuration are bounded before the request begins.

The two V0 operation profiles fix content capture off, a direct provider
request path, an asynchronous telemetry path, the official
`opentelemetry-instrumentation-botocore` scope, and version `0.1.0` of the
separately installed `iip-opentelemetry-aws-bedrock` usage adapter. The
official scope records the currently shipped legacy provider attribute,
`gen_ai.system`, which IIP normalizes to `aws.bedrock`. A conflicting
`gen_ai.provider.name`/`gen_ai.system` pair is rejected instead of selecting
one implicitly.

The streaming profile consumes the returned event stream through the official
instrumentation wrapper, requires message start/stop and a final metadata
event, proves that the span does not finish before consumption, and normalizes
only final provider usage metadata. Streamed message fragments never enter the
adapter, report, normalized record, or cost ledger.

Amazon Bedrock defines its response `inputTokens` as uncached input when prompt
caching is active, while OpenTelemetry defines `gen_ai.usage.input_tokens` as
total input including cache. The adapter therefore publishes:

```text
gen_ai.usage.input_tokens = inputTokens + cacheReadInputTokens + cacheWriteInputTokens
```

It also publishes the provider cache values as
`gen_ai.usage.cache_read.input_tokens` and
`gen_ai.usage.cache_creation.input_tokens`. The qualification fixtures use
non-zero cache values so a pass cannot be produced by zero-fill behavior.
Reasoning remains missing, so the usage record is intentionally `partial` and
does not claim intrinsic exact-cost eligibility. Absent provider cache meters
also remain absent.

The offline level runs the real boto3 client, botocore request machinery, and
official instrumentation against `botocore.stub.Stubber` inside a no-network
container. Because Stubber validates an event-stream member as a modeled
mapping, the harness replaces that member through a scoped public `after-call`
handler with a botocore `EventStream` fixture before the instrumentation wrapper
sees it. The live level makes one explicitly enabled `Converse` or
`ConverseStream` request with the same content-disabled instrumentation.
Neither level covers invoice reconciliation, model quality, customer Collector
or PKI interoperability, or production traffic volume.

Customer promotion uses the separate [customer Bedrock qualification
contracts](customer-bedrock-qualification-report-contract.md), which bind this
underlying live evidence to a reviewed target and immutable application image
without retaining the target or credential values in the transportable report.
