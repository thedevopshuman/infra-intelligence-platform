# ADR 0154: Require verified PostgreSQL transport security in packaged profiles

**Status:** Accepted

**Date:** 2026-09-08

## Context

PostgreSQL is the initial authority for tenant resources, observations, events,
evidence, investigations, governed actions, audit records, and AI usage and cost
facts. The packaged workloads previously received one opaque database URL and
passed it directly to libpq. A production-shaped Helm configuration could
therefore satisfy preflight by naming the database Secret while the effective
connection used plaintext, encryption without server identity, an ambient CA,
or a DSN-supplied downgrade.

The customer PostgreSQL continuity qualifier in ADR 0130 uses
`sslmode=verify-full`, but that separate read-only observer does not prove how
the API, worker, receiver, migrator, or backup client connects. Ingress and OTLP
TLS also do not protect traffic between those workloads and the authoritative
database.

## Decision

1. Add one adapter-owned PostgreSQL connection-security policy with exactly two
   modes: `verify-full` and `insecure-local`. When a database-backed runtime is
   composed from environment, `verify-full` is the default.
2. `verify-full` requires an explicit readable CA file and at least one DNS or
   IP host. It rejects Unix-socket targets and DSN-supplied TLS policy fields,
   including attempted downgrade or replacement of the selected trust root.
   The adapter passes `sslmode=verify-full` and the explicit CA directly to
   libpq. Packaged Python clients reject ambient libpq transport/service
   variables at composition and connection time; they never temporarily change
   the shared process environment while concurrent clients connect.
3. `insecure-local` is an explicit development-only opt-in and forces
   `sslmode=disable`. It is never accepted by a current production deployment
   preflight profile. Repository-controlled Docker Compose and disposable Kind
   fixtures that use plaintext PostgreSQL declare this mode rather than relying
   on an implicit default.
4. Every packaged database consumer uses the same selected policy: API,
   workflow worker, isolated OTLP receiver, readiness probes, migration Job,
   maintenance command, and backup CronJob. The backup wrapper rejects TLS
   policy embedded in the database URL before invoking libpq tooling and never
   prints the URL or parsed connection fields. That utility-image boundary
   accepts only explicit TCP PostgreSQL URIs with a database path and no query
   or fragment. Keyword conninfo is rejected rather than partially parsed by a
   shell; existing backup Secrets using that syntax must be migrated.
5. The Helm chart mounts one separately referenced CA Secret and exact key
   read-only at a fixed path in every database client. The sanitized deployment
   profile records only mode and the already-rendered Secret/key references;
   the Secret value and connection URL remain outside reports.
6. Advance the closed preflight profiles to `production-core-v2` and
   `production-ai-finops-v1`. Both add the ordered
   `database-transport-security` check and require `verify-full` plus the exact
   CA dependency. Historical v1/v0 report shapes remain offline-readable but
   cannot satisfy the current production preflight.
7. Configuration failures use stable non-sensitive codes. A DSN, host,
   username, password, CA path, certificate, libpq error, or provider response
   must not cross a public readiness, HTTP, report, or log boundary.
8. Verified transport proves confidentiality and server identity for the
   selected connection. PostgreSQL availability, promotion, fencing, RPO/PITR,
   certificate issuance and rotation, and customer topology remain separate
   operating and qualification responsibilities.

## Consequences

- A production render can no longer treat possession of a database Secret as
  sufficient evidence of a protected database channel.
- All Python database paths and the separately packaged backup client share one
  declared security posture while preserving adapter and composition-root
  boundaries.
- Existing local plaintext environments require one visible configuration
  change. This is intentional; an unset mode no longer silently means
  plaintext.
- Customer CA rotation should use an overlap bundle and controlled workload
  rollout. The chart references existing Secrets and does not become a
  certificate issuer or lifecycle controller.
- Mutual TLS client identity, private-link policy, certificate revocation, and
  managed-database product selection remain future customer-profile decisions.

## Alternatives considered

- Requiring `sslmode=require` was rejected because encryption without hostname
  verification permits server impersonation.
- Trusting TLS query parameters inside the secret URL was rejected because the
  preflight cannot inspect Secret values without expanding its sensitive-data
  boundary and because one stale URL could override chart policy.
- Reusing the customer continuity observer was rejected because its independent
  least-privilege connection says nothing about the deployed application's
  transport.
- Bundling a private CA in the image or chart was rejected because database
  trust remains customer-owned and environment-specific.
