# ADR 0010: Verify PostgreSQL logical backup recovery before production backup selection

**Status:** Accepted

**Date:** 2026-08-15

## Context

PostgreSQL is the initial authority for immutable observations, the event log, outbox delivery state, source checkpoints, evidence, investigations, governed actions, plugin sessions, and audit records. Resource and relationship projections are rebuildable, but the observation history used to rebuild them is recovery-critical. A database backup that merely restores without errors is insufficient evidence: tenant records, sequence state, and derived-graph consistency must also survive.

Phase 1 needs a repeatable local measurement before a production environment, retention policy, storage provider, or point-in-time recovery service has been selected. This experiment must not be mistaken for a production backup schedule or disaster-recovery commitment.

## Decision

Provide a Docker Desktop recovery experiment that:

- persists one reference workflow through the PostgreSQL application and adapter boundaries;
- quiesces that controlled workload and takes a complete `iip` schema backup using PostgreSQL custom-format `pg_dump`;
- restores with `pg_restore` into a separately named fresh database in the disposable test service;
- compares canonical row digests for every platform table and captures identity-sequence state;
- verifies the source did not change during the measured backup window;
- runs the tenant-scoped projection-rebuild dry run against the restored database and rejects drift;
- measures recovery-point age and time from recovery start through integrity and projection verification;
- uses 60-second recovery-point-age and 120-second recovery-readiness values only as local experiment guardrails.

The experiment uses an isolated Compose project, removes its test container and volume on exit, and never reads the long-running development database. The generated report contains no connection string, credentials, raw evidence, or resource documents.

## Consequences

- Recovery is tested against authoritative records, derived records, and future insert sequence state rather than inferred from a successful restore command.
- A contributor with Docker Desktop can repeat the same measurement with `make test-backup-restore`.
- The committed measurement is reproducible evidence for the Phase 1 implementation gate, not a production SLO.
- Projection recovery remains independently useful: a full database restore preserves the projections, while the dry run proves they still agree with accepted observations.
- Production backup encryption, protected off-host storage, scheduling, retention, restore authorization, key management, WAL archiving, and point-in-time recovery remain deployment decisions.

## Revisit triggers

Replace or extend this logical-backup experiment when a production hosting model is selected, continuous writes require measured point-in-time recovery, database size makes logical restore miss the accepted objective, or a managed PostgreSQL provider becomes the recovery authority.
