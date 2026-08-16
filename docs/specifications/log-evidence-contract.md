# Log evidence request and result contracts

**Status:** v1alpha1

**Machine contracts:**

- `contracts/schemas/log-evidence-request.schema.json`
- `contracts/schemas/log-evidence-result.schema.json`

These contracts define a bounded, backend-neutral historical log query for already-known tenant resources. They deliberately exclude LogQL, provider URLs, index names, raw search expressions, credentials, and client options. A selected adapter translates the normalized request to its configured backend and returns records through the application-owned `TelemetryLogsBackend` port.

They do not turn OTLP into a query protocol. The separately authenticated `/v1/logs` receiver accepts selected push data and produces `OtlpLogsEvidence`; historical querying of an existing customer backend still requires an adapter behind this contract.

## Identity and authority

Authenticated tenant and actor identity is authoritative. Request identity fields are consistency assertions, and every `resourceRef` must resolve inside that tenant before a backend runs. The integration ID selects protected configuration but conveys no endpoint or credential.

The query requires one or more logical service names. Normalized severities and exact attribute equality/inequality filters are optional upper bounds. `neq` requires the attribute to exist and differ; a missing attribute does not match. A backend must implement these exact semantics or fail closed rather than silently broadening the query.

The closed time range is limited to seven days and must end no later than `requestedAt`. The deadline is limited to five minutes after request time. Repeating a query creates new immutable Evidence because late or corrected log records may change the result.

## Normalized records

Each result record has an opaque deterministic `log_` identifier, exactly one requested resource, timestamp, logical service, normalized severity, bounded UTF-8 body, bounded string attributes, and optional trace/span correlation. Trace and span IDs appear together or not at all. Records are unique and sorted by timestamp and ID.

`complete` requires records and no warning. `partial` requires records and a stable `backend-partial` or `record-limit` warning. `no-data` requires no records and no warning. Summary counts must match the normalized records.

## Privacy and redaction

Log bodies are untrusted and may contain credentials, personal data, or prompt-injection text. They are marked confidential by the reference query service and pass the shared structured redactor before hashing or persistence. Attribute names and values are allowlisted/bounded by adapters; secret-shaped attribute values fail closed. Provider response bodies and exception text never reach the public error surface.

The immutable Evidence envelope uses type `telemetry.logs`, ephemeral retention, exact resource references, request digest, and redaction metadata. Reports and future agents must cite the Evidence ID and read only the committed tenant-scoped artifact, never raw adapter output.

## HTTP and SDK binding

`POST /v1/evidence/logs/queries` accepts `LogEvidenceRequest` and returns the committed Evidence envelope with `201 Created`. The normalized result remains in the evidence store.

| HTTP status | Stable code | Meaning |
| --- | --- | --- |
| `400` | `logs.request.invalid` | Contract, identity, scope, filter, time, or limit validation failed. |
| `401` | `authentication.required` / `authentication.invalid` | Interactive Bearer authentication failed. |
| `403` | `policy.denied` | The actor cannot collect the scoped evidence. |
| `408` | `evidence.deadline.exceeded` | The bounded collection deadline elapsed. |
| `503` | `evidence.provider.unavailable` / `storage.unavailable` | Backend, normalization/redaction, or storage failed closed. |

Python and TypeScript SDKs expose the public request/result types and collection operation. The default runtime returns honest `no-data`; a production historical log adapter remains an explicit integration unit.
