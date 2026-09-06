# IIP OpenTelemetry AWS Bedrock usage adapter

This separately installed OpenTelemetry Python adapter corrects the pinned
official botocore instrumentation's Amazon Bedrock `Converse` and
`ConverseStream` usage attributes. It does not proxy, wrap, or replace the
application's provider client, and it never reads prompt or response content.

Amazon Bedrock reports `inputTokens` as uncached input when prompt caching is
active. OpenTelemetry defines `gen_ai.usage.input_tokens` as total input,
including cache reads and cache creation. The adapter preserves the provider
response and enriches only the official client span:

- `gen_ai.usage.input_tokens` becomes provider `inputTokens` plus any reported
  cache-read and cache-write quantities;
- `gen_ai.usage.cache_read.input_tokens` receives
  `cacheReadInputTokens`; and
- `gen_ai.usage.cache_creation.input_tokens` receives
  `cacheWriteInputTokens`.

Missing provider meters remain missing. In particular, the adapter does not
invent reasoning usage or treat an absent cache meter as zero.

The package is deliberately pinned to the exact upstream private extension
boundary qualified by this repository. Install it beside ordinary
OpenTelemetry Python auto-instrumentation and start the application with the
normal `opentelemetry-instrument` command. Its pre-instrument entry point loads
before the official botocore instrumentor. For manual setup, call `install()`
before `BotocoreInstrumentor().instrument(...)`.

Any adapter failure is contained inside the telemetry callback. It can lose
enrichment, but it cannot fail the provider call. Upgrade the pinned botocore
or OpenTelemetry instrumentation only with a new compatibility qualification.
