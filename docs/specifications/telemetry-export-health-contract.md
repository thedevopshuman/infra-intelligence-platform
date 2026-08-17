# Telemetry export health report contract

**Status:** v1alpha1

**Machine contract:** `contracts/schemas/telemetry-export-health-report.schema.json`

`TelemetryExportHealthReport` is a bounded, process-local view of whether the configured OpenTelemetry metric and trace exporters are delivering. It distinguishes configuration from observed delivery without coupling the platform to a Collector or telemetry backend vendor.

## Signal states

The report always contains `metrics` followed by `traces`. Each signal has one state:

- `disabled`: no exporter is composed for that signal;
- `awaiting-first-attempt`: enabled, but the process has not completed an export attempt;
- `healthy`: the latest completed attempt succeeded; or
- `degraded`: the latest completed attempt failed.

`attempts`, `successes`, `failures`, and `consecutiveFailures` are process-lifetime, saturating counters. They reset when the process restarts and are not an availability-window SLI. Timestamps describe completed attempts only. A successful attempt resets `consecutiveFailures` and returns the signal to `healthy`; historical failure totals and the last bounded failure remain visible.

Failure codes are provider-neutral. `telemetry.export.rejected` means the exporter returned a failure result, `telemetry.export.exception` means the exporter raised, and `telemetry.export.failed` is the safe fallback. The report never contains endpoints, headers, credentials, payloads, tenant telemetry, provider responses, or exception text.

Overall status is `disabled` when neither signal is enabled, `degraded` when any enabled signal is degraded, `awaiting-first-attempt` while any enabled signal has not attempted delivery, and otherwise `healthy`. Disabled signals do not make an enabled healthy signal degraded.

## API, authority, and availability

`GET /v1/operations/telemetry/export-health` requires Bearer authentication, the `platform-admin` role, and policy approval for `telemetry-export-health:read`. The result is process-wide rather than tenant data; the tenant on the operator identity is used only as explicit policy scope and is not returned.

This endpoint is intentionally separate from `/healthz` and `/readyz`. A customer-selected Collector or backend outage must not stop resource ingestion, investigation, or action-governance traffic. The report covers only the control-plane process answering the request. The separate [deployment telemetry export health contract](telemetry-deployment-export-health-contract.md) aggregates recent pseudonymous API and workflow-worker heartbeats without changing these process-local semantics.

| HTTP status | Code | Meaning |
| --- | --- | --- |
| `400` | `request.invalid` | Query parameters were supplied. |
| `401` | `authentication.required` / `authentication.invalid` | Bearer authentication failed. |
| `403` | `policy.denied` | The platform-admin role or policy authorization is absent. |
| `503` | `telemetry.export-health.unavailable` | The process-local reader returned inconsistent state. |
