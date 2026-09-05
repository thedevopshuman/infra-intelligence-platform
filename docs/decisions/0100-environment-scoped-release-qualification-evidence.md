# ADR 0100: Retain environment-scoped packaged release qualification evidence

**Status:** Accepted  
**Date:** 2026-09-05

## Context

The release manifest and checksums prove what was packaged, while the packaged
install and N-1 gates prove runtime behavior only in transient terminal output.
Adding local test claims to the portable release manifest would incorrectly
make one Docker Desktop and Kind observation look like an intrinsic property of
the bundle or every customer environment.

## Decision

1. Successful packaged gates write a separate `ReleaseQualificationReport`
   adjacent to, never inside, the immutable release bundle.
2. The report recomputes and records the verified release-manifest digest,
   candidate revision, application/chart versions, image index digest, packaged
   platforms, clean-source state, and a minimized local environment profile.
3. `packaged-install` and `n-minus-one-upgrade` are closed, independently
   recorded profiles. Their check identifiers and measurements are derived and
   validated; arbitrary labels or caller-supplied summaries do not qualify.
4. A report is `qualified` only when both profiles are present once and passed.
   A standalone successful profile remains useful but explicitly `incomplete`.
5. Recording refuses a dirty checkout, source mismatch, unverified bundle,
   unpackaged host platform, inconsistent runtime identity, migration
   regression, non-zero availability failure, or invalid in-flight drain fact.

## Consequences

Operators receive durable, machine-readable evidence for the exact candidate
and environment instead of copying terminal text. Running the install profile
starts a new qualification report; the subsequent N-1 profile completes it
when candidate and environment identity match. Reports contain no credentials,
tenant data, endpoints, or cluster names.

The local report remains unsigned and cannot establish publisher identity or
generalize to a customer cluster. Production promotion still requires
organizational signatures, policy verification, vulnerability handling, and
customer-environment interoperability and resilience profiles.
