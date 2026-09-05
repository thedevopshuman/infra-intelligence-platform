# ADR 0109: Protect every enabled application workload during planned disruption

**Status:** Accepted
**Date:** 2026-09-06

## Context

The production profiles require redundant API, workflow-worker, and optional
OTLP receiver replicas, but the chart previously protected only API pods with
a disruption budget and zero-unavailable rollout. Worker and receiver replicas
could still be placed on one node or voluntarily disrupted together. The
receiver also used the process default for `SIGTERM`, so an accepted telemetry
request could be interrupted during an ordinary rollout.

Replica count alone is not availability. A chart can make the intended
Kubernetes application boundary reviewable, but it cannot prove node, zone,
ingress, network, database, storage, or regional continuity in a customer
environment.

## Decision

1. API, workflow-worker, and OTLP receiver Deployments use a surge-first
   `RollingUpdate` with `maxUnavailable: 0`, `maxSurge: 1`, and a bounded
   minimum-ready interval.
2. Each enabled production workload has its own PodDisruptionBudget. A
   production preflight accepts only an integer `minAvailable` that preserves
   at least one replica while permitting at least one voluntary disruption.
   Worker and receiver budgets cannot be enabled when their workloads are
   disabled.
3. One closed topology-spread setting generates a component-specific selector
   for every enabled application Deployment. The production profiles require
   `maxSkew: 1`, `minDomains: 2`, and `DoNotSchedule`; the default remains
   disabled so a one-node development cluster is still installable. Arbitrary
   API-only supplemental constraints remain supported through the existing
   advanced value.
4. The worker retains its cooperative `SIGTERM` stop and receives an explicit
   grace period. The OTLP receiver adopts the same non-daemon draining HTTP
   server as the API, stops acceptance off the signal thread, joins active
   handlers, and closes shared runtime state last. Its pre-stop endpoint delay
   is smaller than its total grace period and is rejected otherwise.
5. `CustomerDeploymentPreflightReport` strengthens the existing
   `pod-disruption-budget` check to cover every enabled component and adds the
   ordered `hard-topology-spread` check. The report retains only booleans,
   counts, digests, and stable failure codes; it does not claim live
   availability.

## Consequences

The shipped production profiles now express a coherent planned-disruption and
placement policy for every long-running application workload. A rolling
upgrade or voluntary node drain has fewer avoidable single-component outages,
and accepted OTLP requests receive the same bounded process-drain behavior as
API requests.

Strict topology spread can leave replicas Pending when the cluster has too few
eligible topology domains. Operators must provide the nodes and labels required
by their selected topology key before installation. PodDisruptionBudgets do not
protect against involuntary node loss, simultaneous infrastructure failure, or
an unavailable shared dependency. Customer ingress draining, sustained load,
automatic PostgreSQL failover/fencing, storage durability, and regional
continuity remain separate qualification gates.
