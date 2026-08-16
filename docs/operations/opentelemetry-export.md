# OpenTelemetry metrics and investigation trace export

**Status:** Reference implementation

The API can export bounded ingestion-freshness metrics and terminal investigation spans over OTLP/HTTP protobuf to an OpenTelemetry Collector or compatible endpoint. Each signal is optional and disabled by default. Public reports and their underlying PostgreSQL/in-memory facts remain authoritative.

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
| `iip.telemetry.record.failures` | dimensionless | Local instrument-recording failures; not network delivery failures |

Every measurement has `iip.ingestion.status`. `IIP_OTEL_INGESTION_ATTRIBUTE_MODE` selects `none`, `source`, or `tenant-source` for identity attributes. The default `source` mode adds `iip.source.id`; choose `none` when source names are sensitive or the source count exceeds the deployment's cardinality budget. `tenant-source` must be an explicit privacy and cost decision.

## Docker Desktop verification

Run the isolated real-Collector gate:

```bash
make test-otel
```

The target starts OpenTelemetry Collector `0.158.0`, sends a reference metric and investigation trace through the official Python SDK, verifies that the Collector debug exporter received `iip.ingestion.checkpoint.age` and `iip.investigation.execute`, and removes the container afterward.

To add a Collector to the long-running development stack, first configure the database password and hashed Bearer identity described in [local development](local-development.md), then run:

```bash
export IIP_OTEL_METRICS_ENABLED=true
export IIP_OTEL_TRACES_ENABLED=true
docker compose --profile telemetry -f deploy/docker-compose.yml up --build --detach
```

The API uses the Collector through the Compose service network and appends `/v1/metrics` or `/v1/traces` to the standard base endpoint. A successful authenticated freshness request records metrics; a newly committed terminal investigation records a span. The test Collector logs detailed payloads, so it is development-only and must not receive sensitive production attributes.

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
| `OTEL_SERVICE_NAME` | `infra-intelligence-api` | OpenTelemetry service identity |
| `OTEL_METRIC_EXPORT_INTERVAL` | `60000` | SDK export interval in milliseconds |
| `OTEL_METRIC_EXPORT_TIMEOUT` | `10000` | SDK export timeout in milliseconds |
| `OTEL_BSP_SCHEDULE_DELAY` | `5000` | Trace batch scheduling delay in milliseconds |
| `OTEL_BSP_EXPORT_TIMEOUT` | `10000` | Trace batch export timeout in milliseconds |
| `OTEL_BSP_MAX_QUEUE_SIZE` | `2048` | Bounded in-memory trace queue |
| `OTEL_BSP_MAX_EXPORT_BATCH_SIZE` | `512` | Maximum trace export batch, no larger than the queue |

The endpoint must be explicit HTTP(S), no longer than 2048 characters, and cannot contain user information, a query, or a fragment. Use `OTEL_EXPORTER_OTLP_HEADERS` or deployment-native authentication instead of embedding credentials in a URL.

## Helm

Set `telemetry.metricsEnabled` and/or `telemetry.tracesEnabled`, `telemetry.otlpEndpoint`, and the matching NetworkPolicy egress selector. If the endpoint requires headers, put their standard SDK value in an existing Secret and configure `telemetry.existingSecret`; the chart references the Secret and does not render the value.

The Helm chart does not deploy a Collector because topology and backend selection belong to the customer deployment. Point it at a customer-controlled Collector so changing exporters or destinations does not require an IIP build.

## Production gaps

The reference adapters are failure-isolated but not a complete production telemetry pipeline. Production enablement still requires an automatic freshness sampling schedule, Collector and exporter queue/delivery monitoring, a defined loss objective, reviewed cardinality budgets, TLS/authentication policy, regional routing, and representative load tests. The trace queue is bounded but not durable. Background SDK delivery failures are not reflected in `iip.telemetry.record.failures`; monitor the Collector and SDK logs/telemetry until an explicit export-health contract is implemented.
