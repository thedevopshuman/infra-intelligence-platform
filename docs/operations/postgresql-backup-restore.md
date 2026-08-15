# PostgreSQL backup and restore experiment

**Status:** Phase 1 local recovery evidence

**Decision:** [ADR 0010](../decisions/0010-postgresql-backup-restore-verification.md)

This experiment proves that the current PostgreSQL substrate can be backed up and restored without losing authoritative records, serving projections, or identity-sequence state. It runs only against a dedicated disposable Compose project and never targets the long-running development stack.

## Run it

Start Docker Desktop, install the pinned verification dependencies, and execute:

```bash
make test-backup-restore
```

The command performs these bounded steps:

1. Start PostgreSQL 18.4 under the isolated `iip-backup-restore` Compose project.
2. Migrate and seed a reconciliation, resource graph edge, event/outbox entry, evidence-backed investigation, governed dry-run action, plugin session, and audit records.
3. Record a canonical source manifest and a database recovery-point marker.
4. Create a consistent custom-format logical backup with `pg_dump` while the fixture workload is quiesced.
5. Restore into the explicitly separate `iip_restore` database.
6. Compare every row in all `iip` tables plus every platform sequence.
7. Run the tenant-scoped projection verifier against the restored database.
8. Emit one JSON measurement and remove the container, backup file, database volume, and isolated Compose project.

Use `--output` when a separate environment needs to retain its own report:

```bash
PYTHONPATH=src:sdks/python/src python3 scripts/backup_restore_experiment.py \
  --output /tmp/iip-postgresql-backup-restore.json
```

The output path must be protected like other operational evidence even though this report intentionally contains only counts, digests, timings, and non-secret environment metadata.

## Interpret the measurement

- `committedRecordLoss` is zero only for this quiesced experiment: the pre-backup source manifest is identical to both the post-backup source and restored manifests.
- `recoveryPointAgeSeconds` is the measured upper bound from the recorded recovery point through backup completion. It is not proof of a continuous-write production RPO.
- `recoveryReadySeconds` includes fresh-database creation, restore, full manifest comparison, and projection verification. It is closer to operator-ready recovery than the restore command duration alone.
- `databaseDigest`, per-table digests, and sequence state prove exact recovery of the captured database state without placing raw platform records in the report.
- `projectionVerification.driftDetected` must be false, proving that restored serving projections agree with immutable accepted observations.
- `objectivesMet` applies only to the 60-second recovery-point-age and 120-second recovery-readiness local guardrails recorded in ADR 0010.

The baseline captured on 2026-08-15 is stored in [the measurement report](measurements/postgresql-backup-restore.json). It restored 22 rows across 15 tables and 4 sequences with an identical digest. Backup duration was 0.135 seconds, recovery-point age was 0.137 seconds, and verified recovery readiness was 0.341 seconds on the local arm64 Docker Desktop environment.

## Production gap

This experiment does not provide scheduled backups, off-host durability, encryption/key policy, retention, restore approvals, regional recovery, or point-in-time recovery. Those controls must be selected with the production PostgreSQL hosting model and tested under sustained writes and representative data volume. Never treat the disposable local dump as a retained backup.
