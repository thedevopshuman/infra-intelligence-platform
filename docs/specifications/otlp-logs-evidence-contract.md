# OTLP logs evidence contract

**Status:** v1alpha1

**Machine contract:** `contracts/schemas/otlp-logs-evidence.schema.json`

`OtlpLogsEvidence` is the normalized artifact produced after a separately authenticated OTLP/HTTP Protobuf logs channel passes admission, tenancy, resource, service, cardinality, time, and redaction controls. It is not accepted from the interactive control-plane API and is never trusted as authority for tenant or resource scope.

## Channel authority

Protected channel configuration binds a SHA-256 token verifier to exactly one tenant, integration, existing resource, service catalog, attribute mapping, handling policy, and limits. Payload `service.name` selects an allowlisted service profile; other resource, scope, and record attributes are ordinary untrusted values. They are retained only when the profile explicitly maps them.

One channel is bound to one resource in this version so a payload cannot self-assert per-record resource identity. Customers needing multiple resources provision separate channel identities or a trusted gateway that routes to distinct channels.

## Supported OTLP profile

The endpoint accepts `ExportLogsServiceRequest` at `POST /v1/logs` with `application/x-protobuf` and `identity` or bounded `gzip`. Empty exports are successful no-ops. Non-empty exports accept:

- string log bodies only;
- OTLP severity numbers normalized to trace, debug, info, warn, error, fatal, or unspecified;
- required event timestamps and optional observed timestamps;
- optional complete 16-byte trace and 8-byte span correlation; and
- mapped scalar string, boolean, integer, or finite-double attributes.

Severity text, instrumentation metadata, schema URLs, and unmapped attributes are intentionally not persisted. Event-name records, complex bodies or attributes, dropped attributes, unknown services, ambiguous mapped values, malformed correlation, invalid time, excess cardinality/bytes, duplicate normalized records, and unsupported compression fail the entire export.

## Artifact semantics

Records use the same normalized shape as historical log results and are unique and ordered by timestamp and ID. The artifact declares its exact signal/protocol, min/max event time, service/record/error counts, and channel metadata. It is stored behind an Evidence envelope with type `telemetry.logs.push`; body redaction occurs before the artifact digest and persistence.

OTLP is an interchangeable push boundary, not a storage authority or historical query API. A customer can change the downstream system by reconfiguring its Collector routing, while this receiver remains an optional selected evidence intake.
