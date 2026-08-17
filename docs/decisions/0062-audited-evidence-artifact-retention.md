# ADR 0062: Preserve immutable Evidence metadata while expiring artifact bytes

**Status:** Accepted
**Date:** 2026-08-17

## Context

Evidence already declares a retention class and optional expiry, but both PostgreSQL and the local store retain artifact bodies forever. Deleting whole records would break historical investigation citations and provenance; silently enabling a default cleanup policy could conflict with customer legal, backup, or incident-response requirements. A process-local cleanup counter would also race across worker replicas and tenants.

## Decision

Preserve the immutable Evidence document, content hash, logical storage reference, and citations after expiry. Delete only artifact bytes and mark their internal storage lifecycle atomically. Metadata reads continue to work; internal artifact reads return unavailable after deletion.

Use an explicit `expiresAt` when present. Otherwise calculate expiry from `recordedAt` and the deployment duration for `ephemeral`, `standard`, or `extended`. Never expire `legal-hold`. Automatic cleanup is disabled by default and runs only for explicitly enrolled worker tenants after customer configuration enables it.

Bound each pass to a configured batch. PostgreSQL Evidence commits and cleanup share a namespaced tenant-keyed transaction advisory lock, so each pass counts and updates current committed artifact state without blocking another tenant or colliding with investigation admission locks. Byte deletion plus one aggregate audit record commit together. The public administrator endpoint is observation-only. Worker and endpoint both pass exact tenant, policy digest, and action through the policy decision point. Logs and audit records contain aggregate counts only.

## Consequences

- historical reports keep stable, verifiable citations while expired artifact bodies are unavailable;
- legal holds override all duration and explicit-expiry processing;
- multiple workers cannot double-expire or double-audit one tenant batch;
- operators can observe eligible backlog before enabling cleanup and after every pass;
- backups may contain artifact bytes captured before expiry, so backup lifecycle must match customer policy;
- physical compaction, external object storage, encryption/KMS integration, restore-time retention reconciliation, and customer-configurable metadata retention remain separate decisions.
