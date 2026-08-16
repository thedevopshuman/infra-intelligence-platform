# ADR 0020: Normalize Kubernetes Events as evidence before investigation correlation

**Status:** Accepted

**Date:** 2026-08-16

## Context

The resource projection identifies current state, but Kubernetes controller Events often explain why that state occurred. Direct `kubectl` execution or Kubernetes client objects inside the investigator would mix credentials, vendor transport, normalization, reasoning, and storage. The repository also already uses CloudEvents for internal platform history; overloading that contract with customer cluster Events would make provenance ambiguous.

## Decision

- Keep platform CloudEvents and customer Kubernetes Event evidence as separate contracts and storage semantics.
- Add bounded `KubernetesEventEvidenceRequest` and normalized `KubernetesEventEvidenceResult` artifacts, stored through the existing immutable Evidence pipeline.
- Define a credential-free `KubernetesEventsBackend` application port. It receives authenticated scope only as normalized tenant, actor, integration, resource identities, filters, limits, time range, and deadline.
- Reject records outside the requested resource, severity, reason, time, count, or byte bounds. Canonically order accepted events by last occurrence and event ID.
- Permit bounded optional message text only through the existing mandatory structured redaction boundary. Never expose raw provider errors.
- Add root-cause-scoped `kubernetesEventSelections` and condition-count interpretations to investigations. Read and assess only the committed artifact bytes.
- Record supporting/contradicting citations and an explicit structured assessment. Leave resource-derived class, confidence, rank, and terminal outcome unchanged in this deterministic slice.
- Treat `no-data` as no-data, partial output as incomplete, and corrupt/mismatched artifacts as a stable evidence gap.
- Execute event selections before metric selections when both are eligible, so tool/evidence budget consumption is deterministic.
- Compose an honest no-data backend by default. Implement live Kubernetes transport as a replaceable adapter, not as core application behavior.

## Consequences

- Local and Docker verification require no Kubernetes cluster and never invent event evidence.
- A later Docker Desktop, kind, or remote-cluster adapter can change transport and credential resolution without changing investigations, HTTP, SDKs, or public schemas.
- Evidence remains replayable, tenant scoped, redacted, content hashed, and independently auditable.
- A caller cannot smuggle cluster-wide selectors, kubeconfig, endpoints, credentials, or raw commands through the public contract.
- Condition taxonomy mapping becomes adapter conformance work and must be versioned additively.

## Revisit triggers

Revisit when real-cluster conformance requires event-series identity rules across Kubernetes API versions, related-resource traversal, namespace policy, minimum occurrence-age semantics, watch streaming, or explicit multi-signal confidence updates.
