# ADR 0148: Keep operational alert delivery customer-owned

**Status:** Accepted

**Date:** 2026-09-06

## Context

IIP emits bounded operational and AI economics metrics over OTLP, but a private
pilot also needs an explicit starting alert policy. Alert storage, evaluation,
routing, contacts, and escalation differ across customer telemetry systems.
Putting an alert manager, contact details, or backend credentials inside IIP
would weaken the vendor-neutral and privacy-first boundary.

An exporter or Collector outage can also prevent the backend from seeing the
very metric that would describe the outage. A backend rule over IIP metrics
therefore cannot honestly prove end-to-end delivery health by itself.

## Decision

1. Keep OpenTelemetry metric semantics as the source contract. Do not import
   Prometheus or alert-manager dependencies into the application core.
2. Ship an optional Helm `PrometheusRule` as the first operational adapter. It
   is disabled by default and requires metric export to be enabled.
3. Bind that adapter to the documented
   `otel-prometheus-underscore-no-suffix-v1` translation profile. Other
   backends translate the same semantic conditions into their own rule model.
4. Aggregate dynamic tenant, source, profile, and resource identities out of
   alert expressions and labels. Operators use authenticated IIP reports for
   authorized drill-down.
5. Install no Prometheus Operator CRD, rule selector, Alertmanager,
   notification receiver, silence, inhibition, credential, or contact point.
   Those remain customer-owned and must be tested in the selected environment.
6. State the observation gap explicitly: external probes plus Collector and
   backend self-monitoring cover missing telemetry and delivery failures.

## Consequences

- A customer using the supported Prometheus translation profile can install a
  bounded default policy with the same Helm release and tune only documented
  durations and coverage thresholds.
- Replacing the telemetry backend does not change IIP collection or domain
  contracts, but it does require a customer/backend-specific rule adapter.
- Rule rendering is repository-tested; actual selection, evaluation, routing,
  escalation, and delivery remain customer qualification gates.
- Alerts contain static scope and severity only. They do not become a new
  tenant data export or evidence authority.

## Alternatives considered

- Bundling a complete Prometheus and Alertmanager stack was rejected because
  it would duplicate the customer observability platform and make IIP less
  vendor-neutral.
- Alerting only on IIP exporter status was rejected because missing delivery
  can make that signal invisible to the evaluating backend.
- Embedding tenant/source labels was rejected because it increases cardinality
  and discloses protected identifiers through notification systems.
