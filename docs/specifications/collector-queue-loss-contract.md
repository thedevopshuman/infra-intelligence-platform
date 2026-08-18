# Collector queue/loss report contract

**Status:** v1alpha1

**Machine contract:** `contracts/schemas/collector-queue-loss-report.schema.json`

`CollectorQueueLossReport` measures the customer's OpenTelemetry Collector's
own internal sending-queue depth and send-loss for its pipeline to IIP's
receiver. It is a different measurement from
[`TelemetryExportBurnRateReport`](telemetry-export-burn-rate-contract.md) and
`TelemetryExportSloReport`: those measure IIP's own outbound exporter
attempts (IIP-to-Collector reliability) from durable local counter samples.
This report measures Collector-to-backend reliability, sourced by querying
the customer's own telemetry backend for the Collector's self-emitted
metrics through the existing backend-neutral `TelemetryMetricsBackend` port.
IIP never operates, stores, or discovers the Collector queue itself; ADR
0080 makes queue capacity, retention, and loss policy an explicit customer
deployment choice. See [ADR 0087](../decisions/0087-collector-observed-queue-loss-objective.md).

## Measurement semantics

The report evaluates one window ending at the evaluation instant
(`durationSeconds`, `300`–`2592000` seconds) for each of the two signals this
receiver accepts, `metrics` and `logs` (not `traces`, unlike the
export-attempt reports).

The report is `disabled`, with a `null` `binding` and every signal
`disabled`, whenever the deployment has not configured a
`CollectorQueueLossBinding` - the `TelemetryMetricsBackend` integration and
the `exporter` component name identifying the customer's IIP-bound
Collector pipeline among any others it may run. When configured, `binding`
names the integration and exporter (non-secret identifiers only; never a
credential or endpoint).

For each enabled signal, the service queries six closed logical metric
names through the configured integration:
`platform.collector.exporter.queue-size`,
`platform.collector.exporter.queue-capacity` (both filtered to the signal's
`dataType`), and, per signal,
`platform.collector.exporter.{sent,send-failed}-{metric-points,log-records}`.
The deployment's Prometheus (or other backend) integration configuration
binds these logical names to whatever the customer's Collector actually
calls them.

`sentDelta` and `failedDelta` are the last minus the first counter reading
observed in the window. A delta that would be negative (the counter appears
to have gone backward, most likely a Collector process restart) is treated
as unmeasurable for that signal rather than understating loss: the signal
reports `no-data` and both deltas are `null`. `lossBasisPoints` is
`failedDelta * 10000 // (sentDelta + failedDelta)`. `queueSize` and
`queueCapacity` are the latest gauge reading observed in the window;
`queueUtilizationBasisPoints` is `queueSize * 10000 // queueCapacity`,
capped at `10000`. Queue fields are independently `null` when the
Collector does not expose them, even when `sentDelta`/`failedDelta` are
present.

Each signal's status is:

- `disabled`: the signal is absent from the binding's configured `signals`,
  or no binding is configured at all;
- `no-data`: the binding is configured and enabled for this signal, but the
  backend returned no eligible send attempts (or an unmeasurable counter
  reset);
- `insufficient-data`: eligible attempts (`sentDelta + failedDelta`) exist
  but are below `minimumEligibleAttempts`;
- `breached`: `lossBasisPoints` exceeds `maxLossBasisPoints`, or
  `queueUtilizationBasisPoints` exceeds `maxQueueUtilizationBasisPoints`;
  either condition alone is sufficient; or
- `meeting`: neither threshold is exceeded.

The deployment `status` uses the most conservative enabled-signal result
(`breached`, then `insufficient-data`, then `no-data`, then `meeting`); it is
`disabled` only when the binding itself is absent, matching the same
conservative precedence ADR 0073 established for the export-attempt SLO.

Backend query results are untrusted: a response naming more than one series
for a single logical metric, a non-numeric point value, or a backend
failure fails the request closed rather than guessing.

## API and authority

`GET /v1/operations/telemetry/collector-queue-loss` accepts no query
parameters. It requires Bearer authentication, the `platform-admin` role,
and policy approval for `collector-queue-loss:read`. The actor tenant is
policy scope only and is not returned.

| HTTP status | Code | Meaning |
| --- | --- | --- |
| `400` | `request.invalid` | Query parameters were supplied. |
| `401` | `authentication.required` / `authentication.invalid` | Bearer authentication failed. |
| `403` | `policy.denied` | Required role or policy authorization is absent. |
| `503` | `telemetry.collector-queue-loss.unavailable` | The configured telemetry backend is unavailable, or returned an ambiguous or invalid result. |
