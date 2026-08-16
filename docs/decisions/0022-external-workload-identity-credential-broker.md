# ADR 0022: Resolve provider leases through an external workload-identity credential broker

**Status:** Accepted

**Date:** 2026-08-16

## Context

The Prometheus and Kubernetes Event adapters already request logical credentials through the application-owned `CredentialBroker` port, but their runnable profiles use static secret-bearing JSON. That proves exact tenant/integration/provider/scope lookup, not production rotation, independent policy, revocation, or workload identity. Adding a different provider-specific secret mechanism to every adapter would duplicate authority logic and keep long-lived customer credentials in ordinary control-plane configuration.

The broker itself must authenticate the calling workload without receiving ambient cluster authority. Its response necessarily contains short-lived secret material, so the protocol cannot become a user-facing control-plane endpoint, model tool, plugin capability, evidence artifact, or general SDK operation.

## Decision

- Define protected `CredentialLeaseRequest` and `CredentialLease` v1alpha1 wire contracts for a separately operated broker. Keep them out of the control-plane OpenAPI surface.
- Reuse the existing application `CredentialBroker` authority tuple: authenticated tenant and actor, selected integration and provider, exact logical credential reference, ordered narrow scopes, and the provider-call deadline.
- Add an `ExternalHttpCredentialBroker` adapter selected explicitly by `IIP_CREDENTIAL_BROKER_MODE=external-http`. The default `static` mode retains the current development brokers.
- Authenticate the broker call with a separately configured workload-identity token file. Read the file for every request so projected rotation takes effect without a process restart. Never accept the workload token or provider lease in the JSON configuration.
- Use only direct TLS-verified `POST /v1/credential-leases` calls. Refuse redirects, user-info endpoints, non-HTTPS endpoints, unexpected paths, non-JSON responses, non-200 responses, oversized bodies, and elapsed deadlines.
- Correlate every response to an opaque request ID. Require a Bearer scheme, mandatory issuance/expiry times, lease coverage through the original deadline, bounded clock skew, and a configured maximum lifetime of at most one hour.
- Share one external broker instance across selected Prometheus and Kubernetes Event adapters. Provider adapters still validate the returned scheme/value and map broker failure to their stable credential-unavailable code.
- Do not cache leases in this first client. Each provider call obtains an independently scoped lease, avoiding cross-actor or cross-request cache authority and making broker-side issuance/audit the source of truth.
- Keep lease objects redacted in representations and never persist request or response bodies. Broker status, body, and exception text are replaced by stable `credential.broker.*` failures before provider adapters translate them.
- In Helm, keep ambient service-account-token automount disabled. External mode uses an explicit projected service-account token with a broker-specific audience and bounded lifetime, plus an optional pinned CA Secret and explicit broker egress CIDR/port.

## Consequences

- Customer provider credentials can leave ordinary control-plane environment configuration while existing evidence/application contracts remain unchanged.
- One broker policy can enforce tenant, actor, integration, provider, scope, and deadline consistently across adapters.
- The external issuer must validate Kubernetes/OIDC issuer, audience, subject, tenant/integration policy, scopes, and credential reference; issue/rotate/revoke provider credentials; and retain a security audit without secret values.
- The control plane still briefly holds the returned secret inside the requesting adapter. Process isolation, memory-hardening, and plugin credential separation remain deployment/runtime work.
- Static brokers remain useful for deterministic tests and local clusters but are explicitly non-production.
- This repository provides the client and conformance boundary, not a highly available credential-issuer service. Production readiness still requires an issuer implementation, availability objectives, audit export, revocation tests, rotation drills, and recovery procedures.

## Revisit triggers

Revisit when a design partner selects SPIFFE/SPIRE, cloud workload identity federation, mutual TLS, client certificates, non-Bearer provider credentials, lease caching, delegated scopes, multiple broker trust domains, or a concrete external secret-manager/issuer product.
