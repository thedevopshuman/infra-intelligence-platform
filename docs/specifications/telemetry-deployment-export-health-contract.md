# Deployment telemetry export health report contract

**Status:** v1alpha1

**Machine contract:** `contracts/schemas/telemetry-deployment-export-health-report.schema.json`

`TelemetryDeploymentExportHealthReport` gives an operator one bounded view of recent OpenTelemetry export outcomes from API and workflow-worker instances that share the operational store. It complements the process-local report without changing its response.

## Reporting and freshness

An instance with at least one configured exporter publishes its process-local `metrics` and `traces` states at startup and on a fixed heartbeat. The stored identifier is a process-specific SHA-256 pseudonym. Pod names, hosts, endpoints, headers, credentials, payloads, provider responses, and exception text are never stored or returned.

`freshness.staleAfterSeconds` declares when a missed heartbeat becomes `stale`. Stale instances make the deployment status `degraded`, even when their last exporter result was healthy, because the result is no longer current. Graceful shutdown retires an instance immediately; crashed instances remain visible until `retentionSeconds` elapses. This is recent observed membership, not a desired-replica oracle.

At most 1,000 instances are returned in deterministic component/identifier order. `summary.truncated` makes overflow explicit and also degrades the overall status. Counters retain the process-lifetime semantics of the process-local contract and are not combined into an availability SLI.

Overall status is:

- `disabled` when no enabled exporter instance has been observed, or every included instance reports both signals disabled;
- `degraded` when any included instance is stale or degraded, or the result is truncated;
- `awaiting-first-attempt` when all instances are current and at least one enabled signal has not attempted export; or
- `healthy` when all current enabled signals report a successful latest attempt.

## API and authority

`GET /v1/operations/telemetry/deployment-export-health` accepts no query parameters. It requires Bearer authentication, the `platform-admin` role, and policy approval for `telemetry-export-health:read`. The operator tenant is policy scope only and is not returned. Internal reporting has no public write route.

The operation stays separate from liveness and readiness. Reporting and cleanup failures are isolated from product traffic; a read fails closed instead of silently substituting incomplete state.

| HTTP status | Code | Meaning |
| --- | --- | --- |
| `400` | `request.invalid` | Query parameters were supplied. |
| `401` | `authentication.required` / `authentication.invalid` | Bearer authentication failed. |
| `403` | `policy.denied` | Required role or policy authorization is absent. |
| `503` | `telemetry.export-health.unavailable` | Shared state is unavailable, corrupt, or inconsistent. |
