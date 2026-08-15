# OpenTelemetry metrics export

**Status:** Reference implementation

The API can export bounded ingestion-freshness metrics over OTLP/HTTP protobuf to an OpenTelemetry Collector or compatible endpoint. Export is optional and disabled by default. The public `IngestionFreshnessReport` and its underlying PostgreSQL/in-memory facts remain authoritative.

This path exports IIP's own operational measurements. It does not receive customer workload telemetry and does not query historical metrics from a customer's backend. Those are separate evidence-provider/receiver units described in the [portability boundary](../architecture/opentelemetry-portability.md).

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

The target starts OpenTelemetry Collector `0.158.0`, sends the reference measurement through the official Python SDK, verifies that the Collector debug exporter received `iip.ingestion.checkpoint.age`, and removes the container afterward.

To add a Collector to the long-running development stack, first configure the database password and hashed Bearer identity described in [local development](local-development.md), then run:

```bash
export IIP_OTEL_METRICS_ENABLED=true
docker compose --profile telemetry -f deploy/docker-compose.yml up --build --detach
```

The API uses `http://otel-collector:4318/v1/metrics` through the Compose service network. A successful authenticated freshness request records the metrics; it does not poll on its own. The test Collector logs detailed metric payloads, so it is development-only and must not receive sensitive production attributes.

## Runtime configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `IIP_OTEL_METRICS_ENABLED` | `false` | Explicitly compose the exporter |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | none outside Compose | Base HTTP endpoint; `/v1/metrics` is appended |
| `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` | unset | Exact metrics endpoint; takes precedence over the base endpoint |
| `OTEL_EXPORTER_OTLP_PROTOCOL` | `http/protobuf` | Only `http/protobuf` is supported by this adapter |
| `OTEL_EXPORTER_OTLP_METRICS_PROTOCOL` | unset | Signal-specific protocol override |
| `OTEL_EXPORTER_OTLP_HEADERS` | unset | SDK header configuration supplied through protected runtime configuration |
| `IIP_OTEL_INGESTION_ATTRIBUTE_MODE` | `source` | `none`, `source`, or `tenant-source` |
| `OTEL_SERVICE_NAME` | `infra-intelligence-api` | OpenTelemetry service identity |
| `OTEL_METRIC_EXPORT_INTERVAL` | `60000` | SDK export interval in milliseconds |
| `OTEL_METRIC_EXPORT_TIMEOUT` | `10000` | SDK export timeout in milliseconds |

The endpoint must be explicit HTTP(S), no longer than 2048 characters, and cannot contain user information, a query, or a fragment. Use `OTEL_EXPORTER_OTLP_HEADERS` or deployment-native authentication instead of embedding credentials in a URL.

## Helm

Set `telemetry.metricsEnabled`, `telemetry.otlpEndpoint`, and the matching NetworkPolicy egress selector. If the endpoint requires headers, put their standard SDK value in an existing Secret and configure `telemetry.existingSecret`; the chart references the Secret and does not render the value.

The Helm chart does not deploy a Collector because topology and backend selection belong to the customer deployment. Point it at a customer-controlled Collector so changing exporters or destinations does not require an IIP build.

## Production gaps

The reference adapter is failure-isolated but not a complete production telemetry pipeline. Production enablement still requires an automatic sampling schedule, Collector and exporter queue/delivery monitoring, a defined loss objective, cardinality budgets, TLS/authentication policy, regional routing, and representative load tests. Background SDK delivery failures are not reflected in `iip.telemetry.record.failures`; monitor the Collector and SDK logs/telemetry until an explicit export-health contract is implemented.
