# ADR 0124: Aggregate exact customer deployment evidence without authorizing production

**Status:** Accepted
**Date:** 2026-09-06

## Context

IIP already emitted a live dependency preflight, a privacy-minimized
post-install health diagnostic, an external HTTPS ingress report, and a
sustained API-pod Eviction continuity report. Each artifact had an intentionally
narrow owner and claim. A release operator still had to compare source,
application, chart, image, target, time, and nested evidence bindings by hand.
That manual join could accept a stale diagnostic, reports from different
clusters, or a continuity report for another release.

ADR 0120 kept customer gates external and explicitly allowed a future additive
profile once environment-specific contracts existed. Boolean acknowledgements
or a broad `production-ready` flag remain prohibited.

## Decision

1. Add `CustomerDeploymentQualificationReport` with the closed
   `single-cluster-control-plane-v1` qualification level.
2. Consume exactly four owning reports: cluster-mode deployment preflight,
   post-install diagnostic, customer ingress, and customer continuity.
3. Require one clean current source, exact application/chart/migration/image
   identity, protected values digest, explicit target bindings, and the
   continuity report's exact nested ingress digest.
4. Re-observe the selected namespace UID and Kubernetes server during
   generation. Reject a different current cluster rather than trusting a
   context name.
5. Require fresh evidence and a healthy observation after the continuity
   window. Retain valid unsuccessful evidence as `not-qualified`; reject
   malformed or crossed inputs.
6. Retain only report IDs/digests, public release identity, aggregate
   timestamps, stable status/error codes, and hashed environment bindings.
7. Keep publication/signature, database, worker/receiver, integration, live AI
   and pricing, regional, capacity, pilot, legal, brand, and governance limits
   fixed in every report.

## Consequences

Customers and release owners receive one reproducible answer to the narrow
question: did this exact installed control plane pass live dependency checks,
external probing, one protected pod disruption, and a post-recovery health
check in this exact cluster? The answer is machine-verifiable without sharing
customer identifiers or Secret values.

The aggregate does not approve installation, artifact trust, production use,
or public release. It cannot replace the remaining external gates and it is
not served by the control-plane API. A future production promotion profile may
consume it alongside separately authorized signature, integration, provider,
pilot, and governance evidence.
