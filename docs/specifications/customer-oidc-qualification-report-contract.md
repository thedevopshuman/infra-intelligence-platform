# Customer OIDC qualification contracts

**Status:** `v1alpha1` executable customer-environment evidence

**Decision:** [ADR 0131](../decisions/0131-customer-oidc-prerequisite-qualification.md)

**Machine contracts:**
`contracts/schemas/customer-oidc-qualification-profile.schema.json` and
`contracts/schemas/customer-oidc-qualification-report.schema.json`

The customer OIDC contracts define one protected qualification input and one
privacy-minimized output. They qualify a selected issuer's verifier and browser
prerequisites against an exact deployed IIP release. They do not model an
interactive user session and are not served by the control-plane API.

## Protected profile

`CustomerOidcQualificationProfile` is a mode-`0600` operator input. Its
`identity` selects one approved qualification tenant, actor, and exact ordered
role set. Its `oidc` section binds the issuer, API audience, discovery/JWKS
locations, claim names, and public-browser client configuration. The redirect
must be the qualified API origin's `/console` route. `objective` bounds the
accepted access-token lifetime and remaining lifetime, request timeout, and
response size.

The profile is configuration, not authority. The access token and CA files are
separate inputs and never appear in the profile example used as documentation.
Operators should copy the example outside the checkout, replace every example
value, restrict it to mode `0600`, and handle it under the customer's identity
configuration policy.

## Minimized report

`CustomerOidcQualificationReport` contains:

- the exact application, chart, contracts, migration, source, and image
  identity returned by the authenticated runtime;
- SHA-256 bindings for the API target, protected profile, issuer discovery
  metadata, and runtime subject;
- fixed transport and protocol facts;
- only response byte counts, JWK count, token lifetime measurements, and
  start/completion times;
- seventeen ordered checks and a derived summary; and
- four mandatory limitations covering interactive authorization/MFA,
  logout/session/consent, disablement/revocation latency, and issuer
  HA/certificate/key rotation.

The `coq_` identifier is derived from the complete report metadata and
specification. Semantic validation recomputes check ordering, summary, status,
time relations, measurement bounds, runtime digest, limitations, and report
identity. Offline verification additionally rebinds current clean source,
image digest, API target, and protected profile.

The report rejects fields named for URLs, issuer, audience, client, identity,
roles, tokens, or credentials. Raw discovery/JWKS documents, response bodies,
headers, errors, and access-token material are never retained.

## Qualification boundary

`qualified` means that direct CA-verified, no-redirect requests observed all of
the following for one selected access token and exact release:

1. exact issuer discovery and JWKS endpoint binding;
2. advertised authorization-code, `S256`, and unauthenticated public-client
   token endpoint support;
3. one compatible RSA signing JWK for the token `kid`;
4. an exact deployed console public-client profile;
5. exact-origin, non-credentialed token-endpoint CORS and browser-enforced
   denial for an unrelated origin;
6. exact token claims and bounded token lifetime;
7. matching API-derived tenant, actor, and roles;
8. authenticated exact release identity; and
9. denial of the signature-tampered token.

The qualifier intentionally does not submit an authorization request or token
exchange. It therefore cannot claim that redirect registration, login, MFA,
consent, logout, user disablement, emergency revocation, or issuer availability
has been exercised. Those outcomes remain external evidence even when this
report is qualified.

Python and TypeScript SDKs expose both shapes for release tooling. They do not
load the protected profile, handle credentials, initiate login, or grant
identity-provider authority.
