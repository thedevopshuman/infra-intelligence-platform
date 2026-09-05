# AI attribution contracts

**Status:** v1alpha1

AI attribution maps observed OpenTelemetry workload identity to reviewed
application and team ownership without trusting application-supplied ownership
labels. It produces a separate immutable fact and never rewrites the source
`AiUsageRecord`.

## `AiAttributionPolicy`

One policy is a protected, tenant-scoped snapshot. Each rule matches the
observed `serviceName` and may further constrain service namespace, deployment
environment, one IIP resource reference, and an effective-time interval. A
match allocates stable application and team IDs plus display names.

Rules have unique priorities and are serialized by descending priority, then
rule ID. The first matching rule wins. `effectiveFrom` is inclusive and
`effectiveUntil` is exclusive. A new reviewed snapshot receives a new policy
ID and version; existing snapshots are immutable.

The policy source carries a locator, retrieval time, and content digest.
`test-fixture` sources require an explicit non-production runtime flag.
Telemetry attributes, API callers, and optional SDK clients cannot install or
select policies.

## `AiUsageAttributionRecord`

One record binds exactly one usage record, policy snapshot, source digest, and
engine version. `effectiveAt` is the model invocation `startedAt`, never worker
processing time. `observedIdentity` is copied from the normalized usage record
so persistence can revalidate the decision against both immutable sources.

A result is either:

- `allocated`, with the winning rule, application, and team; or
- `unallocated`, with `no-matching-rule`.

Unallocated is an explicit data-quality fact, not a default team. Record
identity is deterministic from tenant, usage, policy ID/version/source hash,
and engine version. `resolvedAt` is deliberately excluded from that identity.

## CloudEvent

`io.iip.ai.usage-attributed.v1` commits atomically with a new attribution
record. It contains record, usage, policy, result status, and—only for an
allocated result—application and team IDs. It omits observed token quantities,
prices, money, content, display names, and resource references. Exact retries
do not emit duplicate events.

## Compatibility and SDK boundary

The Python and TypeScript SDKs expose the two public document shapes. They do
not instrument model calls, manage protected policies, or perform attribution.
Breaking matching, precedence, effective-time, identity, or authority semantics
requires a new contract version and migration guidance.

There is no control-plane HTTP route in this unit, so the OpenAPI documents are
unchanged. Query and allocation export surfaces are a separate roadmap unit.
