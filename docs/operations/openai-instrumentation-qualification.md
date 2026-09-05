# OpenAI instrumentation qualification

**Status:** Executable offline interoperability profile; live profile opt-in

This gate qualifies one exact official OpenTelemetry Python OpenAI
instrumentation profile against IIP's metadata-only GenAI trace receiver. The
application still calls OpenAI directly: IIP is not a proxy, is not on the
request path, and does not add an instrumentation SDK.

The reviewed OpenAI and instrumentation dependencies are isolated in
`requirements/openai-compatibility.txt`. They do not enter the IIP product
image or provider-neutral domain and application packages.

The upstream instrumentation is currently beta. Keep its SDK, semantic
convention, scope, and emitted attribute profile pinned and rerun this gate
before upgrading any of them.

## What the default gate proves

```bash
make test-openai-instrumentation
```

Docker builds the pinned compatibility image and runs it with no external
network, a read-only root filesystem, no Linux capabilities, bounded CPU,
memory, PIDs, and temporary storage. Inside that container, the real OpenAI
Python client calls a loopback HTTP fixture through
`chat.completions.create`. The official OpenTelemetry wrapper emits the span;
the fixture does not handcraft the compatibility evidence.

The gate verifies:

- OpenAI Python `3.8.0`,
  `opentelemetry-instrumentation-genai-openai` `1.1b0`,
  `opentelemetry-util-genai` `1.1b0`, and OpenTelemetry SDK `1.44.0`;
- `gen_ai.provider.name=openai`, operation `chat`, request/response model,
  input/output token totals, and instrumentation scope
  `opentelemetry.util.genai.handler`;
- content capture and GenAI detail events remain disabled;
- `cloud.region=global` comes from the deployment-owned OpenTelemetry
  resource and is not inferred from an endpoint;
- the actual emitted span passes through IIP's protobuf decoder, protected
  channel allowlist, normalization, ingestion service, and in-memory ledger;
  and
- a deterministic asynchronous exporter failure cannot change the successful
  provider call.

The source-bound output is
`dist/openai-instrumentation-offline-report.json`. Its qualification level is
`offline-sdk-interoperability`; it is not evidence of a live OpenAI service,
streaming, private endpoints, or customer Collector behavior.

## Exact-cost limitation

The pinned official profile exposes input/output totals but does not emit every
cache-write and reasoning breakdown required by IIP's exact token price
arithmetic. The protected channel has an empty `zeroWhenAbsent` list, so those
meters remain missing and normalized usage remains `partial`. This profile
cannot claim exact-cost eligibility.

The multi-provider local AI FinOps demo separately sends a complete synthetic
OpenAI-shaped span with every price meter present, including explicit zeros.
That proves the common ledger and data-driven cost engine; it does not upgrade
the official instrumentation claim or turn missing upstream meters into zero.

## Opt-in live qualification

The live gate makes one bounded direct API call. It is excluded from
`make verify` and requires an exact approved model plus a short-lived API key.
Do not use a personal or long-lived key.

```bash
export IIP_OPENAI_LIVE_TEST_ENABLED=true
export IIP_OPENAI_MODEL='<approved-model-id>'
export OPENAI_API_KEY='<short-lived-api-key>'
make test-openai-live
unset OPENAI_API_KEY
```

The harness never prints or persists the prompt, response, API key, provider
response ID, trace/span IDs, or token quantities. The report retains the model
and deployment-owned `global` region because qualification cannot be
generalized to an untested profile.

Live qualification still does not establish invoice agreement, private-rate
correctness, rate-limit behavior, sustained load, embeddings, responses API,
streaming, customer PKI, or Collector interoperability. Qualify each additional
API and instrumentation profile independently.
