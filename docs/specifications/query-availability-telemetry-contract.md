# Query availability telemetry contract

**Status:** v1alpha1 semantic convention

This contract defines backend-neutral OTLP metrics for recognized control-plane `GET` queries. It is an operational telemetry contract rather than an HTTP resource, so the machine envelope is OpenTelemetry Metrics protobuf instead of a repository JSON Schema.

## Metrics

| Metric | Instrument | Unit | Value |
| --- | --- | --- | --- |
| `iip.query.requests` | monotonic counter | `1` | `1` per completed recognized query |
| `iip.query.duration` | histogram | `s` | elapsed monotonic serving time |
| `iip.telemetry.record.failures` | monotonic counter | `1` | local metric-recording failures |

Every query counter and duration observation has exactly these bounded attributes:

| Attribute | Type | Values |
| --- | --- | --- |
| `iip.query.operation` | string | one closed operation listed below |
| `iip.query.outcome` | string | `success`, `not-found`, `conflict`, `invalid`, `unauthenticated`, `denied`, `unavailable`, or `internal-error` |
| `iip.query.availability` | string | `available`, `unavailable`, or `excluded` |
| `iip.query.objective.window_seconds` | integer | deployment-configured rolling window |
| `iip.query.objective.minimum_availability_basis_points` | integer | deployment-configured target |
| `iip.query.objective.minimum_eligible_requests` | integer | deployment-configured sample floor |

The closed operations are `console-authentication`, `session`, `runtime-version`, `resources-list`, `resource-neighborhood`, `resource-timeline`, `ingestion-freshness`, `telemetry-export-health`, `telemetry-deployment-export-health`, `telemetry-export-slo`, `telemetry-export-burn-rate`, `collector-queue-loss`, `event-delivery-health`, `event-delivery-slo`, `investigation-completion-slo`, `evidence-retention`, `evidence-get`, `investigation-get`, `investigation-status`, `investigation-job-get`, `actions-list`, `action-get`, `action-workflow-get`, `plugin-session-get`, and `plugin-invocation-status`.

## Eligibility and arithmetic

- `invalid`, `unauthenticated`, and `denied` outcomes are `excluded`.
- `success`, `not-found`, and `conflict` outcomes are `available`.
- dependency/service failures are `unavailable`; an uncaught handler failure is `internal-error` and `unavailable`.
- unknown routes and non-query surfaces emit no observation.

For a configured window, the eligible denominator is the sum of `available` and `unavailable` request counts. Availability basis points are `floor(available * 10000 / eligible)`. A backend reports insufficient data until the eligible count reaches `minimum_eligible_requests`, then compares the result with `minimum_availability_basis_points`.

Metric recording and exporter delivery cannot change the HTTP response, roll back a query, or alter readiness. Raw paths, query values, numeric status codes, error text, tenant/actor identity, credentials, resource/investigation/action/evidence identifiers, bodies, and provider output are prohibited attributes.
