# ADR 0099: Drain API endpoints and active requests before termination

**Status:** Accepted  
**Date:** 2026-09-05

## Context

The packaged N-1 gate proves sustained short reads, but a process receiving
`SIGTERM` used the standard immediate signal behavior and the default
`ThreadingHTTPServer` daemon request threads. A request already waiting on a
database or provider could therefore be terminated even when another replica
kept the Service available.

## Decision

1. The API installs idempotent `SIGTERM` and `SIGINT` handlers that request
   server shutdown outside the signal handler's main thread. The server stops
   accepting new work, uses non-daemon request threads, joins active handlers,
   and closes the runtime only after those handlers finish.
2. The Helm API Deployment uses a surge-first rolling strategy with
   `maxUnavailable: 0`, `maxSurge: 1`, and a short minimum-ready period.
   A closed `apiTermination` values object declares the total pod grace period
   and the smaller pre-stop endpoint-propagation delay. Invalid relationships
   fail during rendering.
3. The packaged N-1 gate supports a target whose latest migration is equal to
   or newer than the ancestor. It continues to reject migration regression and
   requires exact migration count after re-upgrade.
4. After the target is stable, the gate holds an authenticated Resource read on
   a PostgreSQL lock, terminates that exact API pod, and requires the original
   connection to return the tenant data before the grace period expires. It
   then requires the replacement Deployment to become ready.

## Consequences

Short Service reads and one deliberately blocked in-flight data read now have
separate executable evidence. Operators must set the grace period above the
endpoint delay and the longest request duration they intend to preserve.
Kubernetes still sends `SIGKILL` after the configured limit, so unbounded work
is not promised. Customer ingress/LB connection draining, streaming endpoints,
node loss, database failover, and production latency distributions remain
environment-specific certification gates.
