# Bedrock instrumentation compatibility report

**Status:** v1alpha1 operational evidence

`BedrockInstrumentationCompatibilityReport` binds a compatibility claim to one
IIP source revision, one exact Python dependency set, one Bedrock operation,
one model and region, and either an offline botocore stub or a live AWS call.
`compatible` means all eight `Converse` checks or all nine `ConverseStream`
checks passed for that exact qualification level; it does not silently promote
offline evidence into live-provider proof or non-streaming evidence into
streaming proof.

The report contains no credential, prompt, response, trace/span ID, provider
request ID, tenant ID, token quantity, cost, or endpoint. Model and region are
retained because interoperability cannot be generalized across untested
Bedrock profiles.

The two V0 operation profiles fix content capture off, a direct provider
request path, an asynchronous telemetry path, and the official
`opentelemetry-instrumentation-botocore` scope. It records the currently
shipped legacy provider attribute, `gen_ai.system`, after IIP normalizes it to
`aws.bedrock`. A conflicting `gen_ai.provider.name`/`gen_ai.system` pair is
rejected instead of selecting one implicitly.

The streaming profile consumes the returned event stream through the official
instrumentation wrapper, requires message start/stop and a final metadata
event, proves that the span does not finish before consumption, and normalizes
only the final provider token totals. Streamed message fragments never enter
the report, normalized record, or cost ledger.

The pinned instrumentation reports provider input/output totals but does not
report cache-read, cache-write, or reasoning subsets. The resulting usage is
therefore intentionally `partial` and is not exact-cost eligible. A live model
may become exact-cost eligible only after its billing capabilities and missing
field semantics are separately qualified; configuring absent breakdowns as
zero without that proof would understate spend.

The offline level runs the real boto3 client, botocore request machinery, and
official instrumentation against `botocore.stub.Stubber` inside a no-network
container. Because Stubber validates an event-stream member as a modeled
mapping, the harness replaces that member through a scoped public `after-call`
handler with a botocore `EventStream` fixture before the instrumentation wrapper
sees it. The live level makes one explicitly enabled `Converse` or
`ConverseStream` request with the same content-disabled instrumentation.
Neither level covers invoice reconciliation, model quality, customer Collector
or PKI interoperability, or production traffic volume.
