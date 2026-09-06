# ADR 0120: Aggregate local release readiness without overstating promotion

**Status:** Accepted
**Date:** 2026-09-06

## Context

The repository emits separate source-bound reports for packaging, upgrade,
vulnerabilities, capacity, identity, policy, telemetry, provider
instrumentation, recovery, availability, context, plugins, and production
configuration. Operators previously had to infer candidate readiness from a
directory of individually valid files. A partial or stale directory could look
complete, while a simple percentage could blur the boundary between local
evidence and customer or organizational approval.

## Decision

1. Add a `ReleaseReadinessReport` that verifies one release bundle and an
   ordered, closed set of 18 repository-controlled evidence documents.
2. Validate each input against its owning schema, exact successful status,
   expected profile, clean source, candidate revision, and applicable release
   identity before marking it passed.
3. Retain only content digests and minimized binding/status metadata. Keep the
   aggregate outside the immutable release bundle.
4. Use `locally-qualified` only when every local requirement passes. Never use
   `ready`, `production-ready`, or `promotable` for this profile.
5. Always enumerate eight non-overridable external gates covering publication,
   organizational identity, customer environment evidence, live AI/pricing,
   design-partner operation, and legal/brand/governance decisions.
6. Do not accept boolean acknowledgements or free-form claims as substitutes
   for future environment-specific contracts and evidence.

## Consequences

Release owners get one machine-readable inventory that fails closed on missing,
stale, dirty, malformed, or crossed evidence. Support recipients can verify
the same aggregate without receiving paths, customer identifiers, secrets, or
raw findings.

The report intentionally cannot authorize publication or production use. When
customer and organizational gates gain executable contracts, additive
readiness profiles may consume those reports; local evidence will not be
silently reinterpreted.
