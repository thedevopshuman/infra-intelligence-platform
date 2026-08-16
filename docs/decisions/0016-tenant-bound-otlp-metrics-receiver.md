# ADR 0016: Accept selected OTLP metrics through tenant-bound evidence channels

**Status:** Accepted

**Date:** 2026-08-16

## Context

ADR 0012 keeps OpenTelemetry interchange separate from storage/query authority. ADR 0013 implements outbound platform metrics, while ADRs 0014 and 0015 implement bounded historical queries against customer-selected backends. A customer may instead choose to push a selected metric stream into the platform. That flow needs channel authentication, tenancy, admission, normalization, and retention semantics that neither the control-plane authenticator nor historical query contract provides.

## Decision

- Add an optional, disabled-by-default OTLP/HTTP binary Protobuf metrics endpoint at `/v1/metrics`.
- Authenticate it with a separate Bearer channel registry containing only SHA-256 token verifiers. Bind tenant, integration, fixed resource references, metric/attribute catalogs, limits, sensitivity, and retention exclusively from protected configuration.
- Never treat OTLP attributes as tenant, integration, resource, credential, or policy authority.
- Support bounded gauges and numeric delta/cumulative sums initially. Reject unsupported metric kinds or semantics atomically instead of silently dropping or coercing them.
- Support `identity` and bounded `gzip`; cap both request and decompressed bytes and all normalized cardinality/time dimensions.
- Normalize accepted input into `OtlpMetricsEvidence`, then use the existing Evidence policy, resource-resolution, redaction, hashing, and immutable persistence path.
- Return standard binary OTLP success/failure messages. Empty exports are successful no-ops; partial success is not used in this version.
- Keep the receiver on the existing reference HTTP process/Service. Deployments may isolate it behind a dedicated gateway or process later without changing application contracts.

## Consequences

- Standard OTLP/HTTP exporters can send selected metrics without choosing the platform's future telemetry storage backend.
- A leaked interactive API token cannot authenticate the receiver, and a payload cannot escape its configured tenant.
- The first implementation is deliberately not a general OTLP backend: traces, logs, histograms, arbitrary attributes, unbounded retention, querying, and forwarding are absent.
- Runtime operators must provision channel secrets and a metric catalog before enabling the endpoint. Token rotation currently requires overlapping channel entries or a configuration rollout.
- Microsecond timestamp normalization can reject distinct nanosecond samples that collide after normalization; no sample is silently overwritten.

## Revisit triggers

Revisit when traces/logs or histogram evidence are required, a durable ingest queue is selected, managed deployments require workload identity or mTLS, receiver isolation needs a dedicated listener, partial acceptance becomes necessary, or retention/data-residency requirements select a telemetry store/forwarding topology.
