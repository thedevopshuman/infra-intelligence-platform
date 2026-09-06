# ADR 0117: Publish exact verified OCI indexes before promotion

**Status:** Accepted
**Date:** 2026-09-07

## Context

The release builder produces a verified multi-platform OCI layout with SPDX
and SLSA attestations for the control plane and trusted plugin-mediation bridge.
The repository can verify organizational signatures after publication, but it
does not yet move those exact indexes into a registry. Rebuilding for a
registry could produce a different provenance graph, while trusting a tag
would permit substitution.

Repository ownership, registry namespace, and final brand remain external
decisions. Publication therefore has to accept explicit destinations without
embedding an organization in the platform.

## Decision

1. Copy each already-verified OCI layout to an explicit registry repository by
   its bundle-derived index digest. Do not rebuild during publication.
2. Verify both `repository@sha256:...` references and the convenience version
   tags after the copy. Retain only a successful, source-bound
   `ReleasePublicationReport`; partial publication emits no success evidence.
3. Require two distinct repositories on one explicit registry host and an
   exact `v<application-version>` tag. Customer installation and signature
   verification use only immutable digest references.
4. Keep publication separate from promotion. The publication report remains
   visibly unsigned until the existing organizational signature and SBOM
   vulnerability gates pass.
5. Add a tag-triggered GitHub workflow that derives GHCR destinations and the
   exact Sigstore workflow identity from repository context. It uses a
   protected release environment, minimum scoped permissions, digest-pinned
   third-party Actions and tools, and never persists checkout credentials.
6. Keep registry credentials and GitHub OIDC material in workflow-owned
   process boundaries. Neither enters the publication report or release
   bundle.

## Consequences

- published image bytes, attestations, and index digests are identical to the
  locally verified bundle;
- a mutable version tag cannot become an installation authority;
- workflow retries are idempotent when an existing tag already names the exact
  digest and fail on an observed conflicting tag;
- repository owners must still configure tag/release immutability, release
  environment protection, package visibility, and organizational trust;
- public SDK/package registries and the final project name remain separate
  decisions.
