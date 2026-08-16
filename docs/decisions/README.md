# Architecture decision records

Decision records are numbered, dated, and append-only after acceptance. A new decision supersedes an old one instead of rewriting history.

| ADR | Status | Decision |
| --- | --- | --- |
| [0001](0001-modular-contract-first-foundation.md) | Accepted | Modular, contract-first foundation |
| [0002](0002-cloudevents-envelope.md) | Accepted | CloudEvents-compatible event envelope |
| [0003](0003-neutral-project-name.md) | Accepted | Neutral placeholder naming |
| [0004](0004-postgresql-observation-store-and-outbox.md) | Accepted | PostgreSQL observation/event store and transactional outbox |
| [0005](0005-credential-derived-request-identity.md) | Accepted | Credential-derived request identity behind a replaceable authenticator |
| [0006](0006-deterministic-investigation-and-dry-run-actions.md) | Accepted | Deterministic investigation baseline and dry-run-only governed action authority |
| [0007](0007-reconciliation-membership-and-tombstones.md) | Accepted | Complete-snapshot membership and deterministic resource tombstones |
| [0008](0008-observation-history-projection-rebuild.md) | Accepted | Accepted observation history is the recovery authority for rebuildable serving projections |
| [0009](0009-provider-cursor-sets-and-watch-recovery.md) | Accepted | Provider cursor sets commit atomically and expired Kubernetes watches recover through full reconciliation |
| [0010](0010-postgresql-backup-restore-verification.md) | Accepted | PostgreSQL logical backups require full-state and projection recovery verification |
| [0011](0011-ingestion-freshness-semantics.md) | Accepted | Ingestion freshness derives from committed checkpoints, accepted observations, and durable delivery state |
| [0012](0012-opentelemetry-portability-boundary.md) | Accepted | OpenTelemetry is a replaceable telemetry interchange, not a storage or query authority |
| [0013](0013-otlp-http-ingestion-metrics-export.md) | Accepted | Export bounded ingestion freshness measurements over optional, failure-isolated OTLP/HTTP |
| [0014](0014-backend-neutral-telemetry-evidence-query.md) | Accepted | Normalize bounded historical metric queries behind a replaceable telemetry evidence port |
| [0015](0015-prometheus-telemetry-evidence-adapter.md) | Accepted | Use an allowlisted Prometheus adapter with exact request-scoped credential resolution |
| [0016](0016-tenant-bound-otlp-metrics-receiver.md) | Accepted | Accept selected OTLP metrics through tenant-bound, allowlisted Evidence channels |
| [0017](0017-investigation-telemetry-selection.md) | Accepted | Select bounded telemetry queries inside investigations |
| [0018](0018-evidence-aware-metric-assessment.md) | Accepted | Assess stored metric evidence with declared threshold rules |
| [0019](0019-baseline-window-telemetry-assessment.md) | Accepted | Compare ordered telemetry windows inside one committed artifact |
| [0020](0020-kubernetes-event-evidence-and-correlation.md) | Accepted | Normalize Kubernetes Events as evidence before investigation correlation |
