# Inspect AI history availability

**Status:** Read-only lifecycle prerequisite; no AI payload cleanup enabled

Use `GET /v1/ai/economics/history-availability` with the same authenticated
tenant and `ai-economics:read` permission used for allocation reports. Supply
exactly `start` and `end` as canonical UTC timestamps for a positive half-open
interval no longer than 31 days, for example:

```text
/v1/ai/economics/history-availability?start=2026-10-01T00%3A00%3A00Z&end=2026-10-04T00%3A00%3A00Z
```

Send the credential through the existing protected client/Bearer mechanism,
not the URL. There is no tenant, source-generation, or cleanup query parameter.

## Interpret the result

- `available`: the interval has no recorded retirement. Zero counts do not
  prove the absence of real provider usage; separately inspect receiver,
  Collector, sampling, meter, and cost/attribution coverage.
- `history-retired`: at least one locally recorded invocation has a retirement
  marker. The counts expose no record identity and cannot recover source
  payloads or reconstruct historical cost.
- `410 ai.history.retired` from allocation or exact-invocation observation:
  required history is retired. Do not treat the result as zero usage, a pending
  calculation, or a retryable provider-ingestion delay. Do not show surviving
  rows as a complete historical total.
- `503 storage.unavailable`: storage could not establish a safe result; it is
  not an empty-history result.

An availability read is a point-in-time observation, not a lock or guarantee
for later queries. The owning allocation, exact-invocation, and savings-cohort
reads independently enforce retirement in their own consistent snapshots.
Query telemetry uses the closed `ai-history-availability` operation label and
must not include request scope, tenant, counts, or correlation identifiers.

## What this release does not do

There is no AI-retention enable flag, duration, worker cleanup schedule, public
marker-writing endpoint, or physical payload deletion in this unit. Do not
manually insert markers or delete ledger rows as an operating procedure. Marker
storage prepares read behavior for a future lifecycle; it is not a supported
retention/erasure command or a way to reclaim storage.

Finding evidence pins, all-generation payload retirement, retry identity,
foreign-key handling, audit, replay, and backup/restore compatibility must be
qualified together before cleanup can be enabled. Retired history cannot be
repriced or reattributed from the availability report. Keep existing recovery
and storage-capacity procedures; outbox retention and Evidence artifact
retention are distinct policies and do not retire AI usage.

See the [contract](../specifications/ai-history-availability-contract.md),
[ADR 0161](../decisions/0161-explicit-ai-history-availability.md), and
[AI economics architecture](../architecture/ai-economics.md).
