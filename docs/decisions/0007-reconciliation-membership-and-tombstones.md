# ADR 0007: Reconciliation membership and tombstones

**Status:** Accepted
**Date:** 2026-08-14

## Context

A resource disappearing from a provider list is not enough to prove deletion. Lists may be partial, cancelled, expired, incorrectly scoped, or produced by a source that has changed authority. The host therefore needs durable knowledge of the last complete membership for the exact source scope before it can convert absence into a lifecycle transition.

The public collection result assigns contiguous sequences only to plugin observations. Host-generated tombstones still need deterministic ordering and must not allow a checkpoint to move ahead of an incomplete deletion pass.

## Decision

Persist the last complete reconciliation membership behind an application-owned `ReconciliationRepository`. A record is tenant- and source-scoped and contains the stream, snapshot ID, scope digest, final host sequence, candidate checkpoint, canonical result digest, present resource UIDs, generated tombstone UIDs, and commit time.

For the current `v1alpha1` checkpoint model, one `sourceId` owns one stable reconciliation scope. A different scope digest is rejected before resource mutation; an operator must use a different source identity for a different scope. This avoids deleting objects merely because one source alternated between namespace subsets.

After validating and durably ingesting all observations in a complete reconciliation result, the host compares the new UID set with the prior complete membership. Missing UIDs are sorted and converted into explicit `status.lifecycle: deleted` Resource observations. They consume deterministic source sequences immediately after the plugin's `nextSequence`. The host atomically commits the new membership and a checkpoint whose sequence includes those tombstones. Partial, failed, and cancelled results never update membership or generate tombstones.

An exact retry of the latest snapshot is identified by its canonical result digest and returns the existing projection without emitting duplicate events. Reuse of a snapshot ID with different content fails closed.

PostgreSQL stores membership in `iip.source_reconciliations`; the in-memory adapter implements the same port and lock boundary.

## Consequences

- Deletion is evidence from two complete snapshots, not an inference from a partial response.
- Tombstones retain stable identity and history while closing current relationship edges.
- A crash before membership/checkpoint commit requires retrying the exact collection result; accepted resource writes and tombstones are idempotent.
- Scope changes are deliberately explicit and may require a later multi-scope checkpoint contract if one source must own independent cursors.
- This decision does not implement Kubernetes watch streaming, `410 Gone` recovery, a full projection rebuild command, or retention-time physical deletion.
