# ADR 0151: Qualify customer alert evaluation and one notification route

**Status:** Accepted

**Date:** 2026-09-08

## Context

The chart and release gates now prove that the operational rules are valid
Prometheus syntax and that a customer cluster exposes the selected
`PrometheusRule` API and namespace. Component heartbeats also provide an idle,
metadata-only signal. None of those facts proves that the customer's evaluator
selects the installed rule, evaluates the translated metrics, hands alerts to
its router, or delivers firing and recovery notifications.

Installing or reconfiguring Prometheus, Alertmanager, or a contact destination
would cross the platform's ownership boundary. Deliberately stopping an IIP
component merely to test routing would also create unnecessary customer risk.

## Decision

1. Add a protected customer operational-alert qualification profile and a
   minimized, source-bound report. They remain offline operational artifacts
   and do not add a control-plane API. The profile carries the opaque cluster
   and namespace bindings from the qualified customer deployment.
2. Read the customer-selected Prometheus-compatible v1 and
   Alertmanager-compatible v2 APIs over CA-verified HTTPS with proxies and
   redirects disabled. Credentials remain in three distinct mode-`0600` files.
3. Require the production rule group to contain every rule for the selected
   `core-v1` or `ai-finops-v0` profile with healthy evaluation state. Require a
   positive heartbeat series for every expected component service.
4. Require a customer-created `IIPQualificationSynthetic` rule in a separate
   group. The customer first drives it firing and then inactive; the qualifier
   requires it to be healthy and inactive after recovery and never creates,
   updates, deletes, silences, or submits an alert.
5. Read an independently protected receipt endpoint that returns exactly one
   recent firing and one recent resolved receipt for the selected probe and
   route. Treat that endpoint as customer evidence, not as a platform-owned
   notification service.
6. Retain only counts, booleans, timestamps, objective values, release identity,
   deployment bindings, and SHA-256 endpoint/input bindings. Cap report
   validity at the protected profile's age boundary and reject evidence dated
   beyond the reviewed clock-skew objective. Do not retain URLs,
   service names, route/probe IDs, labels, credentials, certificate values,
   alert payloads, or contacts.

## Consequences

- A customer can produce machine-verifiable evidence for one exact rule set and
  notification route without granting IIP mutation authority.
- The report separates rule loading, rule health, translated heartbeat
  presence, router readiness, firing delivery, recovery delivery, and latency.
- The result depends on the authenticity and retention behavior of the
  customer receipt service. That dependency remains an explicit limitation.
- One passing route does not qualify other receivers, silences, inhibition,
  escalation, human acknowledgement, HA, regional aggregation, or a real
  component failure.

## Alternatives considered

- Posting alerts directly to Alertmanager was rejected because it bypasses
  Prometheus rule selection and evaluation.
- Having the qualifier install a temporary `PrometheusRule` was rejected
  because a read-only evidence tool should not acquire Kubernetes mutation
  authority.
- Stopping an IIP component to fire a production heartbeat rule was rejected
  because routing can be qualified without creating an application incident.
