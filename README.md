# Infrastructure Intelligence Platform

> Temporary neutral project name. Product and company naming are intentionally tracked separately under [`docs/research/brand`](docs/research/brand/README.md).

Infrastructure Intelligence Platform (IIP) is an architecture-first foundation for building a vendor-neutral control plane that understands infrastructure as a resource graph and event timeline, then lets governed agents investigate and act with evidence.

This repository now contains an **executable local reference slice across Roadmap Phases 1–5**. It includes live Kubernetes reconciliation with per-type cursor resume and watch-expiration recovery, durable membership and tombstones, tenant-scoped at-least-once outbox delivery through replaceable publishers with finite retry quarantine, privileged delivery-health reporting, deployment-configured rolling publication SLOs, and exact-generation governed replay, durable PostgreSQL resources/evidence/investigations/actions/audit, tenant-scoped projection verification and rebuild, source-bound full-schema logical recovery plus local physical replication/promotion/PITR qualification, automatic tenant/source ingestion-freshness sampling with optional OTLP/HTTP metrics export, backend-neutral control-plane query availability and latency metrics, optional bounded investigation trace export, privileged process-local and deployment-wide exporter-delivery health plus sampled exporter SLO windows and a two-window sampled exporter burn-rate report, a customer-Collector-observed sending-queue depth and send-loss objective sourced from the Collector's own self-metrics through the existing backend-neutral metric query port, an authenticated runtime identity dashboard tied to release and Helm provenance, public console authentication discovery with OIDC Authorization Code + `S256` PKCE plus separate real-TLS verifier and full browser-exchange evidence, backend-neutral bounded metric and log evidence queries, real Prometheus-compatible metric and Loki/OpenSearch log adapters, value-minimized resource/deployment-change evidence, allowlisted and redacted local-file or immutable-revision GitHub repository/runbook context evidence, isolated optional tenant-bound OTLP/HTTP metrics and logs evidence receivers plus metadata-only GenAI usage trace intake with CA-verified SPIFFE mTLS and executable intermediate-chain, expired-certificate, CRL-revocation, fail-closed CRL-freshness, and CRL-rollout evidence, PostgreSQL-before-success durability, and a persistent customer-Collector queue profile, normalized Kubernetes Event evidence with a live exact-scope read-only API adapter, a shared external workload-identity credential-broker client with source-bound real-TLS local compatibility evidence, an external policy-decision adapter with source-bound real-TLS input/digest/snapshot/rotation/recovery evidence, deterministic auditable cross-signal investigation planning with protected tenant profiles and one bounded provider-gap promotion, durable asynchronous investigation dispatch with tenant-explicit worker claims, heartbeats, crash recovery, bounded tenant-fair scheduling, a deployment-wide live-lease cap, bounded per-tenant backlog admission, and executable 128-tenant PostgreSQL overload/capacity evidence, cooperative cancellation, unit-aware threshold assessment, explicit, scope-end rolling, and fixed-period or calendar-day-aligned seasonal baseline comparison, event-condition correlation, conservative document/log/change-count assessment from committed artifacts, adversarial instruction-boundary release scoring proven against a shared multilingual, multi-technique adversarial instruction corpus across the live context, log, and offline scoring paths, evidence-backed investigation and evaluation, one-shot fail-closed governed actions, automatic non-replaying reconciliation of expired action attempts, an opt-in UID-bound Kubernetes restart adapter with server-side dry-run, readiness verification, and rollback, bounded plugin sessions, a signed digest-pinned no-network Docker plugin runner, restart-durable plugin invocation claims with terminal-result replay, durable cancellation, and administrator-only post-deadline reconciliation without replay, invocation-scoped host-mediated plugin JSON reads that keep the container offline and credentials host-side, proposal-only plugin action mediation that enters the ordinary independent-approval queue without execution authority, and an executable exact-host plugin compatibility matrix. The local Phase 1 exit gate is complete, but the later phase exit gates are not: customer GitHub/GitHub Enterprise and additional service-catalog qualification, production broker selection and receiver interoperability, customer issuer, credential, policy-bundle, PKI, and Collector interoperability qualification, additional customer telemetry backends/signals, regional exporter aggregation, customer-environment sustained workload/failover certification, production-environment action interoperability, customer plugin action-provider interoperability, multi-host plugin certification, design-partner operation, and legal/brand decisions remain. It is not yet a production system.

The packaged Kubernetes release uses surge-first zero-unavailable API, worker,
and receiver rollouts, component-specific disruption budgets, exact-selector
hard topology spread, bounded endpoint-propagation delays, and signal-aware
active-request draining for API and OTLP intake. Its N-1 gate continuously
reads authenticated release identity and tenant data and also terminates a pod
while one database read is intentionally blocked; both the connection and the
replacement Deployment must recover cleanly. Running both packaged gates
retains a separate manifest-bound, environment-scoped qualification report;
incomplete or internally inconsistent evidence cannot be promoted by the
repository verifier.

An aggregate `ReleaseReadinessReport` verifies the exact bundle and all 18
repository-controlled evidence documents, while permanently labeling the
result `local-candidate-only` and enumerating the eight external customer and
public-launch gates. This gives release owners one fail-closed inventory
without converting local Docker/Kind evidence into a production claim.

A separate owned three-node Kind gate now turns those availability declarations
into live evidence. It spreads two replicas of the API, worker, and receiver
across two workers, commits one real OTLP metric, continuously probes exact
release identity and OTLP intake through their Services, drains one worker
through the Eviction API, and requires zero probe failures before, during, and
after recovery. The retained report contains aggregate states and digests only;
it does not claim involuntary-node, database, regional, or customer-cluster
availability. See the [Kubernetes availability qualification runbook](docs/operations/kubernetes-availability-qualification.md).

For a customer-like deployment, a separate continuity gate runs at least five
minutes of direct verified-HTTPS liveness, readiness, authentication, and exact
release-identity probes while evicting one ready API pod through an
explicit-context, UID-preconditioned `policy/v1` request. It requires redundant
capacity, active PDB protection, bounded recovery, and declared availability
and p95 latency objectives, then retains only aggregate measurements and
pseudonymous bindings. The disruptive path is never part of `make verify` and
requires an explicit enable flag; see the [customer continuity qualification
runbook](docs/operations/customer-continuity-qualification.md).

After that disruption, an additive customer deployment gate binds the live
dependency preflight, exact installed health, nested ingress evidence, and
continuity report to one clean release and current namespace UID/server. It
requires a fresh post-continuity health observation and retains only report
digests and hashed target bindings. Its fixed limitations prevent this narrow
single-cluster result from becoming a publication, database, integration,
regional, pilot, or governance claim; see the [customer deployment
qualification runbook](docs/operations/customer-deployment-qualification.md).

A separately enabled fixed-rate load gate exercises only authenticated runtime
identity reads through that same verified HTTPS boundary. It uses one global
monotonic schedule, counts late slots instead of issuing catch-up bursts,
enforces a 250,000-request ceiling, and retains aggregate latency, success,
scheduler, and closed failure-category measurements. It intentionally does not
claim mixed-endpoint, write, database, worker, receiver, failover, regional, or
long-window capacity; see the [control-plane load qualification
runbook](docs/operations/control-plane-load-qualification.md).

After registry publication, a separate promotion verifier derives both OCI
index digests from that verified manifest and checks exact Cosign signer
identity, issuer, version, and transparency evidence under a reviewed policy.
Its local public-key profile is explicitly non-promotable. The checked example
retains placeholders until repository ownership and organizational release
identity are accepted; see the [release procedure](docs/operations/release-artifacts.md).
The next promotion gate extracts every platform SPDX attestation from both
release images and evaluates it with a digest-pinned scanner and fresh database
under closed severity thresholds and exact expiring exceptions. Only minimized
counts, digests, and exception IDs are retained; see the [vulnerability
qualification runbook](docs/operations/release-vulnerability-qualification.md).

The protected tag-only release workflow now connects those gates: from a clean
exact-version tag at the fetched `main` tip it builds the verified bundle,
copies both OCI indexes unchanged to GHCR, signs their immutable digests with
GitHub OIDC, qualifies signatures and every attached SPDX SBOM, signs the
customer archive, and only then creates a GitHub release. It remains dormant
until repository owners configure the `release` environment, protected tag
rules, GHCR package immutability/visibility, and organizational ownership. See
the [release procedure](docs/operations/release-artifacts.md).

The Docker recovery gates retain separate source-bound logical
`PostgreSQLRecoveryQualificationReport` and physical
`PostgreSQLContinuityQualificationReport` evidence. The latter proves local
streaming catch-up, manual promotion, complete row and safe sequence state,
archived WAL, and recovery to a named pre-change boundary. Neither report
claims automatic failover, protected customer storage, regional recovery, or a
customer RPO/RTO.

The packaged chart includes a value-free External Secrets Operator handoff for
the required database Secret. A local Kind gate proves the verified,
digest-pinned controller creates exact target keys with named-secret-only source
authority and propagates a source rotation. The
[external-secret runbook](docs/operations/external-secrets.md) inventories every
optional chart Secret boundary and keeps customer store credentials outside IIP.

The chart also emits a sanitized deployment profile and ships composable
production-core and AI FinOps values examples. A source-bound preflight renders
the complete chart, evaluates closed production checks, and can use one
explicit Kubernetes context to verify referenced Secret keys, ConfigMaps, and
a bound backup claim before installation. Its retained report contains only
counts and digests and remains explicitly `pre-install-only`; customer
interoperability and resilience evidence cannot be promoted from a local or
static pass. See the
[customer deployment preflight](docs/operations/customer-deployment-preflight.md).

After installation, an exact-context deployment diagnostic summarizes API,
worker, and receiver rollout state, immutable image identity, readiness,
restarts, unschedulable Pods, and crash-loop signals without collecting logs,
object names, customer identifiers, provider messages, or Secret values. Its
`healthy` result is explicitly point-in-time support evidence rather than a
production or availability claim. See the
[post-install diagnostic runbook](docs/operations/deployment-diagnostics.md).

The exact-host plugin matrix has separate offline observer, host-mediated read,
and proposal-only action-provider rows. The action row runs in Docker's
no-network sandbox, reaches the real governed proposal service through the SDK
and trusted relay, and must stop before approval or execution. Customer-owned
plugin and live environment qualification remain deployment gates.

The operational console and SDKs expose public non-secret authentication discovery, rolling transport-neutral event-publication attainment, and useful asynchronous investigation-completion attainment. The OIDC browser profile is provider-neutral and never carries a client secret; separate executable real-TLS profiles certify the shipped verifier's exact claims/cache/rotation/outage behavior and the actual authorization redirect, `S256` exchange, exact-origin CORS, replay denial, and token-to-API path. Customers still own issuer enrollment, MFA/session/logout policy, redirect registration, token-endpoint CORS, certificate/revocation objectives, and claim interoperability. The API and SDKs also expose observe-only, tenant-scoped Evidence artifact-retention state; automatic byte expiration is disabled by default, bounded, policy-gated, legal-hold aware, and audited when explicitly enabled. Recognized control-plane reads emit a deployment-objective-bound query availability counter and duration histogram over OTLP. A separate direct, no-redirect external probe now qualifies liveness, readiness, authentication, exact release identity, availability, and latency through customer HTTPS ingress while retaining aggregate-only evidence. Continuous regional scheduling, long-window aggregation, burn-rate policy, and alert routing remain deployment work.

## Start here

1. Read the [product constitution](docs/product/constitution.md).
2. Read the [architecture overview](docs/architecture/overview.md).
3. Review the [contracts index](docs/specifications/README.md).
4. Follow the [initial roadmap](docs/roadmap/initial-roadmap.md).
5. When changing code with Codex, follow [AGENTS.md](AGENTS.md).

## AI economics extension

The repository now includes a metadata-only, OpenTelemetry-native AI FinOps
flow. Bedrock- and OpenAI-shaped traces reach the same normalized usage ledger,
data-driven calculated-cost engine, protected application/team allocation,
three evidence-backed deterministic rules, and provider-neutral Grafana dashboard. IIP
remains outside the inference path
and does not collect prompts or responses by default. An isolated, tenant-bound
`/v1/traces` route now normalizes approved GenAI client metadata
and atomically stores usage plus its event. A tenant-explicit background cost
service now loads protected versioned catalogs and atomically records
explainable calculated estimates plus value-minimized events. A separate
protected effective-time attribution service now maps observed service/resource
identity to immutable application/team facts, records unmatched usage explicitly,
and revalidates each decision against its exact usage and policy sources. A bounded
authenticated API now joins those facts to the exact calculated-cost generation,
and the worker exports protected application/team IDs to dedicated dashboard views
without accepting workload-controlled ownership labels. A read-only AI Economics
console view queries the same bounded, generation-bound
allocation contract for a selected one-hour through 30-day window. It presents
exact calculated-cost subunits as estimates, shows unpriced, ambiguous, pending,
and unallocated coverage before totals, verifies the authenticated tenant and
requested scope in the response, and never fetches prompt or response content.
The same view reads the newest committed finding through a tenant- and
interval-bound paginated API, preserves calculated, unpriced, and unresolved
money, and keeps the mandatory validation warning visible without
dereferencing evidence or granting action authority.
A separate
tenant-explicit deterministic service evaluates fixed context-growth,
retry-amplification, and qualified model-cost windows, revalidates every cited
source fact, and
atomically records each evidence-backed finding plus a value-minimized event.
Provider retry facts are mapped through protected channel configuration;
retry monetary savings remain explicitly unresolved until billed-attempt
evidence exists. Lower-cost model findings require a protected, immutable,
time-bounded suitability report proving workload-specific quality, latency,
safety, and compliance gates; price difference alone cannot create a
recommendation. An offline provider-neutral catalog qualifier now binds an
exact catalog to a protected required-scope policy and proves source freshness,
publication order, non-overlapping effective prices, and unique coverage. Its
tamper-evident report contains digests and aggregate counts—not negotiated
rates, model names, source locators, or credentials. A separate exact AWS
Bedrock Price List importer now binds an official retained public snapshot and
protected SKU/term/dimension mappings into a provider-neutral catalog with
integer-only conversion and minimized reproducibility evidence. Neither report
records organizational approval or invoice agreement. The production worker
now requires one current exact `production-catalog` report per catalog tenant
and revalidates it before every registration/cost pass; expired or altered
evidence stops work before the catalog enters the ledger. Separate bounded projections export request/token volume,
pricing coverage, calculated cost, context change, retry change, qualified
model-cost difference, and potential saving through the existing OTLP metrics
boundary. A disposable
Collector/Prometheus/Loki/Grafana topology and deterministic multi-provider
full-flow gate now renders the usage, cost, change, saving, coverage, and protected
application/team allocation dashboard. Separate
no-network gates exercise the exact pinned official botocore `Converse` and
`ConverseStream` instrumentation, including deferred stream-span completion,
the shipped legacy provider attribute, and service-specific scope; another
exercises the real OpenAI Python client and official
chat-completions instrumentation. Both prove receiver normalization and
asynchronous exporter failure isolation without importing provider SDKs into
the product image. Missing upstream token breakdowns remain unresolved rather
than becoming zero. Live Bedrock model/region/operation, live OpenAI
streaming/private-endpoint behavior, and invoice qualification remain. See the
[receiver runbook](docs/operations/ai-usage-receiver.md),
[attribution runbook](docs/operations/ai-attribution.md),
[cost-engine runbook](docs/operations/ai-cost-engine.md),
[AWS Bedrock public-price import](docs/operations/aws-bedrock-price-catalog-import.md),
[savings-engine runbook](docs/operations/ai-savings-engine.md),
[local AI FinOps dashboard](docs/operations/ai-finops-local-demo.md),
[Bedrock instrumentation qualification](docs/operations/bedrock-instrumentation-qualification.md),
[OpenAI instrumentation qualification](docs/operations/openai-instrumentation-qualification.md),
[telemetry contract](docs/specifications/ai-economics-telemetry-contract.md),
[product direction](docs/product/ai-finops-vision.md),
[architecture](docs/architecture/ai-economics.md), [contracts](docs/specifications/ai-economics-contracts.md),
[attribution contracts](docs/specifications/ai-attribution-contracts.md),
and [roadmap](docs/roadmap/ai-finops-roadmap.md).

## Local quick start

Use Python 3.11 or newer and Node.js 24 for the TypeScript SDK boundary. Python and npm verification dependencies are locked for reproducible local and CI behavior. With Docker Desktop running, the durable product stack now has a one-command start:

```bash
make dev-up
```

The command creates protected local-only credentials under `.iip/`, builds and starts PostgreSQL, the API, and a durable workflow worker, waits until database connectivity and schema readiness are proven, and prints the operator token. The non-interactive worker dispatches investigations, closes expired action attempts without replaying impact, delivers outbox events to the explicit local structured-log sink, and samples the enrolled local ingestion source. Open [http://127.0.0.1:8080/console](http://127.0.0.1:8080/console); the console discovers local-token mode and prompts for that token. The browser console shows the answering process's runtime identity, exact-tenant event-delivery health, and rolling publication attainment, then uses live tenant resources, graph/timeline queries, queued investigations, evidence, and a paginated governed-action queue; it does not display mock operational data. Run `make dev-credentials` to exercise proposal, independent approval, and one-shot execution with the three separate local identities.

Evidence artifact retention remains disabled in the local stack unless `IIP_EVIDENCE_RETENTION_ENABLED=true` is supplied. When enabled, the non-interactive worker processes only its explicitly enrolled tenant and emits aggregate, content-free completion logs.

Mandatory credential redaction is always enabled for supported Evidence media.
Customers can add reviewed exact-tenant email/IPv4 detectors without custom
code by supplying the protected policy wrapper described in the [evidence
redaction runbook](docs/operations/evidence-redaction.md). Custom policy
configuration is optional and cannot weaken the built-in baseline.

Use `make dev-status` to inspect the containers, `make dev-credentials` to show the separate operator, approver, and executor tokens, `make test-local-product` to exercise the complete durable customer workflow without printing credentials, and `make dev-down` to stop the stack while preserving its database.

For repository verification and the isolated integration gates:

```bash
python3 -m pip install --requirement requirements/verify.txt
make verify
# With Docker Desktop running:
make test-postgres
make test-capacity
make test-credential-broker
make test-oidc
make test-policy-engine
make test-external-secrets
make test-backup-restore
make test-postgres-continuity
make test-deployment-preflight
make test-otel
make test-otlp-receiver
make test-ai-finops
make test-aws-bedrock-price-import
make test-bedrock-instrumentation
make test-openai-instrumentation
make test-prometheus
make test-loki
make test-plugin-compatibility
make test-helm-install
make qualify-kubernetes-availability PYTHON=.venv/bin/python
make release-bundle PYTHON=.venv/bin/python
# Exercise the digest-pinned SBOM scanner and its no-network scan path:
make test-release-vulnerabilities PYTHON=.venv/bin/python
# After building a bundle, prove its install and the selected N-1 transition:
# IIP_RELEASE_BUNDLE=/absolute/path/to/bundle make test-release-install
# IIP_RELEASE_BUNDLE=/absolute/path/to/bundle IIP_UPGRADE_FROM_REVISION=<revision> make test-release-upgrade
# Or run both and require one complete manifest-bound report:
# IIP_RELEASE_BUNDLE=/absolute/path/to/bundle IIP_UPGRADE_FROM_REVISION=<revision> make qualify-release
# Or execute every repository-controlled local release gate in one run:
# IIP_UPGRADE_FROM_REVISION=<revision> make qualify-local-release PYTHON=.venv/bin/python
# With the local kind cluster and explicit kubeconfig:
IIP_KUBECONFIG=/absolute/path/to/.kube/config make test-kubernetes-events
IIP_KUBECONFIG=/absolute/path/to/.kube/config make test-kubernetes-actions
IIP_KUBECONFIG=/absolute/path/to/.kube/config make test-kubernetes-live
# Configure a local Bearer identity as described in docs/operations/local-development.md
# only when running the in-memory API outside Docker.
make run
```

Before a customer Helm installation, merge the non-secret production examples
into a protected values file and run the live gate with an explicit context:

```bash
IIP_DEPLOYMENT_PROFILE=production-core-v1 \
IIP_DEPLOYMENT_VALUES=/absolute/protected/customer.values.yaml \
IIP_DEPLOYMENT_NAMESPACE=iip-system \
IIP_KUBERNETES_CONTEXT=customer-production \
make preflight-deployment-live PYTHON=.venv/bin/python
```

Then, in another terminal:

```bash
curl http://localhost:8080/healthz
curl -X POST http://localhost:8080/v1/resources \
  -H 'content-type: application/json' \
  -H "authorization: Bearer $IIP_DEV_BEARER_TOKEN" \
  --data @contracts/examples/resource.json
```

The API authenticates a Bearer credential into actor, tenant, and role context, exposes its non-secret runtime identity at `GET /v1/system/version`, validates collection/resource boundaries, and exposes graph/timeline, ingestion-freshness, normalized metric, log, Kubernetes Event, resource-change, and repository/runbook context evidence queries. A separate optional process accepts OTLP metrics/logs and metadata-only GenAI usage traces on port `4318` without exposing control-plane routes or credentials; its production profile requires both a CA-verified SPIFFE client certificate bound to the channel and the independent channel Bearer credential. The API accepts bounded investigations synchronously or through durable background jobs that survive HTTP disconnects, exposes queue attempts and cooperative cancellation, and supports separately governed actions and plugin-session workflows. The action default is non-mutating; the opt-in Kubernetes API executor adds server dry-run and a doubly enabled live path. Default external provider backends honestly return no data; selected live adapters require protected endpoint, trust, scope, and credential configuration. The built-in change provider reads only tenant-scoped accepted observation history, while context reads cataloged local files under a protected root or exact GitHub repository paths at an administrator-pinned commit through a request-scoped broker lease, direct verified HTTPS, Git blob validation, mandatory redaction, and explicit egress. The default local profile remains in memory; the Docker profile persists operational records in PostgreSQL. Its worker has a private, unserved health listener: liveness is process-only, dependency readiness verifies PostgreSQL and the latest packaged schema, and graceful shutdown withdraws readiness before bounded work is joined. The Helm chart has a strict closed values contract, resolves every application workload from one optional immutable OCI digest, injects the chart and digest into the runtime report, applies packaged PostgreSQL migrations through a separately enabled database-only hook, keeps Services internal, can declare an existing-certificate TLS Ingress with exact controller NetworkPolicy authority, can schedule checksum-complete logical backups into pre-created protected storage, ships a controller-neutral existing-Secret synchronization example, and emits a sanitized production profile for the pre-install gate. Clean revisions can be packaged as an unsigned multi-platform release candidate with separately attested control-plane and plugin-mediation bridge OCI layouts, checksums, SPDX SBOM, SLSA provenance, chart, contracts, and SDK artifacts. The local release gates install that exact package and prove a selected N-1 migration, data-preserving application rollback, idempotent re-upgrade, and bounded zero-failure authenticated Service reads throughout the transition; see the [release procedure](docs/operations/release-artifacts.md).

The owned three-node Kind gate additionally proves zero-failure API access,
durable non-empty OTLP metric intake, and bounded investigation completion
while one application failure domain is drained.

For a complete local candidate in one fail-closed run, select the supported
predecessor explicitly and use
`IIP_UPGRADE_FROM_REVISION=<commit> make qualify-local-release PYTHON=.venv/bin/python`.
The command emits revision-named bundle, qualification, vulnerability, and
18-of-18 readiness evidence; it never promotes the candidate or substitutes
for customer and organizational gates.

## Repository map

| Path | Purpose |
| --- | --- |
| `api/` | Public protocol descriptions, starting with OpenAPI |
| `contracts/` | Machine-readable schemas and valid examples |
| `src/iip/` | Provider-neutral reference kernel and composition root |
| `sdks/` | Public Python and TypeScript client boundaries |
| `plugins/` | Public-contract-only plugins and conformance fixtures; vendor code stays outside the kernel |
| `deploy/` | Helm chart and local Kubernetes overlay |
| `docs/` | Living product, architecture, specifications, research, and roadmap |
| `scripts/` | Repository validation, integration, capacity, recovery, and release checks |
| `tests/` | Boundary and vertical-slice tests |

## Architectural stance

- Resource identity and event history are the system of record.
- Every agent conclusion must be traceable to evidence.
- Read, propose, approve, and execute are different authority levels.
- Vendor adapters and plugins depend on stable platform ports; the domain never depends on a vendor SDK.
- Contracts are versioned before implementations fan out.
- Tenant isolation and auditability are design inputs, not later hardening tasks.

## Current decisions and open questions

Accepted foundations live in [`docs/decisions`](docs/decisions/README.md). PostgreSQL is accepted as the initial resource/event and operational record substrate, the deterministic investigator/non-mutating action boundary is accepted as the safety baseline, and OTLP through an OpenTelemetry Collector is the accepted telemetry-portability direction. Tenant-scoped outbox dispatch with local structured-log and authenticated HTTPS publishers, finite failure quarantine, value-minimized delivery-health reporting, transport-neutral rolling publication objectives, exact-generation governed replay, optional outbound ingestion-metrics and terminal-investigation trace export, automatic exact-source freshness sampling, privileged process-local plus shared-store API/worker exporter-delivery health, rolling sampled export objectives, a two-window sampled export burn-rate report, and a customer-Collector-observed sending-queue depth and send-loss objective, authenticated runtime/release identity, replaceable historical metric/log/Kubernetes Event/context evidence ports, the first Prometheus-compatible metric and Loki and OpenSearch log adapters, isolated tenant-bound inbound OTLP metrics/logs receivers plus metadata-only GenAI usage trace intake with CA-verified SPIFFE mTLS, separate channel credentials, intermediate client-chain and fail-closed CRL freshness/rollout, PostgreSQL-before-success durability, and a persistent customer-Collector queue profile, a live exact-scope Kubernetes Event API adapter, protected local-file and immutable-revision GitHub context adapters with source-bound real-TLS compatibility evidence, OIDC/JWKS API authentication, public console discovery with Authorization Code + `S256` PKCE, an external HTTPS policy-decision adapter with executable local real-TLS compatibility evidence, an external HTTPS credential-broker client with explicitly projected workload identity and executable local real-TLS compatibility evidence, auditable cross-signal request and protected-catalog planning, tenant-scoped workflow workers, one-shot governed execution and non-replaying action reconciliation, the opt-in verified Kubernetes restart adapter, a signed no-network plugin runner with durable invocation ownership, cancellation, post-deadline reconciliation, invocation-local host-mediated read connectivity, proposal-only action mediation, and executable exact-host compatibility evidence, unit-aware threshold assessment, ordered two-window and fixed-period or calendar-day-aligned seasonal baseline comparison, event-condition correlation, conservative document/log/change-count correlation, generation-bound AI cost allocation, exact public Bedrock price import, and provider-neutral Bedrock/OpenAI-shaped dashboard views are executable; a production credential issuer and customer-specific issuer/broker/policy/PKI/Collector qualification, production receiver interoperability, customer GitHub/GitHub Enterprise and additional service-catalog qualification, additional production backends/signals, regional exporter aggregation, live Bedrock/OpenAI qualification, organizational price approval and invoice reconciliation, model-provider selection, customer action-provider interoperability, licensing, company, and brand decisions remain explicit roadmap work.

Evidence artifact retention is also accepted as a disabled-by-default, tenant-scoped lifecycle that preserves immutable metadata and citations while expiring only bounded artifact bodies under policy and audit.

Tenant-bound evidence redaction policy is accepted as an additive privacy
control. It records content-derived policy provenance, distributes the same
generation to every evidence-producing workload, and never permits optional
rules to disable credential inspection.

## Licensing

No open-source license has been selected yet. Until that decision is recorded, do not assume permission to redistribute this repository. See the [open-source and commercial boundary](docs/product/open-source-boundary.md).
