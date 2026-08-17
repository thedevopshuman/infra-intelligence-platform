# Evidence artifact retention operations

Evidence artifact cleanup is disabled by default. The API can still report what would be eligible, allowing an operator to review policy before any byte deletion.

## Pre-enable checklist

1. Confirm customer legal, incident-response, audit, and data-residency requirements for each retention class.
2. Confirm backup retention and access match the policy. Expiring live bytes does not remove earlier backup copies.
3. Confirm any required Evidence uses `legal-hold`; that class is never automatically expired.
4. Query `GET /v1/operations/evidence/retention` with a tenant-scoped platform-administrator credential and record the policy digest and eligible count.
5. Verify the workflow worker has only the intended explicit `worker.tenants` enrollment and external policy permits the fixed `evidence-retention:expire` action for those tenants.

## Helm configuration

```yaml
worker:
  enabled: true
  tenants: [tenant-acme]

evidenceRetention:
  enabled: true
  intervalSeconds: 3600
  ephemeralSeconds: 86400
  standardSeconds: 2592000
  extendedSeconds: 31536000
  batchSize: 100
```

Durations must be ordered `ephemeral <= standard <= extended`. A worker pass expires at most `batchSize` artifact bodies per tenant. Multiple worker replicas are safe: PostgreSQL serializes only the exact tenant and commits deletion plus the aggregate audit record together.

## Observe and monitor

The authenticated endpoint returns `disabled`, `cleanup-required`, or `current`. It does not execute cleanup. Alert when `remainingEligible` remains nonzero for multiple configured intervals, the worker log event `evidence.retention.completed` reports failures, or eligible backlog grows faster than bounded passes can drain it. Worker logs contain totals only and deliberately omit tenant/evidence identity.

After enabling, verify:

- the report's policy digest matches the reviewed values;
- worker passes report expected aggregate expiration without failures;
- Evidence metadata and investigation citations remain readable;
- expired artifact reads are unavailable while `legal-hold` artifact bytes remain; and
- PostgreSQL audit records exist for actual expiration batches.

Disable cleanup immediately by setting `evidenceRetention.enabled: false` if policy or backup scope is uncertain. Disabling stops future deletion; it cannot restore already expired bytes. Recovery from a backup is a separate authorized operation and may reintroduce bytes whose retention must be reconciled.
