# ADR 0005: Credential-derived request identity

**Status:** Accepted
**Date:** 2026-08-14

## Context

The foundation HTTP surface accepted `x-iip-tenant-id` and `x-iip-actor-id` directly from callers and supplied permissive local defaults. That made tenant and actor assertions request-controlled, contradicting the accepted tenant invariant and preventing the API from representing a safe gateway boundary.

The production identity provider and service-identity mechanisms are not yet selected. Choosing a particular OIDC issuer, workload-identity system, API gateway, or mutual-TLS deployment now would couple the application to an unvalidated operating environment.

## Decision

Protected HTTP operations require `Authorization: Bearer <credential>`. The surface passes the credential to an application-owned `Authenticator` port and receives an `ActorContext` containing actor ID, tenant ID, and roles only after verification. Tenant and actor assertion headers are neither documented nor trusted. Health and readiness endpoints remain unauthenticated.

The local reference adapter compares the SHA-256 verifier of a presented high-entropy opaque token against verifier records supplied through `IIP_AUTH_IDENTITIES_JSON`. Configuration includes actor, tenant, and roles but never the raw token. Comparisons use a constant-time primitive, invalid credentials produce only `authentication.invalid`, missing credentials produce `authentication.required`, and missing or malformed server configuration fails closed.

This hashed-token adapter is for local development, Docker Compose, and deterministic tests. It is not the production identity-provider decision. Future OIDC, workload identity, or mutual-TLS adapters must implement the same port, validate issuer/audience or peer identity as applicable, and derive—not accept—tenant and actor context.

The Python and TypeScript SDKs move to version 0.2 and replace caller-supplied tenant/actor options with a Bearer token. The HTTP paths and resource contracts remain `v1alpha1`; this ADR and the SDK READMEs are the migration notes for the breaking client-constructor change.

## Consequences

- Caller-controlled identity headers can no longer cross the HTTP trust boundary.
- Authentication and policy authorization remain separate decisions; a valid credential does not imply permission for every action.
- Compromise of a local verifier configuration still enables offline guessing, so local tokens must contain at least 256 bits of entropy and the verifier configuration is handled as a secret.
- Local and deployed API processes require explicit authentication configuration instead of receiving an implicit development identity.
- Token issuance, expiry, rotation workflow, revocation, federation, and production identity-provider selection remain unresolved and must be completed before a public multi-tenant deployment.
