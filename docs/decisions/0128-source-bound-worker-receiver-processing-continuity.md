# ADR 0128: Qualify worker and receiver processing during a planned node drain

**Status:** Accepted

**Date:** 2026-09-06

## Context

ADR 0110 proves that redundant API and OTLP Services remain reachable while a
worker node is drained, and that the API, workflow-worker, and OTLP-receiver
Deployments retain their declared capacity. Its continuous OTLP check uses an
empty protobuf body, however, and its worker evidence is limited to Kubernetes
Ready state. Those observations cannot prove that the receiver commits real
telemetry or that a surviving worker claims and completes durable work.

The new private worker readiness boundary in ADR 0127 is a prerequisite for
trustworthy pod state, but readiness is still not processing evidence. A
separate cluster harness would duplicate image, topology, credential, cleanup,
and source-binding authority without improving the failure observation.

## Decision

Upgrade the owned Kind qualification to `local-multi-node-kind-v2` and retain
the stronger observations in `KubernetesAvailabilityQualificationReport`.

1. Keep the three-node topology, exact immutable image, two replicas per
   application component, PodDisruptionBudgets, and hard worker-node spread.
2. Replace every empty OTLP availability request with a non-empty, allowlisted
   metric. HTTP 200 is counted only across the receiver's existing
   PostgreSQL-before-success boundary, so every successful sample proves a
   normalized durable intake operation.
3. After the harness has verified each stable baseline, drained-node, and
   recovered topology state, submit one bounded investigation through the API,
   wait for the durable worker to complete it, and fetch its immutable terminal
   report. Failed, cancelled, malformed, or timed-out work fails qualification.
4. Continue the independent API and receiver probe while each workflow is in
   progress. The disruption workflow starts only after the target node has no
   application pod, so a pre-drain completion cannot satisfy it.
5. Retain only per-phase submission/completion/failure counts, bounded poll
   counts, and completion duration. Investigation IDs, tenant, cluster,
   namespace, nodes, pods, endpoints, credentials, response bodies, and raw
   errors remain temporary.
6. Require the v2 profile in aggregate local release readiness. Existing v1
   evidence remains historically valid for its narrower route-availability
   claim but cannot satisfy the current release gate.

## Consequences

The local planned-disruption gate now proves API availability, receiver durable
metric intake, and workflow completion with one replica of each application
component unavailable. It also proves recovery before a final processing pass.

This remains a bounded local, planned-disruption observation with a shared
single PostgreSQL instance outside the disrupted failure domain. It does not
qualify involuntary loss, database failover, customer PKI/Collector behavior,
production traffic mix, long-window throughput, zones, regions, or a customer
SLO. Customer deployment qualification must repeat the relevant processing and
failure profiles in the actual environment.
