# ADR 0131: Qualify customer OIDC verifier and browser prerequisites externally

**Status:** Accepted
**Date:** 2026-09-07

## Context

ADRs 0078 and 0116 prove the shipped OIDC verifier and browser Authorization
Code with `S256` PKCE behavior against disposable local real-TLS fixtures.
Those reports deliberately do not prove that one selected customer issuer,
console origin, claim mapping, trust chain, or deployed release interoperates.
The customer deployment aggregate therefore still carried a broad identity
interoperability non-claim.

Running a complete interactive login inside release automation would require a
user account, passwordless or MFA interaction, consent and session policy, and
possibly privileged identity-provider automation. It would also risk retaining
authorization codes, PKCE material, cookies, or tokens. A narrower external
gate can prove the deployed verifier and the browser's machine-observable
prerequisites without acquiring those authorities or overstating the result.

## Decision

Add a protected `CustomerOidcQualificationProfile` and privacy-minimized
`CustomerOidcQualificationReport` with the closed
`customer-oidc-verifier-browser-prerequisites-v1` qualification.

1. An operator supplies a mode-`0600` profile containing the reviewed issuer,
   discovery and JWKS endpoints, API audience, claim mapping, expected
   qualification identity, public-client configuration, console redirect, and
   bounded transport/token objectives. A separately protected file contains
   one short-lived access token obtained through the customer's ordinary
   issuer process.
2. The host-side qualifier uses direct, CA-verified HTTPS with proxies and
   redirects disabled. API and issuer trust bundles are explicit inputs.
3. Issuer discovery must bind the exact issuer, authorization endpoint, token
   endpoint, and JWKS endpoint and must advertise authorization-code, `S256`,
   and public-client token authentication support. The selected token's `kid`
   must identify exactly one compatible RSA signing JWK.
4. The deployed public console discovery document must exactly match the
   protected browser profile. The console redirect origin must equal the
   qualified API origin.
5. A token-endpoint preflight must allow only the exact console origin, `POST`,
   and `content-type`, without credentialed CORS. A second origin must not
   receive itself or a wildcard as an allowed origin.
6. The qualifier locally checks the token's RS256 header, issuer, audience,
   actor, tenant, exact ordered roles, `iat`, `exp`, and bounded lifetime. The
   deployed API must independently authenticate the token into the same
   session context, return the exact release identity, and reject a
   signature-tampered derivative.
7. The retained report contains only content digests, public release identity,
   bounded sizes/counts/times, fixed protocol facts, and stable check results.
   It contains no issuer or API URL, audience, client, identity, role, token,
   credential, or raw response.
8. Qualification requires an explicit identity-observation enable flag and a
   clean checkout at the deployed source revision. Offline verification
   rebinds the report to the current source, exact image, protected profile,
   and API target.
9. Upgrade the customer deployment aggregate to
   `single-cluster-database-oidc-prerequisites-v4`. It requires this report
   after preflight and before disruption testing and binds its API target to
   the existing continuity target.

This gate follows the issuer-binding, exact redirect, public-client, and PKCE
security direction in [OAuth 2.0 Security Best Current Practice](https://www.rfc-editor.org/rfc/rfc9700),
[OpenID Connect Discovery 1.0](https://openid.net/specs/openid-connect-discovery-1_0.html),
and [RFC 7636](https://www.rfc-editor.org/rfc/rfc7636). The profile is
intentionally stricter than permissive discovery clients: an issuer that does
not advertise `none` and `S256` is not qualified by inference.

## Consequences

- A customer deployment can prove that one real issuer configuration, selected
  identity, browser prerequisites, and exact IIP release agree without adding
  an identity-provider SDK or a client secret.
- The access token is used only by the external qualifier and deployed API. It
  never enters a report, command argument, Helm value, ConfigMap, SDK model, or
  platform Evidence record.
- This gate does not perform `/authorize` or `/token`, observe MFA or consent,
  qualify logout or session behavior, measure disablement or revocation
  latency, or exercise issuer HA, certificate rotation, or signing-key
  rotation. Those require separately owned customer tests.
- The report is customer-environment evidence, not an identity certification,
  artifact promotion decision, or public-production approval.
