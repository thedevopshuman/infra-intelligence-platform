# ADR 0085: Executable evidence that the OTLP receiver rejects an expired client certificate

**Status:** Accepted
**Date:** 2026-08-18

## Context

ADR 0080 binds the isolated OTLP receiver's mutual TLS boundary to a
CA-verified SPIFFE client identity, and the executable local compatibility
profile already proves an untrusted-issuer certificate is rejected and that
the receiver picks up a rotated server certificate without a restart. It did
not separately prove that a certificate issued by the *trusted* CA is still
rejected once its own validity window has closed. Expiry is the most basic
customer PKI interoperability property, and it is a distinct failure mode
from an untrusted issuer: a deployment could satisfy chain trust while a
workload runs on a stale, unrotated certificate past its expiry, and nothing
in the existing profile would catch that regression.

## Decision

Extend the local compatibility fixture generator (`compatibility_tls.py`)
to also issue a client certificate signed by the same trusted CA as every
valid identity, but with a validity window that has already closed
(`notBefore`/`notAfter` both in the past). Add
`expired-client-certificate-rejected` as a sixteenth check in the executable
OTLP receiver compatibility profile, verified the same way the existing
untrusted-issuer check is verified: the client TLS handshake against the
live isolated receiver must fail (`ssl.SSLError` / `URLError`) rather than
reach the application layer.

The check is additive to the closed, ordered check list `make
test-otlp-receiver` already certifies and writes into
`OtlpReceiverCompatibilityReport`; `docs/specifications/otlp-receiver-compatibility-contract.md`
and the JSON Schema move from 15 to 16 required checks.

## Consequences

- a regression that stopped enforcing certificate expiry at the TLS layer
  now fails the executable gate instead of only being caught by an
  untrusted-issuer test that cannot distinguish "wrong CA" from "right CA,
  expired cert";
- the fixture change is additive and local-only; no public contract,
  endpoint, or credential shape changes;
- certificate revocation (CRL/OCSP), intermediate CA chain depth, and
  customer-specific PKI/rotation cadence remain separate, explicitly
  deferred qualification: this ADR proves only that the receiver's own TLS
  boundary enforces the standard X.509 validity window it is given, not that
  a customer's issuer, revocation infrastructure, or rotation automation is
  itself correct.

ADRs 0088–0090 subsequently add current-CRL revocation and freshness evidence,
a one-intermediate client hierarchy, and explicit receiver-rollout evidence.
Customer-specific PKI and rotation qualification remain open.
