# ADR 0038: Signed no-network plugin runner

**Status:** Accepted
**Date:** 2026-08-17

## Context

Plugin sessions bound capabilities and limits but did not prove artifact origin
or keep untrusted code out of the control-plane process. Giving a generic plugin
the API pod's network, filesystem, environment, or Kubernetes identity would
violate the product constitution even if the manifest declared those powers.

## Decision

The first executable plugin profile is a dedicated Docker-based stdio runner.
It accepts only Ed25519-signed OCI artifacts addressed by an exact SHA-256
digest and already present on the runner host. Publisher public keys are explicit
installation trust roots. Signature validation precedes session and method
validation; signature trust never expands granted capabilities.

The profile always disables networking and host mounts, passes no plugin
credentials or host environment, uses a read-only filesystem and non-root UID,
drops every Linux capability, enables `no-new-privileges`, and bounds CPU,
memory/swap, PIDs, file descriptors, temporary storage, input, output, and wall
time. It denies plugins requesting network, secrets, or actions. The host creates
the result envelope and exposes only stable failure codes.

## Consequences

- A networkless plugin can pass a real build/sign/execute conformance gate using
  only public contracts and SDKs.
- Artifact pulling and trust-root changes remain separate reviewed installation
  operations; invocation uses `--pull=never`.
- The API deployment does not receive the Docker socket. A production runner is
  a separate failure and privilege domain.
- The Kubernetes observer's live transport remains a development path until a
  mediated provider proxy can enforce declared destinations and request-scoped
  leases without ambient cluster credentials.
- Durable cross-restart invocation claims are required before action plugins or
  other side-effecting capabilities can be enabled. ADR 0070 later enables only
  proposal creation; it does not give the container mutation authority.
