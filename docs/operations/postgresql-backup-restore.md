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
2. Migrate and seed a reconciliation, resource graph edge, event/outbox entry, evidence-backed investigation, governed dry-run action, plugin session, durably cancelled plugin invocation claim/result, and audit records.
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

The latest retained clean-revision baseline is stored in [the measurement report](measurements/postgresql-backup-restore.json). It records the exact tables present at its capture time; later migrations, including the AI usage, protected price-catalog, and calculated-cost ledgers, are automatically included by the experiment but require a new clean-revision run before release promotion. The values are local Docker Desktop guardrails, not production SLO claims.

## Scheduled Helm backup baseline

The chart can schedule the same logical format into a pre-created protected PersistentVolumeClaim. It never creates storage because encryption, replication, immutability, retention, and access controls belong to the customer's storage operating model. A minimal values fragment is:

```yaml
database:
  existingSecret: iip-database

backup:
  enabled: true
  schedule: "0 2 * * *"
  timeZone: Etc/UTC
  destination:
    existingClaim: iip-protected-backups

networkPolicy:
  enabled: true
  databaseEgress:
    enabled: true
    namespaceSelector:
      kubernetes.io/metadata.name: database-system
    podSelector:
      app.kubernetes.io/name: postgresql
```

The job uses a digest-pinned PostgreSQL 18 client, cannot overlap, receives no Kubernetes token, and can reach only DNS and the selected database. It publishes `<name>.dump.sha256` only after `pg_dump` and `pg_restore --list` succeed. Treat that sidecar as the completion marker and alert on missed schedules, failed Jobs, missing checksums, storage capacity, replication lag, and retention failures.

Before enabling it for customer data, prove that the claim is encrypted, replicated away from the database failure domain, immutable for the required window, capacity-monitored, and covered by an authorized deletion policy. Exercise a restore into an isolated database on a recurring schedule. `make test-helm-install` performs this backup/checksum/restore flow against a disposable kind database; it does not certify the customer's storage.

## Production gap

The chart now provides scheduling and a least-authority logical-dump job, but it does not provide off-host durability, encryption/key policy, immutability, retention, restore approvals, regional recovery, or point-in-time recovery. Those controls must be selected with the production PostgreSQL hosting model and tested under sustained writes and representative data volume. Never treat the disposable local dump as a retained backup.
