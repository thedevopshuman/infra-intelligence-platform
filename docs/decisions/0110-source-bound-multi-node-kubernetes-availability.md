# ADR 0110: Qualify planned Kubernetes disruption in an isolated multi-node cluster

**Status:** Accepted
**Date:** 2026-09-06

**Extended by:** [ADR 0128](0128-source-bound-worker-receiver-processing-continuity.md),
which replaces this v1 profile in current release qualification while retaining
its narrower historical claim.

## Context

ADR 0109 defines zero-unavailable rollout, component-specific disruption
budgets, hard topology spread, and graceful termination for the API, worker,
and receiver. Helm rendering and deployment preflight prove configuration but
cannot prove that Kubernetes eviction, scheduling, Services, and the live
processes interact correctly during a node drain. Reusing a developer or
customer cluster would make cleanup authority ambiguous and the result
non-reproducible.

## Decision

Add a closed `local-multi-node-kind-v1` qualification profile and a
`KubernetesAvailabilityQualificationReport`.

1. The harness creates and owns a uniquely named Kind cluster with one
   control-plane and two workers. It rejects existing cluster names and passes
   the exact generated context to every Kubernetes and Helm command.
2. PostgreSQL and an independent aggregate-only probe are pinned to the
   control-plane. Two replicas of each IIP component are hard-spread across the
   workers under component-specific `minAvailable: 1` budgets.
3. The probe first commits one synthetic resource and one non-empty OTLP metric.
   It then continuously verifies exact release identity through the API Service
   and authenticated empty protobuf exports through the receiver Service.
4. The harness changes probe phase before draining one worker through the
   Eviction API. It requires exact baseline, disruption, and recovered replica,
   topology-domain, and PDB states and at least 20 zero-failure probe attempts
   per phase and signal.
5. Raw snapshots, identifiers, endpoints, and credentials are temporary. The
   retained content-derived report contains only versions, digests, aggregate
   states, checks, and explicit limitations. Python and TypeScript expose the
   artifact type, but no HTTP route accepts it.

## Consequences

- Chart availability semantics now have live, repeatable planned-disruption
  evidence rather than configuration-only coverage.
- The probe and PostgreSQL do not share the disrupted worker failure domain,
  making the application Service outcome observable without conflating the
  test with database continuity.
- Receiver TLS is intentionally disabled in this isolated gate; existing
  compatibility evidence remains authoritative for transport identity.
- A successful local report cannot be promoted into claims about involuntary
  node loss, database HA, customer ingress/controllers, sustained production
  load, multiple regions, or customer RPO/RTO.
- Customer release qualification must repeat relevant failure and load tests in
  the actual deployment environment.
