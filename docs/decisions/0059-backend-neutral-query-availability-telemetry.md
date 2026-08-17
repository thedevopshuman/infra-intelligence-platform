# ADR 0059: Backend-neutral query-availability telemetry

**Status:** Accepted

## Context

The control plane exposes authenticated read operations but does not yet produce a measured query-availability signal. Inferring availability from application logs would couple operators to text formats and risk leaking request identity. Storing request observations in the primary PostgreSQL database would also become blind during the database outages that the signal must capture.

## Decision

Measure recognized `GET` query operations at the HTTP response boundary and offer one bounded observation to an application-owned telemetry sink. Export two standard OTLP metrics: a request counter and a duration histogram. Use only a closed operation label, a closed semantic outcome, the availability classification, and deployment-owned objective values. Never export raw paths, query parameters, status/error text, tenant or actor identity, credentials, resource or investigation identifiers, evidence, request bodies, or response bodies.

Classify syntactically invalid, unauthenticated, and policy-denied requests as `excluded`; they remain observable but do not enter the availability denominator. Classify a successfully served result, including a valid not-found or conflict response, as `available`. Classify server errors, dependency-unavailable responses, and uncaught handler failures as `unavailable`. Health, readiness, static console assets, mutation endpoints, OTLP receiver endpoints, and unknown routes are outside this query SLI.

The default deployment objective is 99.90% availability over a 3,600-second window after at least 100 eligible requests. Carry those objective values as bounded metric attributes so a customer-controlled OpenTelemetry Collector or downstream backend can aggregate and alert without application-specific configuration discovery. Recording and export remain failure-isolated from the customer query and from readiness.

## Consequences

- changing the telemetry backend or Collector exporters does not change query code or SLI semantics;
- client mistakes and authorization denials cannot consume the service error budget;
- valid not-found results demonstrate that the query path was available;
- stable operation labels bound cardinality and do not disclose customer identity;
- process crashes, network paths before the process, and total loss of all replicas require an external ingress or synthetic availability signal;
- long-term/regional aggregation, burn-rate policy, and notification routing remain deployment decisions.
