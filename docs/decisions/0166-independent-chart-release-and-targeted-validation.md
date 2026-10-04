# ADR 0166: independent chart release and targeted validation

**Status:** Accepted implementation boundary
**Date:** 2026-10-04

## Context

Chart and application versions are already separate, but chart publication
currently happens only inside the full application release. Rebuilding images
for a chart/documentation change slows delivery without testing the changed
boundary more effectively. Customers may also already operate PostgreSQL,
Grafana and a Collector; IIP should not fork or implicitly take ownership of
those shared systems.

## Decision

Add a chart-only release path using immutable `helm-v<chart version>` tags,
the protected release environment and a dedicated keyless signing identity.
Package committed chart files and license notices. Require a clean exact tag
at the fetched protected `main` tip and an immutable approved-namespace default
application image; reject
placeholder images and existing release collisions. `appVersion` identifies
the selected application, not the source repository's current package version.
The workflow performs no application build, image push or Docker Hub login.
Selection of a fully qualified application artifact remains a release-owner
prerequisite; a syntactically valid digest is not vulnerability approval.

Keep the historical application bundle/manifest format unchanged. This is an
additive independently signed chart archive, not an unannounced migration of
the existing release contract. Prepare automation in the current repository;
a separate chart repository and future OCI/index hosting require their own
explicit destination and signer-identity migration.

Keep the core chart bring-your-own-services. Future optional stack profiles
reuse upstream charts through pinned, locked dependencies and tested generic
Secret/CA/endpoint configuration. Cluster-wide operators and shared production
services remain separately managed. Do not weaken the core strict values or
TLS defaults to make a bundled demo work.

For CI, route only an explicit docs/chart path allowlist to lightweight checks.
Unknown, mixed, application, contract and CI changes select the existing full
jobs. Invalid or unavailable Git change information fails to the full path.
Manual `auto`, `full` and `charts` modes make the intent visible; manual charts
mode provides only chart/documentation evidence. No scheduled full tests are
introduced. Local completion still follows `AGENTS.md` and owning-boundary tests.

Provide an explicit local-Kind smoke command using an already-published image
and a fresh owned namespace. It never selects an ambient customer context,
builds an app image, deletes a cluster or modifies existing user stacks. Its
disposable database is a test fixture, not a new supported PostgreSQL chart.

## Consequences

- Chart-only development and releases can proceed without app rebuilds.
- Upstream reuse removes forks, not our integration/support obligations.
- Exact image signatures, vulnerability policy, install/upgrade evidence and
  protected publication approval are not replaced by a fast smoke test.
- Deferred coverage has stable IDs, owners and triggers in the
  [coverage ledger](../roadmap/release-test-coverage.md).
- The [delivery plan](../roadmap/installation-release-delivery-plan.md) records
  the end-to-end sequence and the remaining publication prerequisites.
