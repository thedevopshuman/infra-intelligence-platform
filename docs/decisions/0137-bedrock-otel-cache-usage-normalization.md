# ADR 0137: Normalize Bedrock cache usage before OTLP export

**Status:** Accepted

**Date:** 2026-09-08

## Context

The pinned official Python botocore instrumentation copies Amazon Bedrock
`inputTokens` into `gen_ai.usage.input_tokens` and omits the cache-read and
cache-write counters for both `Converse` and `ConverseStream`.

Those are not equivalent semantics when prompt caching is active. [Amazon
Bedrock documents](https://docs.aws.amazon.com/bedrock/latest/userguide/prompt-caching.html)
`inputTokens` as only input that was neither read from nor written to cache,
and defines total request input as `inputTokens + cacheReadInputTokens +
cacheWriteInputTokens`. [OpenTelemetry
defines](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)
`gen_ai.usage.input_tokens` as total input including cached token types, with
cache read and cache creation as subsets. Passing the provider value through
unchanged can understate usage, while subtracting cache meters again in the
cost engine can produce an invalid breakdown.

Waiting for upstream instrumentation would leave the V0 Bedrock path unable
to observe cache economics. Treating absent cache counters as zero would make
an unsupported billing claim. Adding an IIP inference SDK or proxy would
violate the preferred collection architecture.

## Decision

Add a separately installed Python OpenTelemetry provider adapter:
`instrumentation/python/aws-bedrock`.

1. The adapter is an `opentelemetry_pre_instrument` extension. It replaces only
   the pinned official botocore Bedrock extension loader, preserving the
   official span scope and normal auto-instrumentation path.
2. For `Converse` and `ConverseStream`, it maps provider cache-read and
   cache-write quantities to the standard OpenTelemetry cache attributes and
   publishes total input as provider uncached input plus each reported cache
   quantity.
3. Streaming retains only the four integer usage counters until the official
   completion callback runs. It does not retain message events or content.
4. Missing cache counters remain missing. The adapter does not infer zero and
   does not invent reasoning usage.
5. Message-content telemetry is disabled inside the replacement extension.
   The adapter reads only provider usage metadata.
6. Every enrichment operation catches telemetry failures. Provider return
   values, exceptions, credentials, request content, and response content are
   unchanged, so telemetry fails open relative to the inference call.
7. Because this integration uses an upstream private extension point, the
   distribution pins exact botocore and instrumentation versions. The Docker
   qualification gate must prove both non-streaming and streaming behavior
   before any version changes.
8. The package is not the public IIP SDK and does not enter the IIP application
   image, domain, application, SDK transport, plugin runtime, or provider
   request path.

## Consequences

- Bedrock cache meters and OTel total-input semantics are executable without a
  proxy or application code changes beyond ordinary auto-instrumentation
  installation.
- The normalized usage record remains `partial` while reasoning usage is not
  reported. A later cost-engine decision may calculate a complete estimate
  only when the selected output rates prove that the missing split is
  mathematically irrelevant; this ADR does not make that claim.
- Customers must install the exact adapter dependency beside their
  instrumented application. Other languages, SDK versions, Bedrock APIs, and
  model-specific fields require separate adapters and qualification profiles.
- Provider-published rates still produce a calculated estimate, not an
  invoice. Invoice reconciliation remains a separate future boundary.
