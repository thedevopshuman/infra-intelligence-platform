# ADR 0089: Fail-closed OTLP client CRL freshness

**Status:** Accepted
**Date:** 2026-09-05

## Context

ADR 0088 makes a customer-supplied certificate revocation list effective at
the receiver's TLS boundary, but it does not independently validate the CRL's
validity window or expose expiry through readiness. Continuing to accept OTLP
traffic after `nextUpdate` would make a stale list look authoritative even
though newly revoked workload certificates may be missing. Merely relying on a
deployment runbook would leave this identity guarantee outside executable
product behavior.

CRL retrieval and issuer operation still belong to customer PKI. The receiver
can nevertheless validate the bounded artifact it was explicitly given and
stop accepting telemetry when that artifact is no longer current.

## Decision

When `IIP_OTLP_TLS_CLIENT_CRL_PATH` is configured, accept exactly one PEM CRL
no larger than 1 MiB. Its issuer and signature must match a CA certificate in
the configured client trust bundle with explicit CA and CRL-signing authority.
It must parse, contain an ordered `lastUpdate` and
`nextUpdate`, have a `lastUpdate` no later than the evaluation time, and have a
`nextUpdate` strictly later than the evaluation time. Invalid or already
expired CRL state fails receiver startup with the existing stable TLS
configuration error.

Retain the parsed validity window for the life of the process. Re-evaluate it
before every OTLP transport-identity authorization and every readiness probe.
Once `nextUpdate` is reached, readiness returns its existing value-minimized
unavailable response and metrics/logs intake returns the existing receiver
unavailable response before reading a telemetry body. Liveness remains
dependency-free so the orchestrator can observe and replace the process.

The CRL remains loaded once into the OpenSSL trust store. Startup re-reads it
after the OpenSSL load and rejects a concurrent mounted-Secret rotation rather
than pairing different trust and validity state. Updating a mounted file alone
is not claimed as live rotation: the receiver must be rolled after a new CRL is
projected. The compatibility profile adds an expired-CRL
startup-rejection check alongside the revoked-certificate handshake check.

## Consequences

- stale revocation state can no longer silently remain in service;
- orchestration sees CRL expiry through readiness without exposing issuer,
  certificate, serial number, path, or validity timestamps;
- a missing `nextUpdate`, wrong issuer/signature, malformed or oversized PEM,
  future `lastUpdate`, and an expired validity window all fail with one stable
  code;
- customers must schedule CRL publication and receiver rollout before expiry;
- automatic distribution-point fetching, hot reload, OCSP, intermediate-chain
  qualification, and customer-specific rotation cadence remain separate work.

## Revisit triggers

Revisit when a customer requires hot CRL rotation without a pod rollout, OCSP,
or an externally managed revocation agent with stronger freshness guarantees.
