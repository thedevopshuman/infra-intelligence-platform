# OTLP receiver compatibility report

**Status:** v1alpha1 operational evidence

`OtlpReceiverCompatibilityReport` is source-bound evidence that the isolated
receiver passed the local production compatibility profile. It records the
source revision and dirty state, runtime versions, fixed transport/identity and
durability semantics, exact ordered checks, and a derived summary.

The profile proves route isolation, CA-verified server TLS, plaintext denial,
client-CA validation, exact SPIFFE URI SAN and channel binding, independent
Bearer authentication, control-credential denial, official metrics and logs
exporters, certificate rotation without restart, minimized health probes,
transactional PostgreSQL evidence persistence, syntactic validation of the
durable Collector sending-queue example, and secret redaction. A compatible
report has all 15 checks passed.

The report contains no endpoint, token, tenant, resource, certificate,
certificate subject, SPIFFE ID, payload, or database address. It qualifies the
exact source and local Docker profile only. A customer must separately qualify
its PKI, Collector storage, queue capacity, network path, certificate rotation,
database durability, and operational alerts.
