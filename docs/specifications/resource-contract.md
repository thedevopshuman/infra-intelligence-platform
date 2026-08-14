# Resource contract

**Status:** v1alpha1  
**Machine contract:** `contracts/schemas/resource.schema.json`

A resource is the canonical, tenant-scoped representation of one infrastructure object. Each submission is an observation; the graph projects the latest accepted observation while preserving provenance and history through events.

## Identity

The platform UID is deterministic from:

```text
sha256(tenant_id + unit-separator + provider + unit-separator + type + unit-separator + external_id)
```

The first 32 lowercase hexadecimal characters are prefixed with `res_`. This makes retries and independent observers converge. `displayName`, labels, health, and location are never identity inputs.

The same external object appearing through two providers does not automatically merge. Identity reconciliation creates an explicit `same_as` relationship with confidence and provenance.

## Envelope

- `apiVersion` and `kind` select the contract.
- `metadata` owns platform scope, UID, observation time, source ordering, and low-cardinality labels.
- `spec` owns source identity, display fields, provider attributes, and relationships.
- `status` owns normalized health and lifecycle projections.

Provider attributes are namespaced by the observation source and must exclude secrets. Consumers cannot assume an attribute exists across resource types.

## Relationships

Relationships are typed edges from the observed resource to a target provider identity or platform UID. Common vocabulary begins with `owns`, `contains`, `runs_on`, `routes_to`, `depends_on`, `reads_from`, `writes_to`, `deployed_from`, and `same_as`.

An edge has observation provenance and time in storage even though the compact ingress contract inherits those from the resource observation. Disappearance requires an explicit reconciliation event; absence in one partial observation is not deletion.

The read API canonicalizes direction, retains the asserting resource as provenance, and resolves only same-tenant platform UIDs into graph nodes. See the [resource query contract](resource-query-contract.md).

## Observation ordering

`metadata.observation` makes source ordering explicit without treating wall-clock time as a sequence. It contains:

| Field | Semantics |
| --- | --- |
| `sourceId` | Stable tenant-scoped integration or collector identity. |
| `streamId` | One ordered source generation. It persists across ordinary process restarts and changes only when the source intentionally resets ordering. |
| `sequence` | Monotonic safe integer within `(tenant, sourceId, streamId)`. |
| `mode` | `incremental` for watch/change delivery or `reconciliation` for an authoritative snapshot pass. |
| `resourceVersion` | Optional provider-native opaque version used for provenance and duplicate diagnostics, never cross-provider ordering. |
| `checkpoint` | Optional credential-free opaque cursor that may be committed after the observation and its event are durable. |
| `snapshotId` | Required for reconciliation observations and prohibited for incremental observations. |

The cursor is additive and optional in `v1alpha1` so existing clients remain compatible. Production collectors must send it. Authenticated collector policy binds the actor to the declared `sourceId`; the payload field is never authority by itself. Once a sequenced observation is accepted for a resource, an unsequenced observation cannot replace that projection. A later contract version may make the cursor mandatory.

## Write semantics

- The same stream and sequence with the same canonical payload is a duplicate: return the current projection and do not emit another observed event.
- The same stream and sequence with different content is a conflict and fails closed.
- A lower sequence in the same stream is stale. It may be retained as history by a durable adapter but cannot replace the latest projection.
- A higher sequence in the same stream replaces the latest projection.
- Sequences from different streams are incomparable. A new stream may take ownership only through a reconciliation observation; an incremental write from an unexpected stream is a conflict.
- Provider resource versions and checkpoints are opaque. Lexical or numeric comparison of them is forbidden unless a provider-specific adapter defines that rule behind the platform port.
- Tenant mismatch between authenticated context and payload fails closed.

The reference API returns stable conflict errors for stale or conflicting writes. Durable storage may preserve rejected observations for audit, but it must not publish them as accepted resource changes.

## Checkpoint and reconciliation semantics

A checkpoint advances only after every resource mutation represented by that cursor and its corresponding event are durable. With a transactional outbox, resource projection, immutable observation, outbox event, and an explicitly safe checkpoint update share one transaction. A crash before commit replays safely; a crash after commit resumes after the checkpoint. The reference HTTP surface never infers checkpoint safety from the payload; trusted collector workflow context must mark the cursor as a completed commit boundary.

Reconciliation observations share a `snapshotId`. Missing resources are not tombstoned until the source emits a successful completion marker for the entire declared scope. A partial, cancelled, expired, or failed snapshot never implies deletion. Checkpoint persistence and completion markers belong to the ingestion workflow and are not inferred from the last resource received.

## Deletion and tombstones

Deletion is an explicit resource observation with `status.lifecycle: deleted`. A tombstone retains canonical identity and observation provenance while omitting mutable attributes and relationships. It remains queryable in history and closes active graph edges; physical removal is a later retention operation. A resource reappearing with a newer valid observation becomes active again without changing UID.

`contracts/examples/resource-tombstone.json` is the canonical deleted-resource example. During complete reconciliation the trusted host may synthesize this observation for a UID present in the prior complete membership but absent from the new one. Plugin output itself never gains deletion authority from an incomplete result.
