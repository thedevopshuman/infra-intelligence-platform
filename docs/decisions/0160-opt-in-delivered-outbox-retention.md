# ADR 0160: Expire only delivered outbox state under explicit audited policy

**Status:** Accepted

**Date:** 2026-10-04

## Context

The transactional outbox retains successful delivery-disposition rows
indefinitely. Those rows are required for the rolling publication objective,
while pending and quarantined rows are required for delivery and governed
recovery. Deleting by age alone could therefore hide an undelivered event,
invalidate the configured SLO window, or weaken at-least-once recovery.

The immutable event log already remains the authoritative replay source and
enforces tenant/source/event identity. Outbox cleanup can be narrower: remove
only old state proving that one publication was durably acknowledged, without
deleting the event or claiming that the downstream receiver processed it.

## Decision

Add a disabled-by-default, exact-tenant retention policy for delivered outbox
rows. A row is eligible only when `created_at` and `published_at` are both
strictly older than the deployment cutoff, `published_at` is non-null, and the
row has no lease or quarantine state. Pending, leased, retrying, and
quarantined rows are never eligible.

Require an explicit duration between 30 days and 10 years whenever the policy
is enabled. The 30-day minimum covers the largest event-delivery SLO window
supported by this release. Bound each pass to a configured batch. Serialize
one tenant's evaluation and deletion in PostgreSQL, use bounded lock and
statement timeouts, and commit row deletion plus one aggregate audit record
atomically. The immutable `event_log`, audit history, and CloudEvents identity
are never changed.

Expose an administrator-only observe endpoint with aggregate counts and a
policy digest. The endpoint cannot execute cleanup. The non-interactive worker
uses a fixed system actor, exact tenant enrollment, and a separate policy
action. Reports, logs, and audit records contain counts only, not event,
outbox, subject, destination, or provider details.

## Consequences

- retained outbox history continues to cover the declared rolling publication
  objective;
- late acknowledgements receive a full retention interval before eligibility;
- quarantine and governed replay semantics remain unchanged;
- immutable events remain replayable and retain their deduplication identity;
- backups can contain already-expired delivery state and need a compatible
  lifecycle;
- disabled event publishing produces no eligible rows and remains an
  unbounded backlog until a privacy-safe publisher is selected; and
- this policy does not bound the event log, audit log, AI economics ledger, or
  any downstream receiver's storage.

## Revisit when

Revisit when customer evidence requires multi-destination acknowledgement,
receiver-processing receipts, finite event-log replay, broker-native delivery
state, partitioned physical retention, or a separately governed bulk-recovery
policy.
