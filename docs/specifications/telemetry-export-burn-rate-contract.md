# Telemetry export burn-rate report contract

**Status:** v1alpha1

**Machine contract:** `contracts/schemas/telemetry-export-burn-rate-report.schema.json`

`TelemetryExportBurnRateReport` calculates a multi-window error-budget burn
rate for deployment-wide OTLP exporter reliability from the same bounded
pseudonymous counter samples [ADR 0073](../decisions/0073-sampled-telemetry-export-slo.md)
already retains. It is a second, faster-warning read over that data, not a
new sample store. See [ADR 0082](../decisions/0082-multi-window-telemetry-export-burn-rate.md).

This report measures IIP-to-Collector export-attempt reliability only. It
says nothing about Collector-to-backend queue depth or loss, regional
aggregation, or alert routing; those remain the customer telemetry backend's
responsibility.

## Measurement semantics

The report evaluates two nested windows that end at the same evaluation
instant: a `short` window and a `long` window (`shortWindowSeconds <
longWindowSeconds`, both `300`–`2592000` seconds). Each window is aggregated
from the exact monotonic-counter-delta calculation ADR 0073 defines,
independently for `metrics` and `traces`.

For an eligible window (at least one enabled observation), the error budget
is `10000 - minimumAttainmentBasisPoints` basis points, the allowed
proportion of failed attempts implied by the shared rolling-SLO objective.
The observed failure proportion for that window, in basis points, is
`failedAttempts * 10000 // eligibleAttempts`. The window's burn rate is that
observed proportion divided by the error budget and scaled by `100`, so
`100` means the window is consuming budget at exactly the sustainable rate
implied by the objective, and `600` means six times that rate.

Each window's per-signal status is:

- `disabled`: no in-window sample enabled the signal;
- `no-data`: the signal was enabled but made no measurable export attempt;
- `insufficient-data`: attempts exist but do not meet the shared minimum
  eligible-attempt cohort;
- `sustainable`: the burn rate is at or below `100`;
- `elevated`: the burn rate exceeds `100` but is below the deployment's
  `criticalBurnRateHundredths` threshold; or
- `critical`: the burn rate meets or exceeds `criticalBurnRateHundredths`.

`attainmentBasisPoints` and `burnRateHundredths` are `null` only for
`disabled` and `no-data` windows; they are present (and can still describe an
immature cohort) for `insufficient-data`, `sustainable`, `elevated`, and
`critical` windows.

The per-signal status combines both windows: `critical` is reported only
when **both** the short and the long window independently report `critical`.
A window reporting `critical` while its partner does not is treated as
`elevated` before the two are compared, so a lone critical window cannot
outrank a confirmed two-window agreement, and the more conservative of the
two is reported using the same order the deployment status uses below
(`disabled < sustainable < no-data < insufficient-data < elevated <
critical`). The deployment `status` uses the most conservative enabled-signal
result (`critical`, then `elevated`, then `insufficient-data`, then
`no-data`, then `sustainable`); it is `disabled` only when both signals are
disabled. Genuine uncertainty (`no-data`, `insufficient-data`) outranks a
comfortable `sustainable` read from a partner window or signal, so a quiet or
immature channel cannot mask a channel that lacks enough data to judge.

Samples contain no tenant data, telemetry payload, raw workload identity,
endpoint, headers, credentials, provider response, or exception text, the
same as ADR 0073. The configured sample retention must be at least the long
window's duration.

## API and authority

`GET /v1/operations/telemetry/export-burn-rate` accepts no query parameters.
It requires Bearer authentication, the `platform-admin` role, and policy
approval for `telemetry-export-burn-rate:read`. The actor tenant is policy
scope only and is not returned.

| HTTP status | Code | Meaning |
| --- | --- | --- |
| `400` | `request.invalid` | Query parameters were supplied. |
| `401` | `authentication.required` / `authentication.invalid` | Bearer authentication failed. |
| `403` | `policy.denied` | Required role or policy authorization is absent. |
| `503` | `telemetry.export-burn-rate.unavailable` | Sample storage is unavailable, corrupt, or inconsistent. |
