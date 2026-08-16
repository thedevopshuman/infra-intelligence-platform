# ADR 0026: Derive value-minimized change evidence from accepted resource history

**Status:** Accepted

**Date:** 2026-08-17

## Context

The product constitution treats the resource graph and timeline as equal system-of-record concerns. Current resource state, Kubernetes Events, metrics, and logs can explain impact, but an investigation also needs to establish whether a rollout, image, scaling target, configuration, relationship, or lifecycle recently changed. Raw observation diffs would duplicate complete resource documents into Evidence and could unnecessarily expose credentials, configuration values, or hostile provider text.

## Decision

- Add `ResourceChangeEvidenceRequest` and `ResourceChangeEvidenceResult` public contracts and an authenticated collection endpoint.
- Implement the first provider against the application-owned tenant-scoped `ResourceRepository.history` port. It reads accepted immutable observations only; stale/conflicting records remain audit facts and never become effective-state changes.
- Return change kind, affected resource, changed JSON Pointer paths, before/after observation hashes, source cursor provenance, and observation/recording times. Do not copy changed values into this artifact.
- Classify image, desired scale, relationships, status, creation, and deletion explicitly. Treat other labels/specification paths as configuration.
- Bound resource count, query range, changes, observations scanned per resource, artifact bytes, and deadline. A truncated scan returns `partial`, including when it found no matching change; absence in partial data is never treated as no change.
- Pass the normalized result through the normal authorization, redaction, hashing, and atomic Evidence store.

## Consequences

- Deployment-change evidence works with both the in-memory and PostgreSQL resource-history profiles and requires no ambient provider credential.
- The same contract can later be populated by deployment systems only behind an adapter that preserves tenant scope, value minimization, and explicit completeness.
- Path-only evidence establishes that a class of state changed but cannot by itself explain intent. Repository, deployment-system, and runbook context remain separate evidence sources.
- Long histories can return partial under the per-resource scan limit. A later store optimization may add time-indexed/reverse history queries without changing the public contract.

## Revisit triggers

Revisit when customers require author/approver identity, commit or rollout linkage, diff values under sensitivity policy, time-indexed history queries, or provider-native deployment records.
