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
