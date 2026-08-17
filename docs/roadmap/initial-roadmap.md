# Initial implementation roadmap

**Status:** Working plan  
**Planning horizon:** Foundation through first design partner

**Implementation note (2026-08-17):** The repository contains one executable local reference slice through every phase: live reconciliation collection and checkpoint ingestion (Phase 1), durable evidence and deterministic investigations (Phase 2), repeated-run scoring (Phase 3), separation-of-duties one-shot actions plus an opt-in verified Kubernetes restart (Phase 4), and bounded plugin handshake metadata (Phase 5). These are implementation units, not completed phase exit gates. Remaining gate work is listed below and external pilot/legal/brand outcomes cannot be completed by repository code alone.

The roadmap is outcome-based. Dates should be added after team size and pilot constraints are known. Each phase ends with evidence that the next risk is worth taking.

## Phase 0 — foundation (current repository)

**Outcome:** One coherent project can be opened in Codex and extended without rediscovering product or architecture intent.

Delivered:

- product constitution, scope, glossary, open/commercial proposal;
- logical architecture, security baseline, investigation lifecycle, repository boundaries;
- OpenSRE reference analysis and separate brand research track;
- resource, event, agent, and plugin `v1alpha1` contracts and examples;
- runnable resource-ingestion vertical slice with ports/adapters;
- Python and TypeScript SDK boundaries;
- Helm/Kubernetes deployment skeleton, strict customer values contract, immutable application image selection, controlled schema-migration hook, guarded existing-certificate TLS Ingress, least-authority scheduled logical backups, immutable bounded CI dependencies, install/upgrade/recovery quality gates, and an attested unsigned release bundle.

Exit gate: `make verify` passes and a new contributor can trace resource ingress through authorization, storage, and event emission.

## Phase 1 — resource and event substrate

**Outcome:** A single-tenant local deployment maintains a correct Kubernetes resource graph and replayable timeline.

Deliverables:

- choose and document authoritative graph/observation store and event transport;
- formalize evidence, investigation request/report, pagination, error, and integration-config contracts;
- Kubernetes collector plugin for cluster, namespace, workload, pod, service, ingress, config, and ownership relationships;
- idempotent ingestion, source checkpoints, deduplication, reconciliation, deletion/tombstone semantics;
- event projection and query API for resource history and neighborhood;
- service identity/authentication for collectors;
- tenant-partition and replay tests, backup/restore experiment, and ingestion SLOs.

Exit gate: a seeded cluster can be rebuilt from observations/events; graph correctness and source lag are measurable.

Reference slice delivered: explicit live kind collection, safe image-pull status normalization, host-side collection validation, durable PostgreSQL projection/event/evidence substrate, complete-snapshot membership, deterministic tombstones, exact-result replay, atomic reconciliation/checkpoint/provider-cursor commit, per-API-path list/watch resume with `410 Gone` full-reconciliation recovery, tenant-scoped at-least-once outbox delivery through local structured-log and authenticated HTTPS publishers, finite terminal quarantine with a privileged value-minimized exact-tenant delivery-health report, transport-neutral rolling publication SLOs from mature durable outbox cohorts, exact-generation quarantine recovery through the one-shot governed-action chain, a privileged tenant-scoped projection drift/rebuild command from accepted observation history, a measured full-schema PostgreSQL backup/restore experiment with canonical integrity verification, an opt-in database-only Helm CronJob that writes checksum-complete logical backups to customer-managed storage, a live isolated restore gate, tenant-scoped point-in-time freshness/source-lag telemetry, optional outbound OTLP/HTTP freshness metrics, automatic exact-source sampling from a non-interactive tenant-enrolled worker, and privileged process-local plus shared-store API/worker exporter-delivery health. The local Phase 1 exit gate is complete; production broker selection, storage durability/retention/PITR, workload objectives, long-term/regional SLO aggregation and alerting, bulk-recovery policy, and receiver interoperability remain Phase 3 deliverables.

## Phase 2 — evidence and investigation vertical slice

**Outcome:** A read-only Kubernetes incident produces a structured, evidence-backed report.

Deliverables:

- durable evidence storage and investigation request/report APIs against the Phase 1 contracts;
- providers for Kubernetes events/status, deployment changes, logs, metrics, and repository/runbook context;
- bounded agent runtime with tool caps, duplicate cache, context/cost/time budgets, cancellation, and terminal reasons;
- correlation between alert, affected resource neighborhood, and recent changes;
- structured hypotheses, contradictions, unknowns, and evidence citations;
- CLI/API report surface and traceable investigation timeline;
- model-provider abstraction with data-handling policy and redaction.

Exit gate: target scenarios meet required evidence and root-cause scoring in repeated runs within declared latency and cost.

Reference slice delivered: durable evidence artifacts, authenticated investigation POST/GET/status/cancel APIs, asynchronous job submit/get/cancel APIs, tenant-scoped PostgreSQL dispatch claims, heartbeats, bounded retries and stale-claim takeover, pre-tool durable requests and bounded execution leases, cooperative cancellation, conservative stale-execution recovery without tool replay, bounded deterministic agent, resource-state and value-minimized resource/deployment-change providers, backend-neutral metric, log, and repository/runbook context request/result contracts, replaceable `TelemetryMetricsBackend`, `TelemetryLogsBackend`, and `ContextDocumentsBackend` ports with honest no-data defaults, real Prometheus-compatible metric and Loki historical-log adapters, an allowlisted root-confined file context adapter with mandatory redaction and explicit untrusted-data semantics, exact request-scoped local credential resolution, a shared external HTTPS credential-broker client authenticated by explicitly projected workload identity, isolated tenant-bound OTLP metrics and logs evidence receivers with separate credentials, admission, OpenAPI, and NetworkPolicy, auditable risk-aware planning across request-declared and protected tenant-catalog event, context, change, metric, and log candidates under inherited scope and budgets, one bounded promotion of an accepted candidate after a provider gap releases capacity, unit-aware threshold assessment, ordered explicit and scope-end rolling two-window plus fixed-period seasonal baseline comparison, conservative document/log-record/change-count assessment, and value-minimized correlation from committed artifacts, normalized Kubernetes Event request/result contracts, a replaceable `KubernetesEventsBackend` with an honest no-data default and a live exact-scope read-only HTTPS adapter, real-cluster least-privilege conformance, event-condition assessment from committed artifacts, authenticated evidence APIs/SDKs, structured root-cause taxonomy, supporting and contradicting citations, unknowns, recommendations, and zero-model cost ledger. Remaining: a production credential issuer/interoperability gate, additional remote repository/service-catalog and customer telemetry backends/signals, receiver workload identity/mTLS and buffering, redaction policy expansion, calendar-aware or learned baseline policy, and a production model-provider decision.

ADR 0062 subsequently adds observe-only Evidence retention reporting and bounded, tenant-serialized artifact-byte cleanup without deleting immutable Evidence metadata or citations.

ADR 0063 subsequently adds provider-neutral public console discovery and browser Authorization Code + `S256` PKCE without introducing a client secret or weakening the existing access-token verifier. Customer issuer, credential, claim, redirect, CORS, MFA/session/logout, and revocation interoperability remain production qualification gates.

## Phase 3 — evaluation and operational hardening

**Outcome:** Agent changes can be promoted by measurable reliability rather than demos.

Deliverables:

- scenario format containing graph fixture, timeline, alert, expected root-cause class, required/forbidden evidence, and red herrings;
- deterministic replay and scored repeated-run harness;
- prompt/tool/model version tracking and regression dashboard;
- OpenTelemetry traces, metrics, logs, cost ledger, audit records, and privacy controls through a configurable OTLP endpoint/Collector;
- concurrency, crash recovery, cancellation, noisy-neighbor, and prompt-injection tests;
- SLOs for ingestion freshness, query availability, and investigation completion.

Exit gate: releases have comparable scorecards, and failures can be explained from platform telemetry.

Reference slice delivered: deterministic weighted scorer, hard gates, budget checks, red-herring resistance, adversarial instruction-boundary scoring with live context/log containment tests, repeated-run harness, durable tenant-scoped background dispatch with heartbeats and crash takeover, bounded process-local parallelism, rotated tenant polling, a deployment-wide same-tenant live-lease cap, a durable per-tenant outstanding-job admission ceiling, concurrent PostgreSQL verification of both limits, and a source-bound executable PostgreSQL profile covering 128-tenant admission, overload isolation, idempotency at capacity, first-pass claims, and terminal capacity release, durable pre-tool investigation leases, cooperative cancellation and conservative stale-execution recovery, auditable risk-aware cross-signal budget planning with protected tenant-catalog generation and one bounded provider-gap promotion, dependency-aware PostgreSQL/schema readiness, automatic exact-source freshness sampling, optional OTLP/HTTP exporters for bounded ingestion-freshness metrics and terminal investigation traces, privileged process-local plus shared-store API/worker exporter-delivery health with failure recovery and staleness detection, rolling sampled per-signal export-attempt SLOs, an authenticated console/API/SDK runtime version report tied to release revision and Helm image identity, normalized historical metric/log evidence boundaries, Docker-verified Prometheus and Loki query adapters, isolated tenant-bound OTLP metrics/logs evidence receivers, deterministic investigation metric/log selection, closed unit-aware threshold and log-count assessment, ordered explicit and scope-end rolling two-window plus fixed-period seasonal difference/ratio assessment, deterministic Kubernetes Event condition correlation, and a real-cluster verified read-only Kubernetes API adapter. The OTLP/Collector portability direction is accepted in ADR 0012, the first outbound metric adapter in ADR 0013, the backend-neutral metric query in ADR 0014, the Prometheus translation boundary in ADR 0015, the metrics receiver boundary in ADR 0016, the investigation selection/assessment boundaries in ADRs 0017–0019, ADR 0032, ADR 0034, ADR 0054, ADR 0075, and ADR 0076, the Kubernetes Event boundaries in ADRs 0020–0021, the log query/OTLP intake boundary in ADR 0023, the log investigation boundary in ADR 0024, the Loki adapter boundary in ADR 0025, lifecycle durability in ADR 0030, investigation trace export in ADR 0031, adversarial release gating in ADR 0033, dispatch in ADR 0039, tenant-fair admission in ADR 0060, bounded backlog admission in ADR 0061, local capacity evidence in ADR 0074, receiver isolation in ADR 0041, readiness in ADR 0042, freshness sampling in ADR 0043, exporter health and SLO measurement in ADRs 0052, 0072, and 0073, and runtime identity in ADR 0053. Remaining: calendar-aware or learned baselines, additional production backends/signals, receiver workload identity/mTLS, durable buffering decisions, Collector loss objectives and regional/burn-rate aggregation, customer-environment sustained workload/failover certification, expanded multilingual/indirect-injection coverage, expanded privacy controls, and measured release SLOs.

Update: ADR 0058 adds a deployment-configured rolling useful-completion SLO over durable asynchronous jobs, including explicit late, failed, cancelled, and unfinished misses. ADR 0059 adds a privacy-bounded query availability counter and duration histogram with explicit objective values over the replaceable OTLP metrics boundary. External ingress/synthetic coverage, long-term/regional aggregation, burn-rate policy, and alert routing remain open.

## Phase 4 — governed actions and workflows

**Outcome:** The platform can safely propose and execute a narrow reversible remediation.

Deliverables:

- action proposal/result and approval contracts;
- durable workflow state machine, idempotency, retries, expiry, and verification;
- policy decision service and explainable policy audit;
- one reversible Kubernetes action with dry-run and rollback;
- approval UI/API and separation-of-duties controls;
- credential broker for request-scoped, short-lived access.

Exit gate: duplicate delivery cannot duplicate impact; policy, approval, execution, and verification are fully reconstructable.

Reference slice delivered: versioned proposal/approval/result and execution-lifecycle contracts, investigation- and target-bound closed parameters, role separation, content-digested policy checks at proposal/approval/execution time, a durable one-shot pre-impact execution claim, concurrent duplicate rejection, fail-closed stale-lease recovery without replay, an exact-tenant background timer that atomically closes expired execution leases with one audit record, read-time expiry for unexecuted immutable proposals, atomic terminal result/state persistence, audit references, a default no-impact validator, an explicit Kubernetes API restart adapter with observed-UID and resource-version preconditions, server-side dry-run, brokered `resources:read` + `workloads:patch`, generation/readiness verification and prior-annotation rollback, a tenant-scoped paginated workflow read model and console controls for proposal, independent decision, one-shot execution, and verification review, plus production-configurable OIDC/JWKS authentication and an external HTTPS policy-decision adapter whose immutable snapshot references flow into action records. Remaining: customer issuer/policy-bundle and credential-issuer interoperability, and production-environment mutation/rollback gates.

Console onboarding update: the web surface now discovers a closed non-secret authentication profile and supports provider-neutral Authorization Code + `S256` PKCE while retaining local-token and issued-access-token fallbacks. Production qualification still requires a real customer issuer/policy bundle, exact redirect and CORS validation, claim mapping, and session/revocation behavior.

## Phase 5 — plugin SDK and first design partner

**Outcome:** An external contributor can build and operate a least-privilege integration without platform-internal imports.

Deliverables:

- plugin handshake, capability tokens, cancellation, structured errors, and conformance suite;
- out-of-process runner with signing, digest verification, permission enforcement, and resource limits;
- SDK generators or aligned hand-written Python/TypeScript SDKs;
- integration setup/verification experience and compatibility matrix;
- design-partner deployment, onboarding runbook, support/security channels, and feedback telemetry;
- licensing, contributor, trademark, and open/commercial decisions before public launch.

Exit gate: a plugin built only from public docs/SDK passes conformance and runs in a pilot without elevated control-plane credentials.

Reference slice delivered: session, invocation/result, status, cancellation, reconciliation, read mediation, proposal-only action mediation, and executable compatibility-report contracts; declared-capability subset enforcement; token reference/digest handling; expiry and limit framing; authenticated status/cancel/reconcile APIs and SDKs; live observer transport; Ed25519 publisher trust; digest-pinned pre-pulled OCI artifacts; a real out-of-process Docker conformance runner with no general network or container credentials, read-only non-root execution, bounded CPU/memory/swap/PIDs/files/tmpfs/time/input/output; exact-tenant PostgreSQL request claims that bind canonical content, serialize session limits across replicas, survive restarts, retain terminal results, replay only completed results, propagate durable cooperative cancellation, and allow policy-approved platform administrators to close post-deadline ambiguity without replay; request-scoped host-mediated JSON reads through a fresh Unix socket, protected destination/credential bindings, exact path/query limits, per-read policy and pre-egress audit, brokered host-only leases, direct no-redirect TLS, and bounded header-free JSON responses; proposal-only action grants bound to manifest type, target, dry-run policy, derived idempotency/expiry, current policy, pre-proposal audit, and the standard investigation-scoped governed action queue; a separately attested multi-platform bridge image in the verified release bundle; plus a Docker-enabled CI matrix that binds exact source, host, SDK, protocol, plugin, relay, offline, and mediated-read outcomes. Remaining: live customer provider and credential-broker interoperability qualification, customer action-provider interoperability, cancellation propagation into future owning integration workflows, multi-host customer certification, design-partner deployment, and public governance/legal decisions.

The local compatibility matrix now also includes a separately signed
`host-mediated-action-proposal` row. It exercises an isolated SDK-only action
provider through the trusted relay and real governed action service, then proves
the result remains pending approval with no approval or execution record.
`Customer action-provider interoperability` above now means qualification of a
real customer-owned plugin and environment; the local protocol gate is delivered.

## Parallel track — company and brand

This track never blocks technical discovery but must complete before a public launch:

- positioning interviews and category language;
- name architecture and screened candidate set;
- legal, trademark, domain, package, and social verification;
- visual/voice identity and migration plan from neutral identifiers;
- company, ownership, license, contributor, and trademark policies.

All working material stays under `docs/research/brand/` until accepted.

## Decision backlog

| Decision | Latest responsible phase | Evaluation criteria |
| --- | --- | --- |
| Graph/observation store | **Accepted: ADR 0004** | PostgreSQL 16–18 initially; revisit from measured temporal/graph workload |
| Durable event transport | **Accepted: ADR 0004** | PostgreSQL event log and outbox initially; external broker remains replaceable |
| Credential broker client | **Accepted: ADR 0022** | External HTTPS lease exchange with projected workload identity; issuer/product interoperability remains open |
| Workflow engine | Phase 3 end | Durable timers, approvals, retries, audit, self-hosting burden |
| Policy engine | **Accepted integration boundary: ADR 0037** | External engine remains customer-selectable; exact decisions require tenant-bound immutable snapshot references |
| Console authentication | **Accepted public-client boundary: ADR 0063** | Authorization Code + `S256` PKCE; customer issuer interoperability and session policy remain deployment gates |
| Plugin runtime | **Accepted profiles: ADRs 0038, 0064–0070** | Signed no-network execution, durable ownership, mediated reads, proposal-only actions, and executable compatibility evidence; customer interoperability remains |
| Model/provider strategy | Phase 2 start | Data policy, tool use, structured output, cost, evaluation stability |
| License and governance | Before public pilot | Community utility, commercial sustainability, contributor clarity |

## First ten implementation issues

1. **Completed:** Add evidence and investigation schemas plus examples.
2. **Completed:** Build schema validation in CI using a pinned validator.
3. **Completed:** Specify observation version/checkpoint and stale-write semantics.
4. **Completed:** Implement PostgreSQL-backed resource repository behind the application port.
5. **Completed:** Implement an outbox so resource updates and events are atomic.
6. **Completed:** Create Kubernetes collector plugin skeleton and conformance fixture.
7. **Completed:** Add graph neighborhood and resource timeline API queries.
8. **Completed:** Define the evaluation scenario contract and one known Kubernetes failure.
9. **Completed:** Implement evidence provider ports and content hashing/redaction metadata.
10. **Completed:** Add authentication middleware that derives tenant/actor context and removes development header trust.

Subsequent hardening completed a disabled-by-default, tenant-scoped, audited Evidence artifact-retention lifecycle while preserving immutable metadata and citations.
