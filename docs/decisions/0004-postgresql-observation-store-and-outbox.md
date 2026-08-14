# ADR 0004: PostgreSQL observation store and transactional outbox

**Status:** Accepted
**Date:** 2026-08-14

## Context

Phase 1 needs correct resource ordering, immutable observations, a replayable event timeline, explicit source checkpoints, and atomic event publication. Choosing separate graph, event-stream, and checkpoint products before workload evidence would add distributed transaction and operating burden to the first executable substrate.

The storage choice must preserve tenant scope, deterministic resource identity, idempotent retries, rejected-observation audit, and replaceable application ports. The initial event transport must provide replay and competing-consumer delivery without making a database queue the permanent fleet-scale architecture.

## Decision

Use supported PostgreSQL major versions 16 through 18 as the Phase 1 authoritative store for:

- latest resource projections and immutable resource observations;
- the immutable, offset-addressed platform event log;
- source checkpoints committed only at an explicit safe boundary; and
- a transactional outbox for at-least-once event delivery.

Store identity, tenancy, ordering, lifecycle, and replay fields in relational columns. Retain the canonical public resource and event documents in `jsonb`. Add separately indexed relationship rows when the graph-neighborhood query unit is implemented; do not introduce a graph database before measured queries justify it.

One accepted observation transaction writes the projection, immutable observation, event-log row, and outbox row. It may also advance a checkpoint when a trusted caller states that every mutation represented by the cursor is durable. Duplicate observations do nothing; stale and conflicting observations may be retained but never emit accepted events or advance checkpoints.

Serialize competing writes for the same `(tenant, resource UID)` with a transaction-scoped advisory lock and lock the current projection for update. Outbox workers claim deterministic batches with row leases and `FOR UPDATE SKIP LOCKED`. Consumers deduplicate on the CloudEvents identity `(tenantid, source, id)` and acknowledge only a lease they own.

PostgreSQL is the initial durable event transport, not a permanent claim that a relational outbox replaces every broker. Kafka, NATS JetStream, cloud queues, or another transport may later sit behind the outbox publisher port when throughput, retention, independent consumer, or regional-failure evidence requires it.

Use Psycopg 3.3.4 for the Python adapter and raise the project minimum to Python 3.11. Python 3.9 is end-of-life, Python 3.10 is already in security-only maintenance near its end-of-life, and the current Psycopg release supports Python 3.10 or newer.

## Consequences

- Resource state, history, event publication, and optional safe checkpoint advancement have one commit boundary.
- A local deployment and backup experiment operate one authoritative database before derived search or graph indexes are added.
- Explicit tenant columns and predicates remain mandatory at every port and table key. Database roles, row-level-security policy, encryption, backup, and retention hardening remain required before a multi-tenant production deployment.
- `SKIP LOCKED` intentionally provides queue-worker semantics, not a consistent general query view.
- The event log is replayable by a monotonic storage offset, while public event identity remains transport-independent.
- Database connection pooling and an outbox dispatcher process are deferred until measured concurrency requires them; the adapter currently opens a bounded transaction per operation.
- PostgreSQL integration tests run in CI and through the Docker Desktop test profile. The default in-memory profile remains available for contract development.

## Revisit triggers

Reconsider the event transport or add a specialized graph/index store when measurements show sustained outbox lag, retention beyond practical PostgreSQL partitioning, independently scaled consumer fleets, cross-region failure-domain requirements, or graph traversal workloads that cannot meet the query SLO with relational indexes.

## References

- [PostgreSQL versioning policy](https://www.postgresql.org/support/versioning/)
- [PostgreSQL row locking and `SKIP LOCKED`](https://www.postgresql.org/docs/18/sql-select.html#SQL-FOR-UPDATE-SHARE)
- [Psycopg transaction behavior](https://www.psycopg.org/psycopg3/docs/basic/transactions.html)
- [Psycopg 3.3.4 package metadata](https://pypi.org/project/psycopg/)
