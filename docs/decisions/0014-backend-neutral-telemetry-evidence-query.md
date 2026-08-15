# ADR 0014: Normalize historical metric queries behind a telemetry evidence port

**Status:** Accepted

**Date:** 2026-08-15

## Context

[ADR 0012](0012-opentelemetry-portability-boundary.md) distinguishes OTLP transport from historical backend queries. Investigations need metric evidence already retained in customer-selected systems, but PromQL or a commercial query model in the application contract would make that system an architectural dependency. Accepting arbitrary query text would also weaken tenant enforcement, cardinality bounds, secret controls, and replay semantics.

## Decision

- Introduce versioned `TelemetryEvidenceRequest` and `TelemetryEvidenceResult` contracts for the first supported signal, metrics.
- Define a closed provider-neutral selector: metric identity, equality/inequality attribute filters, aggregation, step, group-by attributes, time range, and explicit output/deadline bounds.
- Keep authenticated actor/tenant context authoritative. Payload identity fields are consistency assertions, and every resource resolves in that tenant before backend execution.
- Define `TelemetryMetricsBackend` in application ports. Vendor query languages, endpoints, SDKs, and credential resolution belong in adapters selected by `bootstrap.py`.
- Validate normalized backend results in the application layer before creating an artifact. Reject mismatched scope, non-finite or unordered points, out-of-range timestamps, excess cardinality/bytes, unsafe text, inconsistent counts, and unsupported warning/status combinations.
- Store the normalized result through the existing Evidence pipeline so redaction, content hashing, immutable persistence, provenance, retention, and tenant checks are reused.
- Return only the Evidence envelope from the HTTP collection operation. Artifact bytes remain behind evidence access policy.
- Use a no-data backend for the default reference runtime. It demonstrates the replaceable boundary without manufacturing customer telemetry.
- Keep inbound OTLP reception separate. A receiver requires authenticated channel-to-tenant binding, admission limits, and retention policy and may later normalize accepted metrics to the same result contract.

## Consequences

- Investigations and SDKs can request a stable metric evidence shape while customers change storage backends by changing adapter composition.
- The initial selector is deliberately less expressive than PromQL or vendor APIs. Unsupported semantics fail explicitly rather than being silently translated.
- A production backend adapter and credential broker remain required before real customer metrics can be queried.
- Logs and traces are not accepted by the v1alpha1 request; they require their own minimization and redaction decisions.
- The normalized artifact is replayable and hash-bound to the complete request, but live backend results may naturally differ across execution times.

## Revisit triggers

Revisit when the first design partner selects a backend, when equality filters cannot express a required investigation, when logs or traces are added, when an OTLP receiver is implemented, or when measured workloads require different cardinality and time-window maxima.
