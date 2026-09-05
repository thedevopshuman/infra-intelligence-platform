# Event contract

**Status:** v1alpha1  
**Machine contract:** `contracts/schemas/event.schema.json`

The event envelope follows CloudEvents 1.0 structured JSON so transports and SDKs can remain conventional. The current stable CloudEvents specification release is 1.0.2, while the wire-level `specversion` remains `1.0`.

## Required context

| Field | Semantics |
| --- | --- |
| `id` | Unique within `source`; used with source for deduplication. |
| `source` | Stable URI-reference identifying the producer boundary. |
| `type` | Versioned taxonomy: `io.iip.<domain>.<verb>.v<major>`. |
| `time` | When the fact occurred at the source, not ingestion time. |
| `subject` | Primary resource, investigation, workflow, or action identity. |
| `tenantid` | Mandatory platform extension; verified against authenticated channel. |
| `data` | Event-type-specific object. |

`correlationid` groups a larger flow. `causationid` points to the immediately preceding event/action. `traceparent` carries distributed tracing context without replacing either.

## Delivery semantics

The platform targets at-least-once ingestion. Consumers deduplicate by `(tenantid, source, id)`. Ordering is guaranteed only within a documented partition; event time may arrive out of order. Consumers use event time for historical reasoning and ingestion sequence for replay bookkeeping.

Events are immutable. A correction creates a new event that references the prior event through its data and causation chain. Large evidence is stored separately and referenced by locator plus content hash.

`io.iip.resource.observed.v1` data includes the canonical resource UID, observation hash, and source ordering cursor when supplied. Duplicate retries do not emit another observed event. Stale or conflicting observations fail before publication and use stable external error codes rather than masquerading as accepted changes.

## Initial taxonomy

- `io.iip.resource.observed.v1`
- `io.iip.resource.lifecycle-changed.v1`
- `io.iip.investigation.requested.v1`
- `io.iip.investigation.completed.v1`
- `io.iip.action.proposed.v1`
- `io.iip.action.approved.v1`
- `io.iip.action.executed.v1`
- `io.iip.policy.decided.v1`
- `io.iip.plugin.failed.v1`
- `io.iip.ai.usage-recorded.v1`
- `io.iip.ai.cost-calculated.v1`

`io.iip.ai.usage-recorded.v1` is emitted only after the tenant-scoped usage
record is durable. Its data contains bounded routing identity and the canonical
deduplication digest; it never contains prompts, responses, token content, or
price configuration. See the [AI economics contracts](ai-economics-contracts.md).

`io.iip.ai.cost-calculated.v1` is emitted atomically with an immutable cost
record. It binds the usage, catalog version, engine version through its source,
and resolved status. Its routing data deliberately omits rates, quantities,
totals, and protected catalog contents.

Event type versions change only for incompatible `data` semantics. Additive optional data fields remain within the major version.
