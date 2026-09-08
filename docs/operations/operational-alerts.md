# Operational alert-policy handoff

**Status:** Reference Prometheus adapter; customer operation required

**Date:** 2026-09-06

IIP can render an optional Prometheus Operator `PrometheusRule` from its Helm
chart. The rules cover missing component telemetry, backend-observed platform
availability, ingestion freshness, local metric-recording failure, and—when
enabled—AI usage and cost coverage. They are a portable starting policy, not a
monitoring service or an SLA.

OpenTelemetry remains the source signal contract. Prometheus is only the first
rule adapter. The chart installs no CRD, Prometheus server, rule selector,
Alertmanager, notification receiver, silence, inhibition, credential, or
contact point.

## Prerequisites and enablement

Before enabling the profile, the customer telemetry owner must:

1. export IIP metrics through the selected customer Collector;
2. expose those metrics to a Prometheus-compatible evaluator using the exact
   `UnderscoreEscapingWithoutSuffixes` translation strategy;
3. install the `monitoring.coreos.com/v1` `PrometheusRule` CRD;
4. ensure the selected Prometheus instance discovers the rule namespace and
   labels; and
5. configure and privately test Alertmanager routing, ownership, escalation,
   silences, inhibition, and a synthetic notification.

The relevant Collector exporter is:

```yaml
exporters:
  prometheus:
    endpoint: 0.0.0.0:8889
    enable_open_metrics: true
    translation_strategy: UnderscoreEscapingWithoutSuffixes
```

Copy and review the overlay rather than editing the chart defaults:

```bash
helm lint deploy/helm/infra-intelligence \
  --values /protected/path/production-core.values.yaml \
  --values /protected/path/production-ai-finops.values.yaml \
  --values /protected/path/production-operational-alerts.values.yaml

helm upgrade --install iip deploy/helm/infra-intelligence \
  --namespace iip-system \
  --values /protected/path/production-core.values.yaml \
  --values /protected/path/production-ai-finops.values.yaml \
  --values /protected/path/production-operational-alerts.values.yaml
```

Start from
`deploy/helm/infra-intelligence/examples/production-operational-alerts.values.yaml`.
Set `operationalAlerts.namespace` and `labels` to the exact values selected by
the customer's Prometheus rule selectors. The chart rejects enabled alerts
when `telemetry.metricsEnabled` is false. Metric export also requires
`telemetry.otlpEndpoint`.

Both production deployment profiles require this bounded policy. Before
installation, run the [customer deployment preflight](customer-deployment-preflight.md)
against the exact context. Static mode checks the closed policy; cluster mode
also discovers the exact namespaced PrometheusRule API and confirms the target
namespace exists. That pass still does not prove selector, evaluation, or
notification delivery.

After installation, run the read-only
[customer operational-alert qualification](customer-operational-alert-qualification.md).
It verifies rule selection and health, translated heartbeat presence, router
readiness, and one customer-generated synthetic firing/recovery receipt. The
qualifier does not install a rule or send an alert.

Before publishing a release, validate both rendered production profiles with
the same Prometheus parser used by the reference backend:

```bash
make test-operational-alerts
```

This Docker Desktop gate runs Prometheus `promtool` against the rendered core
and AI FinOps rule groups. It proves syntax for the selected documented
metric-name profile only; it does not prove a customer's Collector translation
or contact or configure a customer monitoring system.

## Rule inventory

| Rule | Enabled when | Condition |
| --- | --- | --- |
| `IIPApiTelemetryAbsent` | alert profile | no API heartbeat reached the backend during the configured lookback |
| `IIPWorkflowWorkerTelemetryAbsent` | workflow worker | no worker heartbeat reached the backend during the configured lookback |
| `IIPOtlpReceiverTelemetryAbsent` | any OTLP receiver | no receiver heartbeat reached the backend during the configured lookback |
| `IIPQueryAvailabilityBelowObjective` | alert profile | eligible query availability is below the chart objective after its sample floor |
| `IIPOtlpReceiverAvailabilityBelowObjective` | any OTLP receiver | eligible receiver availability is below the chart objective after its sample floor |
| `IIPIngestionFreshnessObjectiveViolated` | alert profile | at least one emitted source evaluation is outside its freshness objective |
| `IIPTelemetryRecordingFailures` | alert profile | an IIP process rejected a local metric-recording operation in the configured window |
| `IIPAiUsageCoverageIncomplete` | AI usage receiver | one or more requests in an emitted profile window lack supported usage facts |
| `IIPAiCostCoverageUnresolved` | AI cost engine | one or more emitted requests are unpriced, ambiguous, or pending |

Availability objectives and sample floors come from `queryAvailabilitySlo`
and `otlpIngest.availabilitySlo`. Alert durations and AI coverage thresholds
come from `operationalAlerts`. Component heartbeat lookback and pending
duration come from `telemetryHeartbeatWindowSeconds` and
`telemetryHeartbeatForSeconds`; the chart requires the lookback to span at
least two export intervals and two health cycles. Missing ordinary workload
series do not fire availability, freshness, or AI rules. Use independent HTTPS
probes, Collector queue/loss and health monitoring, and backend self-monitoring
alongside the component heartbeat rules.

Expressions aggregate dynamic identities before evaluation, and emitted alert
labels contain only static severity and scope. Do not add tenant IDs, source
IDs, resource names, profile IDs, prompts, responses, provider messages, or
credentials to alert labels or notification templates.

## Component telemetry absence

Each enabled process records `iip.telemetry.heartbeat=1` during its existing
failure-isolated exporter-health cycle. The only metric attribute is the closed
component type; the configured OTel resource `service.name` selects API,
workflow-worker, or receiver in the Prometheus translation. No traffic is
required, so an idle deployment remains observable.

An alert means that the customer backend has not received that service's
heartbeat for the complete lookback and pending duration. It does not identify
whether the process, SDK exporter, network, Collector, backend ingestion, or
rule evaluator failed. Check the independent HTTPS probe and Kubernetes
readiness first, then exporter health, Collector queue/loss, backend ingestion,
and rule evaluation. Do not silence this alert merely because workload traffic
is intentionally idle.

## Query availability

Confirm external HTTPS reachability and the exact release identity first.
Compare the rule window, target, and sample floor with the installed values.
Use the authenticated availability report and minimized deployment diagnostic
for drill-down. Excluded invalid, unauthenticated, denied, and unsupported
requests are intentionally outside the denominator.

## Receiver availability

Check receiver pods, dependency readiness, client TLS/channel qualification,
and admission limits. An authenticated rate-limited request is unavailable;
invalid, unauthenticated, denied, and disabled requests are excluded. TLS
handshake failures occur before the receiver measurement and require Collector
or synthetic telemetry.

## Ingestion freshness

Open the tenant-authorized ingestion freshness report to identify the source
and exact violation. Check provider collection, checkpoint age, observation
age, pending events, worker readiness, and database availability. Source
identity is deliberately absent from the alert.

## Local recording failures

Inspect process health and bounded application logs under the customer's data
handling policy. This counter means the local instrumentation API rejected a
measurement. It does not mean the OTLP exporter, Collector, backend, or alert
delivery succeeded. Those hops need independent monitoring.

## AI usage and cost coverage

Use authenticated, tenant-scoped usage, allocation, price qualification, and
savings views. Incomplete usage and unresolved price states are not zero cost.
Check the selected instrumentation compatibility profile, protected channel
mapping, catalog generation, qualification expiry, model/region/operation
match, and worker freshness. Never copy prompts or responses into a ticket.

## Non-Prometheus backends

Translate the semantic conditions and objective values above into the
customer backend's rule language. Do not reuse these metric names if the
Collector/backend applies a different name translation. Preserve aggregation,
minimum-sample, privacy, and missing-data semantics. Qualify one deliberately
firing synthetic condition and one recovery notification before relying on
the adapter in a pilot. The bundled customer qualification targets the
documented Prometheus/Alertmanager API profile; another backend needs an
equivalent source-bound adapter and report rather than an unsupported claim.
