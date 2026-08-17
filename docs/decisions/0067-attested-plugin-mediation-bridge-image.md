# ADR 0067: attested plugin-mediation bridge image

**Status:** Accepted
**Date:** 2026-08-17

## Context

ADR 0066 keeps signed plugins on Docker's `none` network and uses a small,
trusted relay to carry invocation-local Unix-socket frames to the host-mediated
provider boundary. The conformance runner can build that relay locally, but a
customer runner must not depend on an ambient source tree or mutable local
build. Shipping only the control-plane image would leave a security-critical
runtime component outside the release revision, checksum, SBOM, provenance, and
promotion evidence.

## Decision

Build the plugin-mediation bridge from the same clean revision as the control
plane and package it as a separate multi-platform OCI layout in every release
bundle beginning with application `0.41.0`. Give the bridge its own artifact
role and image metadata in `ReleaseManifest`, and require the same declared
Linux platform set, SPDX SBOM attestation, and SLSA v1 provenance attestation as
the control plane.

Keep the bridge separate from the control-plane image because it has a smaller
runtime purpose and a distinct trust/deployment boundary. Production promotion
must publish, sign, verify, and pin both image digests. The `v1alpha1` schema
addition stays optional so older manifests remain valid, while the repository
verifier requires the bridge for `0.41.0` and later bundles.

## Consequences

- A current release bundle contains every repository-owned image needed by the
  host-mediated no-network runner.
- Operators can review and promote the relay independently without rebuilding
  it at invocation time.
- Omitting, substituting, or removing attestations from the current bridge
  image fails bundle verification.
- Registry publication, publisher signatures, vulnerability policy, and the
  dedicated production runner deployment remain external promotion gates.
