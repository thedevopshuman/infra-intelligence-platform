# ADR 0009: Provider cursor sets and watch-expiration recovery

**Status:** Accepted
**Date:** 2026-08-15

## Context

One Kubernetes collection scope contains resources served by multiple API paths. Each list response has its own opaque `resourceVersion`; an aggregate digest cannot safely resume any of those watches. Namespace-scoped access also requires a separate API request for every configured namespace and type. A single string checkpoint therefore cannot represent the provider state needed to resume without granting cluster-wide read authority.

Kubernetes retains watch history for a bounded period and may return HTTP `410 Gone` when a requested version is no longer available. Guessing a later version can miss changes, while treating one failed stream as an empty list can produce false tombstones.

## Decision

Extend the additive `v1alpha1` resource-collection boundary with opaque provider cursor state:

- a complete result may return `completion.providerCursors`, keyed by provider-defined stream identity;
- a later request may carry the last committed `checkpoint` and exact cursor map in `spec.resume`;
- the host treats cursor keys and values as credential-free opaque strings and never interprets Kubernetes ordering;
- the cursor map is committed atomically with observations, reconciliation membership, tombstones, and the aggregate source checkpoint;
- a resume request must exactly match the host's current tenant/source checkpoint and begin at the next source sequence.

The Kubernetes observer owns one cursor for the Namespace list, one for the Node list, and one for each configured `(namespace, resource type)` API path. It disables list pagination for this bounded reference scope so every stored cursor describes one complete list response.

The current correctness-first watch cycle uses watches as change detectors. After any watch event, bookmark-only timeout, disconnect cycle, or `410 Gone`, it performs a fresh full per-stream list and emits a complete reconciliation snapshot. If any stream expires, the prior cursor set is discarded as a unit; no cursor is guessed and no partial list authorizes deletion. The new cursor set becomes authoritative only after trusted host ingestion commits the complete snapshot.

Incremental graph patching is deferred because relationships such as ownership, selectors, scheduling, and routes can depend on objects outside one watch event. Full reconciliation preserves deterministic relationship and tombstone semantics.

## Consequences

- Collection resume state scales with the declared namespace/type scope instead of being forced into a bounded string.
- Namespace-specific reads remain compatible with least-privilege Role bindings.
- A watch expiration causes extra list load but cannot create a silent observation gap or false deletion.
- Full lists for different resource types are not a cross-type transactional snapshot; their individual versions and the resulting aggregate digest retain that provenance.
- Provider cursors are recovery state, not authority or credentials, and incomplete results cannot advance them.
- A later optimized incremental collector must preserve relationship correctness and the same atomic cursor-commit rule.

## Revisit triggers

Revisit the relist-after-change strategy when measured API-server load or collection latency exceeds the Phase 1 SLO, or when an incremental relationship-maintenance design has replay and deletion proofs equivalent to full reconciliation.
