# Console authentication discovery contract

**Status:** v1alpha1

**Machine contract:** `contracts/schemas/console-authentication.schema.json`

`ConsoleAuthenticationConfiguration` is the public, non-secret bootstrap document used before a browser has a credential. `GET /v1/authentication/console` is therefore unauthenticated and returns one of three modes:

- `local-token` for the hashed development-token profile;
- `access-token` when the API validates OIDC tokens but browser sign-in is not configured; or
- `oidc-pkce` when the console can initiate OAuth 2.0 Authorization Code flow with PKCE against the deployment's OIDC issuer.

An `oidc-pkce` document contains only reviewed public-client metadata: issuer, client ID, authorization and token endpoints, exact redirect URI, requested scopes, display label, and the fixed `S256` challenge method. It never returns JWKS contents, token-verifier settings, tenant or role claim names, CA paths, client secrets, access tokens, refresh tokens, authorization codes, or PKCE verifiers.

## Browser security semantics

The console creates a fresh high-entropy state value and PKCE verifier for every sign-in attempt. It stores those values only in browser-tab session storage until the callback, validates the returned state before exchanging the code, and removes authorization parameters from browser history. The exchange is sent directly to the configured HTTPS token endpoint; the control plane never becomes an OAuth token proxy. Only the returned Bearer access token is accepted, and it is immediately verified by the existing server-side issuer, audience, signature, expiry, tenant, actor, and roles boundary through `GET /v1/session`.

The configured redirect URI must exactly match the current console origin at runtime. HTTPS is mandatory except for an explicit `localhost` or `127.0.0.1` loopback redirect used during interoperability testing. Authorization and token endpoints are always HTTPS, credential-free, query-free, fragment-free, and supplied by protected deployment configuration. The console's Content Security Policy admits network requests only to its own origin and the configured token-endpoint origin.

The console never persists a refresh token or ID token. Its existing “keep for this browser tab” option stores the access token in `sessionStorage`; otherwise it remains only in page memory. Local manual-token entry stays available for development and as an explicit access-token fallback, but production onboarding should configure `oidc-pkce`.
