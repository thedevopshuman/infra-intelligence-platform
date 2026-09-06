# ADR 0140: Aggregate customer AI FinOps prerequisites without claiming live flow

**Status:** Accepted

**Date:** 2026-09-08

## Context

The platform has separate evidence for local AI FinOps behavior, an installed
customer release, customer Collector delivery, one live Bedrock call, and an
approved production price catalog. Those reports are independently useful but
can be stale, crossed between releases or environments, or interpreted as proof
that one provider invocation traversed the complete deployed path. They do not
share an invocation identifier, and the live Bedrock qualifier intentionally
does not send its span through the selected customer Collector and deployed
ledger.

Copying provider targets, customer endpoints, tenant identifiers, prices, or
credentials into an aggregate would also turn transportable prerequisite
evidence into an unnecessary data export.

## Decision

Add a protected `CustomerAiFinopsPrerequisiteProfile` and a minimized
`CustomerAiFinopsPrerequisiteReport`.

The profile selects one exact release, environment, streaming Bedrock
collection profile, production catalog and qualification policy, and the
Prometheus/Grafana V0 presentation profile. It is an operator-controlled input,
must be owned by the invoking user, and must have mode `0600`.

The qualifier consumes exactly six reports:

1. local release readiness;
2. local AI FinOps runtime compatibility;
3. customer deployment qualification;
4. customer OTLP receiver qualification;
5. customer Bedrock live qualification; and
6. production AI price-catalog qualification.

It validates every source schema and status, requires current evidence, binds
source-controlled reports to one clean revision, cross-checks release and image
identity, proves that release readiness names the exact runtime report, proves
that customer deployment names the exact receiver report and its target
bindings, and binds the selected production catalog and policy. The retained
report contains only content digests, pseudonymous binding digests, bounded
counts, stable checks, timestamps, and fixed limitations. Its identifier is
derived from the complete retained content.

The qualification boundary is permanently
`prerequisite-aggregation-only`. Even a `prerequisites-ready` result must retain
limitations stating that the same live invocation, Bedrock-to-customer-
Collector delivery, deployed catalog secret binding, long-running Collector
configuration, non-Prometheus dashboard portability, invoice/private pricing,
and sustained regional availability are not qualified.

## Consequences

- Release operators get one fail-closed inventory of the evidence required
  before attempting the true customer AI FinOps V0 flow.
- A stale, altered, missing, cross-release, cross-environment, test-price, or
  incomplete report produces `not-ready`; it is never silently omitted.
- Provider targets, region, model, customer endpoint, tenant, rates, token
  quantities, content, and credentials are absent from the transportable
  report.
- This aggregate does not close the Phase A exit gate. A subsequent,
  separately designed qualification must prove that one live Bedrock
  invocation traveled through the selected customer Collector, deployed
  receiver, usage ledger, cost engine, aggregate export, and Grafana view.
- Prometheus/Grafana is the certified V0 presentation profile; backend-neutral
  collection and domain contracts do not imply that arbitrary dashboard query
  backends are already qualified.
