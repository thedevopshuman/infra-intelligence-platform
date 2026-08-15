# Ingestion freshness report contract

**Status:** v1alpha1

**Machine contract:** `contracts/schemas/ingestion-freshness-report.schema.json`

`IngestionFreshnessReport` is a point-in-time, tenant-scoped evaluation of one configured ingestion source. It exposes enough information to operate collection freshness and event-delivery health without disclosing provider checkpoints, opaque cursor values, credentials, or resource contents.

## Measurement semantics

`checkpoint.ageSeconds` is the primary source-freshness signal. It measures from the last complete, durably committed collection result to `metadata.evaluatedAt`. Partial, failed, cancelled, stale-resume, and scope-conflicting collections cannot advance the checkpoint, so the age continues to grow until a new complete boundary commits.

`latestObservation` is present when the source has at least one accepted observation:

- `ageSeconds` measures provider-observation time to evaluation time;
- `ingestionDelaySeconds` measures provider-observation time to durable platform recording time;
- `observedAt` remains provider-originated, while `recordedAt` is platform storage time.

An empty but complete reconciliation can have a valid checkpoint and no accepted observation. In that case `latestObservation` is absent, observation/delay objectives are not evaluated, and checkpoint freshness still describes whether collection is running.

`delivery.pendingEvents` counts undelivered transactional-outbox events created by this source. `oldestPendingEventAgeSeconds` is required when the count is nonzero and measures how long the oldest source event has remained unpublished.

All ages are nonnegative. A timestamp beyond the configured clock-skew allowance is clamped to zero for serialization and adds `clock-skew-detected`; it never produces a negative or deceptively healthy measurement.

## Objectives and status

Objectives are selected by trusted platform configuration, not request parameters:

- maximum checkpoint age;
- maximum observation age;
- maximum ingestion delay;
- maximum pending-event age; and
- maximum accepted clock skew.

Every breached objective adds its stable code to `violations`. `within-objective` requires an empty violation list; `breached` requires at least one violation. These Phase 1 defaults are local reference objectives, not production SLO commitments. Production targets require representative volume, deployment topology, and design-partner requirements.

The report is an instantaneous SLI evaluation, not an availability-window calculation. Error-budget windows, percentile aggregation, alert routing, and OpenTelemetry/Prometheus export remain Phase 3 operational-hardening work.

## API and tenancy

`GET /v1/telemetry/ingestion?sourceId=<source>` authenticates first, derives tenant and actor scope from the credential, authorizes `ingestion-telemetry:read`, and queries exactly that tenant/source pair. A caller cannot override objectives or tenant scope.

| HTTP status | Code | Meaning |
| --- | --- | --- |
| `400` | `request.invalid` | `sourceId`, query multiplicity, or an unknown query field is invalid. |
| `401` | `authentication.required` / `authentication.invalid` | Bearer authentication failed. |
| `403` | `policy.denied` | The actor cannot inspect tenant telemetry. |
| `404` | `ingestion.source_not_found` | No committed checkpoint exists in the actor's tenant scope. |
| `503` | `storage.unavailable` | The authoritative telemetry adapter is unavailable. |
