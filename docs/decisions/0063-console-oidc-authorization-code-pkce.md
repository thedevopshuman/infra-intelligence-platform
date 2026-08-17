# ADR 0063: Browser OIDC authorization with PKCE

**Status:** Accepted

**Date:** 2026-08-17

## Context

The API already verifies issuer- and audience-bound OIDC access tokens, but the customer console only accepts a manually pasted Bearer credential. That is appropriate for local development and inadequate for production onboarding, MFA, federation, and issuer-managed session policy. Adding a client secret to the static console or turning the control plane into a general token proxy would create new credential custody and abuse surfaces.

## Decision

Add a public, non-secret console-authentication discovery contract. In configured OIDC mode, the browser acts as a public OAuth client and uses Authorization Code with a fresh `S256` PKCE challenge and state value. The browser sends the authorization code directly to the exact configured HTTPS token endpoint, keeps transient state and verifier material within the browser tab, removes callback parameters from history, and presents only the resulting access token to the existing Bearer authentication boundary.

The runtime validates a closed optional browser profile inside the existing OIDC configuration. Authorization and token endpoints reject credentials, query strings, and fragments. Redirects require HTTPS except for loopback-only local interoperability. The public document omits verifier-only and secret configuration. Console assets receive a narrowly expanded `connect-src` containing only the token endpoint's origin.

The platform does not store refresh tokens, issue sessions, select an identity vendor, or weaken issuer/audience/claim verification. Deployments without the browser profile remain in explicit access-token mode; local hashed-token mode remains unchanged.

## Consequences

- Customers can connect the console to a standards-based OIDC public client without changing the API or SDK authentication model.
- The identity provider continues to own user enrollment, MFA, consent, token lifetime, revocation, and allowed redirect origins.
- Production qualification must verify the selected issuer's browser token-endpoint CORS behavior, redirect registration, CSP origin, claim mapping, and logout/session policy.
- Providers that cannot support a browser public client require a future separately reviewed backend-for-frontend profile; this decision does not introduce client secrets into browser or ConfigMap content.
