# ADR 0072: Deployment-wide telemetry export health

**Status:** Accepted

## Context

ADR 0052 deliberately made exporter health process-local. That proves a single SDK exporter can reach its configured OTLP destination, but an operator querying one API replica cannot see workflow-worker or peer-replica delivery failures. Making the Collector a serving dependency would turn an observability outage into a product outage.

## Decision

Keep process-local exporter tracking as the source of each snapshot and add a failure-isolated internal heartbeat into the shared operational store. Identify a runtime only by a per-process SHA-256 pseudonym and closed component class. Retain recent snapshots for a bounded interval, mark missed heartbeats stale, retire graceful shutdowns, and expose a separate administrator/policy-protected deployment report.

Do not expose a public reporting route, raw workload identity, exporter configuration, provider details, or tenant telemetry. Do not couple the heartbeat to liveness, readiness, ingestion, investigation, or action execution. The deployment report describes recent observed members; orchestrator desired-state and absent-replica alerting remain deployment responsibilities.

## Consequences

- API and worker exporter loss is visible through one backend-neutral operation;
- crashed or partitioned reporters become explicitly stale before bounded expiry;
- failures to persist a heartbeat never interrupt customer workflows;
- shared-store unavailability makes the report unavailable instead of fabricating health;
- counters remain per process, so measured exporter availability windows and Collector queue/loss objectives remain separate work.
