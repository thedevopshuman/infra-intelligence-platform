# Customer OTLP receiver qualification contracts

**Status:** `v1alpha1` executable customer-environment evidence

**Decision:** [ADR 0134](../decisions/0134-customer-otlp-receiver-interoperability-qualification.md)

**Machine contracts:**
`contracts/schemas/customer-otlp-receiver-qualification-profile.schema.json`
and
`contracts/schemas/customer-otlp-receiver-qualification-report.schema.json`

These contracts qualify one exact customer IIP OTLP endpoint with a
digest-pinned official OpenTelemetry Collector. They are host-side release
artifacts, not control-plane resources, and therefore do not add an OpenAPI
route.

## Protected profile and inputs

`CustomerOtlpReceiverQualificationProfile` is a mode-`0600` reviewed input. It
contains the selected root receiver endpoint, fixed Collector distribution and
image, exact allowlisted metric/log/GenAI identities, and request/delivery
objectives. It must not contain credentials or content fields.

The API credential and metrics, logs, and traces channel credentials are four
separate mode-`0600` files. All four values must be pairwise distinct.
The receiver CA, client certificate, and mode-`0600` private key are separate
inputs. The API may use system trust or its own CA file. No credential, private
key, or digest of either is retained.

## Qualification behavior

The qualifier first verifies `/v1/system/version` through direct HTTPS. The
pinned Collector HTTP client has no redirect-denial setting, so the qualifier
also sends an empty authenticated mTLS OTLP request through its own no-proxy,
redirect-denying client to each signal path and requires direct acknowledgement
without creating an artifact. It then validates and runs the pinned Collector
with three pipelines and submits one synthetic Protobuf item per signal.
Collector input acceptance is measured separately from downstream delivery.

Qualification requires the Collector's own exact-exporter self-metrics to show:

- at least one sent metric point, log record, and span above the baseline;
- no send-failed increment for any signal;
- all three exporter queues drained; and
- delivery within the reviewed latency objective.

The customer IIP receiver acknowledges metrics/logs only after Evidence and its
artifact commit transactionally, and acknowledges GenAI traces only after the
normalized usage record commits. Consequently, a Collector sent counter is
evidence of crossing the IIP durability boundary, not merely entering the
Collector.

The GenAI probe contains only approved metadata and two token counts. The log
uses one fixed non-sensitive sentinel. Qualification traffic remains real
customer data under the selected channel's configured retention policy.

## Minimized report

The `corq_` identifier is content-derived. The report contains 20 ordered
checks, derived direct and Collector counts/status, exact source and release identity, and digests
of the API target, receiver target, protected profile, signal set, API and
receiver trust bundles, and public client certificate. It rejects target,
service, model, provider, region, instrumentation, credential, private-key, and
payload fields.

Semantic validation recomputes check shape, status, summary, time/profile-age
relationships, delivery counts, failed-send and queue conditions, latency, and
the report identifier. Offline verification recomputes every external binding.

`qualified` does not cover a customer's long-running Collector configuration,
application fail-open behavior, queue/disk sizing and restart recovery,
sustained throughput, PKI/CRL/OCSP operations, or node/zone/region failures.
