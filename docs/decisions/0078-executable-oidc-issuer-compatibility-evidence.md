# ADR 0078: Executable OIDC issuer compatibility evidence

**Status:** Accepted

**Date:** 2026-08-17

## Context

ADRs 0037 and 0063 define an RS256 OIDC/JWKS verifier and public browser Authorization Code + `S256` PKCE discovery profile. Unit transports prove parsing and cache rules without a real certificate chain, socket, redirect response, endpoint outage, or live JWKS rotation. Calling that sufficient customer identity-provider qualification would hide material CORS, redirect, claim, session, revocation, certificate, network, and availability dependencies.

## Decision

Add a disposable Docker JWKS fixture and closed `local-oidc-rs256-jwks-v1` compatibility profile. Generate ephemeral standards-complete CA/server certificates and two RSA signing generations per run. Keep both private signing keys on the host; mount only public JWKs, fixture state, TLS server material, and a value-minimized request audit into the container.

Exercise the shipped HTTPS transport and OIDC authenticator. Prove CA verification, stable denial for untrusted trust roots and redirects, exact RS256/issuer/audience/time/claim enforcement, tenant/actor/role derivation, algorithm/header-indirection denial, public PKCE discovery minimization, one-fetch cache behavior, unknown-key refresh throttling, rotation without process restart, removed-key denial, outage failure after cache expiry, recovery, and secret-free evidence. Use a deterministic monotonic clock only to cross the accepted five-second refresh and 30-second cache boundaries without sleeping; network and TLS remain real.

Emit a closed report bound to source revision, dirty state, application/runtime identity, and Docker server. Keep the fixture out of bootstrap, Helm, public APIs, and SDKs. It serves no authorization or token endpoint and is not an identity provider.

## Consequences

- authentication regressions gain repeatable real-TLS, redirect, cache, rotation, outage, and recovery evidence;
- release claims can distinguish a delivered verifier profile from customer issuer qualification;
- cached keys intentionally continue to verify otherwise valid tokens until cache expiry; emergency revocation objectives remain an issuer/deployment decision;
- production rollout still requires a gate against the selected customer issuer and browser flow, including exact redirects, token-endpoint CORS, MFA/consent, claims, key/certificate rotation, revocation, logout/session behavior, and availability objectives.
