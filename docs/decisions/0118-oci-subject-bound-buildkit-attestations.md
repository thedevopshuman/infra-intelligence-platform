# ADR 0118: Bind BuildKit attestations through the OCI subject graph

**Status:** Accepted
**Date:** 2026-09-07

## Context

ADR 0108 required every extracted SPDX in-toto statement to name its platform
manifest in the statement `subject` array. The pinned BuildKit/Syft release
path emits an empty in-toto `subject` array for local OCI layouts. Instead, it
binds the predicate to the platform through both the attestation-manifest
`subject` descriptor and the enclosing index descriptor's
`vnd.docker.reference.digest` annotation.

Rejecting that standards-based OCI attachment shape makes the real release
bundle fail even though its verified descriptor graph contains an exact
content-addressed subject. Accepting an empty subject without checking the OCI
attachment graph would be unsafe.

## Decision

Supersede only ADR 0108's in-toto-subject requirement as follows:

1. Start from the exact platform manifest digests declared by the already
   verified release manifest.
2. Require the enclosing attestation descriptor to name one of those digests
   through `vnd.docker.reference.digest` and to declare the BuildKit
   attestation-manifest relationship.
3. Require the attestation manifest's closed `subject` descriptor to repeat
   that digest with OCI image-manifest media type and the exact byte length of
   the digest-verified subject blob.
4. Require the in-toto `subject` field to be an array. An empty array is valid
   under the exact OCI subject binding above; if it is non-empty, it must also
   contain the exact platform digest.
5. Continue requiring exactly one digest-verified SPDX predicate for every
   declared role and platform. Missing, duplicate, unbound, or conflicting
   subjects fail closed.

## Consequences

- current BuildKit OCI output can pass the production vulnerability gate
  without rewriting signed or attested material;
- subject authority remains the exact release-manifest digest and verified OCI
  graph, never a filename, tag, platform label, or traversal order;
- older fixtures without an OCI subject descriptor are intentionally rejected;
- a future attestation format needs its own explicit compatibility decision and
  executable evidence.
