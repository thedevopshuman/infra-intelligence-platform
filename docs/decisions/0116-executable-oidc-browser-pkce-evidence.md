# ADR 0116: Executable OIDC browser PKCE evidence

**Status:** Accepted
**Date:** 2026-09-06

## Context

The console implements a public Authorization Code + `S256` PKCE client, and
the OIDC issuer compatibility gate proves the API's JWKS verifier and minimized
browser discovery. That fixture intentionally exposes no authorization or
token endpoint. Static source assertions therefore do not prove the exact
redirect, CORS, public-client, one-time code, and token-to-API path that a user
depends on during sign-in.

Combining browser-flow evidence with the existing verifier report would also
change a closed release profile and blur two independently operated issuer
responsibilities.

## Decision

Add a separate `OidcBrowserCompatibilityReport` and disposable real-TLS
`local-oidc-browser-pkce-v1` profile. Extend only the test fixture with strict
authorization and token endpoints. The fixture accepts one exact public client,
console redirect, scope set, console origin, and `S256` challenge. Authorization
codes are random, process-local, single-use, limited to 128 outstanding
entries, expire after 120 seconds, and are consumed on the first token attempt.
The token endpoint rejects cookies, client secrets, unknown or
duplicate form fields, other origins, wrong verifiers, and replay.

Drive the flow from the runtime's minimized public discovery document and
verify the returned access token through the shipped `/v1/session` HTTP route
and production-facing `OidcJwtAuthenticator`. Use a pre-signed ephemeral access token
so the container receives no RSA private signing key. Retain only fixed check
outcomes and non-sensitive environment/source identities in the report.

Keep the fixture and runner outside bootstrap, Helm, public APIs, and SDKs. Do
not describe the local profile as customer identity-provider qualification.

## Consequences

- Release testing now exercises the browser protocol boundary instead of only
  inspecting its source and discovery document.
- The existing JWKS/verifier report remains backward compatible and separately
  attributable.
- The profile proves no client secret, refresh token, cookie-backed token
  exchange, redirect following, or platform-issued session is required.
- Customer redirect registration, CORS, MFA, consent, logout/session policy,
  revocation, certificates, networks, and availability still require real
  environment evidence.
