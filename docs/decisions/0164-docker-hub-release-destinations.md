# ADR 0164: Short Docker Hub release destinations

**Status:** Accepted release configuration  
**Date:** 2026-10-04

## Context

The owner selected Docker Hub and approved creating the public repositories
`thedevopshuman/iip` and `thedevopshuman/iip-bridge`. Both repositories now
exist. A repository without image tags is not a published release.

## Decision

Use `docker.io/thedevopshuman/iip` and
`docker.io/thedevopshuman/iip-bridge` as the canonical destinations for the
existing protected GitHub release workflow. This supersedes only the GHCR
destination selection in ADR 0117, decision 5. The neutral product name and
GitHub source repository are unchanged. This is not a second registry mirror.

Keep the publication helper and its public report registry-neutral. Copy the
existing verified OCI indexes without rebuilding, retain their exact digests,
and sign their Docker Hub references using the existing exact GitHub workflow
identity. Existing signature, transparency and vulnerability gates remain.

The job runs only in `thedevopshuman/infra-intelligence-platform`, requires the
`release` environment, and reads its `DOCKERHUB_USERNAME` and `DOCKERHUB_TOKEN`
secrets only at login. Reject a missing token or a username other than
`thedevopshuman` before building. Use password-stdin, a private temporary Docker
configuration outside the checkout, and an always-run logout. Remove GitHub
Packages write permission; retain GitHub release and OIDC signing permissions.
An expiring Read & Write Docker PAT is operator-managed, not stored in source,
artifacts or chat. It is account-bound, not a claim of per-repository scoping.

Expose the existing small Compose source kit as a direct GitHub release asset,
with its own Sigstore bundle under the same exact workflow identity. Operators
need not download the full OCI-image archive to authenticate that kit. Do not
modify the verified bundle after building it.

## Consequences

The two short names are distribution locations, not new product branding.
Public pulls need no publisher token; publishing requires protected CI access.
The workflow configuration and empty repositories do not prove a successful
push, signing, anonymous installation, supported-host compatibility or public-v1
readiness. Registry version-tag immutability, GitHub tag/main protections,
exact-revision qualification and release approval remain pre-publication checks.

See the [release runbook](../operations/release-artifacts.md#protected-github-release-path)
and [Compose kit guide](../operations/community-installation-kit.md).
