# OIDC identity-provider qualification

**Status:** Executable verifier profile; customer issuer qualification required

The production-facing authenticator verifies RS256 access tokens against one explicitly configured HTTPS JWKS endpoint and derives actor, tenant, and roles only from signed claims. The optional browser profile exposes non-secret Authorization Code + `S256` PKCE discovery; the platform never holds an OAuth client secret, refresh token, identity-provider password, or server session.

## Local verifier compatibility

Run the real-transport profile with Docker Desktop:

```bash
make test-oidc
```

The target generates a one-hour CA/server chain and two ephemeral RSA signing generations, keeps private signing keys on the host, and starts a non-root read-only public-JWKS fixture over HTTPS. It exercises the shipped verifier and writes `dist/oidc-issuer-compatibility-report.json` before removing the isolated Compose project.

The closed profile verifies CA trust and untrusted-CA denial, redirect refusal, RS256 and header restrictions, exact issuer/audience/time/claim checks, tenant/actor/role derivation, minimized PKCE discovery, JWKS caching and refresh throttling, live key rotation, removed-key denial, expired-cache outage behavior, recovery, and secret-free evidence. The fixture serves no authorization or token endpoint and is not an identity provider.

## Customer qualification

Before rollout, repeat interoperability against the selected customer issuer and real console origin. At minimum verify:

- exact issuer, API audience, actor, tenant, and role claims for every supported user class;
- Authorization Code with `S256` PKCE, exact redirect registration, and no client secret;
- token-endpoint CORS allows only the exact console origin, `POST`, and required content type without cookies;
- MFA, consent, session lifetime, logout, user disablement, and emergency revocation behavior;
- JWKS and certificate rotation, removed-key propagation, issuer outage, and recovery objectives;
- NetworkPolicy/DNS/proxy trust, CA chain, certificate expiry alerting, and ingress/CSP origins;
- denial for other audiences, tenants, malformed role claims, algorithm changes, redirects, and untrusted roots.

The verifier caches a valid JWKS for the configured interval. A token signed by a cached key can remain valid during a short issuer outage until that cache expires; after expiry the verifier fails closed if it cannot refresh. Choose cache and token lifetimes with the customer's revocation and availability objectives rather than treating the local 30-second profile as production guidance.

## Rollback

Changing `IIP_AUTH_MODE` or OIDC configuration requires a process restart. Roll back only to another approved issuer configuration or a protected break-glass identity procedure. `local-hashed` mode is for controlled evaluation, not a silent production fallback. Never copy an access token, signing key, client secret, local verifier, authorization code, or refresh token into Helm values, ConfigMaps, logs, reports, support tickets, or evidence.
