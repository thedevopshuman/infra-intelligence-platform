# Telemetry export SLO report contract

**Status:** v1alpha1

**Machine contract:** `contracts/schemas/telemetry-export-slo-report.schema.json`

`TelemetryExportSloReport` measures deployment-wide OTLP exporter reliability over a rolling window without coupling product readiness to the telemetry backend. It is distinct from the latest process-local and deployment health reports.

## Measurement semantics

API and workflow-worker reporters append bounded samples of cumulative `metrics` and `traces` exporter counters. The store calculates non-negative deltas between consecutive samples for the same pseudonymous process and signal. A process that starts inside the window contributes its counters from zero; otherwise its first in-window sample is a baseline unless a retained predecessor exists. Attempts are attributed to the later sample, so boundary precision is the configured reporting interval.

Each signal is assessed independently:

- `disabled`: no in-window sample enabled the signal;
- `no-data`: the signal was enabled but made no measurable export attempt;
- `insufficient-data`: attempts exist but do not meet the minimum cohort;
- `meeting`: the successful-attempt proportion meets the objective; or
- `breached`: a mature attempt cohort misses the objective.

The deployment status uses the most conservative enabled-signal result: `breached`, then `insufficient-data`, then `no-data`, then `meeting`; it is `disabled` only when both signals are disabled. Attainment uses integer basis points: `successfulAttempts * 10000 // eligibleAttempts`.

Samples contain no tenant data, telemetry payload, raw workload identity, endpoint, headers, credentials, provider response, or exception text. History is bounded by deployment retention and must be at least as long as the configured SLO window. Graceful reporter retirement removes only the latest live-health row; historical samples remain until retention expiry.

## API and authority

`GET /v1/operations/telemetry/export-slo` accepts no query parameters. It requires Bearer authentication, the `platform-admin` role, and policy approval for `telemetry-export-slo:read`. The actor tenant is policy scope only and is not returned.

| HTTP status | Code | Meaning |
| --- | --- | --- |
| `400` | `request.invalid` | Query parameters were supplied. |
| `401` | `authentication.required` / `authentication.invalid` | Bearer authentication failed. |
| `403` | `policy.denied` | Required role or policy authorization is absent. |
| `503` | `telemetry.export-slo.unavailable` | Sample storage is unavailable, corrupt, or inconsistent. |
