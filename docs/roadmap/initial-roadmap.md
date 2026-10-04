# Initial implementation roadmap

**Status:** Working plan  
**Planning horizon:** Foundation through public v1 and design-partner adoption

**Release sequencing (2026-10-04):** The owner selected public open-source v1
first. Follow the [public v1 release plan](public-v1-release-plan.md) for that
launch; the phase outcomes and optional private-pilot admission gates below
remain in force for their own scopes.

**Implementation note (2026-08-17):** The repository contains one executable local reference slice through every phase: live reconciliation collection and checkpoint ingestion (Phase 1), durable evidence and deterministic investigations (Phase 2), repeated-run scoring (Phase 3), separation-of-duties one-shot actions plus an opt-in verified Kubernetes restart (Phase 4), and bounded plugin handshake metadata (Phase 5). These are implementation units, not completed phase exit gates. Remaining gate work is listed below and external pilot/legal/brand outcomes cannot be completed by repository code alone.

The roadmap is outcome-based. Dates should be added after team size and pilot constraints are known. Each phase ends with evidence that the next risk is worth taking.

The separately sequenced [AI FinOps roadmap](ai-finops-roadmap.md) extends this
foundation with an OpenTelemetry-native AWS Bedrock vertical slice. Its
contract, architecture, metadata-only trace intake, usage ledger, and
versioned pricing runtime are delivered. The first evidence-backed
context-growth rule and its privacy-bounded OTLP aggregate metric projection
are also executable. A second source-bound retry-amplification rule now maps
provider attributes through protected channel policy and reports an unresolved
monetary status rather than guessing billed retry cost. A third rule now
requires an immutable workload suitability report before comparing a qualified
candidate-model cohort with the reference-model cohort and calculating a
scenario saving. The reference dashboard topology and separate pinned official
botocore `Converse`/`ConverseStream`
offline interoperability gates are executable. A separately installed
metadata-only OTel adapter now maps provider cache counters and corrects
Bedrock uncached input into OTel total-input semantics for both operations;
reasoning remains explicitly unresolved. A protected, expiry-bound
customer qualification workflow now binds one explicitly selected live
model/region/operation to the clean source and immutable release image while
retaining no credential, target, prompt, response, request ID, or token count;
running that billable customer call remains an external gate. Public Bedrock
price acquisition
is now an executable exact-source operation: a protected mapping turns a
retained official Price List Bulk API snapshot into the provider-neutral
catalog and minimized reproducibility evidence before separate qualification
and runtime promotion. Organizational approval, private rates, and invoice
reconciliation remain outside that import claim.
The first Phase B unit is also executable: protected effective-time policies
map observed service/resource identity to separate immutable application/team
attribution facts, including explicit unallocated coverage. Bounded
generation-bound allocation queries, cost joins, privacy-safe export
dimensions, and application/team dashboard views are executable.
The local Phase C gate is also executable: a separately authenticated
OpenAI-shaped trace reaches the same normalized ledger, data-driven price
engine, protected allocation views, and provider-neutral Grafana dashboard.
An isolated pinned official OpenAI Python profile proves offline SDK and
instrumentation interoperability while conservatively leaving missing usage
meters unresolved; live-provider and streaming qualification remain.

## Phase 0 — foundation (current repository)

**Outcome:** One coherent project can be opened in Codex and extended without rediscovering product or architecture intent.

Delivered:

- product constitution, scope, glossary, open/commercial proposal;
- logical architecture, security baseline, investigation lifecycle, repository boundaries;
- OpenSRE reference analysis and separate brand research track;
- resource, event, agent, and plugin `v1alpha1` contracts and examples;
- runnable resource-ingestion vertical slice with ports/adapters;
- Python and TypeScript SDK boundaries;
- Helm/Kubernetes deployment skeleton, strict customer values contract, immutable application image selection, controlled schema-migration hook, guarded existing-certificate TLS Ingress, a controller-neutral value-free external-secret handoff with exact-key/rotation/least-authority local conformance, least-authority scheduled logical backups, surge-first zero-unavailable API/worker/receiver rollouts, component-specific disruption budgets, hard topology spread, bounded API/receiver endpoint and request draining, immutable bounded CI dependencies, source and packaged install/upgrade/recovery quality gates, data-preserving packaged N-1 application rollback/re-upgrade conformance with sustained zero-failure authenticated Service reads and a blocked in-flight read, a manifest-bound environment-scoped qualification report, closed non-secret production core and AI FinOps values examples plus a minimized static/explicit-context customer deployment preflight, an attested unsigned release bundle, digest-preserving exact-index registry publication with retained unsigned evidence, a tag-triggered protected GitHub release workflow, an exact-digest organizational signature-verification boundary with explicit non-promotable local evidence, and exact-SBOM vulnerability qualification under pinned scanner, fresh database, closed threshold, and expiring-exception policy. Activating the workflow still requires an accepted repository namespace, protected release environment, tag rules, GHCR package policy, and organizational ownership.

The local release evidence is also aggregated into one exact-bundle
`ReleaseReadinessReport`. It fails closed unless all 19 repository-controlled
reports match and always keeps the eight customer, organizational, live
provider, design-partner, and legal/brand gates explicit. It is a local
candidate inventory, not a production promotion decision.

Packaged PostgreSQL transport now has an explicit fail-closed policy rather
than inheriting security from an opaque Secret URL. Current production
preflight profiles require hostname-verified libpq TLS and a separately
referenced CA for every API, worker, receiver, migration, maintenance, and
backup path; plaintext remains a named local-fixture exception. Database PKI
lifecycle, private networking, automatic failover, fencing, RPO/PITR, and
regional recovery remain customer qualification work.

Customer AI FinOps prerequisites also have a separate minimized aggregate. It
binds the exact local candidate/runtime, customer deployment/receiver, live
Bedrock streaming, and production catalog reports under one protected profile.
The result remains `prerequisite-aggregation-only`. A separate explicitly
enabled orchestrator now makes one live Bedrock streaming call, exports its
metadata-only span through the selected customer OTLP route, observes the exact
usage/attribution/cost records, requires the protected-application Prometheus
aggregate to advance, verifies the Grafana dashboard panels, and emits a
minimized source-bound report. The repository mechanism is complete; executing
that billable flow with customer credentials and endpoints remains external.

A separate customer pilot readiness aggregate now joins the exact local
candidate, registry publication, organizational signatures, qualified customer
deployment, post-deployment bounded read load, bounded sustained core workload,
customer-approved planned-failure overlap, AI prerequisites, and
same-invocation result. The v2 semantic level adds the current post-deployment
`ai-finops-v0` operational-alert report as the tenth source and binds its exact
release, cluster, namespace, protected profile/binding set, and Prometheus
target. Its `design-partner-candidate` status is deliberately a private
preflight rather than pilot acceptance, production certification, or public-
launch approval. The historical nine-input v1 aggregate is not current
admission evidence.

The first private-pilot technical scope and ordered operating handoff are now
explicit. Current release bundles include a checksum-bound documentation
archive with support/security policies, onboarding, minimized diagnostics,
qualification order, privacy-first feedback, rollback, and decommissioning
guidance. It establishes no customer contact, staffed channel, response
objective, approval, or production claim.

The handoff also carries an optional privacy-bounded Prometheus Operator rule
profile for component telemetry absence, backend-observed availability,
freshness, local recording, and AI coverage signals. Customer rule selection,
independent Collector/backend monitoring, notification routes, contacts,
escalation, and long-window/regional operation remain external deployment
gates.
Both production preflight profiles now require the closed policy, and cluster
mode proves exact PrometheusRule discovery plus the target namespace before
installation. Those prerequisites do not infer Prometheus selection or
notification delivery. A separate read-only customer qualifier now observes
all `ai-finops-v0` rules, expected heartbeats, router readiness, and one
synthetic firing/recovery receipt; ADR 0152 requires that evidence in pilot
readiness without transferring monitoring authority.

One fail-closed local release command now composes the source, integration,
recovery, multi-node availability, bundle, packaged N-1, vulnerability, and
19-evidence readiness workflow while retaining each owning report and refusing
dirty or pre-existing candidates. It does not collapse the eight external
customer, organizational, provider, pilot, or governance gates into a local
claim.

A separate source-bound AI FinOps sustained-load gate now exercises the full
single-host Docker path under a bounded fixed-rate synthetic provider mix. It
measures scheduler misses, Collector acceptance, durable usage persistence,
asynchronous attribution and cost completion, direct commit-bound usage-ledger
replay idempotency, stage latencies, pipeline drain, cohort-isolated Prometheus
convergence, and Grafana availability without retaining sensitive or
high-cardinality values. Its exact fixture policy/catalog and attribution/cost
engine generations scope the database measurements, while the runner inspects
both running image identities. Its 9,984-span maximum leaves the wrapper's
15-record seed below the allocation exporter's 10,000-record ceiling. This is a
local regression profile, not
customer workload, provider, billing, failure, backend
lifecycle, long-window SLO, or HA evidence. It is intentionally not the
twentieth release-readiness input: cross-architecture calibration and an
explicit v2 readiness migration remain before that promotion.

Post-install first response now has a separate privacy-minimized
`DeploymentDiagnosticReport`. An operator selects an exact context, namespace,
release, and immutable image digest; the read-only tool retains only aggregate
rollout, pod-health, and identity evidence. Raw object names, logs, messages,
URLs, customer identifiers, and Secret values remain outside the artifact.

Customer identity prerequisites now have a separate external qualification
profile and minimized report. It binds one protected expected identity and
issuer configuration to live discovery/JWKS/CORS, deployed console discovery,
API session derivation, tampered-token denial, and exact release identity. The
customer deployment aggregate consumes that report as its seventh artifact.
Interactive login, MFA, session/logout, disablement/revocation latency, and
issuer/key/certificate rotation and availability remain customer-owned gates.

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

Reference slice delivered: explicit live kind collection, safe image-pull status normalization, host-side collection validation, durable PostgreSQL projection/event/evidence substrate, complete-snapshot membership, deterministic tombstones, exact-result replay, atomic reconciliation/checkpoint/provider-cursor commit, per-API-path list/watch resume with `410 Gone` full-reconciliation recovery, tenant-scoped at-least-once outbox delivery through local structured-log and authenticated HTTPS publishers, finite terminal quarantine with a privileged value-minimized exact-tenant delivery-health report, transport-neutral rolling publication SLOs from mature durable outbox cohorts, exact-generation quarantine recovery through the one-shot governed-action chain, a privileged tenant-scoped projection drift/rebuild command from accepted observation history, source-bound complete-schema PostgreSQL logical recovery plus local physical streaming/promotion/named-target-PITR qualification reports with canonical row, safe sequence, and projection verification, an opt-in database-only Helm CronJob that writes checksum-complete logical backups to customer-managed storage, a live isolated restore gate, tenant-scoped point-in-time freshness/source-lag telemetry, optional outbound OTLP/HTTP freshness metrics, automatic exact-source sampling from a non-interactive tenant-enrolled worker, privileged process-local plus shared-store API/worker exporter-delivery health, a provider-neutral customer writer-endpoint promotion observer using native WAL timeline evidence, pinned official-Collector delivery qualification against one exact customer receiver, and a bounded optional Prometheus rule adapter. The local Phase 1 exit gate is complete; production broker selection, sustained-write storage durability/retention, customer-hosting automatic-failover topology/fencing/RPO/PITR and disaster-recovery qualification, workload objectives, long-term/regional SLO aggregation and customer-owned alert delivery, bulk-recovery policy, and permanent customer Collector/PKI/queue lifecycle qualification remain Phase 3 deliverables.

Readiness update: the workflow worker now exposes only a private liveness and
required-store readiness listener. Helm and Docker use it to reject stale
schema or unavailable PostgreSQL state, and shutdown removes readiness before
bounded work is joined. This is a deployment prerequisite, not worker
throughput or disruption-continuity evidence.

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

Reference slice delivered: durable evidence artifacts, authenticated investigation POST/GET/status/cancel APIs, asynchronous job submit/get/cancel APIs, tenant-scoped PostgreSQL dispatch claims, heartbeats, bounded retries and stale-claim takeover, pre-tool durable requests and bounded execution leases, cooperative cancellation, conservative stale-execution recovery without tool replay, bounded deterministic agent, resource-state and value-minimized resource/deployment-change providers, backend-neutral metric, log, and repository/runbook context request/result contracts, replaceable `TelemetryMetricsBackend`, `TelemetryLogsBackend`, and `ContextDocumentsBackend` ports with honest no-data defaults, real Prometheus-compatible metric and Loki historical-log adapters, an allowlisted root-confined file context adapter with mandatory redaction and explicit untrusted-data semantics, and a protected GitHub Contents adapter with immutable commit-plus-blob provenance, exact brokered read authority, direct verified HTTPS, explicit egress, source-bound local compatibility evidence, and a separately enabled customer GitHub Cloud/Enterprise gate for one broker-qualified exact-commit-and-blob document read with content-free minimized evidence, exact request-scoped local credential resolution, a shared external HTTPS credential-broker client authenticated by explicitly projected workload identity, source-bound real-TLS local compatibility evidence for identity, exact policy, rotation/revocation, audit, outage, and recovery, plus a customer-environment prerequisite that proves selected exact-authority issuance and six single-field denials while retaining no identity or credential values, isolated tenant-bound OTLP metrics and logs evidence receivers with separate credentials, CA-verified intermediate-chain SPIFFE workload identity, PostgreSQL-before-success durability, a validated customer-Collector persistent queue profile, admission, OpenAPI, NetworkPolicy, and executable compatibility evidence, auditable risk-aware planning across request-declared and protected tenant-catalog event, context, change, metric, and log candidates under inherited scope and budgets, one bounded promotion of an accepted candidate after a provider gap releases capacity, unit-aware threshold assessment, ordered explicit and scope-end rolling two-window plus fixed-period seasonal baseline comparison, conservative document/log-record/change-count assessment, and value-minimized correlation from committed artifacts, normalized Kubernetes Event request/result contracts, a replaceable `KubernetesEventsBackend` with an honest no-data default and a live exact-scope read-only HTTPS adapter, real-cluster least-privilege conformance, event-condition assessment from committed artifacts, authenticated evidence APIs/SDKs, structured root-cause taxonomy, supporting and contradicting citations, unknowns, recommendations, zero-model cost ledger, and content-addressed exact-tenant policies that add bounded email/IPv4 Evidence detectors without weakening mandatory credential redaction. Remaining: a production credential issuer and customer lifecycle/HA/audit qualification, additional customer repository documents and remote service-catalog interoperability beyond the first exact GitHub qualification, additional customer telemetry backends/signals, further customer PKI/Collector interoperability (OCSP, customer-specific chain and rotation cadence, CRL distribution qualification), additional redaction value classes/media and source-specific classification, learned baseline policy, and a production model-provider decision.

ADR 0062 subsequently adds observe-only Evidence retention reporting and bounded, tenant-serialized artifact-byte cleanup without deleting immutable Evidence metadata or citations.

ADR 0063 subsequently adds provider-neutral public console discovery and browser Authorization Code + `S256` PKCE without introducing a client secret or weakening the existing access-token verifier. ADR 0078 adds source-bound local real-TLS evidence for exact claims, cache/refresh, key rotation/removal, redirect denial, outage/recovery, and minimized discovery. ADR 0116 separately proves the actual authorization redirect, `S256` token exchange, exact-origin CORS, public-client, replay, and token-to-API path over real local TLS. ADR 0131 adds a real customer issuer prerequisite report for exact discovery/JWKS/CORS, claims, deployed session, and release binding. Interactive customer login, MFA/session/logout, disablement/revocation latency, certificate/key rotation, and issuer availability remain production qualification gates.

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

Reference slice delivered: deterministic weighted scorer, hard gates, budget checks, red-herring resistance, adversarial instruction-boundary scoring with live context/log containment tests and a shared multilingual, multi-technique adversarial corpus, repeated-run harness, durable tenant-scoped background dispatch with heartbeats and crash takeover, bounded process-local parallelism, rotated tenant polling, a deployment-wide same-tenant live-lease cap, a durable per-tenant outstanding-job admission ceiling, concurrent PostgreSQL verification of both limits, and a source-bound executable PostgreSQL profile covering 128-tenant admission, overload isolation, idempotency at capacity, first-pass claims, and terminal capacity release, durable pre-tool investigation leases, cooperative cancellation and conservative stale-execution recovery, auditable risk-aware cross-signal budget planning with protected tenant-catalog generation and one bounded provider-gap promotion, dependency-aware PostgreSQL/schema readiness, automatic exact-source freshness sampling, optional OTLP/HTTP exporters for bounded ingestion-freshness and query/receiver availability metrics plus terminal investigation traces, privileged process-local plus shared-store API/worker/receiver exporter-delivery health with failure recovery and staleness detection, rolling sampled per-signal export-attempt SLOs, a two-window sampled export-attempt burn-rate report, a customer-Collector-observed sending-queue depth and send-loss objective sourced from the Collector's own self-metrics, an authenticated console/API/SDK runtime version report tied to release revision and Helm image identity, normalized historical metric/log evidence boundaries, Docker-verified Prometheus, Loki, and OpenSearch query adapters, isolated tenant-bound OTLP metrics/logs evidence receivers with CA-verified SPIFFE mTLS, PostgreSQL-before-success semantics, a validated persistent Collector queue profile, and executable compatibility evidence, deterministic investigation metric/log selection, closed unit-aware threshold and log-count assessment, ordered explicit and scope-end rolling two-window plus fixed-period or calendar-day-aligned seasonal difference/ratio assessment, deterministic Kubernetes Event condition correlation, a real-cluster verified read-only Kubernetes API adapter, and a protected customer-environment gate for a bounded sustained mix of exact-release API reads, durable OTLP metrics, and asynchronous investigations. The OTLP/Collector portability direction is accepted in ADR 0012, the first outbound metric adapter in ADR 0013, the backend-neutral metric query in ADR 0014, the Prometheus translation boundary in ADR 0015, the metrics receiver boundary in ADR 0016, the investigation selection/assessment boundaries in ADRs 0017–0019, ADR 0032, ADR 0034, ADR 0054, ADR 0075, and ADR 0076, the Kubernetes Event boundaries in ADRs 0020–0021, the log query/OTLP intake boundary in ADR 0023, the log investigation boundary in ADR 0024, the Loki adapter boundary in ADR 0025, the OpenSearch adapter boundary in ADR 0084, lifecycle durability in ADR 0030, investigation trace export in ADR 0031, adversarial release gating in ADR 0033, dispatch in ADR 0039, tenant-fair admission in ADR 0060, bounded backlog admission in ADR 0061, local capacity evidence in ADR 0074, receiver isolation in ADR 0041, readiness in ADR 0042, freshness sampling in ADR 0043, exporter health and SLO measurement in ADRs 0052, 0072, and 0073, runtime identity in ADR 0053, receiver workload identity/buffering in ADR 0080, receiver availability in ADR 0081, multi-window burn-rate calculation in ADR 0082, calendar-aware seasonal baseline alignment in ADR 0083, the OpenSearch log backend in ADR 0084, expired-client-certificate rejection evidence in ADR 0085, the multilingual/technique-diverse adversarial instruction corpus in ADR 0086, the Collector-observed queue/loss objective in ADR 0087, CRL-based revoked-client-certificate rejection evidence in ADR 0088, fail-closed CRL validity-window enforcement in ADR 0089, intermediate-chain plus CRL-rollout evidence in ADR 0090, sustained customer core-workload qualification in ADR 0144, and customer-approved planned-failure overlap qualification in ADR 0146. Remaining: learned baselines, further production backends/signals, further customer PKI and Collector interoperability (OCSP, customer-specific chain and rotation cadence, CRL distribution qualification), regional aggregation of the Collector queue/loss objective, representative production-traffic and involuntary/automatic failover certification, further design-partner-sourced multilingual/indirect-injection corpora and paraphrase/obfuscation coverage, expanded privacy controls, and measured release SLOs.

AI economics qualification update: ADR 0153 adds an explicitly invoked local
fixed-rate synthetic workload over the complete Docker
Collector-to-ledger-to-cost-to-dashboard path. The source-bound report makes
scheduler, persistence, asynchronous completion, latency, drain, usage-ledger
replay, and isolated aggregate convergence regressions comparable without
claiming customer capacity. Replay is paced through the direct commit-bound
receiver only after exact original durability, and database joins require the
exact fixture policy/catalog and engine generations. Its 9,984-span maximum
preserves the rolling allocation exporter's 10,000-record ceiling after the
wrapper seeds the 15-record functional fixture. Cross-architecture calibration
and a future versioned release-readiness promotion remain open.

Privacy update: ADR 0115 delivers the first expanded privacy control through
content-addressed exact-tenant policy rules for bounded email and validated-IPv4
detectors while preserving non-configurable credential inspection. Additional
value classes, media inspectors, and source-specific classification remain.

Qualification update: ADR 0105 adds a direct no-redirect HTTPS ingress probe
with aggregate-only source/target-bound availability and latency evidence. This
closes the portable bounded external qualification harness described above;
continuous multi-region probing, long-term aggregation, and customer
notification operation remain open. ADR 0148 supplies an optional bounded
Prometheus rule profile without claiming those customer-owned outcomes. ADR
0150 adds a traffic-independent OTel component heartbeat and missing-series
rules while leaving independent backend monitoring and notification delivery
external. ADR 0151 adds a read-only customer-environment qualifier for the
documented Prometheus/Alertmanager adapter: it binds loaded and healthy rules,
positive component heartbeats, router readiness, and one current synthetic
firing/recovery receipt. The customer still owns the temporary rule, receipt
service, routes, contacts, escalation, HA, regional aggregation, and real
failure exercises.

Deployment update: ADR 0109 adds zero-unavailable worker and receiver rollouts,
component-specific disruption budgets, chart-generated hard topology spread,
and bounded OTLP active-request draining. These close avoidable application
placement and planned-disruption gaps; involuntary node loss, customer ingress,
database, storage, sustained-load, and regional availability remain external
qualification work.

Qualification update: ADR 0110 adds an owned three-node Kind gate that commits
one real OTLP metric, drains one worker through the Eviction API, requires the
exact expected capacity/PDB/domain state for every application component, and
observes zero failed API or OTLP Service probes through recovery. This closes
the local planned-disruption evidence gap; involuntary loss, shared database,
customer-cluster, sustained-load, and regional availability remain external.

Processing-continuity update: ADR 0128 upgrades that source-bound gate to the
`local-multi-node-kind-v2` profile. Every OTLP probe now commits a non-empty
normalized metric, and a durable investigation must complete after baseline,
after the target node is fully drained, and after recovery. This closes the
local worker/receiver processing-continuity gap; the equivalent customer
environment, shared-database failure, sustained throughput, and regional
profiles remain external qualification work.

Customer-continuity update: ADR 0123 adds a separately enabled customer
profile that overlaps a sustained direct verified-HTTPS probe with one
UID-preconditioned, PDB-governed API pod Eviction. It retains exact-release
recovery, availability, and latency evidence without customer identifiers.
Customer-environment worker, receiver, database, node, zone, sustained
customer-traffic capacity, and long-window regional certification remain
external.

Customer-processing update: ADR 0129 adds a separately enabled customer gate
that sends direct verified-HTTPS identity probes and non-empty mutual-TLS OTLP
metrics while sequentially evicting one worker and one receiver pod. One
durable investigation is submitted only after each reduced-capacity state is
observed, and both components must recover under the declared objective.
Shared-database failure is addressed by the separately observed promotion gate
below; involuntary node/zone/region loss, simultaneous failure, and sustained
representative load remain external.

Customer-deployment update: ADR 0124 aggregates one exact cluster-mode
preflight, post-install diagnostic, customer ingress report, and the ADR 0123
continuity chain. ADR 0129 adds a fifth worker/receiver processing report and
ADR 0130 adds a sixth provider-neutral PostgreSQL timeline-promotion report.
ADR 0131 adds a seventh customer OIDC prerequisite report. ADR 0132 adds an
eighth customer policy report and binds its endpoint, protected profile, and
immutable snapshot-set digests to the same exact source and image. ADR 0133
adds a ninth customer credential-broker authority report and binds its
endpoint, protected profile, authority-set, and CA digests. The
aggregate rechecks the namespace UID/server, requires health after every
workflow, and fails closed on crossed or stale evidence. This closes the manual
correlation gap for the narrow installed database, identity, policy, and
broker-prerequisite profile; artifact trust, complete identity, policy, and
credential lifecycle/HA/audit, database topology/fencing/zero-loss RPO and
regional DR, other integrations, live AI/pricing, regional capacity, sustained
load, pilot, and public-governance gates remain independent.

Customer-policy update: ADR 0132 adds a separately enabled, provider-neutral
customer gate that runs protected reviewed allow and deny cases through the
production external policy adapter. It requires exact tenant/input bindings
and one immutable snapshot while retaining only digests, counts, timing, and
stable checks. Complete action/role/resource coverage, credential and bundle
lifecycle, break-glass, engine/network HA, and audit/SIEM delivery remain
customer-owned gates.

Customer-database update: ADR 0130 adds a separately enabled external observer
that uses `sslmode=verify-full`, a default-read-only session, writable-primary
selection, and native WAL timeline functions. It continues exact-release API,
durable OTLP, and investigation probes while a separately authorized operator
performs a planned promotion. A higher timeline closes the restart-versus-
promotion ambiguity without a cloud-specific API. Automatic failover cause,
topology/failure domain, fencing, split-brain prevention, acknowledged-write
loss, failback, and regional disaster recovery remain customer/provider gates.

Load-qualification update: ADR 0125 adds a separately enabled, fixed-rate
external identity-read workload with exact release binding, explicit scheduler
attainment, closed success and latency objectives, a hard request ceiling, and
aggregate-only evidence. This closes the narrow single-endpoint external read
load gap; representative mixed traffic, writes, database, worker, receiver,
failure-injected, regional, and long-window capacity certification remain
external.

Sustained-workload update: ADR 0144 adds a separately enabled, protected
customer profile for 15-minute through four-hour exact-release API reads,
PostgreSQL-durable OTLP metric writes, and asynchronous investigations under
independent fixed-rate schedules. It closes the first sustained synthetic core
mix gap while retaining customer representativeness, provider traffic, failure
overlap, regional behavior, and long-window SLOs as separate gates.

Pilot-readiness update: ADR 0145 makes that sustained report the eighth private
pilot input, rebinds its protected profile plus API/OTLP targets to the exact
deployment, and requires a post-deployment window. ADR 0146 adds a ninth input
that proves the planned API, worker/receiver, and PostgreSQL continuity windows
fit inside the same customer-approved private-pilot proxy. ADR 0152 advances
the semantic level to `customer-ai-finops-design-partner-v2` and adds the
post-deployment `ai-finops-v0` operational-alert report as the mandatory tenth
input, cross-bound to the exact release, cluster, namespace, alert profile/
binding set, and same-invocation Prometheus target. Historical v1 evidence
cannot satisfy current admission. Representative production traffic,
automatic and involuntary failover, production operation, other alert routes,
human escalation, and partner acceptance remain outside the preflight.

Update: ADR 0058 adds a deployment-configured rolling useful-completion SLO over durable asynchronous jobs, including explicit late, failed, cancelled, and unfinished misses. ADR 0059 adds a privacy-bounded query availability counter and duration histogram with explicit objective values over the replaceable OTLP metrics boundary. Bounded external ingress qualification is delivered by ADR 0105, and ADR 0151 qualifies one synthetic customer notification route. Continuous regional coverage, additional routes, and operating escalation remain open.

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

Reference slice delivered: versioned proposal/approval/result and execution-lifecycle contracts, investigation- and target-bound closed parameters, role separation, content-digested policy checks at proposal/approval/execution time, a durable one-shot pre-impact execution claim, concurrent duplicate rejection, fail-closed stale-lease recovery without replay, an exact-tenant background timer that atomically closes expired execution leases with one audit record, read-time expiry for unexecuted immutable proposals, atomic terminal result/state persistence, audit references, a default no-impact validator, an explicit Kubernetes API restart adapter with observed-UID and resource-version preconditions, server-side dry-run, brokered `resources:read` + `workloads:patch`, generation/readiness verification and prior-annotation rollback, a tenant-scoped paginated workflow read model and console controls for proposal, independent decision, one-shot execution, and verification review, production-configurable OIDC/JWKS authentication with executable local real-TLS compatibility profiles and a minimized external customer prerequisite gate, an external HTTPS policy-decision adapter with executable local real-TLS evidence for exact input/digest/snapshot binding, credential rotation/revocation, fail-closed outage behavior, recovery, and selected customer-bundle allow/deny cases, and customer credential-broker prerequisite evidence for exact-authority issuance and single-field denials. Remaining: interactive customer identity/session/revocation/rotation/availability qualification, complete customer policy coverage/lifecycle/HA/audit, credential-issuer lifecycle/HA/audit and live provider interoperability, and production-environment mutation/rollback gates.

Console onboarding update: the web surface now discovers a closed non-secret authentication profile and supports provider-neutral Authorization Code + `S256` PKCE while retaining local-token and issued-access-token fallbacks. Separate real-TLS local gates cover both the JWKS verifier and full browser exchange. The external customer prerequisite gate now covers one real issuer's discovery/JWKS/CORS, claim mapping, deployed session, and exact release binding. The policy prerequisite gate separately covers one reviewed allow/deny case set. Production qualification still requires interactive redirect/login/MFA/consent, session/logout, disablement/revocation latency, issuer rotation/availability, and complete policy lifecycle/HA/audit evidence.

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

Reference slice delivered: session, invocation/result, status, cancellation, reconciliation, read mediation, proposal-only action mediation, and executable compatibility-report contracts; declared-capability subset enforcement; token reference/digest handling; expiry and limit framing; authenticated status/cancel/reconcile APIs and SDKs; live observer transport; Ed25519 publisher trust; digest-pinned pre-pulled OCI artifacts; a real out-of-process Docker conformance runner with no general network or container credentials, read-only non-root execution, bounded CPU/memory/swap/PIDs/files/tmpfs/time/input/output; exact-tenant PostgreSQL request claims that bind canonical content, serialize session limits across replicas, survive restarts, retain terminal results, replay only completed results, propagate durable cooperative cancellation, and allow policy-approved platform administrators to close post-deadline ambiguity without replay; request-scoped host-mediated JSON reads through a fresh Unix socket, protected destination/credential bindings, exact path/query limits, per-read policy and pre-egress audit, brokered host-only leases, direct no-redirect TLS, and bounded header-free JSON responses; proposal-only action grants bound to manifest type, target, dry-run policy, derived idempotency/expiry, current policy, pre-proposal audit, and the standard investigation-scoped governed action queue; a separately attested multi-platform bridge image in the verified release bundle; a protected customer credential-broker prerequisite covering exact authority issuance and single-field denials; plus a Docker-enabled CI matrix that binds exact source, host, SDK, protocol, plugin, relay, offline, and mediated-read outcomes. Remaining: customer broker lifecycle/HA/audit and live provider qualification, customer action-provider interoperability, cancellation propagation into future owning integration workflows, multi-host customer certification, design-partner deployment, and public governance/legal decisions.

Pilot operating update: an accepted technical scope, ordered onboarding and
exit runbook, root support/security policies, customer-owned feedback
scorecard, and versioned release-bundle handoff are delivered. Actual
design-partner operation and acceptance, named staffed support/security
channels and response objectives, customer-approved feedback sharing, and
public governance/legal decisions remain external.

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
| Credential broker client | **Accepted: ADRs 0022 and 0077** | External HTTPS lease exchange with projected workload identity and a source-bound local real-TLS profile; customer issuer/product qualification remains open |
| Workflow engine | Phase 3 end | Durable timers, approvals, retries, audit, self-hosting burden |
| Policy engine | **Accepted profile: ADRs 0037, 0079, and 0132** | External engine remains customer-selectable; exact decisions require tenant-bound immutable snapshot references, executable local TLS evidence, and selected customer allow/deny evidence; complete coverage, lifecycle, HA, break-glass, and audit qualification remain open |
| Console authentication | **Accepted profiles: ADRs 0063, 0078, 0116, and 0131** | Authorization Code + `S256` PKCE, local verifier/browser evidence, and a customer issuer prerequisite report; interactive identity/session/revocation/rotation/availability remain deployment gates |
| Plugin runtime | **Accepted profiles: ADRs 0038, 0064–0070** | Signed no-network execution, durable ownership, mediated reads, proposal-only actions, and executable compatibility evidence; customer interoperability remains |
| OTLP receiver identity and buffering | **Accepted profile: ADRs 0080 and 0134** | Mutual TLS with exact SPIFFE-to-channel binding, separate Bearer factor, PostgreSQL commit before success, customer-Collector persistent queue, and pinned official-Collector delivery evidence; permanent Collector configuration, customer PKI lifecycle, sustained queue recovery, and regional loss-objective qualification remain open |
| Release signature verification | **Accepted profile: ADR 0107** | Exact manifest digests, signer identity, issuer, verifier version, and transparency evidence; actual repository and organizational identity remain open |
| Release vulnerability qualification | **Accepted profile: ADR 0108** | Exact SPDX subject/layer binding, pinned scanner, fresh database, closed thresholds, expiring exact exceptions, and minimized evidence |
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
