# ADR 0082: Multi-window telemetry export burn-rate report

**Status:** Accepted
**Date:** 2026-08-18

## Context

ADR 0073 exposes one rolling sampled window and a binary meeting/breached
verdict. A single window cannot distinguish a brief blip from a sustained
trend, and it gives no sense of how quickly the configured error budget is
being consumed. Operators need an earlier, calibrated warning before a long
window finally reports `breached`, without adding a second sample store or
exposing anything the sampled counters do not already carry.

This report measures only the exporter attempts the API, workflow worker, and
OTLP receiver make against the customer's OpenTelemetry Collector endpoint.
It says nothing about whether the Collector subsequently queues, forwards, or
drops that data before it reaches a backend; that reliability is the
customer Collector/backend's own signal, as ADRs 0072, 0073, and 0081 already
state and this report does not change.

## Decision

Add `TelemetryExportBurnRateReport`, a second read computed from the exact
same bounded pseudonymous exporter-attempt samples ADR 0073 already retains.
No new sample storage, retention path, or reporter is introduced.

For each signal, evaluate two nested windows that share the same end
instant: a short window and a longer window, both deployment-configured.
Reusing the same deployment-owned `minimumAttainmentBasisPoints` objective as
ADR 0073, derive the allowed failure proportion (`10000 -
minimumAttainmentBasisPoints`) as the error budget, and express each
window's observed failure proportion against it as a burn rate scaled by 100
(`100` means burning the budget at exactly the sustainable rate implied by
the objective; `600` means six times that rate). A window is `disabled` when
no in-window sample enabled the signal, `no-data` when enabled but no
attempt was measured, and `insufficient-data` below the shared minimum
eligible-attempt cohort; otherwise it is `sustainable`, `elevated`, or
`critical` against a deployment-configured critical burn-rate threshold.

Combine the two windows per signal the way multi-window burn-rate alerting
requires: `critical` is reported only when both windows independently reach
`critical`. A single critical window paired with a less severe partner
reports `elevated` instead, so a short blip cannot page alone and a slow
long-window drift is still surfaced before it fully breaches. The deployment
status is the most severe enabled-signal result; it is `disabled` only when
every signal is disabled.

Expose the result at `GET /v1/operations/telemetry/export-burn-rate`,
gated the same way as the ADR 0073 report: Bearer authentication, the
`platform-admin` role, and `telemetry-export-burn-rate:read` policy
approval. The sample retention window already configured for ADR 0073 must
cover the longer of the two burn-rate windows, or composition fails closed
at startup.

## Consequences

- operators get an earlier, calibrated signal that budget consumption is
  accelerating, before a single long window would report `breached`;
- the two-window agreement rule keeps a short-lived blip from reporting the
  same severity as a confirmed sustained trend;
- no additional sample volume, retention, or reporter path is introduced;
  the report is a pure read over ADR 0073's existing samples;
- this signal still describes only IIP-to-Collector export attempts;
  Collector-to-backend queue depth and loss, regional aggregation, and
  customer alert routing remain the customer telemetry backend's
  responsibility and are not measured here.
