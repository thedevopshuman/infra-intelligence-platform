# ADR 0125: Qualify bounded fixed-rate control-plane read load separately

**Status:** Accepted

**Date:** 2026-09-06

## Context

ADR 0074 qualifies investigation dispatch admission and fairness against a
local PostgreSQL profile. ADR 0105 measures low-rate external ingress
availability, and ADR 0123 overlaps that probe with one API-pod Eviction.
None of those results proves that a customer's external control-plane path can
sustain a declared request rate and latency objective. Treating them as load
evidence would overstate both workload shape and environment coverage.

A qualification tool also creates customer traffic. It must be impossible to
trigger a large or bursty run accidentally, and its retained evidence must not
become a new source of customer identifiers, credentials, response content, or
high-cardinality request data.

## Decision

Add a separate `customer-control-plane-read-load-v1` profile and
`ControlPlaneLoadQualificationReport`.

1. The generator targets only authenticated `GET /v1/system/version` through
   the existing verified-HTTPS ingress boundary. It disables ambient proxies,
   denies redirects, closes each connection, and validates every successful
   response against the exact release identity.
2. A run requires a clean source checkout, exact immutable image digest,
   short-lived credential file, and literal traffic-enable flag. Duration,
   rate, concurrency, timeouts, and objectives have closed bounds, with a hard
   ceiling of 250,000 scheduled requests.
3. One monotonic global fixed-rate schedule is divided among bounded workers.
   Late slots are counted as scheduler misses instead of producing a catch-up
   burst. Successful-request attainment uses all scheduled slots as its
   denominator.
4. Retained evidence contains aggregates, closed failure categories, public
   release identity, and SHA-256 bindings only. It excludes the target,
   credential, response bodies, request-level samples, customer identity, and
   raw errors.
5. The report remains outside the immutable release bundle and control-plane
   API. Offline verification rebinds it to the current clean source, target,
   and image but performs no traffic.
6. The profile is additive to customer deployment and continuity evidence. It
   does not qualify write, database, investigation, worker, receiver, mixed
   workload, failure, regional, or long-window behavior.

## Consequences

- Operators gain comparable, source-bound evidence for one customer-facing
  read objective without adding an always-on load service or server authority.
- Explicit traffic authorization and hard limits reduce accidental load, but
  operators still own the maintenance window and target approval.
- The lightweight standard-library client measures end-to-end HTTPS behavior;
  it is not a high-scale benchmark and its scheduler attainment is part of the
  result.
- Production capacity certification still needs representative endpoint mixes,
  database and asynchronous paths, failure injection, longer windows, and the
  customer's own monitoring evidence.
