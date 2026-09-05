# OIDC browser compatibility report contract

**Status:** `v1alpha1`

**Machine contract:** `contracts/schemas/oidc-browser-compatibility-report.schema.json`

`OidcBrowserCompatibilityReport` is executable, source-bound evidence for the
console's public Authorization Code + `S256` PKCE boundary. It is separate from
`OidcIssuerCompatibilityReport`: the issuer report qualifies access-token and
JWKS verification, while this report qualifies the browser-facing
authorization redirect and token exchange over real TLS.

The closed `local-oidc-browser-pkce-v1` profile proves an exact public-client
authorization request, mandatory `S256`, state and issuer binding on the exact
loopback console redirect, token-endpoint CORS for only the console origin,
absence of client-secret and cookie dependence, a successful code exchange,
wrong-verifier and consumed-code replay denial, and verification of the
returned access token through the shipped `/v1/session` HTTP API boundary. Fixture codes are
bounded to 128 outstanding entries and 120 seconds. Check order and identifiers
are fixed for release automation.

The `obc_` identifier is derived from the source revision and dirty state,
environment, profile, and complete check results. `generatedAt` is deliberately
excluded so rerunning an identical profile on the same release and environment
has the same evidence identity.

The report retains no authorization code, PKCE verifier or challenge, state,
access token, signing key, actor, tenant, role, issuer or endpoint URL, origin,
redirect URI, client identifier, certificate path, cookie, or upstream error.
Failed checks may contain only stable error codes.

`compatible` does not qualify a customer's identity provider or user policy.
Real redirect enrollment, MFA, consent, session duration, logout, emergency
revocation, customer CORS behavior, certificate lifecycle, network path, and
availability objectives remain deployment qualification gates. This report is
an operational release artifact outside OpenAPI and both SDKs.
