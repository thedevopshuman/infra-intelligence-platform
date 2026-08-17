# ADR 0079: Executable external policy-engine compatibility evidence

**Status:** Accepted
**Date:** 2026-08-17

## Context

ADR 0037 defines a replaceable HTTPS policy-decision boundary, but unit tests do
not prove certificate handling, redirect denial, credential-file rotation,
bounded response reads, outage behavior, or recovery against a real service.
Claiming generic customer interoperability without that evidence would be
misleading.

## Decision

Add a source-bound `PolicyEngineCompatibilityReport` and a Docker-backed local
profile. The fixture runs unprivileged with no ambient credentials, exposes TLS
only on loopback, accepts one closed actor/action/resource input, requires a
rotating bearer credential, and emits value-minimized audit records. The runner
uses the production adapter and proves all 18 contract checks, including stale
digest and cross-tenant snapshot rejection, before writing a report.

Operational compatibility reports remain outside the public control-plane API
and SDKs. They are environment evidence retained beside a release candidate,
not runtime resources or an authorization decision. The local fixture is not a
production policy engine and does not certify any customer policy bundle.

## Consequences

- Adapter regressions become an executable Docker Desktop release gate.
- Customer qualification has a precise, vendor-neutral minimum profile.
- Policy selection, bundle correctness, high availability, organizational
  change control, and customer audit integration remain deployment gates.
