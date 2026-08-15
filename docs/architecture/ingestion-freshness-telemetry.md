# Ingestion freshness telemetry

**Status:** Phase 1 reference implementation
**Date:** 2026-08-15
**Decision:** [ADR 0011](../decisions/0011-ingestion-freshness-semantics.md)

This boundary answers one operational question: for a tenant and configured source, is the latest completely committed collection and its downstream event delivery still within the platform's current objectives? It reads existing authoritative records; it does not create a second telemetry database or accept provider claims about health.

## Flow and ownership

```mermaid
flowchart LR
    Credential["Bearer credential"] --> HTTP["GET /v1/telemetry/ingestion"]
    HTTP --> Policy["Tenant and policy check"]
    Policy --> Service["Freshness evaluator"]
    Checkpoint["Committed source checkpoint"] --> Repository["Telemetry repository port"]
    History["Accepted observation history"] --> Repository
    Outbox["Pending source events"] --> Repository
    Repository --> Service
    Objectives["Trusted runtime objectives"] --> Service
    Clock["Platform clock"] --> Service
    Service --> Report["IngestionFreshnessReport"]
```

The application owns `SourceIngestionTelemetryRepository` and the calculation. The in-memory and PostgreSQL adapters return raw tenant/source facts. The HTTP surface authenticates, parses one `sourceId`, and maps stable errors. SDKs consume only the public report contract.

## Signals

| Signal | Source | Meaning |
| --- | --- | --- |
| Checkpoint age | Last complete committed source checkpoint | Primary collection freshness, including empty reconciliations |
| Observation age | Latest accepted provider observation time | Age of the newest accepted source fact |
| Ingestion delay | Latest observation time to platform recording time | Delay before the source fact became durable |
| Pending event count and oldest age | Transactional outbox joined to the source event | Downstream delivery backlog |
| Clock-skew violation | Provider/storage timestamp compared with platform time | Prevents future timestamps from appearing healthy |

Partial, failed, cancelled, stale-resume, and scope-conflicting collection results do not advance the checkpoint, so they cannot reset freshness. The result never includes opaque provider cursors, checkpoints, resource documents, credential references, or provider exception text.

## Tenancy and failure behavior

Authentication derives the actor and tenant before query parsing reaches storage. Policy authorizes `ingestion-telemetry:read`, and the repository query requires both tenant and source predicates. A missing source returns the same `ingestion.source_not_found` code whether the source does not exist or exists only in another tenant.

Adapter inconsistency and storage failures fail closed as `storage.unavailable`. Objective values are constructed only in `bootstrap.py`; requests cannot override them. Negative durations are clamped to zero, and timestamps beyond the allowed skew produce a violation rather than a misleading negative measurement.

## Export portability

The report is the authoritative product result, not an observability-backend read model. A future telemetry-sink port will map its bounded measurements to OpenTelemetry metrics and send them asynchronously through an OTLP adapter. A customer-controlled Collector can then change the downstream backend without changing freshness semantics or application code. See the [OpenTelemetry portability boundary](opentelemetry-portability.md).

## Current limit

The report is a point-in-time Phase 1 SLI with local objectives. It proves that source lag is measurable at the API boundary. Aggregated time windows, production SLO/error-budget policy, alert routing, and the runtime OTLP exporter remain Phase 3 work.
