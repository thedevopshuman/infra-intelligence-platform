# ADR 0095: Exact OpenAI instrumentation compatibility profiles

**Status:** Accepted

**Date:** 2026-09-05

## Context

Phase C requires a second provider to exercise the same public usage and cost
boundaries without importing a provider SDK into the product kernel. The
official OpenTelemetry Python OpenAI instrumentation emits standard provider,
operation, model, and input/output usage attributes, but its current beta
profile does not emit all canonical cache/reasoning breakdowns and OpenAI's
public endpoint has no provider region attribute.

A handwritten span proves only IIP normalization. It does not prove upstream
SDK or instrumentation interoperability. A default live call would require a
secret, incur spend, expose a network dependency, and make verification
non-deterministic.

## Decision

1. Qualify the pinned official OpenAI instrumentation and SDK in an isolated
   compatibility image; neither dependency enters the IIP product image.
2. The default gate calls the real client against a loopback HTTP fixture in a
   no-network container. A live call requires explicit opt-in, model, and API
   key environment values.
3. Content capture and GenAI detail events are disabled. The actual emitted
   span passes through the tenant-bound IIP OTLP receiver, and a failed
   asynchronous exporter cannot change the provider-call result.
4. `cloud.region=global` must be supplied as a deployment-owned OpenTelemetry
   resource attribute and protected-channel allowlist value. Server address is
   not interpreted as region.
5. Missing cache-write and reasoning meters remain missing; this official
   profile cannot claim exact-cost eligibility or silently configure absence as
   zero.
6. A separate synthetic complete-span fixture may exercise catalog pricing and
   the common dashboard, but it is clearly non-provider compatibility evidence.

## Consequences

- The second-provider boundary uses OpenTelemetry and the existing IIP receiver;
  no proxy or IIP instrumentation SDK is introduced.
- Offline evidence is deterministic and source-bound but does not prove the
  OpenAI service, streaming, private endpoints, or a customer Collector.
- Exact official-instrumentation cost remains unresolved until upstream emits
  the needed meters or a separately reviewed billing-capability profile proves
  their absence semantics.
- Future OpenAI-compatible providers require their own provider identity,
  endpoint, pricing, and compatibility evidence; they are not implied by this
  OpenAI profile.
