# ADR 0017: Select bounded telemetry queries inside investigations

**Status:** Accepted

**Date:** 2026-08-16

## Context

ADR 0014 defines a backend-neutral historical metric query and ADR 0015 proves it against Prometheus, but the deterministic investigator can collect only resource-state evidence. Letting an agent invent provider queries, endpoints, credentials, resource scope, or fresh budgets would bypass the request's authority ceiling. Requiring a caller to run telemetry queries separately would also leave the investigation unable to account for them in its evidence and tool ledger.

## Decision

- Add optional `telemetrySelections` to `InvestigationRequest`. Each item is a bounded candidate, not a grant, and reuses the exact provider-neutral query and output-limit definitions from `TelemetryEvidenceRequest`.
- A candidate carries an opaque selection ID, integration ID, normalized query, limits, and optional root-cause classes. It never carries tenant, actor, resources, time range, deadline, endpoint, vendor query text, or credentials.
- The runtime derives authenticated identity from the investigation command and inherits resources and time range from investigation scope. It derives a deterministic telemetry request ID and caps the execution deadline at the lesser of the investigation wall-time budget and five minutes.
- Select candidates deterministically in request order after resource classification. A candidate with root-cause classes runs only when the current class matches; one without classes is generally applicable.
- Require both `telemetry.metrics` inside the request evidence upper bound and `telemetry/query` inside the tool upper bound when those bounds are present.
- Count every attempted query against `maxToolCalls` and every committed result against `maxEvidenceItems`. Stop before either budget would be exceeded.
- Execute selected candidates through `TelemetryEvidenceService`, preserving its policy decision, tenant-scoped resource resolution, backend adapter, validation, redaction, hashing, and immutable Evidence persistence.
- Keep metric artifacts in the report-level evidence set. The deterministic baseline does not cite a metric result as hypothesis support merely because it contains data; interpreting metric values requires a later evidence-aware reasoning step.
- Treat an unavailable optional telemetry candidate as an explicit unknown without exposing provider errors. Resource-backed conclusions may remain conclusive when the missing metric was not their cited support.

## Consequences

- The investigation runtime can gather real customer metrics without depending on Prometheus, OTLP storage, or another vendor backend.
- Callers and policy retain an explicit upper bound over every selectable metric, integration, output size, and tool/evidence count.
- Replaying a committed investigation returns the immutable report and does not repeat backend queries.
- The first slice performs deterministic candidate matching and collection, not numeric anomaly interpretation, dynamic query synthesis, or iterative model planning.

## Revisit triggers

Revisit when evaluation scenarios require metric values to raise, lower, support, or contradict hypotheses; when agent manifests and tenant policy need separate query-catalog intersections; or when logs and traces introduce signal-specific selection and privacy rules.
