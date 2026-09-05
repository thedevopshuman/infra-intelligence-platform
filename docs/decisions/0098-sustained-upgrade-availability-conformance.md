# ADR 0098: Prove sustained in-cluster availability across N-1 transitions

**Status:** Accepted  
**Date:** 2026-09-05

## Context

Phase-bound readiness checks can succeed even when a Kubernetes Service is
briefly unavailable during an application upgrade or rollback. They also do
not prove that authenticated data reads continue to return the tenant record
while old and new replicas coexist.

## Decision

Extend the packaged N-1 gate with an independent, least-authority in-cluster
probe. The probe has no service-account token, reads its generated Bearer
credential from a read-only Secret volume, and sends repeated authenticated
requests through the chart's Service for the entire target upgrade,
application rollback, and re-upgrade.

Each sample requires both an allowed release-mode source identity and the
seeded tenant Resource. The gate requires observations from the ancestor and
target at each stable phase, at least 40 requests and 20 complete samples, and
zero transport, authorization, identity, schema, or data failures. Probe state
contains only aggregate counts and closed failure classes; it never records the
credential, response bodies, database values, or provider error text.

## Consequences

The selected packaged N-1 pair has executable evidence for bounded, sustained
authenticated read availability through the internal Kubernetes Service. This
does not certify customer ingress controllers, production latency or load,
node/database failure, multi-zone disruption, connection draining for long
requests, or uninterrupted mutation workflows. Those remain environment and
workload-specific promotion gates.
