# Event outbox retention report contract

**Status:** v1alpha1

**Machine contract:** `contracts/schemas/event-outbox-retention-report.schema.json`

`EventOutboxRetentionReport` describes the deployment-owned lifecycle policy
and current retained state of delivered transactional-outbox rows for one
authenticated tenant. It never deletes or rewrites the immutable event log,
CloudEvents identity, audit history, or an undelivered outbox row.

## Semantics

The retention cutoff is `metadata.evaluatedAt - policy.publishedSeconds`. An
outbox row is eligible only when all of the following are true:

- `publishedAt` is present;
- both its durable `createdAt` and `publishedAt` are strictly older than the
  cutoff; and
- it has no lease and is not quarantined.

Pending, available, leased, retrying, and quarantined rows are protected
regardless of age. A late publication starts a new full retention interval:
an old row cannot become eligible until its publication timestamp is also
strictly older than the cutoff.

The public endpoint is observe-only. `mode=observe` does not mutate storage,
so `expired` is zero and `remainingEligible` equals `eligible`. The
non-interactive worker produces internal `mode=expire` reports after deleting
at most `policy.batchSize` eligible outbox rows in one tenant transaction.
For every report:

```text
storedBefore = eligible + protected
eligible <= published <= storedBefore
eligible = expired + remainingEligible
```

`storedBefore` is the number of retained outbox rows before the pass;
`published` counts rows with a durable publisher acknowledgement; `protected`
contains every row that is not eligible, including delivered rows still inside
the retention interval. These aggregate counters never identify an event.

`disabled` means automatic cleanup is not enabled. `cleanup-required` means
the policy is enabled and eligible rows remain. `current` means the policy is
enabled and none remain. The SHA-256 policy digest binds the enabled flag,
published duration, and batch size without exposing infrastructure details.

An `auditRef` exists exactly when the pass expired one or more rows. The
referenced record contains aggregate counts and the policy digest only—never
event or outbox IDs, event documents, subjects, destinations, credentials,
provider output, or exception text.

Deleting a delivered outbox row removes only its delivery-disposition state.
The immutable `event_log` document and its tenant/source/event-ID uniqueness
remain authoritative for replay and consumer deduplication. This contract does
not expire events, audit records, evidence, AI economics records, or receiver
state.

## Authority and errors

`GET /v1/operations/events/retention` requires Bearer authentication, the
`platform-admin` role, and policy approval for
`event-outbox-retention:read`. The tenant comes only from authenticated
context, and the endpoint accepts no query parameters.

Worker cleanup uses the fixed `iip-event-outbox-retention` system actor, the
`system-retention` role, the exact enrolled tenant, and policy action
`event-outbox-retention:expire`.

| HTTP status | Code | Meaning |
| --- | --- | --- |
| `400` | `request.invalid` | Any query field was supplied. |
| `401` | `authentication.required` / `authentication.invalid` | Bearer authentication failed. |
| `403` | `policy.denied` | The administrator role or policy authorization is absent. |
| `503` | `event.outbox-retention.unavailable` | Storage returned unavailable, corrupt, or inconsistent state. |

Automatic cleanup is disabled by default. `publishedSeconds` is between 30
days and 10 years; the minimum covers the largest delivery-SLO window supported
by this release. Enabling cleanup requires an explicit operator-selected
duration, reviewed event-replay and audit requirements, and compatible backup
retention.
