# ADR 0049: least-authority scheduled logical backups

**Status:** Accepted
**Date:** 2026-08-17

## Context

The recovery experiment in ADR 0010 proves that a quiesced logical PostgreSQL backup can be restored and checked, but it does not schedule backups for an installed environment. The Helm deployment needs an operational baseline without choosing a customer's storage provider, creating unprotected storage, granting Kubernetes API authority, or placing database tooling in serving images.

## Decision

Add an opt-in `CronJob` that runs a digest-pinned PostgreSQL 18 client image under that image's fixed non-root `postgres` identity. It receives only the existing database connection Secret and a pre-created PersistentVolumeClaim. It has no service-account token, denies ingress, and can reach only DNS plus the exact database NetworkPolicy destination. Jobs cannot overlap and have bounded retry, deadline, history, resources, and an explicit IANA time zone.

Each run creates a custom-format, owner/ACL-neutral dump under a restrictive umask, validates its catalog with `pg_restore --list`, computes SHA-256, moves the dump into its final name, and publishes the checksum sidecar last. The sidecar is the completion marker; consumers must ignore partial files and dumps without it. The chart neither creates nor deletes backup storage and applies no retention policy.

Extend the disposable kind gate to start a Job from the installed CronJob, verify its completed artifact from the mounted claim, restore it into a separate temporary database, and confirm every packaged schema migration exists. The temporary database, namespace, claim, and backup are deleted after the test.

## Consequences

- Installed environments have a portable scheduled logical-backup baseline and an executable restore check.
- Serving and migration images receive no additional authority or PostgreSQL client tooling.
- Storage encryption, off-cluster replication, immutability, retention, access review, monitoring, and restore authorization stay with the customer's protected storage and database operating model.
- A successful CronJob is necessary evidence, not a production RPO/RTO or disaster-recovery certification; sustained-write, failure, and point-in-time recovery tests remain required.
