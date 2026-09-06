# ADR 0145: Require sustained core-workload evidence before a private pilot

**Status:** Accepted

**Date:** 2026-09-07

**Amendment:** [ADR 0146](0146-customer-reviewed-planned-failure-overlap.md)
adds customer-reviewed planned-failure overlap as the ninth pilot-preflight
input. The eight-input description below is historical context.

## Context

ADR 0143 introduced a private design-partner preflight with seven exact
evidence inputs. Its load input proves only a bounded fixed-rate runtime
identity read. ADR 0144 subsequently added a protected customer-environment
gate for a sustained mix of exact-release API reads, PostgreSQL-durable OTLP
metric writes, and asynchronous investigations.

A private pilot candidate should not pass while that broader core path is
untested, but the new synthetic result still must not be described as
representative customer traffic, failure tolerance, or production operating
acceptance.

## Decision

1. Make `CustomerSustainedWorkloadQualificationReport` the eighth ordered
   input to `CustomerPilotReadinessReport` and require `qualified` status.
2. Extend the protected pilot profile with the exact sustained-workload
   profile digest and OTLP target digest. Rebind the sustained report to that
   profile, the same release and migration, the deployed API target, and the
   deployment-qualified receiver target.
3. Require the sustained workload to start after customer deployment
   qualification. Treat its age and explicit `validUntil` as pilot-report
   validity ceilings.
4. Retain the sustained report's file digest, aggregate start/completion
   timestamps, stable evidence/check results, and the expanded pseudonymous
   customer-environment-set digest. Do not copy workload values, tenant,
   resource, metric, endpoint, credential, or request-level measurements.
5. Require every retained source-report binding to equal its corresponding
   evidence-item digest. The aggregate remains reconstructible only with all
   exact source files.
6. Replace the obsolete “sustained workload not qualified” limitation with
   the narrower truth: customer workload representativeness and failure
   overlap remain unqualified. Production operating qualification stays an
   external gate.

## Consequences

- A private-pilot candidate now has evidence across the read-only load path
  and a bounded sustained API, receiver, PostgreSQL, and worker mix.
- Crossed workload profiles, releases, API targets, OTLP targets, source
  reports, or pre-deployment windows fail closed.
- The preflight still authorizes no traffic, provider call, installation,
  publication, signing, pilot start, mutation, or production promotion.
- Representative workload design, failure overlap, database failover,
  regional behavior, long-window SLOs, and design-partner acceptance remain
  separate work.
