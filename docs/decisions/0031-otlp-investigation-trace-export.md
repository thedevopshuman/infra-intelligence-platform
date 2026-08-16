# ADR 0031: Export investigation execution as bounded OTLP traces

**Status:** Accepted

**Date:** 2026-08-17

## Context

Operators need to understand investigation latency, outcome, and budget consumption in the telemetry backend they already operate. The durable investigation report is the product record, while OpenTelemetry is a replaceable delivery path. Exporting prompts, evidence content, resource details, provider errors, or credentials would create a new sensitive-data and authority surface.

## Decision

- Offer terminal investigation facts through an application-owned `InvestigationTelemetrySink`; OpenTelemetry types remain in the adapter and composition layers.
- Emit one `iip.investigation.execute` span only after the terminal report and lifecycle status commit successfully. A replay of the same report does not emit another span.
- Derive span start/end time, outcome, terminal reason, tool count, evidence count, and measured wall time from the durable report.
- Exclude prompts, summaries, hypotheses, evidence IDs or bodies, resource identifiers, integration details, provider errors, actor identity, credentials, and cancellation prose.
- Default identity attributes to `none`. Operators may explicitly select investigation ID or tenant plus investigation ID after a privacy and cardinality review.
- Export through the official OTLP/HTTP trace SDK with a bounded in-memory batch queue. Recording and delivery failure cannot change investigation behavior or persistence.
- Use standard base or signal-specific OTLP endpoint and protected header configuration, preferably targeting a customer-controlled OpenTelemetry Collector.

## Consequences

- Customers can route investigation traces to another compatible backend without changing platform contracts or application code.
- The trace is an observational projection and cannot be used to reconstruct a report or grant authority.
- Completed, failed recovery, and cancelled reports are observable; a process crash before terminal persistence becomes observable when stale-lease recovery commits its report.
- The bounded queue may lose telemetry on abrupt process termination or sustained exporter failure. Durable telemetry buffering and delivery-health objectives remain production hardening work.

## Revisit triggers

Revisit after representative design-partner load establishes trace cardinality and loss budgets, when background workers add per-step spans, or when an applicable OpenTelemetry semantic convention replaces these provisional `iip.*` attributes.
