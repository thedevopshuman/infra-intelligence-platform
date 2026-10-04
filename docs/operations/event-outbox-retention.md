# Delivered event outbox retention operations

Delivered transactional-outbox cleanup is disabled by default. The API can
still report what would be eligible, so an operator can review the exact policy
before deleting delivery-disposition rows.

This lifecycle removes only old, durably acknowledged outbox rows. It never
deletes the immutable event log, audit records, or pending, leased, retrying,
or quarantined deliveries. At-least-once consumers must continue to
deduplicate by `(tenantid, source, id)`.

## Pre-enable checklist

1. Select a delivered-row duration that is at least the configured event
   delivery-SLO window and meets customer audit, replay, incident-response,
   and receiver-dispute requirements.
2. Confirm backup retention and access match the policy. Cleanup of the live
   database does not remove rows from earlier backups.
3. Confirm every intended tenant has a working, privacy-safe publisher and
   inspect `GET /v1/operations/events/delivery-health`. Cleanup never treats
   disabled delivery, quarantine, or old backlog as success.
4. Query `GET /v1/operations/events/retention` with a tenant-scoped
   platform-administrator credential. Record the policy digest, eligible
   count, and protected count.
5. Verify the workflow worker has only the intended explicit tenant enrollment
   and that policy permits `event-outbox-retention:expire` for those tenants.

## Configuration

The packaged settings are:

| Setting | Environment variable | Constraint |
| --- | --- | --- |
| Enable cleanup | `IIP_EVENT_OUTBOX_RETENTION_ENABLED` | `false` or `true`; default `false` |
| Delivered-row duration | `IIP_EVENT_OUTBOX_RETENTION_PUBLISHED_SECONDS` | explicit when enabled; 2592000–315360000 |
| Batch size | `IIP_EVENT_OUTBOX_RETENTION_BATCH_SIZE` | 1–1000; default 100 |
| Worker interval | `IIP_EVENT_OUTBOX_RETENTION_INTERVAL_SECONDS` | 60–86400; default 3600 seconds |

Equivalent Helm values are:

```yaml
worker:
  enabled: true
  tenants: [tenant-acme]

eventOutboxRetention:
  enabled: true
  publishedSeconds: 7776000
  batchSize: 100
  intervalSeconds: 3600
```

The disabled preview duration is 2592000 seconds (30 days), but enabling the
policy requires an explicit `publishedSeconds` decision. The 30-day minimum
covers the largest event delivery-SLO window supported by this release, not
only the deployment's currently selected window. A worker pass expires at most
`batchSize` rows per tenant.

When cleanup is enabled, every enrolled worker tenant must use the canonical
runtime form `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`. Helm and environment
composition reject incompatible enrollment before constructing a storage
adapter. Retention-disabled deployments keep their existing enrollment
compatibility.

The sanitized Helm deployment profile publishes only the retention settings
`enabled`, `publishedSeconds`, `batchSize`, and `intervalSeconds`. Its profile
hash binds those four values, so preflight and release evidence can detect a
policy or schedule change without exposing credentials or database connection
details. The report's policy digest is narrower: it binds the enabled flag,
delivered-row duration, and batch size because the worker interval does not
change row eligibility.

## Observe and monitor

The authenticated endpoint returns `disabled`, `cleanup-required`, or
`current`; it never performs cleanup. Alert when `remainingEligible` stays
nonzero across multiple configured intervals, protected rows grow unexpectedly,
or worker completion reports failures. Worker completion logs omit tenant
identity. Reports and audit records carry the exact tenant plus aggregate
totals, but deliberately omit event and outbox identities.

PostgreSQL cleanup uses a two-second lock timeout and a ten-second statement
timeout. A lock, statement, audit-write, or transaction failure closes the
pass without claiming that any affected row was pruned.

Each pass calculates exact tenant-wide aggregate counts before its bounded
delete. On a very large outbox that scan can reach the statement timeout, in
which case cleanup fails closed and makes no progress; alert on repeated worker
failures rather than treating the configured batch size as a progress
guarantee. Logical row deletion also does not promise immediate filesystem
space reclamation, which remains subject to PostgreSQL vacuum and storage
operations.

After enabling, verify:

- the report policy digest matches the reviewed values;
- worker passes report expected aggregate expiration without failures;
- delivery health still reports every pending, retrying, leased, or
  quarantined row;
- the delivery-SLO endpoint retains its complete configured rolling window;
- immutable event replay still returns the original CloudEvent identity; and
- one aggregate audit record exists for each nonempty expiration batch.

Disable cleanup immediately by setting
`IIP_EVENT_OUTBOX_RETENTION_ENABLED=false` if policy, publisher, SLO, or backup
scope is uncertain. Disabling stops future deletion; it cannot restore removed
delivery-disposition rows. Recovery from backup is a separate authorized
operation and can reintroduce rows whose retention must be reconciled.

The persistent community profile currently neither exposes these retention
settings nor enables event publication, to avoid placing customer identifiers
in logs. In that state no row becomes eligible, so the community profile must
not claim delivered-outbox cleanup or a bounded outbox. Configure and qualify
a privacy-safe publisher and an explicit retention policy before presenting
this lifecycle as an operational control. This unit also does not implement or
claim bounded AI-ledger storage.

## Verification evidence

On 2026-10-04, the candidate passed `make verify PYTHON=.venv/bin/python`
with 1,581 tests and 85 explicitly gated integration skips, including schema,
SDK, policy, HTTP-handler, worker, Helm-rendering, and preflight checks.
The full `make test-postgres` regression gate also passed in a separately
owned disposable database. The final focused storage run passed all 19 tests
with real PostgreSQL, including concurrent passes, unchanged SLO cohorts and
event replay/deduplication, audit-failure rollback, bounded lock failure, and
an actual worker-process enabled/repeat/disabled cycle.

These runs used synthetic tenants and data. They did not enable cleanup in an
existing installation or qualify customer backlog capacity, backup retention,
legal obligations, receiver delivery, or physical disk reclamation. Rerun the
gates and review operating policy for the actual release candidate.
