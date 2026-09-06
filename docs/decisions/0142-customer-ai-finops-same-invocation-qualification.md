# ADR 0142: Customer AI FinOps same-invocation qualification

**Status:** Accepted
**Date:** 2026-09-08

## Context

The customer AI FinOps prerequisite report binds independently qualified
release, deployment, Collector, Bedrock, and pricing evidence. ADR 0141 adds a
privileged lookup for one exact trace/span pair. Neither artifact proves that
one live provider invocation traversed the selected customer Collector, became
one immutable usage fact under the deployed tenant, reached the active
attribution and pricing generations, appeared in the exported aggregate, and
was usable by the provisioned dashboard.

That final proof needs short-lived trace identity, provider credentials, and
customer endpoints. Retaining those values in portable release evidence would
violate the metadata-minimization boundary. Labeling trace identity in
Prometheus or Grafana would also create a high-cardinality disclosure surface
and couple qualification to one telemetry backend.

## Decision

Add a host-side, explicitly enabled customer qualification harness for one
fixed AWS Bedrock `ConverseStream` request. The request remains direct to
Bedrock. The pinned OpenTelemetry instrumentation and metadata-only adapter
export asynchronously to the selected OTLP/HTTP trace endpoint; IIP is never an
inference proxy and exporter failure cannot become provider-call failure.

The harness requires current exact customer AI FinOps prerequisite evidence,
the same reviewed Bedrock profile, a clean matching source revision, separate
owner-only credential/header files, and verified HTTPS targets. It then:

1. records the protected-application Prometheus counter before the call;
2. verifies the expected Grafana dashboard and required V0 panels;
3. makes exactly one explicitly authorized live provider call;
4. receives the owner-only trace/span correlation produced by that call;
5. polls the privileged tenant-scoped observation operation;
6. requires one exact usage record, expected application/team attribution, and
   calculated cost under the production-qualified catalog document;
7. requires the protected-application aggregate counter to advance; and
8. emits protected run evidence plus a minimized transportable report.

The protected run evidence and retained live response contain correlation or
customer-scoped facts and are written mode `0600`. The transportable report
contains only release identity, canonical document and target digests, record
digests, bounded counts/timing, closed checks, and fixed limitations. It never
contains tenant, environment, endpoint, model, region, application/team,
trace/span, token quantity, rate, amount, credential, prompt, or response
values.

The report binds the exact active attribution-policy and price-catalog
documents returned by the serving observation boundary. Prometheus establishes
only a protected-dimension aggregate delta. Grafana qualification establishes
the provisioned dashboard definition and required panels; it does not turn the
dashboard into an exact-trace store.

## Consequences

- Phase A gains an executable customer-environment same-invocation gate without
  a proxy or an IIP instrumentation SDK.
- The live command is intentionally billable and requires an explicit enable
  flag. It is not part of `make verify`.
- Verification reloads every protected source, exact runtime artifact, and the
  current clean source revision; a copied or independently edited report fails.
- Trace/span correlation, the exact observation, and live compatibility output
  remain protected operational evidence and should be deleted after the
  minimized report is verified and retained according to customer policy.
- A qualified report is narrow evidence for one invocation and one aggregate
  delta. It is not invoice agreement, sustained-load evidence, customer
  Collector/backend lifecycle proof, or node/zone/region availability.
