# Prometheus telemetry evidence

**Status:** Executable reference adapter

The control plane can query a Prometheus-compatible HTTP API for bounded historical metrics and normalize the result into an immutable `TelemetryEvidenceResult` artifact. This is customer telemetry evidence. It is separate from outbound OTLP platform observability and from the inbound OTLP metrics receiver, which accepts selected pushed batches instead of executing historical queries.

The adapter implements Prometheus's range-query HTTP API with form-encoded `POST /api/v1/query_range`. Public requests never contain PromQL, endpoints, backend metric names, or credentials. [ADR 0015](../decisions/0015-prometheus-telemetry-evidence-adapter.md) records the security and translation decisions.

## Docker Desktop interoperability gate

Run:

```bash
make test-prometheus
```

The target starts pinned Prometheus `v3.13.1`, waits for readiness, queries its real self-scraped `up` metric through the adapter, verifies normalized series and points, and removes the container and temporary storage. It is intentionally separate from `make verify` so ordinary contract work does not require Docker or image pulls.

## Runtime selection

The default is safe and network-free:

```text
IIP_TELEMETRY_METRICS_BACKEND=no-data
```

Select Prometheus explicitly and supply the non-secret integration registry through protected deployment configuration:

```bash
export IIP_TELEMETRY_METRICS_BACKEND=prometheus
export IIP_PROMETHEUS_INTEGRATIONS_JSON="$(tr -d '\n' < deploy/prometheus/integrations.example.json)"
```

Each registry entry is scoped by `tenantId` and `integrationId` and includes:

- an explicit HTTP(S) endpoint with no user information, query, or fragment;
- an enabled flag, request timeout, and raw response byte limit;
- an optional opaque `credentialRef`; and
- an allowlisted metric catalog mapping logical metric and attribute names to Prometheus metric and label names plus a normalized unit.

The committed example intentionally has no credential and targets the optional Compose Prometheus service. It is development configuration, not a customer default.

Start that long-running profile with:

```bash
export IIP_POSTGRES_PASSWORD="$(openssl rand -hex 24)"
export IIP_TELEMETRY_METRICS_BACKEND=prometheus
export IIP_PROMETHEUS_INTEGRATIONS_JSON="$(tr -d '\n' < deploy/prometheus/integrations.example.json)"
docker compose -f deploy/docker-compose.yml --profile telemetry-query up --build --detach
```

## Credential resolution

When an integration declares a `credentialRef`, the reference is resolved for the exact authorized request. The local reference broker accepts protected JSON with this shape:

```json
{
  "credentials": [
    {
      "tenantId": "tenant-a",
      "integrationId": "observability-primary",
      "credentialRef": "credential://tenant-a/prometheus/primary",
      "bearerToken": "supplied-by-a-secret-store",
      "expiresAt": "2026-08-16T12:00:00Z"
    }
  ]
}
```

Supply it only through a secret-bearing runtime channel as `IIP_PROMETHEUS_CREDENTIALS_JSON`. Never commit a populated document, put it in a ConfigMap, or pass it in a telemetry request. The Helm chart reads this variable only from `telemetryEvidence.prometheus.credentialsExistingSecret`.

Alternatively select the shared [external credential broker](credential-broker.md). In `external-http` mode, `IIP_PROMETHEUS_CREDENTIALS_JSON` is not required; the adapter sends the exact tenant, actor, integration, `prometheus`, `metrics:read`, logical reference, and deadline to the broker and accepts only a validated short Bearer lease.

The static broker proves exact tenant/integration/reference/scope/deadline resolution but is not the production credential solution. Production requires the external client plus an issuer that validates workload and request policy, issues short-lived access, and supports rotation, revocation, and secret-free audit.

## Translation and limits

`avg`, `min`, `max`, `sum`, and `count` map to Prometheus aggregators. `p50`, `p95`, and `p99` map to the Prometheus `quantile` aggregator. Attribute equality/inequality filters become exact label matchers, and grouping uses only catalogued labels. An inequality also adds a non-empty matcher so a missing Prometheus label cannot satisfy the provider-neutral `neq` semantics.

`rate` currently fails as unsupported because a safe translation needs counter and range-window semantics not present in the public v1alpha1 request. The adapter does not guess.

The adapter refuses redirects, bounds socket time and raw response bytes, requests at most one series beyond the public maximum to detect overflow, accepts only matrix float samples, and maps only allowlisted labels back to logical attributes. Provider warnings become `backend-partial`; provider error and warning text never becomes evidence or an HTTP response.

## Investigation selection

An `InvestigationRequest` may include ordered `telemetrySelections` using the same logical metric, filters, aggregation, grouping, and limits. The candidate's `integrationId` must resolve in this registry, and its metric and attributes must exist in that integration's allowlist. The investigation supplies tenant, actor, resource references, time range, and deadline; none of those can be overridden by the selection.

When `evidenceTypes` or `allowedTools` are present, include `telemetry.metrics` and `telemetry/query` respectively. A candidate with `rootCauseClasses` runs only when the deterministic resource classifier selects a matching class. Attempts and committed Evidence records count against the investigation's tool and evidence budgets.

To let a normalized result support or contradict that class, declare `interpretation` with a statistic, the exact catalog unit, comparison operator, finite threshold, and distinct matched/unmatched dispositions. For example, a dimensionless availability metric may use `minimum`, unit `1`, `gte`, threshold `1`, matched `supports`, and unmatched `contradicts`. The investigator assesses only the committed normalized artifact. A backend/catalog unit mismatch fails closed; no implicit unit conversion occurs, and partial or no-data results are never hypothesis citations.

Use `baselineComparison` instead when the question is about change between periods. Declare non-overlapping baseline and evaluation subranges inside the investigation range, plus `difference` or `ratio`. The adapter still executes exactly one range query for the inherited investigation scope; the investigator splits the committed normalized points locally. A difference keeps the catalog unit, a ratio uses unit `1`, and missing points or a zero baseline ratio denominator produce `incomplete`. The Docker gate exercises this path against Prometheus's real self-scraped `up` series.

## Helm

Set `telemetryEvidence.backend: prometheus`, put the non-secret registry in `telemetryEvidence.prometheus.integrationsJson`, and reference a Kubernetes Secret for credentials when required. If NetworkPolicy is enabled, configure `networkPolicy.prometheusEgress` for the exact Prometheus namespace, pod labels, and port. The chart contains no endpoint credentials by default.
