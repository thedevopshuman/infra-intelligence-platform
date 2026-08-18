# ADR 0088: Executable evidence that the OTLP receiver rejects a revoked client certificate

**Status:** Accepted
**Date:** 2026-08-18

## Context

ADR 0085 proves the OTLP receiver's mutual TLS boundary rejects a
certificate signed by the trusted CA once its own validity window has
closed. Revocation is a distinct failure mode from expiry: a customer
needs to withdraw a workload's identity immediately - a compromised key,
an offboarded integration, a decommissioned Collector - without waiting
days or months for that certificate's natural `notAfter` to arrive.
Nothing in the existing compatibility profile proved the receiver honors
an explicit revocation list at all.

Two standard mechanisms exist for server-side revocation checking: CRLs
(a CA-signed list of revoked serial numbers, checked locally) and OCSP
(a live query to a per-CA responder for one certificate's status). Only
CRL checking is a realistic near-term target here: Python's `ssl` module
has no support for a TLS server issuing an OCSP query against an incoming
client certificate during the handshake - that would require a custom
OCSP client integrated into the handshake path, a materially larger and
riskier change than loading a revocation list ahead of time. This ADR
scopes to CRL checking only and leaves OCSP an explicit gap.

## Decision

Add an optional CRL to the receiver's mutual TLS profile:
`IIP_OTLP_TLS_CLIENT_CRL_PATH` (and the matching Helm
`otlpIngest.tls.clientCrlExistingSecret`/`clientCrlSecretKey`). Absent, the
receiver's behavior is unchanged - chain and validity window only, as
before. Present, `OtlpTlsConfiguration.ssl_context()` loads the CRL file
into the same server-side trust store as the client CA (a second
`load_verify_locations()` call accumulates into the existing OpenSSL
verify store rather than replacing it - confirmed empirically against
this repository's pinned Python/OpenSSL versions) and sets
`ssl.VERIFY_CRL_CHECK_LEAF`, so only the presented client certificate
itself is checked against the list, not its issuers.

The local compatibility fixture generator (`compatibility_tls.py`) gains
`revoked_client_identities`: an otherwise normally-valid client identity
whose serial number is written to a CA-signed CRL (`ca.crl`) alongside the
existing valid/expired/untrusted fixtures. `revoked-client-certificate-
rejected` becomes a seventeenth check in the executable OTLP receiver
compatibility profile, verified the same way expiry is verified: the
client TLS handshake against the live isolated receiver, configured with
the CRL, must fail (`ssl.SSLError` / `URLError`) rather than reach the
application layer. `docs/specifications/otlp-receiver-compatibility-contract.md`
and the JSON Schema move from 16 to 17 required checks.

## Consequences

- a regression that stopped honoring a configured CRL now fails the
  executable gate instead of only being caught in a customer's own
  incident;
- the change is additive and backward compatible: deployments that never
  configure a CRL see no behavior change, and the CRL is loaded once at
  process startup like the certificate and key, not fetched or refreshed
  per connection;
- OCSP-based revocation, CRL distribution-point automation (fetching and
  rotating the CRL file itself), and CRL freshness/expiry enforcement
  remain explicitly deferred: this ADR proves the receiver enforces a CRL
  it is given, not that IIP obtains, refreshes, or validates the
  liveness of that CRL itself - operating a current CRL remains a
  customer deployment responsibility, the same way ADR 0080 makes queue
  capacity and retention a customer choice.

## Revisit triggers

Revisit if a customer requires OCSP, automatic CRL distribution-point
fetching, or CRL freshness enforcement (rejecting connections once a
loaded CRL's `nextUpdate` has passed) before a production claim.
