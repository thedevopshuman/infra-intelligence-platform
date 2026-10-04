# Public open-source v1 release plan

**Status:** Public-first target accepted; release work remains

**Date:** 2026-10-04

**Published predecessor:** Apache-2.0 is selected under
[ADR 0157](../decisions/0157-apache-licensed-learning-release.md). The
[learning release](../releases/learning-v0.84.0.md) is a source-only,
disposable milestone, not the durable installation target or completion of
the production work below.

The project owner selected public open-source v1 before a private customer
pilot. [ADR 0155](../decisions/0155-public-open-source-v1-first.md) records that
order. This plan complements the [infrastructure roadmap](initial-roadmap.md)
and [AI FinOps roadmap](ai-finops-roadmap.md); it does not replace their
unfinished outcomes or certify the current repository as production-ready.

## User outcome

A new user can install a released artifact, connect supported infrastructure
and telemetry, see what exists and changed, investigate a bounded issue with
evidence, and understand AI usage, estimated cost, attribution, changes, and an
evidence-backed savings opportunity. The application keeps calling its model
provider directly. Collection remains asynchronous and metadata-only by
default; IIP does not require an inference proxy or a new customer SDK.

## Accepted installation sequence

The primary public installation target is a persistent, usable single-host
Compose distribution built from versioned published artifacts. The existing
[community preview](../operations/community-installation.md) is its executable
development foundation. The [installation source kit](../operations/community-installation-kit.md)
now packages that path for non-Git use. The launcher can select one fully
qualified application digest, explicitly fetch/check it with four
digest-selected dependencies, and start from the five local image IDs without
implicit pulls or builds. Authenticated publication of those artifacts,
fresh-host first value and supported upgrades remain open. It remains
pre-release.

The second installation target is a full-stack Kubernetes profile delivered
through Helm. It must be a new, explicitly bounded profile that installs the
dependencies needed for a usable stack and proves their persistence, security,
upgrade and recovery behavior. It is distinct from the existing
[production-oriented Helm chart](../operations/helm-deployment.md), which
intentionally requires customer-owned PostgreSQL, identity, ingress,
certificates, observability and other enabled services. The existing chart
must not be relabeled as a bundled installer or weakened to create the new
profile.

The [installation-options matrix](../operations/installation-options.md)
records what each current or planned path runs, which dependencies remain
external, how login and integrations work, and which release claims remain
open. No install command for unpublished artifacts or an unimplemented
Kubernetes profile is implied by this sequence.

## Current evidence

The repository contains working API/console, resource graph/history,
investigation/evidence, PostgreSQL persistence, OTel intake/export, AI pricing,
attribution/savings, Grafana, public SDK, and deployment implementations.
The historical `b51bde0` candidate has 19/19 local release inputs. Those inputs
describe that revision, not later commits or a public/customer deployment.

PostgreSQL transport hardening in `4860878` passed `make verify
PYTHON=.venv/bin/python` (1,300 tests, including 70 explicitly skipped
integration tests), 40 separate live PostgreSQL tests, seven live TLS checks,
and the Helm install/upgrade/backup/restore gate using a TLS-only database.
The latter covers the API and migrator/backup paths; worker and receiver TLS
wiring has composition/render tests but has not yet been exercised by that
live Helm gate. Full release evidence must be regenerated for the selected
clean candidate.

The owner has selected `thedevopshuman` on GitHub. The
[platform repository](https://github.com/thedevopshuman/infra-intelligence-platform)
is public and the Apache-2.0
[learning-v0.84.0 source prerelease](https://github.com/thedevopshuman/infra-intelligence-platform/releases/tag/learning-v0.84.0)
has been published. That source-only milestone is not signed runtime-artifact
publication or a production-v1 qualification.
The initial `v0.84.0` publishing tag failed workflow validation before jobs or
image pushes. The `v0.84.1` attempt passed build and publication, then failed
before signatures when Cosign tried to create its trust cache on a read-only
path. Its unsigned registry tags are not an accepted release. Both Git tags
remain immutable. `v0.84.2` with chart `0.87.2` uploaded and signed both images,
then qualification rejected Cosign's `index.docker.io` claim alias for the
policy's `docker.io` name. The adapter correction preserves exact trust and
adds a manual signature-only recheck without builds or full-suite tests.
The failed job saved no bundle checkpoint; remaining vulnerability, customer
asset signing and GitHub publication work is not completed by that diagnostic.
Normal CI checks actual GitHub Actions syntax and expression contexts.
Successful qualified publication and fresh-host installation remain pending, not
inferred from a passing local test suite. The optional extra local installation
smoke check is manual and was deferred for this publishing attempt; that does
not remove the signature or vulnerability promotion gates.
The existing [website repository](https://github.com/thedevopshuman/website)
remains separate. `thedevopshuman.com` is the selected informational site/docs
domain: The DevOps Human is the umbrella site, with IIP as one product, not a
new name for the whole platform or company. Website branch preparation does
not constitute a live deployment, DNS cutover, or IIP release.

Apache-2.0 and learning-preview contribution/security reporting policies are
now present. The learning tag is a separate source-only prerelease channel;
there is still no production release, organizationally signed public artifact,
or production supported-version policy.
The live customer reports needed for private-pilot admission are also absent.
Examples and implemented qualification scripts are not evidence of completed
external runs.

## Remaining release work

The [persistent community preview](../operations/community-installation.md)
implements an initial source-based installation path: empty startup,
generated protected credentials, verified database/telemetry transport,
operator-reviewed catalogs/attribution, durable state and Collector buffering,
safe configuration generations, and a savings-independent rolling dashboard.
Its digest-selected path now checks the application, PostgreSQL, Collector,
Prometheus and Grafana references before a stopped start, binds Compose to the
actual local image IDs, and disables implicit build and pull. This is local
selection evidence only: it does not authenticate the publisher or source,
verify release signatures, or qualify those images. It does not close the
public-install gate: published authenticated artifacts and a fresh-host
walkthrough, supported upgrade/recovery and customer certificate lifecycle,
database/outbox retention, and real provider evidence remain outstanding. Its
operational limitations must not be presented as a fully supported public-v1
deployment.

The current Helm chart remains a bring-your-own-dependencies deployment path,
not the second full-stack installation target. The bundled Kubernetes profile
still needs a defined dependency and values boundary, storage and credential
model, executable install/upgrade/recovery tests, and fresh-cluster evidence.
Until those exist, the repository has no whole-stack Helm installation.

An [offline encrypted community recovery implementation](../operations/community-recovery.md)
now joins stopped database/queue/backend data and protected state, verifies
exact operational deployment and image identities, and restores into fresh
stopped state with explicit source fencing. The owned local Docker roundtrip
has passed with synthetic persisted/queued usage, deduplication, retained
Grafana/Prometheus data, exact images, and unchanged credentials/trust. That
result is not a customer recovery drill, measured RPO/RTO, cross-version
upgrade, or off-host key/archive custody. Those evidence and lifecycle
obligations remain open, and the gate must be rerun for the release candidate.

The [stopped-stack community transport lifecycle](../operations/community-trust-rotation.md)
adds whole-CA/leaf generations, an atomic selection pointer, operator-staged
external overlap trust, valid-old-generation rollback, and exact-start-receipt
finalization after an operator intake check. It preserves passwords, tokens,
data, and configuration and can prepare from expired source material. Its
owned local synthetic Docker gate passed on 2026-10-04, including old-only
trust rejection, new/overlap acceptance, persisted/queued data, rollback,
encrypted fresh recovery, SQL TLS, and safe repeated startup. This does not
establish a customer exporter rollout, live provider evidence, unattended
renewal, revocation, HA/hot rotation, or enterprise/Kubernetes PKI integration;
those evidence and operating responsibilities remain distinct. The gate must
be rerun for the release candidate.

| Workstream | Required result | Evidence to close it |
| --- | --- | --- |
| Installation and first value | Primary persistent Compose distribution from versioned public artifacts and five exact images, followed by a separately bounded full-stack Kubernetes profile; complete prerequisites, guided first value, and useful empty/error states | Fresh-host Compose installation from authenticated kit and image digests, plus fresh-cluster Kubernetes installation using published artifacts and public instructions, each followed by its supported end-to-end walkthrough |
| Supported scope and compatibility | A supported feature/backend/version matrix; stable public API/SDK behavior and migration policy; experimental interfaces clearly identified | Contract and compatibility tests for each supported path; documented upgrade/rollback and data migration behavior |
| Live AI economics | Qualified Bedrock SDK/operation/model/region; customer-controlled OTel delivery; approved real pricing; attribution, dashboard and evidence-backed findings | A real provider-to-dashboard run with prompts/responses absent, coverage gaps visible, and calculated estimates distinguished from billing totals |
| Reliability and security | Verified transport for every deployed client, tenant isolation, usable identity/permissions, bounded capacity, recovery, monitoring and private disclosure | Clean candidate checks plus supported-deployment exercises for worker/receiver, identity, policy/broker where enabled, Collector buffering, backup restore, failure behavior and alert delivery |
| Public distribution and maintenance | Owner-selected license and repository/registry namespaces, release signing, dependency notices, contribution rules, support/security routes | Verified signed artifacts installed by a new user; correct license/notice files in source and packages; published maintainer and supported-version policies |

The [delivered-outbox lifecycle](../operations/event-outbox-retention.md) adds
an explicitly configured, disabled-by-default cleanup path for old successful
delivery state. It does not remove immutable events, audit records, or
unpublished/quarantined work. In particular, the community publisher is
disabled, so this is not a remedy for its growing undelivered backlog. A
privacy-safe customer-owned event destination and operating policy remain
necessary. AI-ledger retention also remains open. The separate
[history-availability boundary](../operations/ai-history-availability.md) now
distinguishes recorded retirement from missing/pending input and prevents
partial allocation, invocation, and savings-cohort reads. It adds no physical
payload deletion or production marker writer. Cited-finding pins, atomic
retirement/audit, exact retry identity, foreign keys, replay, future
repricing/reattribution limits, and backup/restore still require explicit
lifecycle implementation and proof before cleanup can be enabled. Silently
omitting historical usage is not an acceptable lifecycle.

The release plan must name which features are supported and which remain
experimental before assigning a completion percentage or delivery date. A
public v1 label must not imply that all providers, autonomous actions, plugin
hosts, enterprise workflows, or regional availability have been qualified.
Those larger roadmap outcomes remain active work.

The present contracts use `v1alpha1`. Before a software `1.0.0` release, the
supported public interfaces need an explicit compatibility/deprecation promise
and a versioned migration path; remaining alpha interfaces must be marked
experimental. A product milestone name alone does not stabilize contracts.

## Next three implementation units

1. **Persistent Compose release path.** Publish and authenticate the implemented
   source kit together with the exact application, PostgreSQL, Collector,
   Prometheus and Grafana image digests. Use the implemented explicit image
   fetch/check and no-build/no-pull startup so installation does not require a
   source build. Local digest/architecture inspection is not a substitute for
   publisher authentication, signature qualification or release evidence.
   Freeze its supported host, feature, dependency and login boundary; retain
   generated credentials, non-fixture configuration, Collector queue,
   dashboard, durable data and recovery behavior. Prove installation and
   upgrade on each selected fresh host. An inbound-only AI telemetry profile
   must not require provider credentials or the external broker unless an
   enabled feature actually performs those reads.
2. **Separate full-stack Kubernetes Helm profile.** Define and implement the
   second installation path with its own closed values and dependency boundary,
   durable storage, generated or referenced credentials, installation,
   upgrade and recovery tests, and fresh-cluster evidence. Keep the existing
   bring-your-own production chart intact. Do not infer HA, production PKI,
   external identity or operational support from a bundled single-cluster
   profile.
3. **Live outcome and public release candidate.** Exercise one authorized
   Bedrock invocation through instrumentation, OTel, ledger, qualified real
   pricing, worker, attribution and Grafana with content capture absent and
   coverage gaps visible. Freeze the compatibility policy, regenerate clean
   evidence for the exact artifacts and both advertised installation paths,
   publish/sign them, and reproduce install, upgrade and recovery before
   announcing v1.

The protected release workflow currently builds after `make verify`; it does
not itself consume the full local qualification evidence. Unit 3 must close
that gap by verifying evidence for the exact clean revision and exact built
artifacts before promotion. A passed source test suite alone is insufficient.

## Owner decisions

- **Selected:** GitHub owner `thedevopshuman`, separate platform and website
  repositories, and `thedevopshuman.com` for the umbrella site/product details
  and documentation. Platform source and the learning prerelease are public;
  the separate website repository has its own publication lifecycle.
  Docker Hub image namespaces are now selected and created:
  `thedevopshuman/iip` and `thedevopshuman/iip-bridge`. The workflow targets
  those canonical repositories with its exact GitHub OIDC signing identity
  and a separately signed Compose-kit asset (ADR 0164). Publishing credentials,
  live signing and anonymous install evidence still need completion; namespace
  creation is not image publication. Docker Hub version-tag immutability,
  GitHub version-tag update/deletion protection and main-history protection are
  configured. Main still permits fast-forward development; review live rules
  and exact-revision CI before authorizing a release.
- **Selected:** Apache-2.0 for original project material, with contribution
  rules and retained third-party obligations. Dependency compliance and
  production maintenance commitments remain release work.
- Public identity/naming disposition, responsible maintainers, support and
  private security reporting channels, and supported-version commitments.
- An authorized live provider/test environment and bounded cost allowance for
  real integration qualification.

Credentials and private environment configuration stay outside the repository.
Missing publication decisions do not block local engineering. Apache-2.0 has
been applied and the source learning prerelease published. Additional artifact
publication destinations, production commitments, and live billable calls
still require their applicable choices and authority.
