# OIDC issuer compatibility report contract

**Status:** `v1alpha1`

**Machine contract:** `contracts/schemas/oidc-issuer-compatibility-report.schema.json`

This report is executable, source-bound evidence that the production-facing OIDC/JWKS authenticator satisfies the repository's local real-TLS RS256 profile. `make test-oidc` generates it outside the control-plane API by exercising the shipped verifier against a disposable public-JWKS fixture in Docker. Private signing keys remain on the host and exist only for the duration of the test.

The closed profile verifies CA-trusted HTTPS, untrusted-CA and redirect denial, RS256-only validation, exact issuer/audience/time/claim handling, tenant/actor/role derivation, rejection of untrusted header indirection, minimized `S256` PKCE discovery, bounded JWKS caching and refresh throttling, key rotation without restart, removed-key denial, fail-closed behavior after an expired cache during issuer outage, recovery, and secret-free evidence. Check order and identifiers are fixed for release automation.

`compatible` means every check passed for the exact source revision, application/runtime versions, Docker server, and declared fixture profile. It does not qualify a customer's identity provider, browser token endpoint, CORS policy, redirect enrollment, MFA/consent, logout/session behavior, revocation objectives, network, certificate rotation, or availability topology. A dirty-source report is development evidence only.

The report contains no access token, private signing key, certificate private key, actor, tenant, role, issuer URL, JWKS URL, CA path, client identifier, authorization endpoint, token endpoint, or raw upstream error. Failed checks may expose only stable error codes.

This operational report is intentionally outside OpenAPI and both SDKs. Public browser discovery remains the existing `ConsoleAuthenticationConfiguration` contract; compatibility evidence is a deployment/release artifact, not a tenant API resource.
