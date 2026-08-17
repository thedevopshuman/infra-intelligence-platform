# ADR 0055: Bounded transactional-outbox quarantine

**Status:** Accepted

## Context

The tenant-scoped outbox dispatcher retries provider-neutral publication failures with capped exponential delay. A permanently unavailable or rejecting destination therefore leaves each event eligible forever, grows attempt counts without a terminal boundary, and provides operators no safe API view of which events require intervention.

An automatic replay after an arbitrary delay would hide the failure and could duplicate an external effect. Exposing full event documents or provider responses in a health endpoint would also widen data access and leak untrusted details.

## Decision

Give each delivery a configured attempt budget. The attempt count increments when a named worker acquires a valid tenant-scoped lease. When publication fails on or after the final attempt, atomically clear that owned lease and mark the row quarantined with platform time and the stable `event.publisher.unavailable` code. Quarantined rows are excluded from normal claims and source pending-delivery measurements.

Add an application-owned read model that returns exact-tenant pending, in-flight, retrying, and quarantined counts plus a bounded newest-first quarantine summary. Require the `platform-admin` role and policy decision before storage access. Return only CloudEvents identity/routing metadata, attempt count, quarantine time, and stable error code—never event data, destination details, credentials, provider output, or exception text.

Do not add an ungoverned requeue endpoint. A later recovery workflow must preserve at-least-once semantics while adding explicit policy, idempotency, audit, operator intent, and compatibility with customer receivers.

## Consequences

- permanent failures stop consuming dispatcher capacity after a finite number of attempts;
- operators can distinguish ordinary backlog from terminal delivery failures without database access;
- the immutable event log remains the recovery source of truth while the outbox row records delivery disposition;
- quarantine does not imply event deletion and does not weaken consumer deduplication requirements;
- broker selection, governed replay, retention, multi-destination fan-out, and measured delivery SLO windows remain separate decisions.
