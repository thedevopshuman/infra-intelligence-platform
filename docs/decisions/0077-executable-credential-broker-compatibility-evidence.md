# ADR 0077: Executable credential-broker compatibility evidence

**Status:** Accepted
**Date:** 2026-08-17

## Context

ADR 0022 defines a TLS-only external credential-broker client, exact authority tuple, rotating workload-token file, short lease bounds, stable failures, and secret redaction. Unit tests use an in-memory transport, so they cannot prove the shipped client verifies a real certificate chain, reloads a projected token across real HTTP requests, fails during a broker outage, or recovers when the endpoint returns. The absence of an external customer issuer also makes a broad “production compatible” claim unsafe.

## Decision

Add a disposable Docker fixture and a closed `local-tls-workload-identity-v1` compatibility profile. Generate an ephemeral CA and server certificate, random test-only JWT and lease keys, and exact tenant/integration/provider/credential-reference/scope policy for every run. Exercise the production external HTTP client over CA-verified TLS.

The gate must prove signed issuer/audience/subject/expiry validation; exact-scope issuance; cross-tenant and scope-escalation denial; fresh workload-token reads, rotation, and revocation; stable client failures with no upstream detail; value-minimized audit completeness; untrusted-CA denial; outage fail-closed behavior; and recovery. Emit a closed report bound to the Git revision, dirty state, application/runtime identity, and Docker server.

The fixture is test-only and cannot be selected by product bootstrap, Helm, or the public API. It derives synthetic lease material rather than resolving customer credentials. The report remains outside OpenAPI and SDKs because it is release evidence, not a public control-plane resource.

## Consequences

- local verification gains real socket, TLS, token-rotation, revocation, outage, and recovery evidence;
- compatibility claims are exact, machine-readable, source-bound, and secret-free;
- the fixture cannot be mistaken for a highly available issuer or secret-management product;
- production readiness still requires a gate against the selected customer identity issuer and credential broker, including their availability, revocation propagation, rotation, policy, audit export, certificate rotation, and recovery objectives.
