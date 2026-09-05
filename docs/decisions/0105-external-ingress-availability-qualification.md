# ADR 0105: Qualify external ingress availability with minimized evidence

**Status:** Accepted
**Date:** 2026-09-06

## Context

In-process query and receiver metrics cannot observe a failed ingress path,
process crash, or loss of every replica. The N-1 release gate carries an
internal Service probe during upgrades, but it neither crosses the customer
ingress nor produces reusable steady-state qualification evidence. Readiness
must also remain free of synthetic calls to external identity, policy,
credential, telemetry, and provider systems.

## Decision

1. Add a standalone external qualification client that probes only liveness,
   readiness, and authenticated runtime identity. It does not become a server
   use case or a Helm-managed in-cluster workload.
2. Treat one sample as successful only when all three paths return their
   closed contracts and the runtime identity matches the selected profile.
3. Require verified HTTPS, direct connections with ambient proxies disabled,
   redirect denial, clean source, release identity, an immutable image digest,
   and at least 100 samples for `customer-ingress` evidence. Permit HTTP only
   for an explicitly selected loopback development profile.
4. Read the Bearer credential from a bounded file, send it only to
   `/v1/system/version`, and retain no request, response, credential, endpoint,
   tenant, actor, certificate, or provider data.
5. Retain aggregate availability, latency, path, and closed failure-category
   measurements in a source- and target-bound
   `IngressAvailabilityQualificationReport`. Keep the report outside the
   immutable release bundle and outside OpenAPI and SDKs.

## Consequences

- a deployment can prove that its external network, ingress, API process,
  database readiness, authentication, and exact release identity worked
  together during a declared interval;
- redirects cannot move the Bearer credential and ambient proxy settings
  cannot silently change the measured path;
- local loopback evidence cannot be mistaken for customer ingress evidence;
- the report remains safe for support exchange but its target digest is still
  pseudonymous operational metadata;
- customers still own continuous scheduling, regional probes, certificate and
  DNS expiry monitoring, long-window aggregation, burn-rate policy, and
  notification routing.
