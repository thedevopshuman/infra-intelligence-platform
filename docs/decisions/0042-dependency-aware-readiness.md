# ADR 0042: dependency-aware readiness

**Status:** Accepted  
**Date:** 2026-08-17

## Context

The HTTP surfaces returned the same unconditional response from liveness and readiness. A process could therefore remain in load-balancer rotation while PostgreSQL was unreachable or before the schema required by that image had been migrated. Docker and Kubernetes used that response as their serving gate.

## Decision

`GET /healthz` is process liveness and remains dependency-free. `GET /readyz` calls an application-owned `ReadinessProbe`. The in-process profile becomes ready after successful composition. Every PostgreSQL-backed API or OTLP receiver opens a fresh connection under a one-to-ten-second configured timeout and verifies that the latest migration packaged with the image exists in `iip.schema_migrations`.

Any connection, query, missing-schema, or migration-version failure returns HTTP 503 with only `readiness.unavailable`. Provider exception text, database location, credentials, migration inventory, tenant information, and stack traces do not cross the public boundary. Readiness never mutates or applies migrations; migration ownership remains an explicit startup/job decision.

External telemetry, identity, policy, credential-broker, and Kubernetes providers remain request-scoped and fail closed on their owning calls. Readiness does not create synthetic authorization or provider requests merely to probe them.

## Consequences

- Kubernetes and Docker stop sending traffic to API and receiver processes that cannot use their authoritative store.
- Schema rollout ordering is observable and safe: an old schema cannot appear ready for a newer image.
- Liveness does not restart a healthy process during a transient database outage.
- Production deployments still need database availability objectives, connection-pool decisions, and alerting over readiness failures.
