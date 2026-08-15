# Resource collection contract

**Status:** v1alpha1

**Machine contracts:** `contracts/schemas/resource-collection-request.schema.json` and `contracts/schemas/resource-collection-result.schema.json`

The collection contract is the public boundary between a resource-observer plugin and its host. It carries a bounded, tenant-scoped collection request and a batch of canonical resource observations. It does not define the future plugin process handshake, credential exchange, or transport framing.

## Request authority and scope

The host authenticates the caller and supplies request-scoped tenant, actor, deadline, limits, and capability context. `metadata.tenantId`, `metadata.actorId`, and `spec.sourceId` are consistency assertions, not authority. The host rejects a mismatch before invoking a plugin.

`spec.scope` identifies the provider integration and stable root being observed. Provider-specific `parameters` must be schema-checked by the selected plugin and must never contain credentials or secret values. The Kubernetes observer accepts only a bounded `namespaces` array.

`startSequence` is the first sequence the plugin may assign. Reconciliation requests require one `snapshotId`; incremental requests prohibit it. The deadline and output limits remain enforceable by the host even if the plugin ignores them.

`spec.resume` optionally carries the exact aggregate checkpoint and provider cursor map from the last host-committed complete result. Cursor keys and values are provider-owned, credential-free opaque strings. They grant no authority and the host must reject resume state that does not exactly match its tenant/source checkpoint or does not begin at the next sequence.

## Result invariants

A conforming host validates more than the JSON shape:

- result request, tenant, and source identifiers exactly match the request;
- every resource tenant and observation source/stream/mode match the request;
- observation sequences are contiguous from `startSequence` in deterministic output order;
- reconciliation observations and completion use the requested `snapshotId`;
- `resourceCount` equals the number of observations and `nextSequence` equals `startSequence + resourceCount`;
- `scopeDigest` is SHA-256 over canonical JSON for the complete request scope;
- the serialized result does not exceed `maxOutputBytes` and its observation count does not exceed `maxResources`.

The reference host implements these checks in `ResourceCollectionIngestionService`. It ingests validated observations first and advances `SourceCheckpointRepository` only after every observation and event is durable. Partial results may improve the projection but never advance the checkpoint; replay is therefore safe and idempotent.

For the current checkpoint model, one `sourceId` has one active canonical scope. A later complete request with a different `scopeDigest` is rejected before mutation; a distinct scope uses a distinct source identity. This prevents alternating namespace filters from producing false deletions.

Provider errors are mapped to stable `reasonCode` values. Provider exception text, stack traces, credentials, Kubernetes `Secret` objects, ConfigMap values, token-bearing annotations, and other raw sensitive fields must not cross the boundary.

## Completion and checkpoints

`complete` means the plugin finished the entire declared scope and supplies a credential-free checkpoint. `partial`, `failed`, and `cancelled` require a stable reason code and cannot advance a checkpoint. A result checkpoint is only a candidate: the ingestion workflow commits it after all returned observations and corresponding events are durable.

A complete result may also return `providerCursors`. The host stores this map atomically with the aggregate checkpoint; incomplete results prohibit both. The map is separate from `checkpoint` because a Kubernetes scope can require more cursor state than the bounded aggregate checkpoint string and because the host must pass provider state back without interpreting it.

For reconciliation, only a successful complete result can authorize missing-resource tombstones for the exact `scopeDigest` and `snapshotId`. The host compares the new UID set with the last complete membership, sorts missing UIDs, and emits explicit deleted Resource observations after the plugin observations. These host-generated tombstones consume sequences beginning at `completion.nextSequence`; the committed checkpoint sequence includes them even though `completion.resourceCount` counts plugin observations only.

The new membership and checkpoint commit atomically after every tombstone is durable. Zero observations can therefore be a valid complete snapshot that deletes every resource in the prior membership, but a partial or failed result can never imply deletion. An exact retry of the latest result is matched by a canonical digest and does not emit duplicate events; reusing a snapshot ID with different content fails closed. See [ADR 0007](../decisions/0007-reconciliation-membership-and-tombstones.md).

## Kubernetes list/watch mapping

The example Kubernetes observer lists Namespace and Node collections independently and lists every namespaced resource type separately for each configured namespace. Each response `resourceVersion` is stored under its API-path cursor key. A later reconciliation request can resume all watches from the last committed map. Watches are bounded change detectors: after a change or timeout the observer relists the complete scope before returning a snapshot.

A watch-history expiration such as Kubernetes HTTP `410 Gone` discards the previous cursor set and follows that same full list/reconciliation path; it never guesses a later cursor or treats one expired stream as empty. Watch bookmarks are accepted as progress but are not assumed to arrive at a fixed interval. These semantics follow the Kubernetes [API concepts](https://kubernetes.io/docs/reference/using-api/api-concepts/#efficient-detection-of-changes) guidance and are fixed by [ADR 0009](../decisions/0009-provider-cursor-sets-and-watch-recovery.md).

Owner references create `owns` relationships. Namespace, selector, scheduling, and ingress backend data add normalized `contains`, `routes_to`, `runs_on`, and `reads_from` relationships without copying full provider objects into the graph.
