# PostgreSQL physical continuity qualification

**Status:** Source-bound local physical-continuity regression gate  
**Decision:** [ADR 0103](../decisions/0103-source-bound-postgresql-physical-continuity-evidence.md)  
**Contract:** [PostgreSQL continuity qualification report](../specifications/postgresql-continuity-qualification-contract.md)

This procedure proves the platform schema survives one physical streaming
replica cutover, planned manual promotion, and archived-WAL recovery to a named
boundary. It complements—rather than replaces—the
[logical backup/restore experiment](postgresql-backup-restore.md).

## Run and verify

Start Docker Desktop, install the pinned verification dependencies, and run:

```bash
make test-postgres-continuity
```

The gate writes
`dist/postgresql-continuity-qualification-report.json`. It uses the exact
digest-pinned PostgreSQL image, random runtime credentials, loopback-only random
ports, one private network, and four named volumes. Containers, volumes, and the
network are labeled `iip.continuity.project=iip-pg-continuity` and removed on
success or failure. Cleanup refuses to remove an exact-name collision without
that ownership label.

Retain release evidence only after committing the implementation and rerunning
from a clean checkout:

```bash
IIP_DATABASE_CONTINUITY_REPORT=/absolute/path/to/postgresql-continuity-qualification-report.json \
  make verify-postgres-continuity-report
```

The offline verifier rejects a dirty checkout, a different revision or
application version, an older migration, a changed PostgreSQL image, altered
measurements, a reordered check, or a failed profile.

## Interpret the measurements

- `physicalBackup` covers two complete physical copies and streamed WAL. Its
  byte and time measurements are local inputs, not storage capacity guidance.
- `replication.primaryFlushLsn`, `standbyReplayLsn`, and `replayLagBytes` define
  the measured cutover boundary. Zero loss is claimed only after byte lag is
  zero and every platform table matches.
- `failover.readyMilliseconds` starts immediately before the primary is stopped
  and ends after manual promotion, timeline advancement, complete row/sequence
  safety checks, and projection verification.
- `pitr.readyMilliseconds` starts before the recovery container is launched and
  ends only after named-target promotion, complete target-state comparison,
  before/after marker proof, and projection verification.
- `rowDigest` covers the complete per-table digest map. Raw rows never enter the
  report.
- Sequence digests may differ because PostgreSQL reserves sequence values ahead
  in WAL. `sequenceFloorSatisfied` proves equal configuration and a safe,
  non-regressing recovered floor; gaps are expected and acceptable.
- The default 60-second catch-up, 120-second failover-ready, and 180-second
  PITR-ready limits are local regression guardrails, not published SLOs.

Generated credentials are passed to Docker by inherited environment-variable
name rather than command-line value. The standby's disposable recovery
configuration necessarily contains its runtime replication credential until
the labeled volume is removed. Do not interrupt cleanup to reuse any experiment
volume, and protect retained reports as operational evidence even though they
are value-minimized.

## Production adoption checklist

Choose the customer PostgreSQL service before claiming production continuity,
then qualify at least:

1. topology, synchronous/asynchronous policy, failure domains, quorum, fencing,
   split-brain prevention, and traffic rerouting;
2. workload-specific RPO/RTO under representative sustained writes, data size,
   connection load, and long-running transactions;
3. archive/object-storage encryption, key ownership, immutability, replication,
   retention, deletion, completeness monitoring, and capacity;
4. automatic and planned failover behavior, old-primary isolation, replica
   rebuild, failback, and application reconnection;
5. time/LSN restore selection, corrupt/missing WAL behavior, independent restore
   authorization, recurring restore exercises, and regional disaster recovery;
6. backup/replication credentials through the selected secret manager, rotation,
   revocation, audit, and break-glass procedures; and
7. alerts for replication lag, slot/WAL growth, archive failure, backup age,
   restore-test failure, storage capacity, and objective misses.

The local gate does not install or recommend a production PostgreSQL operator,
managed service, storage class, failover controller, or backup product. Those
remain customer deployment decisions and require environment-specific evidence.

