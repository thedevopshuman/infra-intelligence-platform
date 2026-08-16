# 0028 — Repository and runbook context is allowlisted untrusted evidence

**Status:** Accepted

## Context

Incident investigation benefits from runbooks, ownership metadata, deployment configuration, and nearby source context. These documents may contain secrets, stale guidance, malicious prompt text, or instructions that exceed the caller's authority. Public path or search parameters would also create a filesystem/repository traversal boundary.

## Decision

Repository and runbook context enters through a backend-neutral `ContextDocumentsBackend` and the normal Evidence pipeline. Public requests select only protected logical reference IDs and closed document kinds under authenticated resource scope and explicit limits. The first concrete adapter reads cataloged UTF-8 files under a protected root, rejects traversal and out-of-root symlinks, derives immutable revisions from bytes, and performs mandatory redaction before normalization.

Every normalized document is marked `trust: untrusted` and `instructionPolicy: data-only`. Context text never grants authority or overrides platform, policy, agent, or user instructions. The Evidence artifact is confidential and ephemeral by default.

## Consequences

- Customers can mount runbooks and selected source files without exposing arbitrary file reads.
- Prompt-like text remains visible as evidence but has no control semantics.
- Secrets matching the reference redactor are removed before artifact commit.
- Remote Git or service-catalog adapters can replace the file backend without changing public request authority.
