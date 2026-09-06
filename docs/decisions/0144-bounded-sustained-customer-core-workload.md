# ADR 0144: Qualify a bounded sustained customer core workload externally

**Status:** Accepted

**Date:** 2026-09-07

## Context

ADR 0125 qualifies only fixed-rate `GET /v1/system/version` traffic. ADR 0129
proves short API, receiver, and investigation overlap during planned worker and
receiver pod disruptions. Neither result establishes that an installed
customer environment can sustain those three core paths together for a useful
capacity window.

Calling the existing evidence “representative load” would be incorrect. Actual
customer distributions, burst patterns, evidence sources, data sizes, action
use, AI traffic, and failure modes are not known. An always-on benchmark inside
the product would also introduce credential, traffic, and scheduling authority
that the serving system does not need.

## Decision

Add a separate protected `CustomerSustainedWorkloadProfile`, minimized
`CustomerSustainedWorkloadQualificationReport`, and host-side operational
runner.

1. The first profile is deliberately closed to exact runtime-identity reads,
   non-empty OTLP metric writes, and bounded asynchronous investigations for one
   existing resource. It reuses the production HTTPS/mTLS client and durable
   investigation contract from the processing-continuity qualifier.
2. Probe and workflow traffic use independent monotonic fixed-rate schedules.
   Late slots become explicit misses and are not replayed in bursts. Total
   duration, rate, concurrency, volume, timeouts, and objectives have hard
   bounds.
3. Running traffic requires a literal enable flag, a current content-addressed
   mode-`0600` profile, distinct protected credentials, a client certificate
   and key, direct verified HTTPS, and a clean checkout matching the immutable
   deployed release.
4. The report retains only public release identity, aggregate counts and
   percentiles, stable checks, fixed limitations, and pseudonymous digests. It
   excludes customer identity, targets, credentials, selected workload values,
   per-request samples, request IDs, content, and provider errors.
5. The runner remains outside the API, worker, agent, plugin, Helm workloads,
   and SDK behavior. SDKs expose offline envelope types only. Verification is
   traffic-free and rebinds the report to current source, profile, release, and
   targets.
6. The result is called a bounded synthetic core workload qualification, never
   representative customer capacity or a production availability
   certification. Customer review and broader profiles remain separate gates.

## Consequences

- Release owners gain comparable evidence for sustained interaction across API,
  receiver, PostgreSQL, and workflow-worker paths without introducing a new
  serving component or authority boundary.
- Scheduler misses and failed operations remain in objective denominators, so a
  constrained generator cannot silently produce an optimistic result.
- The standard-library, connection-closing client prioritizes trust-boundary
  clarity over maximum benchmark throughput. Its scheduler attainment must be
  evaluated alongside service results.
- A passing result still requires customer monitoring and does not replace
  failure, regional, long-window, provider, invoice, or design-partner evidence.

## Alternatives considered

- Extending ADR 0125 was rejected because it would blur a narrow read-only gate
  with write and asynchronous semantics.
- Running load from platform pods was rejected because it would add ambient
  network and credential authority and make the measured environment generate
  its own qualification claim.
- Labeling this profile representative was rejected because the repository has
  no customer traffic distribution or acceptance evidence.
