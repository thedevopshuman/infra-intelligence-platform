# ADR 0066: host-mediated plugin read connectivity

**Status:** Accepted
**Date:** 2026-08-17

## Context

The signed runner proves artifact trust, resource isolation, durable ownership,
and cancellation, but always denies network and secret declarations. Giving an
untrusted container ordinary egress, a provider token, the credential broker,
or the control-plane API would turn declarative manifest permissions into
ambient authority and would make destination, path, and credential enforcement
dependent on plugin behavior.

## Decision

Keep the plugin container on Docker's `none` network and pass no credential,
workload identity, endpoint URL, CA path, host environment, or control-plane
token. For invocations with explicitly issued read grants, mount only a fresh
invocation-local Unix-domain socket. Because Docker Desktop cannot bind a macOS
Unix socket into its Linux VM, the portable profile places that socket in a
fresh Docker volume shared read-only with the plugin. A separately digest-pinned,
host-owned relay container has no network, read-only root, dropped capabilities,
bounded resources, and runs non-root after a one-shot volume-permission
initializer; it relays only framed JSON between the socket and the runner's
host stdio. The socket protocol supports bounded
HTTP-JSON `GET` mediation; it cannot express mutation or arbitrary HTTP.

The public grant is bound to the durable invocation digest and is narrower than
the signed manifest's destination and logical-secret upper bounds. A protected
host binding separately owns the exact TLS endpoint, credential reference, CA
configuration, provider scopes, and the same public limits. The host rejects
any mismatch before execution.

For each request, the host validates exact invocation and grant identity,
template-matches one safe path segment at a time, allowlists query keys, enforces
count/byte/deadline limits, obtains policy approval over minimized digests, and
records an audit intent before egress. A provider adapter resolves one
request-scoped lease through the existing protected credential-broker port,
performs a direct TLS request with redirects disabled, and returns only bounded
parsed JSON. Neither broker nor provider error detail is returned.

Action mediation, request bodies, arbitrary methods, redirects, plugin-selected
destinations, raw response bytes, and direct credentials remain prohibited.

## Consequences

- A connected read-only plugin still has no general network route or credential.
- Manifest declarations alone grant nothing; invocation grant, protected
  binding, current policy, broker lease, audit, path, query, deadline, and limits
  must all agree.
- The Unix socket is an invocation-scoped capability and is removed when the
  runner exits; the relay container and volume are also force-cleaned.
- Provider data is exposed to untrusted plugin code, so the host must continue
  to validate, minimize, and treat plugin results as untrusted.
- The API pod still has no container-runtime socket. A production plugin runner
  remains a separate deployment and failure domain.
- Mutating or action-provider plugins remain disabled until a separately
  governed proposal/approval/execution protocol is defined.
- The mediation contracts are an SDK/private-runner boundary, not an OpenAPI
  control-plane route.

## Verification

Schema and cross-contract validation, policy and path-boundary tests, broker and
provider adapter tests, SDK socket tests, a real no-network Docker socket
conformance run, and release/Helm verification cover the first profile.
