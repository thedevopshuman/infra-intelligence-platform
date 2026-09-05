# ADR 0103: qualify PostgreSQL physical continuity mechanics separately

**Status:** Accepted  
**Date:** 2026-09-06

## Context

ADR 0101 binds complete-schema logical backup and restore evidence to one
source revision, but deliberately makes no replication, failover, WAL archive,
or point-in-time recovery claim. Those mechanisms have different correctness
conditions and failure modes. A checklist or an expanded logical-restore label
would not prove them.

PostgreSQL sequence WAL records may reserve values ahead of the last value
observed on the primary. A recovery server can therefore expose a higher safe
sequence floor than the primary even when every platform table row is exactly
replayed. Requiring equal sequence digests would reject correct physical
recovery; ignoring sequence state could hide a duplicate-identifier risk.

## Decision

Add a separate, closed `physical-streaming-pitr-v1` qualification profile and
`PostgreSQLContinuityQualificationReport`. The disposable Docker experiment:

- pins the same PostgreSQL 18.4 image used by the logical profile;
- creates runtime-only database and replication credentials and never places
  either value or a connection string in commands, reports, or source files;
- takes two streamed-WAL physical base backups from representative migrated
  platform state;
- starts an asynchronous physical standby, commits bounded before/after marker
  resources through the ordinary application ingestion boundary, and waits for
  an exact zero-byte replay-lag cutover point;
- compares every platform table and requires every sequence configuration to
  match with a non-regressing last-value floor;
- stops the primary, manually promotes the standby, and repeats complete row,
  sequence-floor, and projection verification;
- archives WAL, recovers the independent base backup to a named restore point,
  and proves the before-target marker exists while the after-target marker does
  not; and
- emits minimized source-bound timings, counts, digests, LSNs, timelines, and
  twenty derived ordered checks under `dist/`, then removes only its exact
  label-owned Docker resources.

Sequence digests remain visible so a change is auditable. Equality is required
for table-row digests; sequence correctness is the stronger operational safety
condition that the recovered value cannot move backward for its declared
increment direction.

The three timing objectives are classified as local regression guardrails.
They are not customer RPO/RTO promises.

## Consequences

The repository now has executable evidence for physical replication, planned
manual promotion, archived-WAL availability, and named-target recovery without
conflating those results with the logical backup baseline.

This local single-host profile does not provide an HA controller, fencing,
split-brain prevention, traffic rerouting, off-host archive durability,
encryption, retention, regional recovery, sustained-write coverage,
representative customer scale, or automatic failover. A production PostgreSQL
service must qualify those properties in its own failure domains and retain a
clean source-bound report only as one regression input.

