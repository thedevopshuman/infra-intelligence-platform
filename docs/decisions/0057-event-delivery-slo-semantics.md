# ADR 0057: Transport-neutral event-delivery SLO windows

**Status:** Accepted

## Context

Point-in-time backlog and quarantine health show what requires attention now, but they do not answer whether the delivery path met a customer objective over time. Counting dispatcher attempts would over-weight retried events, while counting only successful rows would hide terminal failures. A broker-specific metric would also couple the control plane to a transport before workload evidence justifies that choice.

## Decision

Measure one durable transactional-outbox row once in a rolling, exact-tenant cohort. The configured window ends at evaluation time. An event becomes eligible only after its configured publication-latency deadline has elapsed. It meets the objective only when the durable publisher acknowledgement timestamp is at or before `createdAt + maximumDeliveryLatencySeconds`. A later acknowledgement and an event that remains undelivered at evaluation time are both misses; quarantine is reported as a subset of undelivered misses.

Calculate attainment in integer basis points as `floor(withinObjectiveEvents * 10000 / eligibleEvents)`. Report `no-data` for an empty mature cohort, `insufficient-data` below the configured minimum sample, and otherwise `meeting` or `breached` against the configured minimum attainment. Validate all adapter counts and arithmetic before returning a report.

Use the application-owned `EventOutbox` port and existing durable outbox timestamps. Do not emit event payloads, destinations, credentials, provider responses, individual event identities, or exception text. Require `platform-admin` plus policy approval, derive tenant scope from authentication, and accept no caller-selected tenant or objective.

This is a publication-path objective: HTTPS success, broker acceptance, or another publisher's durable acknowledgement ends the measured interval. It does not claim that a customer receiver processed the event. At-least-once receivers must still deduplicate `(tenantid, source, id)`, and end-to-end receiver certification remains separate work.

## Consequences

- retries and governed replay cannot inflate the denominator or retroactively turn a missed deadline into an on-time success;
- the calculation remains stable when the publisher adapter changes;
- low-volume tenants are not labelled healthy or breached from an unrepresentative sample;
- retained outbox rows must cover at least the configured window for a production claim;
- receiver-processing acknowledgement, multi-destination objectives, regional objectives, long-term aggregation, and alert routing remain separate decisions.
