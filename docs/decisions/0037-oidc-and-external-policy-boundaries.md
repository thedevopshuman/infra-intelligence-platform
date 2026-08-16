# ADR 0037: OIDC identity and external policy boundaries

**Status:** Accepted
**Date:** 2026-08-17

## Context

The local hashed-token authenticator and permissive tenant policy are useful for
deterministic development, but they do not provide identity-provider rotation,
token expiry, federation, or deployer-owned authorization rules. Baking a single
identity or policy vendor into the kernel would violate the existing ports and
make customer deployment choices part of domain behavior.

## Decision

Keep local adapters as explicit development modes and add two production-facing
composition choices:

1. `oidc` authentication verifies RS256 Bearer JWTs against one explicitly
   configured HTTPS JWKS endpoint. Issuer, audience, expiry, issued-at, subject,
   key ID, algorithm, tenant claim, and role claim are mandatory. Algorithms are
   fixed by protected configuration, not chosen from untrusted token headers.
   JWKS documents are bounded and cached briefly; rotation refreshes are rate
   limited so unknown key IDs cannot amplify requests to the issuer.
2. `external-http` policy posts the exact actor/action/resource input to one
   explicitly configured HTTPS decision endpoint. The response is closed and
   must echo the canonical input digest and include a tenant-bound immutable
   policy snapshot reference. Stale, cross-input, and unavailable decisions
   deny with a stable code.

Both adapters refuse plaintext endpoints and redirects, support a protected CA
bundle, use bounded I/O, and hide external error text. The policy credential is
an optional protected file read on each request. Kubernetes mounts and network
policy controls are configuration, not ambient authority.

The governed-action service records an external snapshot reference when the
decision supplies one and retains deterministic local references otherwise.
Application and domain packages remain unaware of JWT, JWKS, HTTP, or a policy
vendor.

## Consequences

- Customers can use their OIDC issuer and policy engine without changing public
  contracts or server internals.
- The platform still requires deployment-specific issuer enrollment, claim
  governance, policy bundle review, revocation objectives, and high-availability
  testing before production launch.
- RS256 is the first intentionally narrow interoperability profile. Supporting
  another asymmetric algorithm is an additive security decision with new tests,
  not automatic trust in a JWT header.
- Local Docker onboarding remains offline-friendly with hashed credentials and
  the local policy adapter.
