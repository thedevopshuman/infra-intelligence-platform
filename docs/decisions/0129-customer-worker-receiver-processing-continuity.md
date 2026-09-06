# ADR 0129: Qualify customer worker and receiver continuity with bounded pod Evictions

**Status:** Accepted
**Date:** 2026-09-06

## Context

ADR 0128 proves durable OTLP metric intake and investigation processing during
a worker-node drain, but only in a repository-owned Kind cluster. ADR 0123
proves customer ingress continuity while one API pod is evicted, but explicitly
does not cover worker or receiver processing. A customer release needs a
separate, environment-bound test without turning local evidence into a
production claim or granting the running platform ambient disruption authority.

## Decision

Add a `customer-worker-receiver-pod-eviction-v1` profile and
`CustomerProcessingContinuityQualificationReport`.

1. A protected `CustomerProcessingQualificationProfile` supplies the exact
   tenant, actor, existing Resource, allowlisted metric, and bounded
   investigation tools. The retained report includes only its digest.
2. The host-side qualifier uses direct verified HTTPS for the control plane and
   direct mutual-TLS HTTPS plus a separate channel Bearer credential for OTLP.
   Proxies and redirects are disabled.
3. Every receiver attempt sends a non-empty metric. HTTP 200 therefore crosses
   the receiver's PostgreSQL-commit-before-success boundary.
4. The qualifier observes exact image, redundant ready replicas,
   `maxUnavailable: 0`, and matching PodDisruptionBudgets for the worker and
   receiver. It then performs two sequential `policy/v1` Evictions with exact
   observed-UID preconditions.
5. Baseline, worker-Eviction, receiver-Eviction, and recovered phases each
   require bounded API and receiver samples plus one durable investigation.
   The two disruption requests are submitted only after reduced capacity and
   original-pod unavailability are observed. Completion may overlap recovery,
   so the contract calls this disruption overlap rather than claiming that all
   processing occurred at reduced capacity.
6. Disruption requires a separate explicit enable flag. Raw credentials,
   certificates, tenant/actor/Resource values, endpoints, Kubernetes names,
   UIDs, response bodies, and errors never enter retained evidence.
7. The report stays outside the immutable release bundle and control-plane API.
   It remains an external customer gate until executed against the selected
   environment and exact release.
8. Upgrade the customer deployment aggregate to
   `single-cluster-processing-v2`. It consumes the processing report as a fifth
   owning artifact, rebinds its API target to the customer continuity target,
   verifies its OTLP/profile/component bindings, and requires the final health
   diagnostic after both disruption workflows.

## Consequences

- Operators receive one reproducible, least-authority customer processing gate
  instead of manually correlating pod replacement, OTLP success, and jobs.
- The runtime receives no new Kubernetes mutation authority; only the external
  operator running the qualifier needs narrow pod-Eviction permission.
- The profile produces synthetic metric Evidence and investigation records in
  the selected tenant. Customers must use an approved qualification tenant and
  retention policy.
- This does not qualify database failure, node/zone/region loss, simultaneous
  component failure, sustained customer traffic, or long-window SLOs.
