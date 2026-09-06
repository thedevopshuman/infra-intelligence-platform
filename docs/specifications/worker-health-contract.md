# Workflow worker health contract

**Status:** `v1alpha1` private operational contract

The workflow worker exposes a separate HTTP listener described by
`api/openapi/worker-health.openapi.json`. It exists only so the local container
runtime and Kubernetes kubelet can distinguish a running process from a worker
that can safely use its required durable store.

## Routes

- `GET /healthz` is dependency-free liveness and returns only
  `{"status":"ok"}`.
- `GET /readyz` calls the application-owned `ReadinessProbe`. A durable worker
  is ready only when PostgreSQL is reachable and the latest migration packaged
  with the running image is present.
- A draining worker returns HTTP 503 with only
  `readiness.unavailable`; liveness remains successful while bounded work is
  joined.
- Provider-neutral durable-store failures end only the current bounded
  maintenance pass. The listener remains live and not-ready until the shared
  store probe succeeds again; startup, configuration, and process-boundary
  failures remain fatal.
- Query parameters are rejected and every `/v1`, mutation, tenant, workflow,
  provider, or diagnostic route is absent.

## Deployment and authority

The Helm chart creates no Service or ingress for `worker.healthPort`. The
listener receives no authentication configuration and cannot construct an
actor, select a tenant, inspect a queue, run work, or mutate state. Health
requests are not SDK operations.

Optional OpenTelemetry export and provider availability are deliberately not
readiness dependencies. This contract does not prove queue throughput or
disruption continuity; those require separate measured evidence.
