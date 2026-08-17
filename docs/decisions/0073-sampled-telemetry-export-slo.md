# ADR 0073: Sampled telemetry export SLO

**Status:** Accepted

## Context

ADRs 0052 and 0072 expose latest exporter outcomes, but a current success can hide repeated recent failures and a current failure does not quantify whether a reliability objective was missed. Process counters reset on restart and do not themselves provide a deployment-wide time window.

## Decision

Append bounded pseudonymous samples of cumulative exporter attempts, successes, and failures to the shared operational store. Calculate per-signal rolling success proportions from monotonic counter deltas, treating a retained pre-window sample as the baseline and attributing each delta to its later sample. Evaluate metrics and traces independently against a deployment-owned minimum cohort and attainment threshold, and expose the conservative aggregate through a separate administrator/policy-protected operation.

Retain samples longer than the configured objective window. Keep collection failure-isolated from serving, preserve history across graceful process retirement, reject negative or inconsistent deltas, and return no-data or insufficient-data rather than inventing reliability. Do not store tenant telemetry, payloads, endpoints, credentials, provider errors, or raw workload identity.

## Consequences

- operators can distinguish a latest exporter incident from a sustained objective breach;
- process restarts do not merge counters and newly started processes have an explicit zero origin;
- window-boundary precision is limited by the heartbeat interval;
- database volume grows with reporter count and interval but remains bounded by retention;
- regional aggregation, burn-rate alerting, Collector queue/loss objectives, and customer alert routing remain separate work.
