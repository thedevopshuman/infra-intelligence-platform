# ADR 0050: immutable application image identity

**Status:** Accepted
**Date:** 2026-08-17

## Context

The release path produces a content-addressed multi-platform OCI image and the deployment runbook requires an immutable digest, but the Helm chart could express only `repository:tag`. A tag can move after configuration review, leaving the API, worker, OTLP receiver, and migration hook on bytes that are not the reviewed release artifact.

## Decision

Add an optional, strictly validated `image.digest` and resolve every application workload through one Helm helper. When a digest is present, the rendered reference is exactly `repository@sha256:<64 lowercase hex>` and the tag is ignored. When it is absent, the tag path remains available for local development and controlled evaluation.

The values schema also closes repository and tag syntax so an image field cannot inject malformed YAML or unsupported reference components. The kind install/upgrade gate builds a single-platform local image without provenance metadata, because kind imports only the host-platform manifest from Docker Desktop. After import it registers that exact manifest under its digest reference on every disposable kind node, deploys both Helm revisions with `pullPolicy: Never`, and reads the live Deployment back to prove the digest survived rendering and rollout. The release builder independently retains multi-platform SBOM and provenance attestations.

## Consequences

- API, worker, OTLP receiver, and migration workloads cannot drift independently from the selected OCI digest.
- Customers can bind the chart directly to the OCI index described by the release manifest and signature policy.
- Mutable tags remain a clearly documented development fallback; production promotion must reject an empty digest outside this portable chart contract.
- Registry publication, organizational signing, and signature admission still depend on the selected external release identity and cluster policy.
