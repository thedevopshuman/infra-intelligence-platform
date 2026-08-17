# ADR 0045: controlled Helm schema migrations

**Status:** Accepted
**Date:** 2026-08-17

## Context

PostgreSQL-backed API and OTLP receiver readiness verifies the latest packaged schema but intentionally never mutates it. Docker Compose can migrate its disposable local database on API startup, while Helm disables that behavior. The chart therefore had no complete path for a fresh durable install or controlled upgrade.

## Decision

The Helm chart adds an opt-in `database.migrations.enabled` Job that runs `python -m iip.adapters.postgres` as a `pre-install,pre-upgrade` hook. The migrator already serializes schema changes with a PostgreSQL advisory lock and records each packaged migration. Helm waits for the hook before replacing serving workloads; a failed migration fails the install or upgrade.

Migration remains explicit and disabled by default. Operators must provide the existing database Secret before installation and must choose when schema mutation is allowed. The API and receiver continue to run with auto-migration disabled and independently prove schema readiness.

The Job receives only the database URL Secret. It has no interactive authentication, policy, provider, telemetry, action, plugin, or event-publisher credentials; no service account token; a read-only root filesystem; bounded resources, retries, and wall time; and a dedicated temporary directory. When NetworkPolicy is enabled, a lower-weight pre-hook policy selects only the migration pod, denies ingress, and permits DNS plus the configured database destination.

## Consequences

- A fresh PostgreSQL-backed Helm install can reach readiness through one controlled chart operation.
- An application rollout cannot silently bypass a failed or missing schema transition.
- Migration Jobs are retained until the next hook run for inspection, then replaced with `before-hook-creation`.
- Database backup, compatibility review, maintenance-window policy, and rollback planning remain operator responsibilities. Application rollback does not imply automatic schema rollback.
