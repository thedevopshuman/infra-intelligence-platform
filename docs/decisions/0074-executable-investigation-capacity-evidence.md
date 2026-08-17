# ADR 0074: Executable investigation capacity evidence

**Status:** Accepted  
**Date:** 2026-08-17

## Context

ADRs 0060 and 0061 bound live work and queued work per tenant, but unit and concurrency tests do not state how many tenants or jobs were exercised, bind results to a source/database environment, or prove that an overloaded tenant leaves another tenant admissible. Unqualified throughput claims would also be misleading because provider latency, database class, worker count, and customer topology dominate production capacity.

## Decision

Add an explicit PostgreSQL certification profile that concurrently admits a declared large tenant set, drives one tenant beyond its durable outstanding-job cap, retries accepted identities while that tenant is full, admits a separate tenant, claims one job for every tenant, verifies the same-tenant live-lease cap, terminalizes those claims, and proves released capacity accepts replacement work.

Emit a closed, aggregate-only report containing the exact source revision and dirty state, application/runtime/database identity, profile, latency measurements, and stable pass/fail checks. Gate the local profile with generous p95-operation and suite-duration ceilings that detect pathological regressions without presenting Docker Desktop as a production benchmark. Generate the report outside the serving API and remove stale output before every run.

## Consequences

- queue fairness, isolation, idempotency, and release semantics gain repeatable PostgreSQL evidence at a declared scale;
- local regressions can be compared without exposing tenant or investigation content;
- passing results are valid only for the exact reported environment and clean revision;
- customer sizing still requires environment-specific sustained arrival, provider-latency, worker, database failover, and SLO testing;
- this does not select autoscaling policy, a workflow engine, or a production capacity promise.
