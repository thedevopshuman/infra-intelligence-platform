# ADR 0023: Normalize log evidence and accept selected OTLP logs separately

**Status:** Accepted

**Date:** 2026-08-16

## Context

Investigations need logs, but exposing LogQL, vendor endpoints, arbitrary text search, or raw provider payloads would weaken portability, tenant enforcement, output bounds, and secret minimization. ADR 0012 already distinguishes OTLP interchange from historical backend queries, and ADR 0016 proves a tenant-bound metrics push receiver. Logs carry materially higher privacy and prompt-injection risk than numeric metrics and require their own contract.

## Decision

- Add versioned `LogEvidenceRequest` and `LogEvidenceResult` contracts for historical queries through an application-owned `TelemetryLogsBackend` port.
- Use a closed selector containing logical service names, normalized severities, exact attribute equality/inequality filters, resource/time scope, and explicit record/byte/deadline limits. Do not accept vendor query text.
- Validate every backend record for request scope, time, service, severity, filters, identity, order, attributes, trace correlation, status, warnings, and size before Evidence persistence.
- Treat bodies as confidential untrusted text and apply the shared redactor before hashing. Secret-shaped attributes fail closed; backend response and exception text become stable platform errors.
- Keep the default historical backend honest `no-data`. A production backend adapter is selected later without changing the public contract.
- Add a separate optional OTLP/HTTP Protobuf `/v1/logs` receiver using independently configured channel credentials, never interactive API credentials.
- Bind each logs channel to one tenant, integration, existing resource, service catalog, attribute mapping, handling policy, and admission budget. Payload attributes never grant authority.
- Accept only string bodies, normalized severity numbers, mapped scalar attributes, bounded timestamps, and optional complete trace/span correlation. Fail exports atomically on unsupported or ambiguous data.
- Store non-empty push batches as `OtlpLogsEvidence` behind the existing Evidence boundary. Empty valid exports are successful no-ops.
- Do not add investigation log selection in this unit. Multi-signal planning must declare its own applicability, budget order, and committed-artifact interpretation rules.

## Consequences

- Public callers and SDKs can request portable bounded log evidence while storage/query products remain adapters.
- Customers can send a selected log stream through an OpenTelemetry Collector and change its other destinations without changing platform domain logic.
- The receiver is intentionally not a general log lake: it has no arbitrary tenancy, resource routing, search, long retention, or forwarding.
- Logs receive stricter default sensitivity and require redaction, retention, data-residency, and prompt-injection review before production expansion.
- A real historical backend adapter, receiver workload identity/isolation, durable buffering, investigation selection, and design-partner privacy policy remain future gates.

## Revisit triggers

Revisit when a design partner selects its log backend, body search is required, one channel must represent multiple resources, structured non-string bodies are needed, Collector workload identity replaces channel tokens, or investigation scenarios define required log correlation semantics.
