# ADR 0061: Bound outstanding investigation jobs per tenant

**Status:** Accepted  
**Date:** 2026-08-17

## Context

ADR 0060 prevents one tenant from consuming all live worker leases, but authenticated callers can still enqueue unbounded new investigation IDs. Fair execution without bounded admission allows a burst, retry bug, or abandoned integration to grow PostgreSQL storage and queue delay indefinitely. A process-local counter would diverge across API replicas, and rejecting an exact idempotent retry when full would make safe client recovery unreliable.

## Decision

Configure a maximum number of outstanding investigation jobs for each exact tenant. Count `queued`, `running`, and `cancellation-requested` jobs; terminal `completed`, `failed`, and `cancelled` jobs do not consume capacity. Default the limit to 1,000 and bound configuration from 1 through 100,000.

Check an existing `(tenant, investigation ID)` before the capacity count. Return the existing job for the same canonical request even when the tenant is full, and keep the existing ID-conflict behavior for different content. Reject only a new job above the cap with HTTP `429` and stable code `investigation.queue.capacity-exceeded`. Do not return counts, other job identities, provider text, or tenant configuration.

Enforce admission inside the repository transaction. PostgreSQL uses the same short tenant-keyed transaction advisory lock as claim admission, so concurrent API replicas cannot both observe spare capacity and overfill it. The in-memory reference performs the same existing-ID, count, and insert sequence under its lock. This is an admission boundary, not a deletion or retention policy.

## Consequences

- one tenant cannot grow its active investigation backlog without a configured bound;
- idempotent client recovery remains reliable at full capacity;
- cancellation and other terminal transitions release capacity without deleting audit history;
- enqueue and claim admission serialize briefly within one tenant while different tenants remain independent;
- operators must tune the limit from measured completion capacity and alert before sustained `429` responses;
- completed history still requires a separate retention, archive, and legal-hold decision.
