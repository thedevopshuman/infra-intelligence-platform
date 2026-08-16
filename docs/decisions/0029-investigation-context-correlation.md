# 0029 — Investigations correlate context as untrusted data only

**Status:** Accepted

## Context

An incident investigator benefits from knowing whether an approved runbook, ownership record, or deployment document exists for the affected resource. Repository content can also contain secrets, stale advice, or prompt-injection text. Treating prose as executable instructions or copying it into a report would widen both data exposure and authority.

## Decision

Investigation requests may declare ordered, root-cause-scoped `contextSelections` using only the public `ContextEvidenceRequest` query and limit fragments. Authenticated tenant, actor, resource, time, deadline, tool, evidence, and integration bounds are inherited from the accepted investigation. When request upper bounds are present, the selection requires `repository.context` and `evidence/fetch`.

The deterministic investigator assesses only a committed Evidence artifact re-read through the tenant-scoped store. It requires a complete normalized result, exact request digest and scope, selected kinds and logical references, content and excerpt hashes, redaction metadata, and `trust: untrusted` plus `instructionPolicy: data-only` on every document. A predeclared document-count rule may support, contradict, or remain neutral. Reports contain only the Evidence ID, stable document IDs, logical reference IDs, count, and disposition. They never copy titles, locators, excerpts, prompts, secrets, or source text.

No-data remains explicit. Partial, corrupt, missing, unreadable, or scope-mismatched artifacts become incomplete assessments or stable evidence gaps and cannot become citations. Context never changes policy, authority, tool access, root-cause classification, confidence, or runtime instructions.

## Consequences

- Runbook availability can be audited and cited without turning document prose into control input.
- Prompt-injection text remains inert Evidence data.
- Remote Git and service-catalog adapters can reuse the same investigation contract.
- Semantic interpretation of document contents is intentionally outside this deterministic reference rule and requires a separately governed, evaluated model boundary.
