# OTLP receiver compatibility report

**Status:** v1alpha1 operational evidence

`OtlpReceiverCompatibilityReport` is source-bound evidence that the isolated
receiver passed the local production compatibility profile. It records the
source revision and dirty state, runtime versions, fixed transport/identity and
durability semantics, exact ordered checks, and a derived summary.

The profile proves route isolation, CA-verified server TLS, plaintext denial,
client-CA validation, exact SPIFFE URI SAN and channel binding, independent
Bearer authentication, control-credential denial, official metrics and logs
exporters, certificate rotation without restart, rejection of an otherwise-
trusted but expired client certificate, rejection of an otherwise-trusted and
unexpired but explicitly revoked (CRL) client certificate, minimized health
probes, transactional PostgreSQL evidence persistence, syntactic validation of
the durable Collector sending-queue example, and secret redaction. A
compatible report has all 17 checks passed. See [ADR 0085](../decisions/0085-otlp-receiver-expired-certificate-rejection-evidence.md)
and [ADR 0088](../decisions/0088-otlp-receiver-revoked-certificate-rejection-evidence.md).

The report contains no endpoint, token, tenant, resource, certificate,
certificate subject, SPIFFE ID, payload, or database address. It qualifies the
exact source and local Docker profile only. A customer must separately qualify
its PKI, Collector storage, queue capacity, network path, certificate rotation
propagation, CRL distribution/freshness (OCSP is not checked), database
durability, and operational alerts.
