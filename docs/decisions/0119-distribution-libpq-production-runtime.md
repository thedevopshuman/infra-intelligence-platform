# ADR 0119: Use distribution-managed libpq in the production runtime

**Status:** Accepted
**Date:** 2026-09-07

## Context

The first real vulnerability qualification of the multi-platform release
bundle reached the pinned scanner but could not decode the control-plane SPDX
attestations. The `psycopg-binary` wheel carries an embedded auditwheel
CycloneDX document for native libraries built on CentOS or AlmaLinux. Syft
correctly preserves those RPM package identities beside the actual Debian
runtime packages. Trivy rejects a single third-party SPDX input containing
more than one operating-system package type, so no policy result can be
claimed.

Dropping the embedded package records from the attestation or creating a
filtered scan input would make the vulnerability result weaker than the exact
SBOM claim. The release runtime already has a distribution package manager and
does not need a separately bundled libpq stack.

## Decision

1. The installable kernel depends on the pure `psycopg` distribution, without
   the binary extra.
2. The production Docker image installs Debian's `libpq5` from the repositories
   configured by the digest-pinned base image, removes package-index state, and
   asserts at build time that Psycopg selected its `python` implementation.
3. The resulting image digest and attached SPDX predicate remain the exact
   record of the selected libpq and transitive Debian package versions.
4. Developer and CI verification environments may continue using
   `psycopg[binary]` for host portability. That wheel must not enter a
   production release image.
5. Vulnerability qualification continues scanning the unmodified,
   digest-verified SPDX predicate. A scanner decode failure remains a closed
   release failure.

## Consequences

- libpq security updates follow the production image's Debian package lineage
  instead of a wheel's build-distribution lineage;
- the control-plane SBOM contains one operating-system package family and can
  be evaluated by the accepted pinned scanner;
- source installs of the kernel require an available system libpq unless the
  operator deliberately adds a Psycopg implementation suitable for that host;
- replacing the pure wrapper with a compiled wrapper later requires measured
  capacity evidence and must preserve the single-distribution SBOM property.
