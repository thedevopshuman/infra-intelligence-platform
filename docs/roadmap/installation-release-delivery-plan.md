# Installation and release delivery plan

**Updated:** 2026-10-05

Build a repeatable route from a small reviewed change to a usable self-hosted
release. Keep the persistent Compose path, make the core Helm chart independently
releasable, and add optional upstream services without maintaining forks of
Grafana, PostgreSQL, Prometheus, or OpenTelemetry. This is the execution order
for the [public v1 plan](public-v1-release-plan.md), not a claim that v1 is done.

## Release units and ownership

| Unit | We maintain | Customer or upstream maintains | Version boundary |
| --- | --- | --- | --- |
| IIP application | API, workers, receiver, contracts, migrations, images and Compose kit | Customer data, provider access and infrastructure | Application `vX.Y.Z`; immutable image digests |
| Core `infra-intelligence` chart | IIP workloads, strict values, Secret references, migrations and network controls | PostgreSQL, identity, trust, storage and enabled telemetry services | Chart `helm-vX.Y.Z`; independent `appVersion` and application digest |
| Optional stack profile | Compatible upstream versions, configuration wiring, dashboards and integration tests | Upstream chart/software fixes and customer storage/backup operation | Separate future profile version and dependency lock |
| Website and wiki | Versioned install, token/login, integrations, demo, upgrade and troubleshooting guides | Website hosting and domain | Separate website deployment; no customer database |

The core chart stays bring-your-own-services. Installing an upstream subchart
does not transfer responsibility for its security, storage, upgrade, backup or
compatibility to the upstream maintainer. We still test the combinations we
advertise. Shared production services should normally have their own Helm
releases so an IIP uninstall cannot remove them.

Prepare independent chart automation in the existing source repository first.
The proposed public `thedevopshuman/helm-charts` repository remains an owner
choice, not a prerequisite for local work. Moving later requires carrying
licenses, tests, release protection and signer-identity instructions; do not
silently move existing tags or change the historical application bundle format.

## Delivery sequence

| Step | Deliverable and completion check | Current state |
| --- | --- | --- |
| 1. Short development loop | Automatic docs/chart-only checks; conservative full fallback; manual full checks; one isolated Kind smoke command using an existing image | Implemented tooling in this change; record executed evidence in the coverage ledger |
| 2. Independent core chart | Protected chart-only packaging/signing/publication, exact chart tag, committed licensed archive, no application rebuild or Docker Hub write credential | Implemented workflow; publication waits for a qualified committed default image pin |
| 3. Finish application distribution | Recover and evaluate the old candidate, remediate rejected dependencies in a new candidate, qualify exact signatures and vulnerabilities, sign downloads and publish the persistent kit | Old bundle recovered and signatures verified; fresh scan rejected PyJWT 2.13.0 in `v0.84.2`. New candidate required; no qualified GitHub artifact release yet |
| 4. Publish the first core chart | Select qualified application digest and `appVersion`, bump chart version, run install/upgrade coverage, tag `helm-v<chart version>`, approve the protected release | Pending; placeholder image defaults deliberately block publication |
| 5. Compose upstream services | Pin and lock the selected upstream charts, write closed profile values and Secret/CA wiring, package all locked dependencies with notices | Pending; no all-in-one chart is advertised yet |
| 6. Prove installation and lifecycle | Fresh Compose install and fresh-namespace Helm install, authorized telemetry to ledger/dashboard, upgrades with retained state, recover into fresh storage | Core smoke is narrower; full profile and cross-version evidence remain pending |
| 7. Publish the user journey | Website/wiki with exact download/version, prerequisites, first login, exporter setup, integrations, sample output and recovery; test commands from a clean machine | Existing source docs are the starting point; website publication is separate |
| 8. Release supported v1 scope | Freeze supported versions/features, compatibility policy, maintenance/security routes, exact-artifact evidence and changelog; publish and install anonymously | Pending; unqualified features stay explicitly experimental |

Steps 1–2 can proceed while step 3 is recovered. Step 5 research/configuration
can proceed before publication, but its dependencies cannot be described as
supported until their exact locked packages and runtime combination pass.
Do not rebuild already-signed application images merely to ship a chart fix.

The `v0.84.2` recovery found an actual application dependency rejection, not a
chart or signature-only retry. All six critical/high findings are in PyJWT
`2.13.0`; upstream identifies `2.14.0` as fixed. A dependency change requires a
new immutable application candidate and fresh exact-image qualification.
Keep the old tag and rejected evidence intact. The
[recovery runbook](../operations/release-recovery.md) records reusable local
checkpoints and the remaining protected publication work.

The source and existing verification/provider locks now pin PyJWT `2.14.0`
without changing other resolved dependency versions. The normal release
workflow evaluates vulnerabilities before loading registry credentials or
publishing. These source changes still need a new image build and passing
exact-artifact scan; they do not repair the existing Docker Hub images.

Candidate `0.84.3` (bundled chart `0.87.3`; SDK versions unchanged) carries
that correction and six saved release stages. The archive and evidence
helpers have focused tests; the first live run and failed-job resume still
need execution. See the [retry runbook](../operations/resumable-application-releases.md).

## Dependency integration design

The following are selected implementation directions, not installed or locked
dependencies. Resolve and review exact versions in step 5; never use a floating
`latest` reference in a release.

| Service | Reuse and generic input boundary |
| --- | --- |
| PostgreSQL | External service by default. Supply a database URL through an existing Secret plus a CA Secret, using `verify-full`. Offer an optional [CloudNativePG cluster recipe](https://github.com/cloudnative-pg/charts); install its cluster-wide operator separately, never implicitly with the core application. |
| Grafana | Optional [Grafana Community chart](https://github.com/grafana-community/helm-charts); existing administrator Secret, provisioned datasource and versioned IIP dashboard. Keep external Grafana supported. |
| OpenTelemetry Collector | Optional [official Collector chart](https://opentelemetry.io/docs/platforms/kubernetes/helm/collector/); explicit deployment mode, metadata allowlist before persistent queueing, bounded queue and authenticated TLS export. Disable unwanted default receivers, content capture and debug exporters. |
| Metrics | Optional [Prometheus Community chart](https://github.com/prometheus-community/helm-charts/tree/main/charts/prometheus); explicit persistent storage, retention and scrape/export configuration. Disable unneeded cluster-wide exporters. External compatible endpoints remain supported. |
| Logs | Optional later Loki profile, not a prerequisite for the first AI usage/cost dashboard. Retention and storage remain an explicit choice. |

Use subchart conditions only for dedicated optional services. Parent values
must map to the dependency's real supported keys; Helm does not automatically
template arbitrary strings in a child's values. Pin chart versions and commit
`Chart.lock`; use `helm dependency build` in CI and ship the locked packages.
Review dependency/image changes, licenses and vulnerabilities before release.
[Helm's chart documentation](https://helm.sh/docs/topics/charts/) explains
dependency conditions, value scoping and the independent chart/app versions.

Secrets are references, not plaintext chart values. Identity, database CA,
receiver mTLS, telemetry credentials and provider credentials remain separate.
An inbound AI usage installation does not need AWS credentials inside IIP.
Optional SDK business attribution does not become a new collection requirement.

## Fast development without losing test obligations

Use the [Helm development and release runbook](../operations/helm-development-and-release.md)
for executable commands. Track unfinished coverage in the
[release coverage ledger](release-test-coverage.md), including the trigger and
evidence needed to close each item.

During editing, run tests for the changed boundary. Run `make verify` once for
the completed integrated change as required by `AGENTS.md`; do not rerun the
entire Docker compatibility matrix after every documentation or chart edit.
Unknown changes, application code, contracts, CI logic and mixed changes use
the full automatic CI path. Docs/chart-only changes use the lightweight path.
Manual `scope=charts` is diagnostic, not a full application qualification.

For a failed workflow, rerun failed jobs when successful prerequisites and
their exact artifacts are retained. A failed step within one job is not an
independently rerunnable job. The six-job application workflow now uploads
immutable, checksum-bound checkpoints before the next expensive stage. If a checkpoint
is missing, reconstruct and verify exact inputs; never mark an absent gate as
passed. The existing signature-only workflow diagnoses two image signatures;
it neither signs downloads nor publishes a release.

## Local test environment

Use the existing Docker Desktop-backed Kind cluster `iip-dev`, with explicit
context `kind-iip-dev`; Docker Desktop's separate Kubernetes toggle is not
required. Never select a cluster from the ambient current context. The fast
smoke test owns one unique namespace and disposable PostgreSQL fixture. It
does not delete clusters, rebuild IIP images, alter DNS or touch existing
Compose stacks. A successful single-node smoke run is not HA, backup, network
policy enforcement, fresh-machine, multi-architecture or production evidence.

## Decisions still outside this implementation

- Whether to move chart publication to the proposed separate public repository.
- The supported v1 host/cluster and feature matrix, backed by the ledger's tests.
- Authorized live provider scope and a bounded cost allowance before billable calls.
- Maintainer/support commitments and final product branding; neutral IIP names remain.

These choices do not block local chart and release-process development. No new
paid service, provider call, remote repository or public release is created
merely by committing this plan.
