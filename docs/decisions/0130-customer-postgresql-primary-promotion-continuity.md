# ADR 0130: Qualify customer PostgreSQL continuity with native timeline evidence

**Status:** Accepted
**Date:** 2026-09-07

## Context

The disposable Docker experiment in ADR 0103 proves physical replication,
manual promotion, and point-in-time recovery mechanics on one development host.
The customer deployment aggregate now proves API, worker, and OTLP receiver pod
continuity, but correctly leaves shared-database failure unqualified. A process
restart is not evidence of database failover, and a provider-specific success
response would make the platform's continuity claim depend on one vendor.

PostgreSQL creates a new WAL timeline when recovery completes and a standby is
promoted. Current PostgreSQL exposes the current WAL file and its timeline ID
through read-only functions that do not require superuser. `pg_is_in_recovery`
distinguishes a standby from a writable primary. These native facts provide a
portable observation boundary without granting the qualifier promotion,
replication, filesystem, or provider-control authority.

## Decision

Add the `customer-postgresql-primary-promotion-v1` profile and
`CustomerPostgreSQLContinuityQualificationReport`.

1. A protected `CustomerPostgreSQLContinuityProfile` supplies the tenant,
   actor, existing Resource, synthetic metric, bounded investigation request,
   database name and least-privilege observer role. The retained report contains
   only the profile digest.
2. The external qualifier connects to one explicit PostgreSQL host and port
   with `sslmode=verify-full`, an explicit CA, a mode-0600 password file, and
   optional client certificate identity. It sets
   `target_session_attrs=read-write`, `default_transaction_read_only=on`, and a
   bounded statement timeout.
3. The database probe issues one `SELECT` over `pg_is_in_recovery`, connection
   TLS state, PostgreSQL version, postmaster start time, server address, and the
   timeline returned by `pg_split_walfile_name(pg_walfile_name(
   pg_current_wal_lsn()))`. It stores only timeline integers and structured
   SHA-256 bindings, never the address or connection identity.
4. The qualifier does not initiate failover. After baseline platform probes and
   one completed investigation, it persists another investigation request,
   announces readiness, and waits for an independently authorized operator or
   service to perform a planned promotion.
5. Promotion is accepted only after the same stable endpoint resolves to a
   verified-TLS, read-only probe session on a writable primary with a strictly
   higher WAL timeline and the same PostgreSQL major version. A restart on the
   old timeline does not pass.
6. Direct API and mutual-TLS OTLP probes continue throughout observation.
   Baseline and recovery must be clean; the transition can have only the
   explicitly bounded availability and consecutive-failure budget. A successful
   receiver response retains its PostgreSQL-commit-before-200 meaning.
7. The pre-promotion investigation must remain queryable after promotion, and a
   new recovery investigation must complete. This proves durable workflow
   recovery but does not assert the precise instant at which worker execution
   occurred.
8. Raw database/API/OTLP endpoints, credentials, certificates, database/user,
   tenant, actor, Resource, rows, response bodies, and errors do not enter the
   report. Offline verification recomputes semantics, source identity, target
   bindings, and profile binding.

## Consequences

- Customers can qualify a managed service, Kubernetes operator, or self-managed
  PostgreSQL endpoint with the same evidence contract.
- The platform and qualifier gain no database mutation or cloud-provider
  authority; promotion remains a separately governed operation.
- A higher timeline proves PostgreSQL recovery/promotion, not the provider's
  topology, zone transition, fencing, split-brain prevention, or zero data loss.
  Those claims require provider and environment evidence.
- PostgreSQL 14 or newer is the supported qualification floor. The runtime may
  impose a higher version through its separate deployment prerequisites.
