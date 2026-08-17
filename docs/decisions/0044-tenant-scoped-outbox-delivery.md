# ADR 0044: tenant-scoped outbox delivery

**Status:** Accepted
**Date:** 2026-08-17

## Context

Accepted observations atomically create immutable events and transactional-outbox rows, but no runtime process delivered those rows. The freshness evaluator therefore exposed a permanently growing downstream backlog. A dispatcher must preserve the outbox's exact-tenant lease boundary, tolerate duplicate delivery, and avoid giving a background worker ambient network or credential authority.

## Decision

The tenant-explicit workflow worker may compose one optional `EventDeliveryService`. For each enrolled tenant it claims a bounded outbox batch with its named worker lease, publishes each structured CloudEvent through the application-owned `EventPublisher` port, and acknowledges only a lease it still owns. Provider failure releases the row with the stable `event.publisher.unavailable` code and bounded exponential backoff. A lost acknowledgement is reported as ambiguous and is never converted into success.

Delivery is at least once. The external HTTPS publisher sends the immutable CloudEvents `id` as its idempotency key; receivers must deduplicate on `(tenantid, source, id)`. A crash after publish but before acknowledgement can redeliver the event.

The production chart permits `disabled` or `https-webhook`. HTTPS configuration has one explicit destination and a tenant set exactly equal to the worker enrollment. It requires TLS, refuses redirects and URL credentials/query/fragment, bounds request/response/time, reads a mounted Bearer token for every delivery so rotation does not require a restart, and exposes only stable failures. Its Secret and optional CA mount exist only in the worker pod. When NetworkPolicy is enabled, the worker gets a separate no-ingress policy and the webhook CIDR/port must be explicitly enabled.

Docker Compose uses `stdout-json` as an explicit development sink so the local product can prove acknowledgement without another infrastructure dependency. That mode writes bounded structured CloudEvents and is not available through the Helm chart.

## Consequences

- Source freshness can now distinguish a healthy downstream delivery path from a real backlog.
- Multiple workers can compete safely with `SKIP LOCKED`; slow or failed messages do not block the rest of a claimed batch.
- Event payloads written by the local sink can contain infrastructure identifiers, so Docker logs remain development data and must not be treated as a production event archive.
- [ADR 0055](0055-bounded-outbox-quarantine.md) now stops permanent delivery failures at a finite attempt budget and exposes a value-minimized operator report. Governed replay, broker-specific batching, delivery SLO telemetry, and multi-destination fan-out remain future work.
