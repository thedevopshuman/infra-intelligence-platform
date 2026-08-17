# OTLP receiver availability telemetry contract

**Status:** v1alpha1 semantic convention

This contract defines backend-neutral OTLP metrics for completed IIP OTLP/HTTP
metrics and logs intake requests. It is an operational telemetry contract, so
the machine envelope is OpenTelemetry Metrics protobuf rather than a repository
JSON Schema.

## Metrics

| Metric | Instrument | Unit | Value |
| --- | --- | --- | --- |
| `iip.otlp.receiver.requests` | monotonic counter | `1` | `1` per completed request handled by the receiver |
| `iip.otlp.receiver.duration` | histogram | `s` | elapsed monotonic HTTP serving time |
| `iip.telemetry.record.failures` | monotonic counter | `1` | local metric-recording failures tagged with instrument `otlp-receiver-availability` |

Every request counter and duration observation has exactly these bounded
attributes:

| Attribute | Type | Values |
| --- | --- | --- |
| `iip.otlp.receiver.signal` | string | `metrics` or `logs` |
| `iip.otlp.receiver.outcome` | string | `success`, `invalid`, `unauthenticated`, `denied`, `disabled`, `rate-limited`, `unavailable`, or `internal-error` |
| `iip.otlp.receiver.availability` | string | `available`, `unavailable`, or `excluded` |
| `iip.otlp.receiver.objective.window_seconds` | integer | deployment-configured rolling window |
| `iip.otlp.receiver.objective.minimum_availability_basis_points` | integer | deployment-configured target |
| `iip.otlp.receiver.objective.minimum_eligible_requests` | integer | deployment-configured sample floor |

## Eligibility and arithmetic

- completed `2xx` requests are `success` and `available`; success means the
  normalized Evidence transaction committed before the response;
- `429` is `rate-limited` and `unavailable` because an authenticated request was
  rejected by platform capacity;
- `502`, `503`, and `504` are `unavailable`; other `5xx` responses or uncaught
  handler/write failures are `internal-error`; all are `unavailable`;
- `400`, `413`, and `415` are `invalid`, `401` is `unauthenticated`, `403` is
  `denied`, and `404` is `disabled`; all are `excluded`.

For a configured window, the eligible denominator is the sum of `available` and
`unavailable` request counts. Availability basis points are
`floor(available * 10000 / eligible)`. A backend reports insufficient data until
the eligible count reaches `minimum_eligible_requests`, then compares the result
with `minimum_availability_basis_points`.

Metric recording and exporter delivery cannot change the OTLP response, roll
back Evidence, or alter readiness. Tenant/channel identity, SPIFFE IDs,
certificates, credentials, request/payload content, endpoints, raw paths,
numeric status codes, error/provider text, and Evidence identifiers are
prohibited attributes.

TLS handshake failures occur before HTTP measurement and therefore require
Collector, ingress, or synthetic telemetry. The configured exporter destination
must be a customer-controlled Collector or compatible backend endpoint, never
the IIP intake listener itself.
