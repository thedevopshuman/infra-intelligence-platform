# Bedrock instrumentation compatibility report

**Status:** v1alpha1 operational evidence

`BedrockInstrumentationCompatibilityReport` binds a compatibility claim to one
IIP source revision, one exact Python dependency set, one Bedrock operation,
one model and region, and either an offline botocore stub or a live AWS call.
`compatible` means all eight closed checks passed for that exact qualification
level; it does not silently promote offline evidence into live-provider proof.

The report contains no credential, prompt, response, trace/span ID, provider
request ID, tenant ID, token quantity, cost, or endpoint. Model and region are
retained because interoperability cannot be generalized across untested
Bedrock profiles.

The V0 profile fixes content capture off, a direct provider request path, an
asynchronous telemetry path, and the official
`opentelemetry-instrumentation-botocore` scope. It records the currently
shipped legacy provider attribute, `gen_ai.system`, after IIP normalizes it to
`aws.bedrock`. A conflicting `gen_ai.provider.name`/`gen_ai.system` pair is
rejected instead of selecting one implicitly.

The pinned instrumentation reports provider input/output totals but does not
report cache-read, cache-write, or reasoning subsets. The resulting usage is
therefore intentionally `partial` and is not exact-cost eligible. A live model
may become exact-cost eligible only after its billing capabilities and missing
field semantics are separately qualified; configuring absent breakdowns as
zero without that proof would understate spend.

The offline level runs the real boto3 client, botocore request machinery, and
official instrumentation against `botocore.stub.Stubber` inside a no-network
container. The live level makes one explicitly enabled `Converse` request with
the same content-disabled instrumentation. Neither level covers
`ConverseStream`, invoice reconciliation, model quality, customer Collector or
PKI interoperability, or production traffic volume.
