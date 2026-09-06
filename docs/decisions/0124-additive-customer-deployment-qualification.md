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

## Subsequent extension

[ADR 0129](0129-customer-worker-receiver-processing-continuity.md) upgrades
the aggregate to `single-cluster-processing-v2` by adding exact worker and
receiver processing-continuity evidence. The original control-plane profile
remains historical and is not accepted by the current aggregate schema.

[ADR 0130](0130-customer-postgresql-primary-promotion-continuity.md) then
upgrades it to `single-cluster-database-continuity-v3` by adding portable,
least-privilege PostgreSQL timeline-promotion evidence. Both earlier aggregate
profiles remain historical and are not accepted by the current schema.

[ADR 0131](0131-customer-oidc-prerequisite-qualification.md) upgrades the
current aggregate to `single-cluster-database-oidc-prerequisites-v4`. It adds
one real customer issuer/verifier/browser-prerequisite report, binds its API
target to the continuity target, and keeps interactive login, MFA, session,
logout, revocation, rotation, and issuer-availability evidence outside the
aggregate claim. All three earlier profiles remain historical.

[ADR 0132](0132-customer-policy-engine-bundle-qualification.md) upgrades the
current aggregate to
`single-cluster-database-identity-policy-prerequisites-v5`. It adds one
selected customer policy allow/deny report and binds its exact source, image,
endpoint, protected profile, and immutable snapshot set. Complete policy
coverage, lifecycle, HA, break-glass, and audit evidence remain outside the
aggregate claim. All four earlier profiles remain historical.
