# ADR 0127: Give the workflow worker a private dependency-aware health boundary

**Status:** Accepted

**Date:** 2026-09-06

## Context

The API and isolated OTLP receiver already separate dependency-free liveness
from PostgreSQL/schema readiness. The workflow-worker Deployment had no probe,
so Kubernetes considered a running container Ready even when it could not
reach the operational store or verify the latest packaged migration. A
rollout, PodDisruptionBudget, or future customer continuity report built on
that signal could therefore preserve replica count while all useful worker
processing was unavailable.

The worker must not gain an interactive control-plane surface, authentication
credential, tenant query, or telemetry dependency merely to expose health.
Optional OpenTelemetry delivery must also remain fail-open for product work.

## Decision

1. Start a separate private HTTP listener in the workflow-worker process. It
   exposes only `GET /healthz` and `GET /readyz`; it has no Kubernetes Service,
   ingress, tenant route, SDK, or external API declaration.
2. Liveness is process-only. Readiness invokes the same bounded
   `ReadinessProbe` composed for the worker, which verifies PostgreSQL
   connectivity and the latest packaged schema in durable deployments.
3. Readiness returns only `status: ok` or the stable
   `readiness.unavailable` response. It never returns an endpoint, database,
   migration, tenant, job, provider, credential, or raw error.
4. Receipt of `SIGTERM` or `SIGINT` marks the listener draining before the
   polling loop stops and before bounded in-flight work is joined. Liveness
   remains available during that cooperative drain; readiness does not.
5. The listener accepts only a canonical literal IPv4 bind address and an
   unprivileged port. Helm fixes the bind address to `0.0.0.0`, declares the
   port under the closed worker values object, and adds liveness/readiness
   probes without creating a Service or NetworkPolicy ingress grant.
6. Every shipped Docker worker receives the same private readiness healthcheck
   so local lifecycle status exercises the production process boundary.
7. Provider-neutral durable-store failures in recurring maintenance operations
   are isolated to the current bounded pass. The process remains live, reports
   not-ready through the shared readiness probe, and can resume after the store
   recovers. Startup, configuration, and process-boundary failures remain fatal
   for the runtime supervisor to surface.

## Consequences

Kubernetes and Docker stop routing rollout/disruption decisions through an
implicit container-running signal. A database outage or stale migration
removes workers from readiness without creating dependency-driven restart
churn. An optional telemetry backend remains outside readiness. Graceful
termination advertises not-ready before waiting for bounded work.

This signal proves process and required-store readiness only. It does not
prove queue throughput, tenant fairness, successful provider calls, receiver
intake, or continuity during a disruption. Those remain separate measured
qualification gates.
