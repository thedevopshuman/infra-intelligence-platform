# Public open-source v1 release plan

**Status:** Public-first target accepted; release work remains

**Date:** 2026-10-04

**Learning milestone:** The owner has prioritized a source-only learning
prerelease before production v1 and delegated the license choice. Apache-2.0
is selected under [ADR 0157](../decisions/0157-apache-licensed-learning-release.md).
The [learning release](../releases/learning-v0.84.0.md) provides a disposable
Docker session, not completion of the production work below.

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

The owner has selected `thedevopshuman` on GitHub. The verified source through
`3e2df75` is pushed to the private
[platform repository](https://github.com/thedevopshuman/infra-intelligence-platform).
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
implements an initial source-checkout installation path: empty startup,
generated protected credentials, verified database/telemetry transport,
operator-reviewed catalogs/attribution, durable state and Collector buffering,
safe configuration generations, and a savings-independent rolling dashboard.
It does not close the public-install gate: published artifacts and a fresh-host
walkthrough, supported upgrade/backup and certificate lifecycle, database/outbox
retention, and real provider evidence remain outstanding. Its operational
limitations must not be presented as a fully supported public-v1 deployment.

| Workstream | Required result | Evidence to close it |
| --- | --- | --- |
| Installation and first value | Versioned public images/chart, complete prerequisites, a guided route from empty installation to real resources and AI cost views, useful empty/error states | A fresh-machine install using published artifacts and public instructions, followed by an end-to-end user walkthrough |
| Supported scope and compatibility | A supported feature/backend/version matrix; stable public API/SDK behavior and migration policy; experimental interfaces clearly identified | Contract and compatibility tests for each supported path; documented upgrade/rollback and data migration behavior |
| Live AI economics | Qualified Bedrock SDK/operation/model/region; customer-controlled OTel delivery; approved real pricing; attribution, dashboard and evidence-backed findings | A real provider-to-dashboard run with prompts/responses absent, coverage gaps visible, and calculated estimates distinguished from billing totals |
| Reliability and security | Verified transport for every deployed client, tenant isolation, usable identity/permissions, bounded capacity, recovery, monitoring and private disclosure | Clean candidate checks plus supported-deployment exercises for worker/receiver, identity, policy/broker where enabled, Collector buffering, backup restore, failure behavior and alert delivery |
| Public distribution and maintenance | Owner-selected license and repository/registry namespaces, release signing, dependency notices, contribution rules, support/security routes | Verified signed artifacts installed by a new user; correct license/notice files in source and packages; published maintainer and supported-version policies |

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

1. **Public installation and supported scope.** Audit the complete onboarding
   path and publish the feature/dependency matrix. Make an operator able to
   reach real first value using the documented public interfaces. Explain
   required external services and require only the dependencies actually used
   by each explicitly supported profile; any new profile needs its own
   contract and tests, and must not weaken existing production profiles.
   Prioritize a durable, non-fixture setup from release artifacts, including
   Collector configuration, generated credentials, real price configuration,
   and dashboard import. An inbound-only AI telemetry profile should not need
   outbound provider credentials or the external broker unless an enabled
   feature actually performs those reads.
2. **Live Bedrock and deployment reliability.** Exercise the same invocation
   through instrumentation, OTel, ledger, real price catalog, worker,
   attribution, and Grafana. Complete worker/receiver verified-database tests,
   sustained-load calibration for supported architectures, identity lifecycle,
   backup/recovery and alert-delivery evidence for the supported deployment.
3. **Public release candidate.** Freeze the compatibility policy, select release
   versions, regenerate clean local evidence, package public onboarding and
   license/notice material, publish/sign the exact artifacts, and reproduce
   installation and upgrade from their public locations. Resolve any failures
   before announcing v1.

The protected release workflow currently builds after `make verify`; it does
not itself consume the full local qualification evidence. Unit 3 must close
that gap by verifying evidence for the exact clean revision and exact built
artifacts before promotion. A passed source test suite alone is insufficient.

## Owner decisions

- **Selected:** GitHub owner `thedevopshuman`, separate platform and website
  repositories, and `thedevopshuman.com` for the umbrella site/product details
  and documentation. Current repository privacy is a staging state, not a
  change to the public-first target. Image/package namespace ownership and
  release-signing identities still need to be finalized.
- **Selected:** Apache-2.0 for original project material, with contribution
  rules and retained third-party obligations. Dependency compliance and
  production maintenance commitments remain release work.
- Public identity/naming disposition, responsible maintainers, support and
  private security reporting channels, and supported-version commitments.
- An authorized live provider/test environment and bounded cost allowance for
  real integration qualification.

Credentials and private environment configuration stay outside the repository.
Missing publication decisions do not block local engineering. Publication,
license application, and live billable calls wait for the applicable choices
and authority.
