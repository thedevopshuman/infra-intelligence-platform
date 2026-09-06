# ADR 0123: Qualify customer control-plane continuity with one PDB-governed Eviction

**Status:** Accepted  
**Date:** 2026-09-06

## Context

ADR 0105 measures external ingress without mutation. ADR 0110 proves
component availability during a worker drain, but only inside a disposable
Kind cluster whose database and probe are isolated from the disrupted worker.
Neither proves that a customer's actual ingress continues to serve the exact
release while Kubernetes replaces a control-plane pod. Reusing either report
for that claim would erase an important environment and authority boundary.

## Decision

Add a separate `customer-control-plane-pod-eviction-v1` profile and
`CustomerContinuityQualificationReport`.

1. The harness reuses the existing direct, verified-HTTPS ingress probe and
   binds its complete report by SHA-256 digest.
2. A customer run requires clean source, exact release identity, an immutable
   image digest, redundant ready API capacity, `maxUnavailable: 0`, and a
   matching PDB with one allowed disruption.
3. Every Kubernetes operation names an explicit context and namespace. The
   only mutation is one `policy/v1` pod Eviction carrying the exact observed
   UID precondition, and it requires an explicit disruption enable flag.
4. The probe begins before Eviction, continues through replacement, and
   remains active for a bounded post-recovery period. Closed availability,
   p95 latency, and recovery objectives determine qualification.
5. Retained evidence replaces all customer endpoint and Kubernetes names with
   pseudonymous digests and contains no credentials, identities, response
   bodies, selectors, UIDs, certificates, logs, or raw errors.
6. The report stays outside the immutable bundle and the control-plane API.
   Local release readiness continues to list customer ingress/availability as
   external rather than treating this unexecuted profile as local evidence.

## Consequences

- Operators receive one reviewable command and offline verifier for a real
  customer continuity gate without granting the platform runtime new
  authority.
- The read-only ingress qualifier and isolated Kind availability qualifier
  keep their original claims and safety properties.
- The run creates sustained synthetic read traffic and one real pod
  disruption, so it must be scheduled and separately authorized.
- A successful report covers only the API path and one pod Eviction. Database,
  worker, receiver, node, zone, region, workload-capacity, and long-window SLO
  claims remain open.

