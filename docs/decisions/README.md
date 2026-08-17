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
| [0021](0021-read-only-kubernetes-event-api-adapter.md) | Accepted | Query exact-scope Kubernetes Event evidence through a read-only HTTPS API adapter |
| [0022](0022-external-workload-identity-credential-broker.md) | Accepted | Resolve provider leases through an external workload-identity credential broker |
| [0023](0023-backend-neutral-log-evidence-and-otlp-intake.md) | Accepted | Normalize log evidence and accept selected OTLP logs separately |
| [0024](0024-investigation-log-selection-and-assessment.md) | Accepted | Select and assess committed log evidence conservatively |
| [0025](0025-loki-historical-log-evidence-adapter.md) | Accepted | Translate closed historical log selectors to tenant-scoped Loki queries |
| [0026](0026-resource-history-change-evidence.md) | Accepted | Derive bounded value-minimized changes from accepted resource history |
| [0027](0027-investigation-change-correlation.md) | Accepted | Assess committed value-minimized changes in investigations |
| [0028](0028-untrusted-context-evidence.md) | Accepted | Treat allowlisted repository/runbook content as untrusted Evidence |
| [0029](0029-investigation-context-correlation.md) | Accepted | Correlate context as untrusted data without copying prose or granting authority |
| [0030](0030-durable-investigation-lifecycle.md) | Accepted | Persist bounded investigation leases and cooperative cancellation before tools run |
| [0031](0031-otlp-investigation-trace-export.md) | Accepted | Export bounded terminal investigation traces over optional OTLP/HTTP |
| [0032](0032-scope-end-rolling-baseline.md) | Accepted | Derive auditable rolling metric windows from the accepted investigation scope |
| [0033](0033-adversarial-evidence-release-gate.md) | Accepted | Fail evaluation when instruction-shaped evidence crosses into report output |
| [0034](0034-auditable-cross-signal-planning.md) | Accepted | Plan request-declared evidence candidates across signals and budgets |
| [0035](0035-one-shot-action-execution.md) | Accepted | Claim one governed action attempt before impact and fail ambiguous recovery closed |
| [0036](0036-request-scoped-kubernetes-restart.md) | Accepted | Execute one opt-in UID-bound Kubernetes restart with dry-run, verification, and rollback |
| [0037](0037-oidc-and-external-policy-boundaries.md) | Accepted | Verify OIDC/JWKS identity and delegate exact decisions to a fail-closed external policy service |
| [0038](0038-signed-no-network-plugin-runner.md) | Accepted | Execute signed digest-pinned plugins in a bounded no-network Docker sandbox |
| [0039](0039-tenant-scoped-investigation-dispatch.md) | Accepted | Dispatch investigations through renewable tenant-scoped PostgreSQL worker leases without extending execution budgets |
| [0040](0040-action-timers-and-fail-closed-reconciliation.md) | Accepted | Reconcile expired action attempts atomically without replay and derive proposal expiry without mutation |
| [0041](0041-isolated-otlp-receiver-process.md) | Accepted | Isolate tenant-bound OTLP intake in a dedicated process, listener, credential, and network boundary |
| [0042](0042-dependency-aware-readiness.md) | Accepted | Separate process liveness from bounded PostgreSQL connectivity and schema readiness |
| [0043](0043-explicit-ingestion-freshness-sampling.md) | Accepted | Evaluate explicitly enrolled tenant/source freshness automatically from a non-interactive workflow worker |
| [0044](0044-tenant-scoped-outbox-delivery.md) | Accepted | Deliver leased tenant-scoped CloudEvents at least once through a replaceable bounded publisher |
| [0045](0045-controlled-helm-schema-migrations.md) | Accepted | Apply packaged PostgreSQL migrations through an explicit isolated Helm hook before serving rollout |
| [0046](0046-strict-helm-values-contract.md) | Accepted | Reject unknown, unsafe, and internally inconsistent Helm configuration before rollout |
| [0047](0047-attested-release-bundle.md) | Accepted | Bind install artifacts to one clean revision with checksums, SPDX SBOM, and SLSA provenance |
| [0048](0048-explicit-tls-ingress-and-upgrade-conformance.md) | Accepted | Keep Services internal while declaring exact TLS ingress and proving migration-safe Helm upgrades |
| [0049](0049-least-authority-scheduled-logical-backups.md) | Accepted | Schedule checksum-complete logical backups with database-and-PVC-only authority |
| [0050](0050-immutable-application-image-identity.md) | Accepted | Resolve every application workload from one immutable OCI digest when configured |
| [0051](0051-immutable-ci-execution-dependencies.md) | Accepted | Pin third-party CI actions and service images to reviewed immutable revisions |
| [0052](0052-process-local-telemetry-export-health.md) | Accepted | Track backend-neutral exporter outcomes without coupling API readiness to telemetry delivery |
| [0053](0053-authenticated-runtime-version-identity.md) | Accepted | Expose authenticated runtime identity from release and deployment evidence without guessing |
| [0054](0054-protected-investigation-signal-catalog.md) | Accepted | Freeze tenant-bound reviewed signal candidates without granting query, credential, or scope authority |
| [0055](0055-bounded-outbox-quarantine.md) | Accepted | Stop permanent publication failures at a finite tenant-scoped quarantine boundary |
| [0056](0056-governed-event-delivery-replay.md) | Accepted | Recover one exact quarantine generation through the existing one-shot governed-action chain |
| [0057](0057-event-delivery-slo-semantics.md) | Accepted | Measure rolling transport-neutral publication objectives from durable outbox outcomes |
| [0058](0058-investigation-completion-slo-semantics.md) | Accepted | Measure useful asynchronous investigation completion from durable job outcomes |
| [0059](0059-backend-neutral-query-availability-telemetry.md) | Accepted | Export bounded query availability and latency semantics through OTLP metrics |
| [0060](0060-tenant-fair-investigation-admission.md) | Accepted | Bound process and deployment-wide tenant investigation concurrency without widening authority |
| [0061](0061-bounded-tenant-investigation-admission.md) | Accepted | Bound each tenant's outstanding investigation backlog while preserving idempotent submission |
| [0062](0062-audited-evidence-artifact-retention.md) | Accepted | Preserve immutable Evidence metadata while expiring artifact bytes under explicit audited policy |
| [0063](0063-console-oidc-authorization-code-pkce.md) | Accepted | Let the browser console use a public OIDC client with Authorization Code and S256 PKCE |
| [0064](0064-durable-plugin-invocation-ownership.md) | Accepted | Claim plugin requests durably before execution and replay only stored terminal results |
| [0065](0065-plugin-invocation-cancellation-and-reconciliation.md) | Accepted | Persist cooperative plugin cancellation and close post-deadline ambiguity without replay |
| [0066](0066-host-mediated-plugin-read-connectivity.md) | Accepted | Keep plugins offline while mediating invocation-scoped, policy-checked provider reads |
| [0067](0067-attested-plugin-mediation-bridge-image.md) | Accepted | Ship the trusted mediation relay as a separately attested release image |
| [0068](0068-executable-plugin-compatibility-evidence.md) | Accepted | Generate exact-host plugin compatibility evidence from executable profiles |
| [0069](0069-manifest-bound-plugin-signatures.md) | Accepted | Authenticate the complete plugin declaration before granting runtime trust |
