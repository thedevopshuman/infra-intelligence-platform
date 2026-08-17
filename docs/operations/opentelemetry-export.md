# OpenTelemetry metrics and investigation trace export

**Status:** Reference implementation

The API and workflow worker can export bounded ingestion-freshness metrics, recognized query availability/latency metrics, and terminal investigation spans over OTLP/HTTP protobuf to an OpenTelemetry Collector or compatible endpoint. Each signal is optional and disabled by default. Public reports and their underlying PostgreSQL/in-memory facts remain authoritative.

This path exports IIP's own operational telemetry. It does not receive customer workload telemetry or query historical metrics. Historical queries use the separate [telemetry evidence contract](../specifications/telemetry-evidence-contract.md) and replaceable backend port, while optional tenant-bound OTLP receivers handle selected pushed customer metrics and logs. The [portability boundary](../architecture/opentelemetry-portability.md) keeps these flows distinct.

## Investigation trace

Each newly committed terminal report can emit one `iip.investigation.execute` span whose timestamps match `startedAt` and `completedAt`. The span contains only outcome, terminal reason, measured wall time, tool-call count, and evidence-item count. It excludes prompts, summaries, hypotheses, evidence identifiers/content, resource data, integration details, provider errors, actor identity, and credentials.

`IIP_OTEL_INVESTIGATION_ATTRIBUTE_MODE` defaults to `none`. `investigation` adds the investigation ID; `tenant-investigation` also adds the tenant ID. Both are explicit privacy and cardinality decisions. Replaying a committed report does not duplicate the span. [ADR 0031](../decisions/0031-otlp-investigation-trace-export.md) records the boundary.

## Metric catalog

| Metric | Unit | Meaning |
| --- | --- | --- |
| `iip.ingestion.checkpoint.age` | seconds | Age of the last completely committed source checkpoint |
| `iip.ingestion.observation.age` | seconds | Age of the latest accepted provider observation, when present |
| `iip.ingestion.delay` | seconds | Provider observation to durable recording delay, when present |
| `iip.ingestion.accepted_observations` | dimensionless | Accepted observations retained for the source |
| `iip.ingestion.pending_events` | dimensionless | Unpublished source outbox events |
| `iip.ingestion.pending_event.age` | seconds | Age of the oldest unpublished source event, when present |
| `iip.ingestion.within_objective` | dimensionless | `1` when the point-in-time evaluation has no violation |
| `iip.ingestion.objective.violation` | dimensionless | One bounded series per known violation, with `1` for active |
| `iip.query.requests` | dimensionless | One increment per recognized control-plane read, classified as available, unavailable, or excluded |
| `iip.query.duration` | seconds | Monotonic serving time for the same recognized read |
| `iip.telemetry.record.failures` | dimensionless | Local instrument-recording failures; not network delivery failures |

Every measurement has `iip.ingestion.status`. `IIP_OTEL_INGESTION_ATTRIBUTE_MODE` selects `none`, `source`, or `tenant-source` for identity attributes. The default `source` mode adds `iip.source.id`; choose `none` when source names are sensitive or the source count exceeds the deployment's cardinality budget. `tenant-source` must be an explicit privacy and cost decision.

Query metrics use only closed operation, outcome, availability, and objective attributes. They never include raw paths, query values, tenant/actor identity, credentials, object identifiers, bodies, or error text. Invalid, unauthenticated, and denied requests are exported as `excluded`; valid successful/not-found/conflict responses are `available`; server and dependency failures are `unavailable`. The configured availability basis-point target, window, and minimum eligible count accompany each observation so the customer backend can aggregate the same semantics. See the [query availability telemetry contract](../specifications/query-availability-telemetry-contract.md) and [ADR 0059](../decisions/0059-backend-neutral-query-availability-telemetry.md).

## Docker Desktop verification

Run the isolated real-Collector gate:

```bash
make test-otel
```

The target starts OpenTelemetry Collector `0.158.0`, sends reference freshness and query metrics plus an investigation trace through the official Python SDK, verifies that the Collector debug exporter received `iip.ingestion.checkpoint.age`, `iip.query.requests`, and `iip.investigation.execute`, and removes the container afterward.

To add a Collector to the long-running development stack, first configure the database password and hashed Bearer identity described in [local development](local-development.md), then run:

```bash
export IIP_OTEL_METRICS_ENABLED=true
export IIP_OTEL_TRACES_ENABLED=true
docker compose --profile telemetry -f deploy/docker-compose.yml up --build --detach
```

The API and worker use the Collector through the Compose service network and append `/v1/metrics` or `/v1/traces` to the standard base endpoint. A successful authenticated freshness request records metrics; the worker also samples the explicitly enrolled local source every 60 seconds. A newly committed terminal investigation records a span. The test Collector logs detailed payloads, so it is development-only and must not receive sensitive production attributes.

## Runtime configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `IIP_OTEL_METRICS_ENABLED` | `false` | Explicitly compose the exporter |
| `IIP_OTEL_TRACES_ENABLED` | `false` | Explicitly compose the terminal investigation trace exporter |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | none outside Compose | Base HTTP endpoint; `/v1/metrics` is appended |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | unset | Exact metrics endpoint; takes precedence over the base endpoint |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | unset | Exact traces endpoint; takes precedence over the base endpoint |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/protobuf` | Only `http/protobuf` is supported by this adapter |
| `OTEL_EXPORTER_OTLP_METRICS_PROTOCOL` | unset | Signal-specific protocol override |
| `OTEL_EXPORTER_OTLP_TRACES_PROTOCOL` | unset | Signal-specific protocol override |
| `OTEL_EXPORTER_OTLP_HEADERS` | unset | SDK header configuration supplied through protected runtime configuration |
| `IIP_OTEL_INGESTION_ATTRIBUTE_MODE` | `source` | `none`, `source`, or `tenant-source` |
| `IIP_OTEL_INVESTIGATION_ATTRIBUTE_MODE` | `none` | `none`, `investigation`, or `tenant-investigation` |
| `IIP_QUERY_AVAILABILITY_SLO_WINDOW_SECONDS` | `3600` | Aggregation window carried on query metrics |
| `IIP_QUERY_AVAILABILITY_SLO_MINIMUM_BASIS_POINTS` | `9990` | Availability target carried on query metrics |
| `IIP_QUERY_AVAILABILITY_SLO_MINIMUM_ELIGIBLE_REQUESTS` | `100` | Sample floor carried on query metrics |
| `IIP_INGESTION_MONITOR_TARGETS_JSON` | unset | Worker-only closed list of 1–1000 unique, explicitly tenant-enrolled freshness targets; omission disables sampling |
| `IIP_INGESTION_MONITOR_INTERVAL_SECONDS` | `60` | Worker-only sampling cadence from 5–3600 seconds |
| `OTEL_SERVICE_NAME` | `infra-intelligence-api` | OpenTelemetry service identity |
| `OTEL_METRIC_EXPORT_INTERVAL` | `60000` | SDK export interval in milliseconds |
| `OTEL_METRIC_EXPORT_TIMEOUT` | `10000` | SDK export timeout in milliseconds |
| `OTEL_BSP_SCHEDULE_DELAY` | `5000` | Trace batch scheduling delay in milliseconds |
| `OTEL_BSP_EXPORT_TIMEOUT` | `10000` | Trace batch export timeout in milliseconds |
| `OTEL_BSP_MAX_QUEUE_SIZE` | `2048` | Bounded in-memory trace queue |
| `OTEL_BSP_MAX_EXPORT_BATCH_SIZE` | `512` | Maximum trace export batch, no larger than the queue |
| `IIP_TELEMETRY_HEALTH_INTERVAL_SECONDS` | `30` | Internal API/worker exporter-health heartbeat interval |
| `IIP_TELEMETRY_HEALTH_STALE_AFTER_SECONDS` | `120` | Age at which a missed internal heartbeat degrades deployment health |
| `IIP_TELEMETRY_HEALTH_RETENTION_SECONDS` | `600` | Bounded crashed-instance visibility and cleanup window |
| `IIP_TELEMETRY_EXPORT_SLO_WINDOW_SECONDS` | `3600` | Rolling sampled exporter-attempt window, 300–2,592,000 seconds |
| `IIP_TELEMETRY_EXPORT_SLO_MINIMUM_ATTAINMENT_BASIS_POINTS` | `9900` | Required per-signal successful-attempt proportion |
| `IIP_TELEMETRY_EXPORT_SLO_MINIMUM_ELIGIBLE_ATTEMPTS` | `20` | Per-signal attempt floor before meeting/breached |
| `IIP_TELEMETRY_EXPORT_SLO_RETENTION_SECONDS` | `604800` | Sample retention; must cover the configured SLO window |

The endpoint must be explicit HTTP(S), no longer than 2048 characters, and cannot contain user information, a query, or a fragment. Use `OTEL_EXPORTER_OTLP_HEADERS` or deployment-native authentication instead of embedding credentials in a URL.

## Helm

Set `telemetry.metricsEnabled` and/or `telemetry.tracesEnabled`, `telemetry.otlpEndpoint`, and the matching NetworkPolicy egress selector. If the endpoint requires headers, put their standard SDK value in an existing Secret and configure `telemetry.existingSecret`; the chart references the Secret and does not render the value. `worker.ingestionMonitorTargets` enables automatic sampling for exact tenant/source pairs, `worker.ingestionMonitorIntervalSeconds` controls its cadence, and `telemetry.workerServiceName` gives worker exports a distinct service identity. Each target tenant must also appear in `worker.tenants` or startup fails closed.

The Helm chart does not deploy a Collector because topology and backend selection belong to the customer deployment. Point it at a customer-controlled Collector so changing exporters or destinations does not require an IIP build. Configure `queryAvailabilitySlo` alongside `telemetry.metricsEnabled`; the backend computes `floor(available * 10000 / (available + unavailable))` over the declared window after the minimum eligible count. Add ingress or synthetic availability separately because an application process cannot emit when every replica is unreachable.

## Delivery health

Every completed exporter attempt updates bounded process-local state. An authenticated identity with the `platform-admin` role and policy approval can read it with:

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $IIP_OPERATOR_TOKEN" \
  http://127.0.0.1:8080/v1/operations/telemetry/export-health
```

The process-local report distinguishes disabled, awaiting-first-attempt, healthy, and degraded metric/trace delivery and intentionally omits the configured endpoint and provider failures. The deployment-wide operation includes recent pseudonymous API and workflow-worker heartbeats from the shared store:

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $IIP_OPERATOR_TOKEN" \
  http://127.0.0.1:8080/v1/operations/telemetry/deployment-export-health
```

Missed heartbeats become stale and degrade the deployment report; graceful stops retire immediately. Counters remain per process and reset on restart. Bounded historical samples turn their monotonic deltas into a rolling per-signal objective:

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $IIP_OPERATOR_TOKEN" \
  http://127.0.0.1:8080/v1/operations/telemetry/export-slo
```

The SLO reports disabled, no-data, insufficient-data, meeting, or breached without exposing tenant telemetry or backend details. `/readyz` remains independent, so telemetry backend failure or health-report write failure does not interrupt customer workflows.

## Production gaps

The reference adapters and automatic sampler are failure-isolated but not a complete production telemetry pipeline. Production enablement still requires Collector-side queue/delivery monitoring, an end-to-end loss objective, reviewed cardinality budgets, TLS/authentication policy, regional routing, representative load tests, ingress/synthetic availability, burn-rate rules, and notification routing. The trace queue is bounded but not durable. The sampled SLO covers recent control-plane and worker SDK export attempts, not Collector queue durability, desired replica membership, backend ingestion, or regional aggregation. Query SLO aggregation belongs to the customer telemetry backend and must account for Collector/export loss before making a production claim.
