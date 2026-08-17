# ADR 0047: attested release bundle

**Status:** Accepted
**Date:** 2026-08-17

## Context

A runnable Docker image and Helm chart are not enough for a customer release. Operators need to bind installation artifacts to one source revision, inspect included components, trace how each platform image was built, and detect substituted downloads. Publisher authentication is a separate concern that cannot be honestly completed before a repository host and organizational release identity are selected.

## Decision

Build one clean-revision release bundle containing a multi-platform OCI image layout, Helm chart, public contracts/OpenAPI, and installable Python-source and compiled TypeScript SDK packages. The Python base-image index and BuildKit SBOM generator are digest pinned. The OCI build includes an SPDX SBOM and SLSA v1 provenance statement for every platform.

Finalize the bundle with the versioned `ReleaseManifest` contract and a deterministic `SHA256SUMS` file. Verification recalculates artifact sizes and digests, walks the OCI descriptor graph, and requires both attestation predicate types for every declared platform. Source and chart archives come from the same clean Git revision rather than an ambient working tree.

The local manifest says `signatureStatus: unsigned`. Production promotion must publish the OCI index by digest and sign that immutable digest using a separately accepted organizational identity and verification policy. Checksums and build attestations must never be described as publisher signatures.

## Consequences

- A release candidate is one inspectable bundle rather than unrelated files assembled manually.
- SDK and chart packages are included in the same revision binding as the image and public contracts.
- Local verification detects artifact tampering and missing SBOM/provenance attestations without trusting filenames.
- Registry publication, keyless or managed signing, transparency policy, vulnerability policy, and Git-host workflow remain explicit hosting/governance gates.
