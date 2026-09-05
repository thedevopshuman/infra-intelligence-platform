# OpenAI instrumentation compatibility report

**Status:** v1alpha1 operational evidence

`OpenAIInstrumentationCompatibilityReport` binds a compatibility claim to one
IIP source revision, the pinned OpenAI Python client and official OpenTelemetry
instrumentation, one chat-completions model, and either an offline HTTP fixture
or an explicitly enabled live provider call. Offline evidence never claims
live-provider interoperability.

The report contains no API key, tenant, endpoint, prompt, response, trace/span
identity, provider response ID, token quantity, rate, or monetary amount. Model
and provider region remain because interoperability and pricing cannot be
generalized across profiles that were not exercised.

The accepted profile uses `gen_ai.provider.name=openai`, `chat`, the
`opentelemetry.util.genai.handler` scope, and input/output totals emitted by
`opentelemetry-instrumentation-genai-openai`. OpenAI's public endpoint is
represented by deployment-owned OpenTelemetry resource attribute
`cloud.region=global`; IIP does not derive geography from the server address.

The pinned beta instrumentation exposes input/output totals but not every
cache-write and reasoning subset required by the price engine. Those meters
remain missing, usage remains `partial`, and exact-cost eligibility remains
false. A protected synthetic profile may prove the complete standard attribute
shape for the local dashboard, but it cannot upgrade official-instrumentation
evidence or justify treating absent fields as zero.

The offline level calls the real OpenAI SDK against a loopback-only HTTP fixture
inside a no-network container. The live level requires a separate explicit
enable flag, model, and short-lived API key. Both levels disable content
capture, encode the actual emitted span as OTLP, normalize it through the real
IIP receiver, and prove that an asynchronous exporter failure does not alter
the SDK result.

Neither level qualifies streaming, embeddings, invoice agreement, customer
Collector/PKI behavior, model quality, private pricing, or production traffic
volume.
