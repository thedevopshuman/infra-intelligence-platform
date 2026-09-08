# Customer operational-alert qualification contracts

**Status:** `v1alpha1` executable customer-environment evidence

**Decision:** [ADR 0151](../decisions/0151-customer-operational-alert-route-qualification.md)

**Machine contracts:**
[`customer-operational-alert-qualification-profile.schema.json`](../../contracts/schemas/customer-operational-alert-qualification-profile.schema.json)
and
[`customer-operational-alert-qualification-report.schema.json`](../../contracts/schemas/customer-operational-alert-qualification-report.schema.json)

These contracts qualify one customer-selected Prometheus-compatible evaluator,
Alertmanager-compatible router, and synthetic notification receipt. They are
host-side release artifacts, not control-plane resources, and add no OpenAPI
route.

## Protected profile

`CustomerOperationalAlertQualificationProfile` is a reviewed mode-`0600`
input. It selects `core-v1` or `ai-finops-v0`, three HTTPS endpoints, the exact
production and synthetic rule groups, one opaque probe/route pair, expected
component service names, the customer deployment's opaque cluster/namespace
bindings, and bounded time/response objectives, including an explicit accepted
future clock skew.

The `core-v1` service sequence is `api`, `workflow-worker`; `ai-finops-v0` adds
`otlp-receiver`. The corresponding expected production rule counts are five
and nine. These closed shapes prevent a partial rule set from being called
qualified.

Prometheus, Alertmanager, and receipt bearer values are supplied through three
distinct mode-`0600` files. Their CA bundles are separate file inputs. No
credential or certificate value is copied into the report.

## Read-only observations

The qualifier performs bounded HTTPS `GET` requests with environment proxies
disabled and redirects denied:

- Prometheus v1 build information establishes API readiness;
- Prometheus v1 alert rules establish exact group discovery, expected rule
  presence, healthy rule evaluation, and a healthy synthetic rule;
- an instant `count by(job) (iip_telemetry_heartbeat)` query establishes that
  the documented metric translation exposes every selected component;
- Alertmanager v2 status establishes that the selected router is ready (or is
  deliberately running in supported standalone mode); and
- the receipt endpoint supplies one exact, recent firing/resolved pair for the
  protected probe and route.

The receipt response is closed:

```json
{
  "apiVersion": "iip.qualification/v1",
  "kind": "NotificationReceipt",
  "spec": {
    "probeId": "iip-alert-route-20260908",
    "alertName": "IIPQualificationSynthetic",
    "routeId": "platform-primary",
    "events": [
      {"state": "firing", "sourceAt": "2026-09-08T09:58:00Z", "receivedAt": "2026-09-08T09:58:02Z"},
      {"state": "resolved", "sourceAt": "2026-09-08T09:59:00Z", "receivedAt": "2026-09-08T09:59:03Z"}
    ]
  }
}
```

Both receipt source and arrival times must be within
`maximumObservationAgeSeconds`, ordered firing then resolved, and no earlier
than their source timestamp. The greater delivery latency must meet
`maximumNotificationLatencyMilliseconds`. The synthetic rule must be healthy
and inactive after the recovery receipt.

## Minimized report

`CustomerOperationalAlertQualificationReport` contains twenty ordered
checks, aggregate measurements and observations, exact release identity,
objective values, and eleven SHA-256 bindings. Its validity ends at the earlier
of the requested report lifetime and the protected profile's age boundary. The
`coar_` identifier is derived
from every retained metadata/specification field. Semantic validation
recomputes rule/component expectations, check results, summary, time relations,
release binding, and identifier.

The report excludes endpoints, service names, probe/route IDs, labels,
credentials, certificate values, contact details, and notification bodies.
Offline verification rebinds the protected profile, current clean source,
immutable image, and all three CA bundles and rejects expired or crossed
evidence, including reports generated beyond the accepted future clock skew.

`qualified` proves one current synthetic firing/recovery path only. It does not
qualify the receipt service's authenticity/retention, other routes or receivers,
silences/inhibition, human response/escalation, monitoring HA, long-window or
regional SLOs, disaster recovery, or the effects of a real component failure.
