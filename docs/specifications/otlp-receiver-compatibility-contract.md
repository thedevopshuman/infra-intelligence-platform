# OTLP receiver compatibility report

**Status:** v1alpha1 operational evidence

`OtlpReceiverCompatibilityReport` is source-bound evidence that the isolated
receiver passed the local production compatibility profile. It records the
source revision and dirty state, runtime versions, fixed transport/identity and
durability semantics, exact ordered checks, and a derived summary.

The profile proves route isolation, CA-verified server TLS, plaintext denial,
client-CA validation, a root/intermediate/leaf client-certificate hierarchy,
exact SPIFFE URI SAN and channel binding, independent Bearer authentication,
control-credential denial, official metrics and logs exporters, certificate
rotation without restart, rejection of an otherwise-trusted but expired client
certificate, rejection of an otherwise-trusted and unexpired but explicitly
revoked (CRL) client certificate, minimized health probes, fail-closed rejection
of an expired configured CRL, activation of a newer current CRL through an
atomic projected-file change plus receiver rollout, transactional PostgreSQL
evidence persistence, syntactic validation of the durable Collector sending-
queue example, and secret redaction. A compatible report has all 20 checks
passed. See [ADR 0085](../decisions/0085-otlp-receiver-expired-certificate-rejection-evidence.md),
[ADR 0088](../decisions/0088-otlp-receiver-revoked-certificate-rejection-evidence.md),
[ADR 0089](../decisions/0089-fail-closed-otlp-client-crl-freshness.md), and
[ADR 0090](../decisions/0090-intermediate-ca-and-otlp-crl-rollout-evidence.md).

The closed profile records `clientCertificateChain` as
`root-intermediate-leaf`: the compatibility client presents its leaf and one
issuing intermediate, the root-only handshake test rejects the same leaf when
the intermediate is omitted, and the receiver profile validates the
intermediate-signed CRL against the configured CA bundle. `crlRotation` is
`projected-file-receiver-rollout`: the gate first proves a current client is
accepted, atomically promotes a newer CRL that revokes only that client,
recreates only the receiver, proves the newly revoked client is rejected, and
proves an unaffected client remains accepted.

The report contains no endpoint, token, tenant, resource, certificate,
certificate subject, SPIFFE ID, payload, or database address. It qualifies the
exact source, one-intermediate hierarchy, and local Docker rollout profile only.
A customer must separately qualify its PKI constraints and actual chain depth,
Collector storage, queue capacity, network path, certificate rotation
propagation, CRL publisher/distribution path and pre-expiry cadence (OCSP is not
checked), database durability, rollout availability, and operational alerts.
