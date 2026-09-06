# ADR 0134: Qualify customer OTLP receiver interoperability with a pinned Collector

**Status:** Accepted
**Date:** 2026-09-08

## Context

The repository-owned Docker profile proves the isolated receiver, official
language exporters, PostgreSQL commit boundary, intermediate client chain,
revocation, and Collector queue configuration locally. The customer processing
continuity gate sends a non-empty metric directly to an installed receiver.
Neither result proves that an official Collector can deliver metrics, logs, and
metadata-only GenAI traces across the selected customer network and mTLS
boundary to the exact installed release.

Running arbitrary customer Collector configuration from a release tool would
import unreviewed processors, extensions, exporters, and credentials into the
qualification authority. Merely receiving an HTTP success from a Collector's
input would also be weak evidence: that response precedes asynchronous export
and says nothing about the downstream receiver.

## Decision

Add protected `CustomerOtlpReceiverQualificationProfile` and minimized
`CustomerOtlpReceiverQualificationReport` contracts with the
`customer-pinned-collector-to-iip-receiver-v1` qualification.

1. The profile fixes the receiver endpoint, exact metric/log/GenAI metadata
   accepted by its protected channel catalogs, bounded delivery objectives,
   and the digest-pinned OpenTelemetry Collector Contrib `0.158.0` image.
2. The qualifier reads one control-plane credential and three signal-channel
   credentials from separate mode-`0600` files and requires all four values to
   be pairwise distinct. It
   reads the receiver CA, client certificate, and mode-`0600` client key from
   separate files. No credential, key, or credential digest is retained.
3. Direct verified HTTPS to `/v1/system/version` binds the API endpoint to the
   exact clean source revision, application/chart versions, migration, and
   immutable image.
4. The Collector's HTTP client does not expose a redirect-denial setting. The
   host qualifier therefore sends an empty authenticated mTLS OTLP request
   first to each signal path through a no-proxy, redirect-denying client and
   requires direct success. The IIP receiver treats those valid empty exports
   as no-ops, so the guard creates no duplicate Evidence or usage records.
5. A short-lived, non-root, read-only Collector container receives the same
   three synthetic OTLP/HTTP Protobuf requests only on a random host-loopback
   port. Its three exporters use separate Bearer credentials, one customer
   client certificate, verified TLS, no ambient proxy, and an ephemeral
   host-backed `file_storage` sending queue. No shell is used.
6. The qualifier does not infer delivery from Collector intake success. It
   snapshots the Collector's own exporter sent, send-failed, and queue metrics,
   then requires one delivered item per signal, zero send failures, and drained
   queues within the objective. IIP returns OTLP success only after the
   normalized record or Evidence artifact commits to PostgreSQL, so the sent
   counters cross the receiver durability boundary.
7. The GenAI span contains provider/model/operation/region, instrumentation,
   and token counts only. Prompt, response, tool-call, header, and body
   attributes are absent. The log body is a fixed qualification sentinel.
8. The report retains only source/release identity, hashes of non-secret target
   and trust inputs, counts, timing, fixed behavior, stable checks, and explicit
   limitations. It contains no endpoint, service, model, provider, signal
   credential, private key, or payload value.
9. Offline verification rebinds the report to the same clean source, profile,
   API/receiver targets, trust bundles, public client certificate, and image.

## Consequences

- A customer can prove all three supported intake paths with the same official
  Collector distribution used by the shipped reference configuration.
- An exporter retry that eventually succeeds still fails the zero-send-failure
  check; the narrow qualification is deliberately loss-intolerant.
- The ephemeral profile does not certify the customer's long-running Collector
  configuration, storage sizing, restart recovery, application fail-open
  instrumentation, sustained throughput, PKI lifecycle, or regional topology.
- Those broader outcomes remain separate production and design-partner gates.
