# ADR 0090: Intermediate-CA and OTLP CRL rollout evidence

**Status:** Accepted
**Date:** 2026-09-05

## Context

ADRs 0088 and 0089 prove the isolated OTLP receiver rejects a client listed on
a current CRL, rejects an already expired CRL at startup, and stops readiness
and intake when the loaded CRL reaches `nextUpdate`. Their local fixture issues
client certificates directly from one root CA and does not exercise the
operational step required to replace a CRL. Customer workload PKI commonly
uses an offline root, an issuing intermediate, and short-lived leaf
certificates. A passing direct-root fixture is insufficient evidence for that
shape.

The receiver intentionally loads a CRL once. Mutating a projected file without
replacing the process cannot update the OpenSSL verification store and must not
be described as successful rotation. The product needs executable evidence for
the explicitly selected rollout behavior, not an undocumented assumption that
a mounted Secret eventually changes live TLS state.

## Decision

The local mutual-SPIFFE compatibility profile uses a three-certificate
hierarchy for every trusted OTLP client: a root CA, one CRL-signing client
intermediate CA, and a leaf client certificate. Client certificate files
present the leaf followed by the intermediate. A live root-only server trust
test accepts that chain and rejects the same leaf when its intermediate is
omitted. The receiver's client CA bundle contains the root and issuing
intermediate so startup can independently validate the intermediate-signed CRL
issuer, authority, and signature before OpenSSL enforces leaf revocation.

Extend `OtlpReceiverCompatibilityReport` with the fixed profile values
`clientCertificateChain: root-intermediate-leaf` and
`crlRotation: projected-file-receiver-rollout`. Add two ordered executable
checks:

1. `intermediate-ca-client-certificate` covers the intermediate-issued client
   chain used by the official metrics/logs exporters and the root-only
   positive/negative handshake test.
2. `crl-rotation-with-receiver-rollout` first proves a current client is
   accepted, atomically promotes a newer current CRL that additionally revokes
   that certificate, recreates only the receiver, proves the newly revoked
   certificate is rejected, and proves an unaffected client remains accepted.

The compatibility report therefore contains exactly 20 checks. It remains
source-bound, value-minimized local evidence and does not contain certificate,
issuer, serial-number, identity, endpoint, credential, or payload values.

For Kubernetes, the preferred rollout is a newly named, immutable CRL Secret
followed by an atomic, waiting Helm upgrade that changes
`otlpIngest.tls.clientCrlExistingSecret`. The changed pod template causes the
Deployment to roll and readiness prevents a pod with invalid or stale CRL
state from entering service. If an operator updates the contents under the
same Secret name, they must explicitly restart the receiver Deployment and
wait for rollout completion; projected-file refresh alone is not sufficient.

## Consequences

- the shipped Docker gate now covers the common root/intermediate/leaf client
  hierarchy and an intermediate-signed CRL rather than only a direct-root
  fixture;
- a CRL replacement that is present on disk but not loaded by a new receiver
  process cannot pass the compatibility profile;
- a newly invalid CRL prevents the replacement receiver from becoming ready,
  while the previously loaded pod can continue only until its existing CRL
  reaches `nextUpdate`;
- operators can use versioned immutable Secret names for an auditable rollout
  that Helm and Kubernetes can observe;
- this profile proves one intermediate and one local receiver recreation. It
  does not certify arbitrary customer path length, name constraints, cross-
  signing, CRL distribution availability, multi-replica disruption budgets,
  HSM-backed issuer operation, OCSP, or customer-specific rotation cadence.

## Revisit triggers

Revisit when measured customer rollout availability requires hot SSL-context
replacement, when a selected PKI requires OCSP or cross-signed chains, or when
customer qualification demonstrates a chain or constraint shape not covered
by the one-intermediate profile.
